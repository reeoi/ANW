"""Publish approved stories through platform adapters with dry-run support."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config_loader import LoadedConfig, load_from_environment
from publisher.base_publisher import PublishResult, PublishStatus
from publisher.fansq import FansqPublisher
from queue.db import get_database_path, initialize_database, story_from_row, update_story_status

if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]

import sqlite3


def find_one_approved_story(db_path: str | Path):
    """Return the oldest approved story waiting to publish, or None."""

    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT id, title, content, status, score, retry_count, review_notes,
                   created_at, updated_at, published_at
            FROM stories
            WHERE status = 'approved'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
    return story_from_row(row) if row is not None else None


def apply_publish_result(db_path: str | Path, result: PublishResult, commit_dry_run: bool = False) -> bool:
    """Persist publishing status when appropriate.

    Dry-run leaves queue state unchanged by default so verification is repeatable.
    Passing ``--commit-dry-run`` deliberately records the simulated outcome.
    """

    if result.story_id is None or not result.should_update_status:
        return False
    if result.dry_run and not commit_dry_run:
        return False
    if result.status not in {PublishStatus.PUBLISHED, PublishStatus.PAUSED, PublishStatus.FAILED}:
        return False
    return update_story_status(db_path, result.story_id, str(result.status), result.message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish one approved ANP story.")
    parser.add_argument("--platform", default="fansq", choices=["fansq"], help="Target platform adapter")
    parser.add_argument("--dry-run", action="store_true", help="Simulate publishing without browser submission")
    parser.add_argument(
        "--real",
        action="store_true",
        help="Attempt real Playwright automation. Requires prepared login_state_path.",
    )
    parser.add_argument(
        "--dry-run-outcome",
        default="success",
        choices=["success", "paused"],
        help="Simulation path for dry-run verification.",
    )
    parser.add_argument(
        "--commit-dry-run",
        action="store_true",
        help="Persist dry-run result to SQLite; by default dry-run keeps approved status unchanged.",
    )
    parser.add_argument("--wait-on-pause", action="store_true", help="Wait for Enter after a safe pause")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_from_environment()
    if args.dry_run:
        config = _with_runtime_dry_run(config, True)
    if args.real:
        config = _with_runtime_dry_run(config, False)

    db_path = initialize_database(config)
    story = find_one_approved_story(db_path)
    if story is None:
        print("No approved story found; nothing to publish.")
        return 0

    publisher = FansqPublisher(config)
    result = publisher.publish_story(
        story,
        dry_run=bool(config.data.get("runtime", {}).get("dry_run")),
        dry_run_outcome=args.dry_run_outcome,
        wait_on_pause=args.wait_on_pause,
    )
    changed = apply_publish_result(db_path, result, commit_dry_run=args.commit_dry_run)

    state_note = "status_updated" if changed else "status_preserved"
    print(
        f"story_id={story.id} platform={result.platform} status={result.status} "
        f"{state_note} message={result.message}"
    )
    if result.screenshot_path:
        print(f"screenshot={result.screenshot_path}")
    return 0 if result.status in {PublishStatus.PUBLISHED, PublishStatus.PAUSED} else 1


def _with_runtime_dry_run(config: LoadedConfig, dry_run: bool) -> LoadedConfig:
    data = dict(config.data)
    runtime = dict(data.get("runtime", {}))
    runtime["dry_run"] = dry_run
    data["runtime"] = runtime
    return LoadedConfig(data=data, path=config.path, warnings=config.warnings)


if __name__ == "__main__":
    raise SystemExit(main())
