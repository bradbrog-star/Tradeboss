"""Alpaca Market Data API (REST) - an independent cross-check on
Robinhood's own quote data, from a real subscription (Algo Trader Plus
recommended - full SIP; the free tier is IEX-only, a much thinner slice
of the tape for illiquid microcaps specifically).

Deliberately limited to latest quote/trade (REST-polled), not a
streaming tape. Alpaca's trading-status/LULD halt messages are delivered
over their websocket stream, which needs a persistent-connection
architecture this bot doesn't have (it's a polling loop). Building that
correctly, guessing at message schemas, the night before a live run is
exactly how a bug slips into production - that's a deliberate follow-up,
not rushed. Halt detection for now stays on Robinhood's own
trading_halted flag (see scanner.py), not Alpaca.

Requires ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY in .env. Off by
default (enable_alpaca_cross_check in config.yaml) - this is new,
untested-against-live-data code; enabling it changes what the score sees,
so it's opt-in rather than silently live for whatever's already scheduled
to trade.
"""

import os

import requests

BASE_URL = "https://data.alpaca.markets/v2/stocks"


def _headers() -> dict | None:
    key = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_latest_quote(symbol: str, log) -> dict | None:
    """Returns {'bid_price', 'ask_price', 'bid_size', 'ask_size', 'ts'} or
    None if unavailable (no credentials, symbol not covered by your plan,
    or the request failed)."""
    headers = _headers()
    if not headers:
        return None
    try:
        resp = requests.get(f"{BASE_URL}/{symbol}/quotes/latest", headers=headers, timeout=5)
        if resp.status_code != 200:
            log.warning("Alpaca quote lookup for %s failed: HTTP %s", symbol, resp.status_code)
            return None
        quote = resp.json().get("quote") or {}
        if not quote:
            return None
        return {
            "bid_price": quote.get("bp"),
            "ask_price": quote.get("ap"),
            "bid_size": quote.get("bs"),
            "ask_size": quote.get("as"),
            "ts": quote.get("t"),
        }
    except Exception as e:
        log.warning("Alpaca quote lookup for %s errored: %s", symbol, e)
        return None


def get_latest_trade(symbol: str, log) -> dict | None:
    """Returns {'price', 'size', 'ts'} or None."""
    headers = _headers()
    if not headers:
        return None
    try:
        resp = requests.get(f"{BASE_URL}/{symbol}/trades/latest", headers=headers, timeout=5)
        if resp.status_code != 200:
            log.warning("Alpaca trade lookup for %s failed: HTTP %s", symbol, resp.status_code)
            return None
        trade = resp.json().get("trade") or {}
        if not trade:
            return None
        return {"price": trade.get("p"), "size": trade.get("s"), "ts": trade.get("t")}
    except Exception as e:
        log.warning("Alpaca trade lookup for %s errored: %s", symbol, e)
        return None


def get_cross_check(symbol: str, robinhood_price: float, log) -> dict | None:
    """Compares Alpaca's latest trade price against Robinhood's, as a data-
    quality sanity check - not a trading signal. Returns
    {'alpaca_price', 'diff_pct'} or None if Alpaca data is unavailable."""
    trade = get_latest_trade(symbol, log)
    if not trade or trade.get("price") is None:
        return None
    alpaca_price = float(trade["price"])
    if not robinhood_price:
        return {"alpaca_price": alpaca_price, "diff_pct": None}
    diff_pct = (alpaca_price - robinhood_price) / robinhood_price
    return {"alpaca_price": alpaca_price, "diff_pct": diff_pct}
