import robin_stocks.robinhood as r


def _to_float(x):
    try:
        return float(x) if x not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def _is_halted(quote: dict) -> bool:
    return str(quote.get("trading_halted")).lower() == "true"


def evaluate_symbol(symbol: str, cfg: dict, log) -> dict | None:
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

    if not (cfg["price_min_usd"] <= last_price <= cfg["price_max_usd"]):
        return None

    gain_pct = (last_price - prev_close) / prev_close
    if gain_pct < cfg["min_intraday_gain_pct"]:
        return None

    relative_volume = None
    try:
        fundamentals_list = r.stocks.get_fundamentals(symbol)
        fundamentals = fundamentals_list[0] if fundamentals_list else {}
        volume = _to_float(fundamentals.get("volume"))
        avg_volume = _to_float(fundamentals.get("average_volume"))
        if volume and avg_volume:
            relative_volume = volume / avg_volume
            if relative_volume < cfg["min_relative_volume"]:
                return None
    except Exception as e:
        log.warning("%s: could not evaluate volume (%s); continuing without it", symbol, e)

    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "gain_pct": gain_pct,
        "relative_volume": relative_volume,
        "ask_price": _to_float(quote.get("ask_price")) or last_price,
    }


def get_runner_candidates(cfg: dict, log) -> list[dict]:
    """Scan Robinhood's top up-movers and return the subset that pass the
    runner filters in config.yaml."""
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
            candidate = evaluate_symbol(symbol, cfg, log)
        except Exception as e:
            log.warning("Skipping %s: %s", symbol, e)
            continue
        if candidate:
            candidates.append(candidate)
    return candidates
