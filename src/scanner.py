import robin_stocks.robinhood as r

from .momentum_tracker import MomentumTracker


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

    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "gain_pct": gain_pct,
        "relative_volume": relative_volume,
        "ask_price": _to_float(quote.get("ask_price")) or last_price,
    }


def get_runner_candidates(cfg: dict, log, tracker: MomentumTracker) -> list[dict]:
    """Scan Robinhood's top up-movers and return the subset that pass the
    runner filters in config.yaml, including the continuation check."""
    try:
        movers = r.markets.get_top_movers(direction="up") or []
    except Exception as e:
        log.warning("Failed to fetch top movers: %s", e)
        return []

    candidates = []
    for m in movers:
        symbol = m.get("symbol")
        if not symbol:
            continue
        try:
            candidate = evaluate_symbol(symbol, cfg, log, tracker)
        except Exception as e:
            log.warning("Skipping %s: %s", symbol, e)
            continue
        if candidate:
            candidates.append(candidate)
    return candidates
