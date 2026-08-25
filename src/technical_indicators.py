"""Local technical indicators computed from Robinhood's own intraday
candles (robin_stocks.stocks.get_stock_historicals) - no separate data
vendor. Both RSI and VWAP are computed from a single shared candle fetch
per symbol per cycle, not two separate API calls.
"""

import robin_stocks.robinhood as r


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Standard Wilder's-smoothed RSI over a closing-price series. Returns
    None if there isn't enough history yet (fewer than period+1 closes)."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_vwap(candles: list[dict]) -> float | None:
    """Session VWAP using typical price (H+L+C)/3 weighted by bar volume -
    the reference line a momentum trader actually watches: price holding
    above VWAP signals buyers are in control; a pullback TO VWAP that
    reclaims it on volume is a classic high-probability re-entry (the
    "VWAP reclaim" setup), which is what recent_price_change/distribution
    detection elsewhere in this codebase are trying to approximate without
    an explicit reference line. Returns None if there's no volume data to
    weight by."""
    total_dollar = 0.0
    total_volume = 0.0
    for c in candles:
        try:
            high = float(c["high_price"])
            low = float(c["low_price"])
            close = float(c["close_price"])
            volume = float(c["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        typical_price = (high + low + close) / 3
        total_dollar += typical_price * volume
        total_volume += volume

    if total_volume <= 0:
        return None
    return total_dollar / total_volume


def get_intraday_candles(symbol: str, log) -> list[dict]:
    """Today's 5-minute candles including extended hours, so a premarket
    or after-hours run contributes to both RSI and VWAP."""
    try:
        return r.stocks.get_stock_historicals(symbol, interval="5minute", span="day", bounds="extended") or []
    except Exception as e:
        log.warning("%s: intraday candles fetch failed: %s", symbol, e)
        return []


def get_momentum_indicators(symbol: str, log, rsi_period: int = 14) -> dict:
    """Returns {'rsi': float|None, 'vwap': float|None} from one shared
    candle fetch. This is a momentum-CONFIRMATION input (both higher RSI
    and price-above-VWAP nudge the score up), not an "overbought, avoid"
    filter - consistent with the strategy's own framing that a stock isn't
    automatically too late just because it already ran."""
    candles = get_intraday_candles(symbol, log)
    if not candles:
        return {"rsi": None, "vwap": None}

    closes = []
    for candle in candles:
        try:
            closes.append(float(candle["close_price"]))
        except (KeyError, TypeError, ValueError):
            continue

    return {
        "rsi": compute_rsi(closes, rsi_period),
        "vwap": compute_vwap(candles),
    }
