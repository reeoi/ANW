"""cli.generate — single-story c_pipeline run.

Phase C wiring (PLAN §3.1, decision #26 / P2):

    python -m cli.generate
    python -m cli.generate --theme 强行覆盖的题材 --word-count 9000
    python -m cli.generate --story-id 42 --resume-from phase_3

Without arguments, the pipeline picks a theme from theme_pool.json,
runs all six phases, and writes the final story to ``5_最终稿.md``. With
``--theme/--style/--word-count`` overrides, the override values feed into
Phase 0's pitch synthesis (see phase0_select.select_theme).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config_loader import load_from_environment
from generator.c_pipeline.orchestrator import PipelineError, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one short story end-to-end via the c_pipeline."
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="Override Phase 0 theme (Phase 1 still picks final_title).",
    )
    parser.add_argument(
        "--style",
        default=None,
        help="Optional style hint (becomes the target_platform / tone).",
    )
    parser.add_argument(
        "--word-count",
        type=int,
        default=None,
        help="Override target word count (used for Phase 2 ±10% check).",
    )
    parser.add_argument(
        "--story-id",
        type=int,
        default=None,
        help="Run against an existing stories row (for resume / continue).",
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="phase_0 / phase_1 / phase_2 / phase_3 / phase_4 / phase_5.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print the final summary + char count after success.",
    )
    parser.add_argument(
        "--print-ids",
        action="store_true",
        help="Print the story_id on success (one per line).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_from_environment()

    overrides: dict[str, object] = {}
    if args.theme:
        overrides["theme"] = args.theme
    if args.style:
        # ``--style`` is a tone hint; the pitch carries tone_keywords downstream.
        # We also coerce it to target_platform if it matches a known platform key.
        if args.style in {"番茄短篇", "七猫短篇", "黑岩短篇", "点众短篇", "知乎盐言"}:
            overrides["target_platform"] = args.style
    if args.word_count:
        if args.word_count <= 0:
            print("--word-count must be positive", file=sys.stderr)
            return 2
        overrides["target_length"] = int(args.word_count)

    try:
        result = run_pipeline(
            story_id=args.story_id,
            config=config,
            overrides=overrides or None,
            resume_from=args.resume_from,
        )
    except PipelineError as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    if args.print_ids:
        print(result.story_id)

    print(
        f"Generated story_id={result.story_id} "
        f"status={result.status} "
        f"final_phase={result.final_phase} "
        f"chars={result.char_count} "
        f"cost_cny={result.total_cost_cny:.4f} "
        f"duration_s={result.duration_seconds:.2f} "
        f"used_fallback={result.used_fallback} "
        f"needs_human={result.needs_human}"
    )
    if args.print_summary:
        print("--- final_title ---")
        print(result.final_title)
        print("--- summary ---")
        print(result.summary)
        print("--- final_content_path ---")
        print(result.final_content_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
