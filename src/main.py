import time
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import robin_stocks.robinhood as r

from . import auth, executor
from .config import load_config
from .logger import get_logger
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


def get_latest_prices(symbols: list[str], log) -> dict:
    if not symbols:
        return {}
    try:
        quotes = r.stocks.get_quotes(symbols)
    except Exception as e:
        log.warning("Failed to fetch latest prices: %s", e)
        return {}

    prices = {}
    for q in quotes or []:
        if not q:
            continue
        try:
            prices[q.get("symbol")] = float(q.get("last_trade_price"))
        except (TypeError, ValueError):
            continue
    return prices


def manage_open_positions(risk: RiskManager, cfg: dict, dry_run: bool, log):
    positions = risk.open_positions()
    if not positions:
        return

    prices = get_latest_prices(list(positions.keys()), log)
    force_exit = _past_force_exit(cfg, _now_et())

    for symbol, pos in list(positions.items()):
        price = prices.get(symbol)
        if price is None:
            log.warning("%s: no current price (possibly halted); cannot evaluate exit", symbol)
            continue

        change_pct = (price - pos["entry_price"]) / pos["entry_price"]
        reason = None
        if change_pct <= -cfg["stop_loss_pct"]:
            reason = f"stop loss ({change_pct:.1%})"
        elif change_pct >= cfg["take_profit_pct"]:
            reason = f"take profit ({change_pct:.1%})"
        elif force_exit:
            reason = "force exit time reached"

        if reason and executor.exit_position(symbol, pos["qty"], dry_run, log, reason=reason):
            pnl = risk.record_close(symbol, price)
            log.info("Closed %s: %s, P&L $%.2f", symbol, reason, pnl or 0.0)


def try_open_new_positions(risk: RiskManager, cfg: dict, dry_run: bool, log):
    if not risk.can_open_new_position():
        return

    candidates = get_runner_candidates(cfg, log)
    candidates.sort(key=lambda c: c["gain_pct"], reverse=True)

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
            "Candidate %s: last $%.2f (+%.1f%% today), rel volume %s",
            symbol,
            candidate["last_price"],
            candidate["gain_pct"] * 100,
            f"{candidate['relative_volume']:.1f}x" if candidate["relative_volume"] else "n/a",
        )
        result = executor.enter_position(symbol, candidate["ask_price"], dollar_amount, dry_run, log)
        if result:
            risk.record_open(result["symbol"], result["qty"], result["price"])


def run_once(risk: RiskManager, cfg: dict, dry_run: bool, log):
    prices = get_latest_prices(list(risk.open_positions().keys()), log)
    risk.refresh_kill_switch(prices)

    manage_open_positions(risk, cfg, dry_run, log)

    now = _now_et()
    if _in_entry_window(cfg, now) and not _past_force_exit(cfg, now):
        try_open_new_positions(risk, cfg, dry_run, log)


def main():
    cfg = load_config()
    log = get_logger()
    dry_run = cfg["dry_run"]

    log.info("Starting Tradeboss runner bot. dry_run=%s", dry_run)
    if dry_run:
        log.info("DRY RUN MODE: no real orders will be placed.")
    else:
        log.warning("LIVE MODE: real orders WILL be placed with real money.")

    auth.login()
    risk = RiskManager(cfg, log)

    while True:
        now = _now_et()
        if not _is_market_open(now):
            log.info("Market closed (%s ET). Sleeping.", now.strftime("%H:%M"))
            time.sleep(60)
            continue

        try:
            run_once(risk, cfg, dry_run, log)
        except Exception as e:
            log.exception("Error during trading cycle: %s", e)

        time.sleep(cfg["scan_interval_seconds"])


if __name__ == "__main__":
    main()
