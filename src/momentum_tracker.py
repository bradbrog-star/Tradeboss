import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "momentum_state.json"
ET = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


class MomentumTracker:
    """Rolling per-symbol history of (timestamp, price, cumulative day
    volume) built up across scan cycles.

    A stock isn't disqualified just because it's already up a lot - what
    matters is whether fresh buying is still arriving. This lets the
    scanner compare "right now" against a few cycles ago to tell a name
    that's still accelerating apart from one that's already stalling,
    even when both show the same big day-over-day gain. Resets
    automatically each new trading day.
    """

    def __init__(self, max_samples: int = 15):
        self.max_samples = max_samples
        self.data = self._load_or_reset()

    def _load_or_reset(self) -> dict:
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH) as f:
                    data = json.load(f)
                if data.get("date") == _today_et():
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"date": _today_et(), "symbols": {}}

    def _save(self):
        STATE_PATH.parent.mkdir(exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(self.data, f)

    def record(self, symbol: str, price: float, volume: float | None):
        samples = self.data["symbols"].setdefault(symbol, [])
        samples.append({"t": time.time(), "price": price, "volume": volume})
        if len(samples) > self.max_samples:
            del samples[: len(samples) - self.max_samples]
        self._save()

    def recent_change(self, symbol: str, lookback_cycles: int) -> dict | None:
        """Compare the latest recorded sample to the one `lookback_cycles`
        scans earlier. Returns None if there isn't enough history yet
        (e.g. a symbol that just appeared on the movers list) - callers
        should treat None as "can't tell yet", not as a fail."""
        samples = self.data["symbols"].get(symbol, [])
        if len(samples) <= lookback_cycles:
            return None
        latest = samples[-1]
        past = samples[-1 - lookback_cycles]

        price_change_pct = None
        if past["price"]:
            price_change_pct = (latest["price"] - past["price"]) / past["price"]

        volume_change = None
        if latest.get("volume") is not None and past.get("volume") is not None:
            volume_change = latest["volume"] - past["volume"]

        return {"price_change_pct": price_change_pct, "volume_change": volume_change}
