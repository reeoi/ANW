"""Tests for long-novel chapter writing context continuity."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generator.long_novel.l2_chapter_write import (
    assemble_context,
    ensure_chapter_heading,
    ensure_tracking_files,
    refresh_tracking_head,
    rewrite_chapter_from_source,
    run_draft,
    update_tracking_files,
)


class FakeClient:
    def __init__(self) -> None:
        self.prompt = ""

    def chat_completion(self, messages, **kwargs):
        self.prompt = str(messages[-1]["content"])
        return SimpleNamespace(text="正文")


def test_draft_prompt_includes_character_world_and_outline_context(tmp_path: Path) -> None:
    (tmp_path / "设定" / "角色").mkdir(parents=True)
    (tmp_path / "设定" / "世界观").mkdir(parents=True)
    (tmp_path / "大纲").mkdir()

    (tmp_path / "设定" / "角色" / "角色设定.md").write_text("## 主角：林晚\n- 语言风格：冷幽默", encoding="utf-8")
    (tmp_path / "设定" / "世界观" / "背景设定.md").write_text("现实世界与任务世界融合", encoding="utf-8")
    (tmp_path / "大纲" / "大纲.md").write_text("全书主线：前男友降临", encoding="utf-8")
    (tmp_path / "大纲" / "卷纲_第一卷.md").write_text("第一卷：重逢与追杀", encoding="utf-8")
    (tmp_path / "大纲" / "细纲_第001章.md").write_text("## 第1章\n林晚发现异常", encoding="utf-8")
    ensure_tracking_files(tmp_path, 30)
    (tmp_path / "追踪" / "全书进展.md").write_text("## 全书进展\n- 当前进度：第0章", encoding="utf-8")

    ctx = assemble_context(tmp_path, 1)
    assert "林晚" in ctx["character_profiles"]
    assert "任务世界融合" in ctx["world"]
    assert "全书主线" in ctx["book_outline"]
    assert "第一卷" in ctx["volume_outline"]
    assert "当前进度" in ctx["book_progress"]
    assert "不能改名" in ctx["continuation_constraints"]

    client = FakeClient()
    run_draft(client, tmp_path, 1, target_words=3000)

    assert "角色设定（人物唯一来源，必须严格沿用）" in client.prompt
    assert "必须沿用角色设定中的人物名" in client.prompt
    assert "林晚" in client.prompt
    assert "现实世界与任务世界融合" in client.prompt
    assert "全书主线：前男友降临" in client.prompt
    assert "全书长期进展记忆" in client.prompt
    assert "续写约束" in client.prompt


def test_ensure_chapter_heading_adds_missing_heading_and_replaces_wrong_number() -> None:
    assert ensure_chapter_heading("正文第一句", 5) == "# 第5章\n\n正文第一句\n"
    assert ensure_chapter_heading("# 第6章\n\n错误续写", 1) == "# 第1章\n\n错误续写\n"


def test_rewrite_chapter_uses_selected_source_without_later_tracking(tmp_path: Path) -> None:
    client = FakeClient()

    rewritten = rewrite_chapter_from_source(
        client,
        "第一章原稿",
        1,
        "第1章",
        "第一章细纲",
    )

    assert rewritten == "# 第1章\n\n正文\n"
    assert "第一章原稿" in client.prompt
    assert "不得续写后续章节" in client.prompt


def test_rewrite_tracking_update_does_not_move_latest_progress_backwards(tmp_path: Path) -> None:
    refresh_tracking_head(tmp_path, 5, "# 第5章\n\n第五章正文", summary_short="第五章摘要")

    update_tracking_files(
        tmp_path,
        1,
        "# 第1章\n\n第一章重写正文",
        client=None,
        advance_current=False,
    )

    context = (tmp_path / "追踪" / "上下文.md").read_text(encoding="utf-8")
    progress = (tmp_path / "追踪" / "全书进展.md").read_text(encoding="utf-8")
    assert "当前进度：第5章已完成" in context
    assert "## 第1章" in progress
