"""[Stub] Generate one story — old single-shot flow disabled.

The original CLI called ``generator/prompt_builder.build_short_story_prompt``
plus ``DeepSeekClient.generate_story`` to produce a 3000-character draft in one
DeepSeek call. The c_pipeline refactor replaces that flow with the multi-phase
orchestrator (see docs/c_pipeline_plan.md §3.1). Until Phase C lands, this CLI
prints a clear notice and exits 0 so smoke tests do not break.
"""

from __future__ import annotations

import argparse


_DEPRECATED_NOTICE = (
    "cli.generate 已停用:旧单步生成路径(prompt_builder + DeepSeekClient.generate_story)"
    "已在 c_pipeline 重构 Phase A 中移除。完整流水线将在 Phase C 上线后通过"
    "`generator.c_pipeline.orchestrator.run_pipeline` 触发。\n"
    "如需手动触发一次生成,请等待 Phase E 集成接线,或直接使用即将提供的"
    "`python -m generator.c_pipeline.orchestrator <story_id>` 入口。"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="[stub] Generate a short story — disabled during c_pipeline refactor."
    )
    parser.add_argument("--theme", default=None, help="(unused) override Phase 0 theme")
    parser.add_argument("--style", default=None, help="(unused) override style")
    parser.add_argument("--word-count", type=int, default=None, help="(unused) override word count")
    parser.add_argument("--print-content", action="store_true", help="(unused)")
    parser.parse_args()
    print(_DEPRECATED_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
