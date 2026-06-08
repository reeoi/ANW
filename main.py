"""Unified ANW entrypoint.

After the scheduler removal (manual-only execution), this entrypoint just:
- loads config + logging,
- initializes the SQLite database,
- prints the URL of the local UI service,
- optionally runs a one-shot SQLite backup.

Auto / semi-auto modes have been removed. The UI's "立即执行一次" button
(or ``cli/generate.py`` for headless callers) is the trigger for a generate
+ AI review pipeline.
"""

from __future__ import annotations

import argparse

from config_loader import ConfigError, load_from_environment
from review_queue.db import initialize_database
from runtime_helpers import (
    backup_sqlite_database,
    configure_logging,
    count_stories_by_status,
    get_monthly_api_limit,
)


def main() -> int:
    """Load config, init DB+logging, print UI hint."""

    parser = argparse.ArgumentParser(description="ANW local pipeline")
    parser.add_argument(
        "--backup-now",
        action="store_true",
        help="Run a SQLite backup immediately, then exit.",
    )
    args = parser.parse_args()

    try:
        config = load_from_environment()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    log_file = configure_logging(config)

    for warning in config.warnings:
        print(f"[config] {warning}")

    db_path = initialize_database(config)
    print(
        f"ANW ready: sqlite={db_path}, log={log_file}, "
        f"monthly_api_budget_cny={get_monthly_api_limit(config)}"
    )

    if args.backup_now:
        backup_path = backup_sqlite_database(config)
        print(f"SQLite backup: {backup_path or 'skipped'}")
        return 0

    print(f"queue_status={count_stories_by_status(config)}")
    print("Start the local UI with:")
    print("  python -m review_queue.human_review")
    print("Then open http://127.0.0.1:8000 .")
    print(f"Recent logs are available at: {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
