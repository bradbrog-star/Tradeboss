from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config.example.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG_PATH} not found. Copy {EXAMPLE_CONFIG_PATH.name} to "
            f"{CONFIG_PATH.name}, review every value, then run again."
        )
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    required = [
        "dry_run",
        "account_equity_usd",
        "daily_loss_limit_pct",
        "max_position_size_usd",
        "max_total_deployed_usd",
        "max_concurrent_positions",
        "max_trades_per_day",
        "stop_loss_pct",
        "take_profit_cap_pct",
        "trailing_giveback_pct",
        "distribution_volume_drop_pct",
        "force_exit_time_et",
        "scan_interval_seconds",
        "price_min_usd",
        "price_max_usd",
        "min_intraday_gain_pct",
        "min_relative_volume",
        "momentum_lookback_cycles",
        "min_recent_price_change_pct",
        "min_recent_volume_shares",
        "entry_window_start_et",
        "entry_window_end_et",
        "enable_webull_source",
        "enable_social_signal",
        "weight_gain",
        "weight_relvol",
        "weight_continuation",
        "weight_buzz",
        "float_provider",
        "float_cache_ttl_hours",
        "float_staleness_days_threshold",
        "float_cross_check",
        "float_disagreement_pct_threshold",
        "require_valid_float",
        "weight_float_turnover",
    ]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(f"config.yaml is missing required keys: {missing}")

    return cfg
