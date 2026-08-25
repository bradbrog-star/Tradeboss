import time

import requests

_cache: dict[str, dict] = {}
_CACHE_TTL_SECONDS = 300


def get_buzz(symbol: str, log) -> dict:
    """Best-effort 'social chatter' signal from StockTwits' public API -
    this is the closest structured, purpose-built equivalent of "checking
    social media" for a specific ticker (StockTwits is literally where
    this retail chatter happens, unlike a generic web/Google search, which
    has no clean unauthenticated API and is fragile to scrape).

    Returns {'messages_recent': int, 'trending': bool,
    'earliest_message_ts_utc': str|None}. This is a scoring input, not a
    hard gate - per the strategy, some runners have no visible catalyst at
    all, so a symbol with zero chatter isn't disqualified, it just scores
    lower on this one component. `earliest_message_ts_utc` is the actual
    origin timestamp of the earliest visible message (StockTwits'
    `created_at`, UTC - not ET like the rest of this codebase) - this is
    what lets detection_events.py record when the attention event actually
    started, not just when we happened to notice it.

    Cached briefly per symbol to stay well under StockTwits' public rate
    limit (~200 unauthenticated requests/hour/IP)."""
    cached = _cache.get(symbol)
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    data = {"messages_recent": 0, "trending": False, "earliest_message_ts_utc": None}
    try:
        resp = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
            timeout=5,
        )
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            data["messages_recent"] = len(messages)
            timestamps = [m["created_at"] for m in messages if m.get("created_at")]
            if timestamps:
                data["earliest_message_ts_utc"] = min(timestamps)
        elif resp.status_code == 429:
            log.warning("StockTwits rate limited; skipping buzz check for %s this cycle", symbol)
    except Exception as e:
        log.warning("StockTwits lookup failed for %s: %s", symbol, e)

    _cache[symbol] = {"ts": time.time(), "data": data}
    return data


def get_trending_symbols(log) -> set:
    try:
        resp = requests.get("https://api.stocktwits.com/api/2/trending/symbols.json", timeout=5)
        if resp.status_code == 200:
            return {s["symbol"] for s in resp.json().get("symbols", [])}
    except Exception as e:
        log.warning("StockTwits trending lookup failed: %s", e)
    return set()
