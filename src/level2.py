"""Robinhood Gold's Nasdaq Level II order book, via robin_stocks'
get_pricebook_by_symbol - a real, cheap ($5/mo Gold) source of book depth
using the broker connection the bot already has. Two honest limits:

- Nasdaq-listed symbols only. NYSE and OTC/pink-sheet tickers - a real
  slice of penny-stock runners - simply won't have data here. That's
  expected, not an error; callers must treat None as "unavailable", not
  "no liquidity".
- It's a polled snapshot, not a streaming tape. Useful for spread/depth
  checks each scan cycle, not for reconstructing continuous trade-by-trade
  time-and-sales history (that's what a real market-data vendor is for,
  see README).
"""

import robin_stocks.robinhood as r

_PRICE_KEYS = ("price", "ask_price", "bid_price", "limit_price")
_SIZE_KEYS = ("quantity", "size", "ask_size", "bid_size", "shares")


def _extract(level: dict, keys: tuple) -> float | None:
    for key in keys:
        if key in level:
            try:
                return float(level[key])
            except (TypeError, ValueError):
                continue
    return None


def get_order_book(symbol: str, log) -> dict | None:
    """Returns {'best_bid', 'best_ask', 'spread', 'spread_pct',
    'bid_size_top', 'ask_size_top', 'bid_ask_imbalance'} or None if
    unavailable (not Nasdaq-listed, no Gold subscription, or the response
    shape didn't parse). `bid_ask_imbalance` ranges -1..1: positive means
    more size stacked on the bid than the ask at the top of book."""
    try:
        book = r.stocks.get_pricebook_by_symbol(symbol)
    except Exception as e:
        log.warning("%s: Level II lookup failed (%s) - likely no Gold subscription or non-Nasdaq symbol", symbol, e)
        return None

    if not book:
        return None

    asks = book.get("asks") or []
    bids = book.get("bids") or []
    if not asks or not bids:
        return None

    best_ask = _extract(asks[0], _PRICE_KEYS)
    best_bid = _extract(bids[0], _PRICE_KEYS)
    if best_ask is None or best_bid is None or best_ask <= 0:
        return None

    ask_size = _extract(asks[0], _SIZE_KEYS)
    bid_size = _extract(bids[0], _SIZE_KEYS)

    spread = best_ask - best_bid
    imbalance = None
    if ask_size is not None and bid_size is not None and (ask_size + bid_size) > 0:
        imbalance = (bid_size - ask_size) / (bid_size + ask_size)

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct": spread / best_ask,
        "bid_size_top": bid_size,
        "ask_size_top": ask_size,
        "bid_ask_imbalance": imbalance,
    }
