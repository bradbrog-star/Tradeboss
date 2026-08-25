"""Effective-float metrics: how hard the float is actually being traded
right now, not just its raw size. All functions here are pure math over
numbers already fetched elsewhere (float_data.py, scanner.py) - nothing
here makes network calls, so it's cheap to compute every scan cycle even
though the underlying float figure itself is cached for a long time.
"""


def compute_effective_float_metrics(
    float_shares: float | None,
    day_cum_volume: float | None,
    recent_window_volume: float | None,
    avg_volume: float | None,
    price: float | None,
) -> dict:
    """
    - day_turnover: today's cumulative volume as a multiple of the float
      ("volume ÷ float") - e.g. 2.5 means the float has fully rotated 2.5x
      since the open. Also exposed as `rotations_since_open` (same number,
      the terminology this trading style actually uses).
    - recent_turnover_rate: volume ÷ float over just the last few scan
      cycles (from MomentumTracker), not the whole day - the CURRENT pace
      of float rotation, distinct from the cumulative day figure. This is
      the "intraday turnover ÷ float" metric: a stock can have low
      cumulative turnover but a suddenly fast recent rate, or vice versa.
    - dollar_volume_over_float_value: approximated as
      (day_cum_volume * price) / (float_shares * price), i.e. price
      cancels out and this collapses to day_turnover UNLESS you wire in a
      true VWAP-weighted dollar volume figure from a provider that
      supplies actual traded dollar volume (not done here - documented
      limitation, not hidden).
    - float_adjusted_relative_volume: today's turnover-of-float vs the
      float-normalized average turnover, i.e. (volume/float) /
      (avg_volume/float) - which simplifies to volume/avg_volume
      (ordinary relative volume) under the simplifying assumption that
      float was roughly the same when avg_volume was computed. Exposed
      under this name for clarity in logs/scoring, not because it's a
      different number under the hood.

    Any input that's missing/zero makes the corresponding output None -
    callers must treat None as "can't compute", never as 0.
    """
    result = {
        "day_turnover": None,
        "rotations_since_open": None,
        "recent_turnover_rate": None,
        "dollar_volume_over_float_value": None,
        "float_adjusted_relative_volume": None,
    }

    if not float_shares:
        return result

    if day_cum_volume is not None:
        turnover = day_cum_volume / float_shares
        result["day_turnover"] = turnover
        result["rotations_since_open"] = turnover
        if price:
            result["dollar_volume_over_float_value"] = (day_cum_volume * price) / (float_shares * price)

    if recent_window_volume is not None:
        result["recent_turnover_rate"] = recent_window_volume / float_shares

    if avg_volume:
        result["float_adjusted_relative_volume"] = (day_cum_volume or 0) / avg_volume

    return result
