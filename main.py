"""Unified ANP entrypoint."""

from __future__ import annotations

import argparse
import time
from dataclasses import replace

from config_loader import ConfigError, LoadedConfig, load_from_environment
from queue.db import initialize_database
from scheduler import (
    backup_sqlite_database,
    configure_logging,
    count_stories_by_status,
    get_monthly_api_limit,
    get_publish_delay_range,
    run_dry_run_pipeline,
    start_scheduler,
)


def main() -> int:
    """Load config and start ANP in auto or semi-auto mode."""

    parser = argparse.ArgumentParser(description="ANP local automation pipeline")
    parser.add_argument("--mode", choices=["auto", "semi-auto"], help="Override runtime mode")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force local mock generation/review/publish and run one end-to-end simulation in auto mode.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="In auto mode, run one generate -> AI review -> publish pipeline and exit instead of waiting.",
    )
    parser.add_argument(
        "--backup-now",
        action="store_true",
        help="Run a SQLite backup immediately, then continue with the selected mode.",
    )
    args = parser.parse_args()

    try:
        config = load_from_environment()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    config = _apply_cli_overrides(config, mode=args.mode, dry_run=args.dry_run)
    log_file = configure_logging(config)

    for warning in config.warnings:
        print(f"[config] {warning}")

    db_path = initialize_database(config)
    mode = config.data.get("runtime", {}).get("mode", "semi-auto")
    delay_min, delay_max = get_publish_delay_range(config)
    print(
        f"ANP ready: mode={mode}, dry_run={config.is_dry_run}, sqlite={db_path}, "
        f"log={log_file}, publish_delay={delay_min}-{delay_max}min, "
        f"monthly_api_budget_cny={get_monthly_api_limit(config)}"
    )

    if args.backup_now:
        backup_path = backup_sqlite_database(config)
        print(f"SQLite backup: {backup_path or 'skipped'}")

    if args.dry_run or args.once:
        result = run_dry_run_pipeline(config)
        print(result.message)
        print(f"queue_status={count_stories_by_status(config)}")
        return 0

    if mode == "auto":
        scheduler = start_scheduler(config)
        print("Auto mode started: APScheduler is running. Press Ctrl+C to stop.")
        print(f"Jobs: {[job.id for job in scheduler.get_jobs()]}")
        try:
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown(wait=False)
            print("Auto scheduler stopped.")
        return 0

    print("Semi-auto mode: start the human review service with:")
    print("  python -m queue.human_review")
    print("Then open http://127.0.0.1:8000 .")
    print(f"Recent logs are available at: {log_file}")
    return 0


def _apply_cli_overrides(config: LoadedConfig, mode: str | None, dry_run: bool) -> LoadedConfig:
    data = dict(config.data)
    runtime = dict(data.get("runtime", {}))
    deepseek = dict(data.get("deepseek", {}))
    if mode:
        runtime["mode"] = mode
    if dry_run:
        runtime["dry_run"] = True
        deepseek["mock"] = True
    data["runtime"] = runtime
    data["deepseek"] = deepseek
    return replace(config, data=data)


if __name__ == "__main__":
    raise SystemExit(main())
