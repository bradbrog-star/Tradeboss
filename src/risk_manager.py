import json
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

    def record_open(self, symbol: str, qty: float, entry_price: float):
        self.state["positions"][symbol] = {
            "qty": qty,
            "entry_price": entry_price,
            "opened_at": datetime.now(ET).isoformat(),
        }
        self.state["trades_today"] += 1
        self._save()

    def record_close(self, symbol: str, exit_price: float):
        pos = self.state["positions"].pop(symbol, None)
        if pos is None:
            return
        pnl = (exit_price - pos["entry_price"]) * pos["qty"]
        self.state["realized_pnl"] += pnl
        self._save()
        return pnl

    def open_positions(self) -> dict:
        return self.state["positions"]
