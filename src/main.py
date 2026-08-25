import time
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import robin_stocks.robinhood as r

from . import auth, executor
from .config import load_config
from .logger import get_logger
from .momentum_tracker import MomentumTracker
from .risk_manager import RiskManager
from .scanner import get_runner_candidates

ET = ZoneInfo("America/New_York")


def _parse_et_time(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _now_et() -> datetime:
    return datetime.now(ET)


def _is_market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)


def _in_entry_window(cfg: dict, now: datetime) -> bool:
    start = _parse_et_time(cfg["entry_window_start_et"])
    end = _parse_et_time(cfg["entry_window_end_et"])
    return start <= now.time() <= end


def _past_force_exit(cfg: dict, now: datetime) -> bool:
    return now.time() >= _parse_et_time(cfg["force_exit_time_et"])


def get_market_snapshot(symbols: list[str], log) -> dict:
    """Batch-fetch {symbol: {'price': float, 'volume': float|None}} for
    open positions. `volume` is today's cumulative share volume, used to
    derive a buying-pace rate for distribution detection."""
    if not symbols:
        return {}

    try:
        quotes = r.stocks.get_quotes(symbols)
    except Exception as e:
        log.warning("Failed to fetch quotes: %s", e)
        quotes = [None] * len(symbols)

    try:
        fundamentals = r.stocks.get_fundamentals(symbols)
    except Exception as e:
        log.warning("Failed to fetch fundamentals: %s", e)
        fundamentals = [None] * len(symbols)

    snapshot = {}
    for i, symbol in enumerate(symbols):
        q = quotes[i] if i < len(quotes) else None
        f = fundamentals[i] if i < len(fundamentals) else None

        price = None
        if q:
            try:
                price = float(q.get("last_trade_price"))
            except (TypeError, ValueError):
                price = None
        if price is None:
            continue

        volume = None
        if f:
            try:
                volume = float(f.get("volume"))
            except (TypeError, ValueError):
                volume = None

        snapshot[symbol] = {"price": price, "volume": volume}
    return snapshot


def _volume_drying_up(pos: dict, cfg: dict) -> bool:
    peak_rate = pos.get("peak_volume_rate")
    last_rate = pos.get("last_volume_rate")
    if not peak_rate or last_rate is None:
        return False
    return last_rate <= peak_rate * (1 - cfg["distribution_volume_drop_pct"])


def manage_open_positions(risk: RiskManager, cfg: dict, dry_run: bool, log, snapshot: dict):
    positions = risk.open_positions()
    if not positions:
        return

    force_exit = _past_force_exit(cfg, _now_et())

    for symbol in list(positions.keys()):
        data = snapshot.get(symbol)
        if not data:
            log.warning("%s: no current price (possibly halted); cannot evaluate exit", symbol)
            continue

        price = data["price"]
        risk.update_position_tracking(symbol, price, data.get("volume"))
        pos = risk.open_positions()[symbol]

        change_pct = (price - pos["entry_price"]) / pos["entry_price"]
        peak = pos.get("peak_price", pos["entry_price"])
        giveback_pct = (peak - price) / peak if peak else 0.0

        reason = None
        if change_pct <= -cfg["stop_loss_pct"]:
            reason = f"stop loss ({change_pct:.1%})"
        elif cfg.get("take_profit_cap_pct") and change_pct >= cfg["take_profit_cap_pct"]:
            reason = f"take-profit cap hit ({change_pct:.1%})"
        elif change_pct > 0 and giveback_pct >= cfg["trailing_giveback_pct"]:
            reason = f"trailing exit: gave back {giveback_pct:.1%} off peak ${peak:.2f}"
        elif change_pct <= 0.02 and _volume_drying_up(pos, cfg):
            reason = "distribution: buying pace dropped off its peak while price stalls"
        elif force_exit:
            reason = "force exit time reached (no overnight holds)"

        if reason and executor.exit_position(symbol, pos["qty"], dry_run, log, reason=reason):
            pnl = risk.record_close(symbol, price)
            log.info("Closed %s: %s, P&L $%.2f", symbol, reason, pnl or 0.0)


def try_open_new_positions(risk: RiskManager, cfg: dict, dry_run: bool, log, tracker: MomentumTracker):
    if not risk.can_open_new_position():
        return

    candidates = get_runner_candidates(cfg, log, tracker)
    candidates.sort(key=lambda c: c["score"], reverse=True)

    for candidate in candidates:
        if not risk.can_open_new_position():
            break
        symbol = candidate["symbol"]
        if symbol in risk.open_positions():
            continue

        dollar_amount = risk.position_size_usd()
        if dollar_amount < candidate["ask_price"]:
            continue

        log.info(
            "Candidate %s: score %.2f, last $%.2f (+%.1f%% today), rel volume %s, buzz %d msgs%s",
            symbol,
            candidate["score"],
            candidate["last_price"],
            candidate["gain_pct"] * 100,
            f"{candidate['relative_volume']:.1f}x" if candidate["relative_volume"] else "n/a",
            candidate["buzz_messages_recent"],
            " (trending)" if candidate["trending"] else "",
        )
        result = executor.enter_position(symbol, candidate["ask_price"], dollar_amount, dry_run, log)
        if result:
            risk.record_open(result["symbol"], result["qty"], result["price"])


def run_once(risk: RiskManager, cfg: dict, dry_run: bool, log, tracker: MomentumTracker):
    positions = risk.open_positions()
    snapshot = get_market_snapshot(list(positions.keys()), log)
    prices = {sym: d["price"] for sym, d in snapshot.items()}
    risk.refresh_kill_switch(prices)

    manage_open_positions(risk, cfg, dry_run, log, snapshot)

    now = _now_et()
    if _in_entry_window(cfg, now) and not _past_force_exit(cfg, now):
        try_open_new_positions(risk, cfg, dry_run, log, tracker)


def main():
    cfg = load_config()
    log = get_logger()
    dry_run = cfg["dry_run"]

    log.info("Starting Tradeboss runner bot (Mr. Michael). dry_run=%s", dry_run)
    if dry_run:
        log.info("DRY RUN MODE: no real orders will be placed.")
    else:
        log.warning("LIVE MODE: real orders WILL be placed with real money.")

    auth.login()
    risk = RiskManager(cfg, log)
    tracker = MomentumTracker()

    while True:
        now = _now_et()
        if not _is_market_open(now):
            log.info("Market closed (%s ET). Sleeping.", now.strftime("%H:%M"))
            time.sleep(60)
            continue

        try:
            run_once(risk, cfg, dry_run, log, tracker)
        except Exception as e:
            log.exception("Error during trading cycle: %s", e)

        time.sleep(cfg["scan_interval_seconds"])


if __name__ == "__main__":
    main()
