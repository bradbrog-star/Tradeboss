import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "day_state.json"
ET = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


class RiskManager:
    """Tracks today's trades/PnL/positions and enforces the limits in
    config.yaml, including the daily-loss kill switch. State is persisted
    to state/day_state.json and reset automatically at the start of each
    new trading day."""

    def __init__(self, cfg: dict, log):
        self.cfg = cfg
        self.log = log
        self.state = self._load_or_reset()

    def _load_or_reset(self) -> dict:
        if STATE_PATH.exists():
            with open(STATE_PATH) as f:
                state = json.load(f)
            if state.get("date") == _today_et():
                return state
        return {
            "date": _today_et(),
            "trades_today": 0,
            "realized_pnl": 0.0,
            "positions": {},
            "kill_switch": False,
        }

    def _save(self):
        STATE_PATH.parent.mkdir(exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2)

    def _unrealized_pnl(self, latest_prices: dict) -> float:
        total = 0.0
        for symbol, pos in self.state["positions"].items():
            price = latest_prices.get(symbol)
            if price is None:
                continue
            total += (price - pos["entry_price"]) * pos["qty"]
        return total

    def refresh_kill_switch(self, latest_prices: dict):
        equity = self.cfg["account_equity_usd"]
        loss_limit = -abs(self.cfg["daily_loss_limit_pct"]) * equity
        day_pnl = self.state["realized_pnl"] + self._unrealized_pnl(latest_prices)
        if day_pnl <= loss_limit and not self.state["kill_switch"]:
            self.state["kill_switch"] = True
            self._save()
            self.log.warning(
                "KILL SWITCH TRIGGERED: day P&L %.2f <= limit %.2f. "
                "No new entries for the rest of the day.",
                day_pnl,
                loss_limit,
            )

    def deployed_usd(self) -> float:
        return sum(p["entry_price"] * p["qty"] for p in self.state["positions"].values())

    def can_open_new_position(self) -> bool:
        if self.state["kill_switch"]:
            return False
        if self.state["trades_today"] >= self.cfg["max_trades_per_day"]:
            return False
        if len(self.state["positions"]) >= self.cfg["max_concurrent_positions"]:
            return False
        if self.deployed_usd() >= self.cfg["max_total_deployed_usd"]:
            return False
        return True

    def position_size_usd(self) -> float:
        remaining_capacity = self.cfg["max_total_deployed_usd"] - self.deployed_usd()
        return max(0.0, min(self.cfg["max_position_size_usd"], remaining_capacity))

    def record_open(self, symbol: str, qty: float, entry_price: float, entry_meta: dict | None = None):
        self.state["positions"][symbol] = {
            "qty": qty,
            "entry_price": entry_price,
            "peak_price": entry_price,
            "trough_price": entry_price,
            "opened_at": datetime.now(ET).isoformat(),
            # Snapshot of the candidate's scoring inputs at entry time, kept
            # around purely so record_close can hand it to the database -
            # lets later research ask "what distinguished winners from
            # losers" without rejoining against candidate_snapshots.
            "entry_meta": entry_meta or {},
        }
        self.state["trades_today"] += 1
        self._save()

    def update_position_tracking(self, symbol: str, price: float, volume: float | None):
        """Called each cycle for an open position. Tracks the peak price
        reached since entry (for the trailing exit) and the peak volume
        rate (shares/sec) seen since entry (for distribution detection -
        exit when the pace of buying has clearly dropped off its own
        recent high while price stalls, not some arbitrary fixed target)."""
        pos = self.state["positions"].get(symbol)
        if not pos:
            return

        pos["peak_price"] = max(pos.get("peak_price", pos["entry_price"]), price)
        pos["trough_price"] = min(pos.get("trough_price", pos["entry_price"]), price)

        now_ts = time.time()
        last_ts = pos.get("last_sample_ts")
        last_vol = pos.get("last_cum_volume")
        if volume is not None and last_ts is not None and last_vol is not None and now_ts > last_ts:
            rate = (volume - last_vol) / (now_ts - last_ts)
            if rate >= 0:
                pos["last_volume_rate"] = rate
                pos["peak_volume_rate"] = max(pos.get("peak_volume_rate", 0.0), rate)
        if volume is not None:
            pos["last_cum_volume"] = volume
            pos["last_sample_ts"] = now_ts

        self._save()

    def record_close(self, symbol: str, exit_price: float) -> dict | None:
        pos = self.state["positions"].pop(symbol, None)
        if pos is None:
            return None
        pnl = (exit_price - pos["entry_price"]) * pos["qty"]
        self.state["realized_pnl"] += pnl
        self._save()
        return {
            "pnl": pnl,
            "entry_ts": pos["opened_at"],
            "entry_price": pos["entry_price"],
            "qty": pos["qty"],
            "peak_price": pos.get("peak_price", pos["entry_price"]),
            "trough_price": pos.get("trough_price", pos["entry_price"]),
            "entry_meta": pos.get("entry_meta", {}),
        }

    def open_positions(self) -> dict:
        return self.state["positions"]
