import robin_stocks.robinhood as r

from .float_data import get_float_record
from .float_metrics import compute_effective_float_metrics
from .momentum_tracker import MomentumTracker
from .social_signal import get_buzz
from .webull_source import get_webull_gainer_symbols


def _to_float(x):
    try:
        return float(x) if x not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def _is_halted(quote: dict) -> bool:
    return str(quote.get("trading_halted")).lower() == "true"


def evaluate_symbol(symbol: str, cfg: dict, log, tracker: MomentumTracker) -> dict | None:
    """Return a candidate dict if `symbol` currently qualifies as a
    momentum runner per config thresholds, else None."""
    quotes = r.stocks.get_quotes(symbol)
    if not quotes or not quotes[0]:
        return None
    quote = quotes[0]

    if _is_halted(quote):
        log.info("%s is halted, skipping", symbol)
        return None

    last_price = _to_float(quote.get("last_trade_price"))
    prev_close = _to_float(quote.get("previous_close"))
    if last_price is None or not prev_close:
        return None

    relative_volume = None
    cum_volume = None
    avg_volume = None
    try:
        fundamentals_list = r.stocks.get_fundamentals(symbol)
        fundamentals = fundamentals_list[0] if fundamentals_list else {}
        cum_volume = _to_float(fundamentals.get("volume"))
        avg_volume = _to_float(fundamentals.get("average_volume"))
        if cum_volume and avg_volume:
            relative_volume = cum_volume / avg_volume
    except Exception as e:
        log.warning("%s: could not fetch volume data (%s)", symbol, e)

    # Record this sample regardless of whether it passes the filters below
    # so continuation history is building up before a name even qualifies.
    tracker.record(symbol, last_price, cum_volume)

    if not (cfg["price_min_usd"] <= last_price <= cfg["price_max_usd"]):
        return None

    gain_pct = (last_price - prev_close) / prev_close
    if gain_pct < cfg["min_intraday_gain_pct"]:
        return None

    if relative_volume is not None and relative_volume < cfg["min_relative_volume"]:
        return None

    # "Not automatically too late because it's up 40%/100%/200% today" -
    # what matters is whether fresh demand is STILL arriving right now.
    # Once there's enough history on this symbol, require recent
    # (last few cycles) price/volume movement, not just the day's
    # cumulative gain, so a stalled-out name doesn't qualify just because
    # of what it did an hour ago.
    change = tracker.recent_change(symbol, cfg["momentum_lookback_cycles"])
    if change is not None:
        recent_price_change = change["price_change_pct"]
        if recent_price_change is not None and recent_price_change < cfg["min_recent_price_change_pct"]:
            log.info(
                "%s: up %.0f%% today but only %.1f%% over last %d cycles - looks stalled, skipping",
                symbol,
                gain_pct * 100,
                recent_price_change * 100,
                cfg["momentum_lookback_cycles"],
            )
            return None
        recent_volume_change = change["volume_change"]
        if recent_volume_change is not None and recent_volume_change < cfg["min_recent_volume_shares"]:
            log.info("%s: recent volume flow too thin (%.0f shares), skipping", symbol, recent_volume_change)
            return None

    # Social chatter is a scoring input, not a hard gate - the strategy
    # explicitly allows for "seemingly no clear reason at all", so zero
    # buzz doesn't disqualify a candidate, it just scores lower on this
    # one component below.
    buzz = {"messages_recent": 0, "trending": False}
    if cfg.get("enable_social_signal", True):
        buzz = get_buzz(symbol, log)

    # Effective float: how hard the float is actually being traded, not
    # just its raw size. The float figure itself is fetched separately
    # (float_data.py) and comes with an honest staleness verdict - a
    # missing/stale float never silently becomes "0" or "fine" here.
    float_record = get_float_record(symbol, cfg, log)
    float_metrics = compute_effective_float_metrics(
        float_shares=float_record["float_shares"],
        day_cum_volume=cum_volume,
        recent_window_volume=change["volume_change"] if change else None,
        avg_volume=avg_volume,
        price=last_price,
    )

    if cfg.get("require_valid_float") and float_record["stale"]:
        log.info(
            "%s: float unverified/stale (%s), skipping (require_valid_float is on)",
            symbol,
            "; ".join(float_record["stale_reasons"]),
        )
        return None

    float_shares = float_record["float_shares"]
    if float_shares is not None:
        min_float = cfg.get("min_float_shares")
        max_float = cfg.get("max_float_shares")
        if min_float and float_shares < min_float:
            return None
        if max_float and float_shares > max_float:
            return None
    elif cfg.get("min_float_shares") or cfg.get("max_float_shares"):
        log.info("%s: float unknown, can't apply float size filter - not disqualifying", symbol)

    recent_price_change = change["price_change_pct"] if change else None
    score = (
        gain_pct * cfg.get("weight_gain", 1.0)
        + (relative_volume or 0.0) * cfg.get("weight_relvol", 0.05)
        + (recent_price_change or 0.0) * cfg.get("weight_continuation", 2.0)
        + min(buzz["messages_recent"] / 20, 1.0) * cfg.get("weight_buzz", 0.5)
        + (cfg.get("weight_buzz", 0.5) if buzz["trending"] else 0.0)
        + (float_metrics["day_turnover"] or 0.0) * cfg.get("weight_float_turnover", 1.0)
    )

    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "gain_pct": gain_pct,
        "relative_volume": relative_volume,
        "buzz_messages_recent": buzz["messages_recent"],
        "trending": buzz["trending"],
        "float_shares": float_shares,
        "float_source": float_record["source"],
        "float_as_of_date": float_record["as_of_date"],
        "float_stale": float_record["stale"],
        "float_stale_reasons": float_record["stale_reasons"],
        "rotations_since_open": float_metrics["rotations_since_open"],
        "recent_turnover_rate": float_metrics["recent_turnover_rate"],
        "float_adjusted_relative_volume": float_metrics["float_adjusted_relative_volume"],
        "score": score,
        "ask_price": _to_float(quote.get("ask_price")) or last_price,
    }


def get_runner_candidates(cfg: dict, log, tracker: MomentumTracker) -> list[dict]:
    """Build a symbol universe from every enabled source (Robinhood's top
    up-movers, plus Webull's if enabled), then return the subset that pass
    the runner filters in config.yaml, including the continuation check."""
    try:
        movers = r.markets.get_top_movers(direction="up") or []
    except Exception as e:
        log.warning("Failed to fetch Robinhood top movers: %s", e)
        movers = []

    symbols = {m.get("symbol") for m in movers if m.get("symbol")}
    symbols |= set(get_webull_gainer_symbols(cfg, log))

    candidates = []
    for symbol in symbols:
        try:
            candidate = evaluate_symbol(symbol, cfg, log, tracker)
        except Exception as e:
            log.warning("Skipping %s: %s", symbol, e)
            continue
        if candidate:
            candidates.append(candidate)
    return candidates
