"""First-detection timestamp tracking. The research question isn't "what
was this stock doing at 5am" - it's "how early after the originating
event could this winner have been distinguished from the hundreds of
stocks that do nothing." That needs the actual sequence (first catalyst,
first abnormal trade, first threshold crossed), not a periodic snapshot.

Called every cycle a symbol is observed AT ALL - including ones that
don't (yet, or ever) qualify as a full scanner candidate - so a name that
starts moving before it clears every filter still gets its first-crossing
timestamps captured. See database.log_first_detection for the
first-occurrence-wins mechanics.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from .database import log_first_detection

ET = ZoneInfo("America/New_York")

# Coarse-to-fine so a stock that jumps straight from +5% to +150% between
# polls still gets every intermediate threshold recorded (at the same
# observed timestamp - our true resolution is scan_interval_seconds, not
# finer than that).
GAIN_THRESHOLDS_PCT = [0.10, 0.20, 0.30, 0.50, 1.00, 2.00, 3.00]
RELATIVE_VOLUME_THRESHOLD = 3.0


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def record_detections(
    symbol: str,
    price: float | None,
    gain_pct: float | None,
    cum_volume: float | None,
    relative_volume: float | None,
    log,
):
    trading_date = _today_et()
    ts = datetime.now(ET).isoformat()

    log_first_detection(trading_date, symbol, "first_seen", ts, price, gain_pct, cum_volume, relative_volume, None, log)

    if gain_pct is not None:
        for threshold in GAIN_THRESHOLDS_PCT:
            if gain_pct >= threshold:
                event_type = f"gain_{int(round(threshold * 100))}pct"
                log_first_detection(
                    trading_date, symbol, event_type, ts, price, gain_pct, cum_volume, relative_volume, None, log
                )

    if relative_volume is not None and relative_volume >= RELATIVE_VOLUME_THRESHOLD:
        log_first_detection(
            trading_date,
            symbol,
            "abnormal_volume",
            ts,
            price,
            gain_pct,
            cum_volume,
            relative_volume,
            f"rel_vol={relative_volume:.1f}x",
            log,
        )


def record_first_social_mention(symbol: str, earliest_message_ts_utc: str | None, messages_recent: int, log):
    """Unlike the events above, this uses the ACTUAL origin timestamp of
    the earliest message we can see (StockTwits' created_at, UTC) as the
    event time - not "now" - since that's the true start of the attention
    event, e.g. a press-release pickup at 4:17:36am, not when we happened
    to poll and notice it."""
    if not earliest_message_ts_utc or not messages_recent:
        return
    trading_date = _today_et()
    log_first_detection(
        trading_date,
        symbol,
        "first_social_mention",
        earliest_message_ts_utc,
        None,
        None,
        None,
        None,
        f"messages_recent={messages_recent} (ts is UTC, not ET)",
        log,
    )
