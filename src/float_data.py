import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).resolve().parent.parent / "state" / "float_cache.json"

# Float changes rarely (secondary offerings, buybacks, lockup expirations) -
# there's no need to burn API budget re-fetching it every 60-second scan
# cycle like price/volume. Cached per symbol with a long TTL instead.


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


def _fetch_fmp(symbol: str, api_key: str, log) -> dict | None:
    try:
        resp = requests.get(
            "https://financialmodelingprep.com/api/v4/shares_float",
            params={"symbol": symbol, "apikey": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("FMP float lookup for %s failed: HTTP %s", symbol, resp.status_code)
            return None
        rows = resp.json()
        if not rows or not isinstance(rows, list):
            return None
        row = rows[0]
        float_shares = row.get("floatShares")
        if float_shares is None:
            return None
        return {
            "float_shares": float(float_shares),
            "shares_outstanding": float(row["outstandingShares"]) if row.get("outstandingShares") else None,
            "as_of_date": row.get("date"),
            "source": "fmp",
            "source_url": row.get("source"),
        }
    except Exception as e:
        log.warning("FMP float lookup for %s errored: %s", symbol, e)
        return None


def _fetch_alpha_vantage(symbol: str, api_key: str, log) -> dict | None:
    try:
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("Alpha Vantage float lookup for %s failed: HTTP %s", symbol, resp.status_code)
            return None
        data = resp.json()
        float_shares = data.get("SharesFloat")
        if not float_shares:
            return None
        return {
            "float_shares": float(float_shares),
            "shares_outstanding": float(data["SharesOutstanding"]) if data.get("SharesOutstanding") else None,
            "as_of_date": data.get("LatestQuarter"),
            "source": "alpha_vantage",
            "source_url": None,
        }
    except Exception as e:
        log.warning("Alpha Vantage float lookup for %s errored: %s", symbol, e)
        return None


_FETCHERS = {"fmp": _fetch_fmp, "alpha_vantage": _fetch_alpha_vantage}
_API_KEY_ENV = {"fmp": "FMP_API_KEY", "alpha_vantage": "ALPHA_VANTAGE_API_KEY"}


def _days_since(date_str: str | None) -> float | None:
    if not date_str:
        return None
    try:
        as_of = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - as_of).days
    except ValueError:
        return None


def _fetch_and_flag(symbol: str, cfg: dict, log) -> dict:
    """Actually hit the provider(s) and build a record with a staleness
    verdict baked in (this is the expensive path - only runs on a cache
    miss)."""
    provider = cfg.get("float_provider", "fmp")
    api_key = os.environ.get(_API_KEY_ENV.get(provider, ""))

    record = None
    if api_key:
        record = _FETCHERS[provider](symbol, api_key, log)
    else:
        log.warning(
            "No API key set for float_provider=%s (%s); float data unavailable for %s",
            provider,
            _API_KEY_ENV.get(provider),
            symbol,
        )

    if record is None:
        record = {
            "float_shares": None,
            "shares_outstanding": None,
            "as_of_date": None,
            "source": None,
            "source_url": None,
        }

    reasons = []
    if record["float_shares"] is None:
        reasons.append("no float figure available from any source")
    else:
        age_days = _days_since(record["as_of_date"])
        if age_days is None:
            reasons.append("provider gave no as-of date to verify freshness")
        elif age_days > cfg.get("float_staleness_days_threshold", 45):
            reasons.append(f"reported float is {age_days} days old")

        if cfg.get("float_cross_check"):
            other_provider = "alpha_vantage" if provider == "fmp" else "fmp"
            other_key = os.environ.get(_API_KEY_ENV[other_provider])
            if other_key:
                other = _FETCHERS[other_provider](symbol, other_key, log)
                if other and other["float_shares"]:
                    diff_pct = abs(other["float_shares"] - record["float_shares"]) / record["float_shares"]
                    if diff_pct > cfg.get("float_disagreement_pct_threshold", 0.25):
                        reasons.append(
                            f"{provider} and {other_provider} disagree on float by {diff_pct:.0%}"
                        )

    record["stale"] = bool(reasons)
    record["stale_reasons"] = reasons
    return record


def get_float_record(symbol: str, cfg: dict, log) -> dict:
    """Returns a float record for `symbol`:

        {
          'float_shares': float | None,   # None means UNKNOWN, not zero
          'shares_outstanding': float | None,
          'source': str | None,
          'source_url': str | None,
          'as_of_date': str | None,
          'stale': bool,
          'stale_reasons': [str, ...],
        }

    Uses a locally cached value when younger than float_cache_ttl_hours;
    otherwise hits the configured provider. `stale`/`stale_reasons` are
    the honest signal to check before trusting float_shares for anything -
    never treat a missing or flagged float as a precise number."""
    cache = _load_cache()
    cached = cache.get(symbol)
    ttl_seconds = cfg.get("float_cache_ttl_hours", 48) * 3600

    if cached and time.time() - cached.get("fetched_at", 0) < ttl_seconds:
        return cached["record"]

    record = _fetch_and_flag(symbol, cfg, log)
    cache[symbol] = {"fetched_at": time.time(), "record": record}
    _save_cache(cache)
    return record
