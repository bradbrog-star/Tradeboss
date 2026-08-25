"""Optional secondary universe source using the unofficial, community
`webull` PyPI package - reverse-engineered, not an official API, and its
method names/response shapes have drifted across versions in the past.

Disabled by default (`enable_webull_source` in config.yaml). If you turn
it on: `pip install webull`, and verify the call below still matches the
installed version's docs before trusting it. Any failure here is caught
and logged; the bot just falls back to the Robinhood-only universe rather
than crashing, since this is meant to widen the candidate list, not be a
single point of failure.
"""


def get_webull_gainer_symbols(cfg: dict, log) -> list[str]:
    if not cfg.get("enable_webull_source"):
        return []

    try:
        from webull import webull
    except ImportError:
        log.warning(
            "enable_webull_source is true but the `webull` package isn't installed "
            "(pip install webull)"
        )
        return []

    try:
        wb = webull()
        movers = wb.get_active_gainer_loser(direction="gainer", pageSize=50) or []
        symbols = []
        for m in movers:
            symbol = (m.get("ticker") or {}).get("symbol") or m.get("symbol")
            if symbol:
                symbols.append(symbol)
        return symbols
    except Exception as e:
        log.warning("Webull source failed (%s); continuing with Robinhood universe only", e)
        return []
