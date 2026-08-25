"""Local technical indicators computed from Robinhood's own intraday
candles (robin_stocks.stocks.get_stock_historicals) - no separate data
vendor needed, and it works for any symbol Robinhood quotes, not just
ones a paid API happens to cover.
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


def get_rsi(symbol: str, log, period: int = 14) -> float | None:
    """Fetches today's 5-minute candles (including extended hours, so a
    premarket run contributes to the reading) and computes RSI from
    closes. This is a momentum-confirmation signal for this strategy, NOT
    a mean-reversion "overbought, avoid" filter - a high RSI here supports
    a candidate rather than disqualifying it, consistent with "not
    automatically too late just because it's already up a lot"."""
    try:
        candles = r.stocks.get_stock_historicals(symbol, interval="5minute", span="day", bounds="extended")
    except Exception as e:
        log.warning("%s: RSI historicals fetch failed: %s", symbol, e)
        return None

    if not candles:
        return None

    closes = []
    for candle in candles:
        try:
            closes.append(float(candle["close_price"]))
        except (KeyError, TypeError, ValueError):
            continue

    return compute_rsi(closes, period)
