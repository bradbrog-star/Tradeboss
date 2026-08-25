#!/usr/bin/env python3
"""Re-imports research/MR_Michael_Historical_Runners_Failures_Controls.xlsx
into state/tradeboss.db's historical_* reference tables. Run this again
any time the workbook is edited/grown - it's idempotent (replaces prior
rows, doesn't duplicate).

Usage: python scripts/import_historical_seed.py [path/to/workbook.xlsx]
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import DEFAULT_HISTORICAL_WORKBOOK, import_historical_workbook  # noqa: E402


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("import_historical_seed")

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HISTORICAL_WORKBOOK
    if not path.exists():
        log.error("Workbook not found: %s", path)
        sys.exit(1)

    counts = import_historical_workbook(path, log)
    for table, count in counts.items():
        log.info("  %s: %d rows", table, count)


if __name__ == "__main__":
    main()
