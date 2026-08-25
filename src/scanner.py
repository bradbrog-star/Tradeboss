import robin_stocks.robinhood as r

from .alpaca_data import get_cross_check as get_alpaca_cross_check
from .database import log_halt_sighting
from .detection_events import record_detections, record_first_social_mention
from .float_data import get_float_record
from .float_metrics import compute_effective_float_metrics
from .level2 import get_order_book
from .momentum_tracker import MomentumTracker
from .social_signal import get_buzz
from .technical_indicators import get_momentum_indicators
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
        log_halt_sighting(symbol, _to_float(quote.get("last_trade_price")), log)
        return None

    last_price = _to_float(quote.get("last_trade_price"))
    prev_close = _to_float(quote.get("previous_close"))
    if last_price is None or not prev_close:
        return None

    gain_pct = (last_price - prev_close) / prev_close

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

    # First-detection timestamps: logged for EVERY observed mover, before
    # any filter below can return None, so a name that hasn't cleared our
    # price/gain/volume bars yet still gets its first-crossing timestamps
    # captured. This is what actually answers "how early was this
    # detectable" - see detection_events.py.
    if cfg.get("enable_detection_events", True):
        record_detections(symbol, last_price, gain_pct, cum_volume, relative_volume, log)

    if not (cfg["price_min_usd"] <= last_price <= cfg["price_max_usd"]):
        return None

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
    buzz = {"messages_recent": 0, "trending": False, "earliest_message_ts_utc": None}
    if cfg.get("enable_social_signal", True):
        buzz = get_buzz(symbol, log)
        if cfg.get("enable_detection_events", True):
            record_first_social_mention(symbol, buzz["earliest_message_ts_utc"], buzz["messages_recent"], log)

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

    # Market cap = price x shares outstanding, from the same float-provider
    # lookup above (no extra API call). "Tiny market cap" from the
    # strategy is mostly already implied by the price/float bounds, so
    # this is exposed as an optional hard ceiling and for logging, not
    # folded into the score (would just double-count size against float).
    shares_outstanding = float_record.get("shares_outstanding")
    market_cap = shares_outstanding * last_price if shares_outstanding else None
    max_market_cap = cfg.get("max_market_cap_usd")
    if max_market_cap and market_cap is not None and market_cap > max_market_cap:
        return None

    # Level II (Nasdaq-listed only, needs Robinhood Gold - see level2.py).
    # A hard spread gate here is a real execution-quality check: entries
    # are limit orders just above the ask (executor.py), so a stock with
    # an absurd spread is a bad idea regardless of how good the momentum
    # signal looks.
    order_book = None
    if cfg.get("enable_level2", True):
        order_book = get_order_book(symbol, log)
        if order_book and order_book["spread_pct"] > cfg.get("max_spread_pct", 0.15):
            log.info(
                "%s: spread %.1f%% wider than max_spread_pct, skipping", symbol, order_book["spread_pct"] * 100
            )
            return None

    # RSI + VWAP, both computed locally from Robinhood's own intraday
    # candles in one shared fetch (no new vendor). Both are momentum-
    # CONFIRMATION inputs, not filters: RSI isn't an "overbought, avoid"
    # signal here (a stock isn't automatically too late just because it
    # ran), and price-above-VWAP is the reference line a real momentum
    # trader watches for "are buyers still in control" - a pullback that
    # holds VWAP and reclaims it on volume is the classic high-probability
    # continuation entry (the bull-flag/VWAP-reclaim setup).
    rsi = None
    vwap = None
    price_vs_vwap_pct = None
    if cfg.get("enable_rsi", True) or cfg.get("enable_vwap", True):
        indicators = get_momentum_indicators(symbol, log)
        rsi = indicators["rsi"] if cfg.get("enable_rsi", True) else None
        vwap = indicators["vwap"] if cfg.get("enable_vwap", True) else None
        if vwap:
            price_vs_vwap_pct = (last_price - vwap) / vwap

    # Alpaca cross-check: DATA QUALITY signal only, not a trading input -
    # off by default. Flags a large divergence between Robinhood's and
    # Alpaca's price for the same symbol at the same moment, which is
    # worth knowing before trusting either broker's feed on a specific
    # illiquid runner. Deliberately not folded into the score - this is
    # new code, untested against live data.
    alpaca_diff_pct = None
    if cfg.get("enable_alpaca_cross_check"):
        alpaca_check = get_alpaca_cross_check(symbol, last_price, log)
        if alpaca_check and alpaca_check["diff_pct"] is not None:
            alpaca_diff_pct = alpaca_check["diff_pct"]
            if abs(alpaca_diff_pct) > cfg.get("alpaca_price_diff_alert_pct", 0.05):
                log.warning(
                    "%s: Robinhood $%.2f vs Alpaca $%.2f (%.1f%% diff) - data quality check",
                    symbol,
                    last_price,
                    alpaca_check["alpaca_price"],
                    alpaca_diff_pct * 100,
                )

    recent_price_change = change["price_change_pct"] if change else None
    score = (
        gain_pct * cfg.get("weight_gain", 1.0)
        + (relative_volume or 0.0) * cfg.get("weight_relvol", 0.05)
        + (recent_price_change or 0.0) * cfg.get("weight_continuation", 2.0)
        + min(buzz["messages_recent"] / 20, 1.0) * cfg.get("weight_buzz", 0.5)
        + (cfg.get("weight_buzz", 0.5) if buzz["trending"] else 0.0)
        + (float_metrics["day_turnover"] or 0.0) * cfg.get("weight_float_turnover", 1.0)
        + (order_book["bid_ask_imbalance"] or 0.0 if order_book else 0.0) * cfg.get("weight_l2_imbalance", 0.5)
        + ((rsi / 100) if rsi is not None else 0.0) * cfg.get("weight_rsi", 0.5)
        + (price_vs_vwap_pct or 0.0) * cfg.get("weight_vwap", 1.0)
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
        "market_cap": market_cap,
        "spread_pct": order_book["spread_pct"] if order_book else None,
        "bid_ask_imbalance": order_book["bid_ask_imbalance"] if order_book else None,
        "rsi": rsi,
        "vwap": vwap,
        "price_vs_vwap_pct": price_vs_vwap_pct,
        "alpaca_diff_pct": alpaca_diff_pct,
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
