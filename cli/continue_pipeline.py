"""cli.continue_pipeline — resume a stalled c_pipeline run from a given phase.

Use case (PLAN §2.1 隐形决策, §4 review_queue/human_review):
    The Web UI's "续跑 from phase X" button calls this CLI.
    Manual rescue:

        python -m cli.continue_pipeline --story-id 42 --resume-from phase_3
        python -m cli.continue_pipeline --story-id 42 --resume-from phase_4

The story row must already exist with the prior phases' artifacts on disk
under ``data/works/{story_id}/``.
"""

from __future__ import annotations

import argparse
import sys

from config_loader import load_from_environment
from generator.c_pipeline.orchestrator import PHASES, PipelineError, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resume a c_pipeline run from a given phase."
    )
    parser.add_argument(
        "--story-id",
        type=int,
        required=True,
        help="The existing stories.id to resume.",
    )
    parser.add_argument(
        "--resume-from",
        required=True,
        choices=list(PHASES),
        help="Phase to resume from (phase_0..phase_5).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_from_environment()
    try:
        result = run_pipeline(
            story_id=args.story_id,
            config=config,
            resume_from=args.resume_from,
        )
    except PipelineError as exc:
        print(f"Resume failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Resumed story_id={result.story_id} from {args.resume_from}; "
        f"final_phase={result.final_phase} status={result.status} "
        f"chars={result.char_count} duration_s={result.duration_seconds:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
