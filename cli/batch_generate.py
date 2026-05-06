"""cli.batch_generate — run N c_pipeline stories serially in one process.

Phase C wiring (PLAN §3.1):

    python -m cli.batch_generate --count 3
    python -m cli.batch_generate --count 5 --theme 强行覆盖
    python -m cli.batch_generate --count 3 --print-ids

Each story goes through ``orchestrator.run_pipeline`` so Phase 0 picks a
fresh theme_pool item per call (consumed_count++). The K2 semaphore is
respected; runs are serial here but if a future caller (Web UI / scheduler)
spawns multiple processes they will share the K2 = 2 cap automatically.
"""

from __future__ import annotations

import argparse
import sys

from config_loader import load_from_environment
from generator.c_pipeline.orchestrator import PipelineError, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch generate N short stories via the c_pipeline."
    )
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help="How many stories to generate (must be ≥ 1).",
    )
    parser.add_argument("--theme", default=None, help="Override Phase 0 theme.")
    parser.add_argument("--style", default=None, help="Optional style / platform hint.")
    parser.add_argument(
        "--word-count",
        type=int,
        default=None,
        help="Override target word count (per story).",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep running remaining stories after a per-story failure.",
    )
    parser.add_argument(
        "--print-ids",
        action="store_true",
        help="Print one story_id per line on success.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.count <= 0:
        print("--count must be ≥ 1", file=sys.stderr)
        return 2
    if args.word_count is not None and args.word_count <= 0:
        print("--word-count must be positive", file=sys.stderr)
        return 2

    config = load_from_environment()

    overrides: dict[str, object] = {}
    if args.theme:
        overrides["theme"] = args.theme
    if args.style and args.style in {"番茄短篇", "七猫短篇", "黑岩短篇", "点众短篇", "知乎盐言"}:
        overrides["target_platform"] = args.style
    if args.word_count:
        overrides["target_length"] = int(args.word_count)

    success: list[int] = []
    failed: list[str] = []

    for i in range(args.count):
        try:
            result = run_pipeline(
                config=config, overrides=overrides or None
            )
            success.append(result.story_id)
            if args.print_ids:
                print(result.story_id)
            print(
                f"[{i + 1}/{args.count}] story_id={result.story_id} "
                f"status={result.status} chars={result.char_count} "
                f"cost_cny={result.total_cost_cny:.4f}"
            )
        except PipelineError as exc:
            failed.append(str(exc))
            print(f"[{i + 1}/{args.count}] failed: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                break

    print(
        f"Batch generation completed: requested={args.count} "
        f"success={len(success)} failed={len(failed)}"
    )
    if failed and not args.continue_on_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
