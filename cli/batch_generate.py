"""[Stub] Batch-generate stories — old single-shot flow disabled.

See ``cli/generate.py`` for the rationale. Phase E will rewire this CLI to
schedule N pipeline runs through the c_pipeline orchestrator with K2 concurrency.
"""

from __future__ import annotations

import argparse


_DEPRECATED_NOTICE = (
    "cli.batch_generate 已停用:旧批量单步生成路径已在 c_pipeline 重构 Phase A 中移除。"
    "Phase E 集成接线后将改为按 daily_publish_plan + theme_pool 触发并发流水线"
    "(K2 = 同时最多 2 篇)。"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="[stub] Batch-generate stories — disabled during c_pipeline refactor."
    )
    parser.add_argument("--count", type=int, default=0, help="(unused)")
    parser.add_argument("--theme", default=None, help="(unused)")
    parser.add_argument("--word-count", type=int, default=None, help="(unused)")
    parser.add_argument("--style", default=None, help="(unused)")
    parser.add_argument("--dry-run", action="store_true", help="(unused)")
    parser.add_argument("--print-ids", action="store_true", help="(unused)")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print(_DEPRECATED_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
