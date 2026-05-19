"""CLI entry point for running Sprint 4 AI review batches."""

from __future__ import annotations

import argparse

from config_loader import load_from_environment
from review_queue.ai_review import run_review_batch
from review_queue.db import initialize_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AI review for pending queued stories.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum pending stories to process")
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="Override audit.approval_threshold for this batch",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.threshold is not None and not (0 <= args.threshold <= 100):
        parser.error("--threshold must be between 0 and 100")

    config = load_from_environment()
    for warning in config.warnings:
        print(f"[config] {warning}")

    db_path = initialize_database(config)
    result = run_review_batch(db_path, threshold=args.threshold, limit=args.limit, config=config)

    print(result.message)
    print(f"reviewed={result.reviewed}")
    print(f"approved={result.approved}")
    print(f"needs_human={result.needs_human}")
    print(f"failed={result.failed}")
    reasons = result.failure_reasons or []
    print("failure_reasons=" + ("; ".join(reasons) if reasons else "none"))
    print(f"database={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
