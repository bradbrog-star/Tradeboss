"""The runner/failure research database. Every candidate the scanner ever
flags gets a row here - whether the bot actually traded it or not - plus
every trade's full outcome (exit reason, P&L, how far it ran for and
against you). This is what phase 4 (replay/slippage) and phase 5 (paper
sample analysis) actually query against; the text log is for watching the
bot live, this is for research after the fact.

Plain sqlite3 (stdlib, no extra dependency) - one file, no server, trivial
to open with any SQL tool or pandas.read_sql for analysis.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).resolve().parent.parent / "state" / "tradeboss.db"
ET = ZoneInfo("America/New_York")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_price REAL,
    prev_close REAL,
    gain_pct REAL,
    relative_volume REAL,
    float_shares REAL,
    float_source TEXT,
    float_stale INTEGER,
    float_stale_reasons TEXT,
    rotations_since_open REAL,
    recent_turnover_rate REAL,
    float_adjusted_relative_volume REAL,
    buzz_messages_recent INTEGER,
    trending INTEGER,
    spread_pct REAL,
    bid_ask_imbalance REAL,
    score REAL,
    entered INTEGER
);

CREATE TABLE IF NOT EXISTS halt_sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_known_price REAL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_ts TEXT NOT NULL,
    entry_price REAL,
    qty REAL,
    exit_ts TEXT,
    exit_price REAL,
    exit_reason TEXT,
    exit_category TEXT,
    pnl REAL,
    peak_price REAL,
    trough_price REAL,
    mfe_pct REAL,
    mae_pct REAL,
    dry_run INTEGER,
    entry_score REAL,
    entry_float_shares REAL,
    entry_rotations_since_open REAL,
    entry_buzz_messages_recent REAL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_ts ON candidate_snapshots(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now(ET).isoformat()


def log_candidate_snapshot(candidate: dict, entered: bool, log):
    """Record a scanner sighting - called for every candidate that passed
    the filters in get_runner_candidates, regardless of whether the bot
    actually entered it (risk limits may have blocked it). This is the
    "runner" side of the runner/failure database."""
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO candidate_snapshots
                   (ts, symbol, last_price, prev_close, gain_pct, relative_volume,
                    float_shares, float_source, float_stale, float_stale_reasons,
                    rotations_since_open, recent_turnover_rate,
                    float_adjusted_relative_volume, buzz_messages_recent,
                    trending, spread_pct, bid_ask_imbalance, score, entered)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _now_iso(),
                    candidate["symbol"],
                    candidate.get("last_price"),
                    candidate.get("prev_close"),
                    candidate.get("gain_pct"),
                    candidate.get("relative_volume"),
                    candidate.get("float_shares"),
                    candidate.get("float_source"),
                    int(bool(candidate.get("float_stale"))),
                    json.dumps(candidate.get("float_stale_reasons") or []),
                    candidate.get("rotations_since_open"),
                    candidate.get("recent_turnover_rate"),
                    candidate.get("float_adjusted_relative_volume"),
                    candidate.get("buzz_messages_recent"),
                    int(bool(candidate.get("trending"))),
                    candidate.get("spread_pct"),
                    candidate.get("bid_ask_imbalance"),
                    candidate.get("score"),
                    int(entered),
                ),
            )
    except Exception as e:
        log.warning("Failed to log candidate snapshot for %s: %s", candidate.get("symbol"), e)


def log_halt_sighting(symbol: str, last_known_price: float | None, log):
    """Record that a symbol was seen halted - the "failure mode" data a
    resting stop order can't help with (see executor.py)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO halt_sightings (ts, symbol, last_known_price) VALUES (?,?,?)",
                (_now_iso(), symbol, last_known_price),
            )
    except Exception as e:
        log.warning("Failed to log halt sighting for %s: %s", symbol, e)


def _exit_category(reason: str) -> str:
    reason = (reason or "").lower()
    if reason.startswith("stop loss"):
        return "stop_loss"
    if reason.startswith("take-profit cap"):
        return "take_profit_cap"
    if reason.startswith("trailing exit"):
        return "trailing"
    if reason.startswith("distribution"):
        return "distribution"
    if reason.startswith("force exit"):
        return "force_exit_time"
    return "other"


def log_trade(
    symbol: str,
    entry_ts: str,
    entry_price: float,
    qty: float,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    peak_price: float,
    trough_price: float,
    dry_run: bool,
    entry_meta: dict,
    log,
):
    """Record a completed trade with its full outcome - the "failure" side
    of the database. `entry_meta` is whatever was captured about the
    candidate at entry time (score, float, rotations, buzz) so later
    analysis can ask "what made a winner different from a loser" without
    having to rejoin against the candidate_snapshots table."""
    mfe_pct = (peak_price - entry_price) / entry_price if entry_price else None
    mae_pct = (trough_price - entry_price) / entry_price if entry_price else None
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO trades
                   (symbol, entry_ts, entry_price, qty, exit_ts, exit_price,
                    exit_reason, exit_category, pnl, peak_price, trough_price,
                    mfe_pct, mae_pct, dry_run, entry_score, entry_float_shares,
                    entry_rotations_since_open, entry_buzz_messages_recent)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    symbol,
                    entry_ts,
                    entry_price,
                    qty,
                    _now_iso(),
                    exit_price,
                    exit_reason,
                    _exit_category(exit_reason),
                    pnl,
                    peak_price,
                    trough_price,
                    mfe_pct,
                    mae_pct,
                    int(dry_run),
                    entry_meta.get("score"),
                    entry_meta.get("float_shares"),
                    entry_meta.get("rotations_since_open"),
                    entry_meta.get("buzz_messages_recent"),
                ),
            )
    except Exception as e:
        log.warning("Failed to log trade for %s: %s", symbol, e)
