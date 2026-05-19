"""Tests for phase6_chapter_title.py — Phase 6 chapter splitting + titling.

Coverage:
- Happy path: LLM returns valid JSON plan, code inserts 第X章 headers
- Output file 6_最终稿_带章节.md is written under work_dir
- Fallback when LLM returns invalid JSON: evenly-spaced chapters w/ placeholder titles
- Fallback when chapters count outside [min, max]: same fallback
- Fallback when first chapter doesn't start at index 0
- Fallback when start_para_index out of range
- render_chapters preserves original paragraphs verbatim (no rewriting)
- Chinese numeral conversion for chapter numbers (一..二十)
- Disabled via config: still produces output via fallback
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from generator.c_pipeline.phase6_chapter_title import (
    Chapter,
    Phase6Result,
    PhaseChapterError,
    _chinese_numeral,
    build_phase6_prompt,
    render_chapters,
    run_chapter_titling,
)


class StubClient:
    def __init__(self, text: str, *, mock: bool = True) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []
        self._mock = mock

    def is_mock(self) -> bool:
        return self._mock

    @property
    def settings(self) -> Any:
        class _S:
            model = "deepseek-v4-pro"
            flash_model = "deepseek-v4-flash"
        return _S()

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        thinking_mode: bool | None = None,
        model: str | None = None,
        temperature: float = 0.8,
        response_format: Any = None,
        purpose: str = "chat",
    ) -> ChatCompletion:
        self.calls.append(
            {"messages": messages, "thinking_mode": thinking_mode, "purpose": purpose}
        )
        return ChatCompletion(
            text=self.text,
            reasoning=None,
            model="deepseek-v4-pro",
            usage=ChatUsage(input_tokens=500, output_tokens=200),
            finish_reason="stop",
            cached=False,
        )


def _config(enabled: bool = True, **chapter_kw: int) -> LoadedConfig:
    chapter_cfg: dict[str, Any] = {"enabled": enabled}
    chapter_cfg.update(chapter_kw)
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(ROOT)},
            "deepseek": {"api_key": "", "mock": True},
            "c_pipeline": {"chapter_titling": chapter_cfg},
        },
        path=Path("config.yaml"),
    )


def _make_workdir(tmp_path: Path, *, paragraphs: int = 12) -> Path:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    body = "\n\n".join(f"段落{i}的内容,主角做了一件事。" for i in range(paragraphs))
    (work / "5_最终稿.md").write_text(body, encoding="utf-8")
    return work


# ============================================================ happy path


def test_happy_path_inserts_chapter_headers(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=12)
    plan = json.dumps({
        "chapters": [
            {"start_para_index": 0, "title": "学区房"},
            {"start_para_index": 4, "title": "撕证书"},
            {"start_para_index": 7, "title": "他回头"},
            {"start_para_index": 9, "title": "钥匙串"},
            {"start_para_index": 11, "title": "结局"},
        ]
    })
    client = StubClient(plan)
    result = run_chapter_titling(_config(), work_dir=work, client=client)

    assert isinstance(result, Phase6Result)
    assert result.used_fallback is False
    assert result.chapter_count == 5
    assert result.titles == ["学区房", "撕证书", "他回头", "钥匙串", "结局"]
    assert result.chaptered_path == work / "6_最终稿_带章节.md"
    assert result.chaptered_path.exists()

    content = result.chaptered_path.read_text(encoding="utf-8")
    assert "第一章 学区房" in content
    assert "第二章 撕证书" in content
    assert "第五章 结局" in content
    # First chapter header precedes first body paragraph
    assert content.index("第一章") < content.index("段落0的内容")
    # All 12 paragraphs preserved verbatim
    for i in range(12):
        assert f"段落{i}的内容" in content


def test_llm_called_with_phase_6_purpose_no_thinking(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=8)
    plan = json.dumps({
        "chapters": [
            {"start_para_index": 0, "title": "起"},
            {"start_para_index": 2, "title": "承"},
            {"start_para_index": 4, "title": "转"},
            {"start_para_index": 6, "title": "合"},
            {"start_para_index": 7, "title": "尾"},
        ]
    })
    client = StubClient(plan)
    run_chapter_titling(_config(), work_dir=work, client=client)
    assert client.calls[0]["purpose"] == "phase_6"
    assert client.calls[0]["thinking_mode"] is False


def test_strips_第X章_prefix_from_llm_titles(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=10)
    # LLM disobeys and prefixes "第一章 " — code should strip it
    plan = json.dumps({
        "chapters": [
            {"start_para_index": 0, "title": "第一章 学区房"},
            {"start_para_index": 3, "title": "第二章 撕证书"},
            {"start_para_index": 6, "title": "他回头"},
            {"start_para_index": 8, "title": "钥匙串"},
            {"start_para_index": 9, "title": "结局"},
        ]
    })
    client = StubClient(plan)
    result = run_chapter_titling(_config(), work_dir=work, client=client)
    # Titles should NOT contain the prefix anymore
    assert result.titles[0] == "学区房"
    assert result.titles[1] == "撕证书"
    content = result.chaptered_path.read_text(encoding="utf-8")
    # No double prefix like "第一章 第一章"
    assert "第一章 第一章" not in content


def test_handles_code_fence_around_json(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=10)
    plan = "```json\n" + json.dumps({
        "chapters": [
            {"start_para_index": 0, "title": "起"},
            {"start_para_index": 2, "title": "承"},
            {"start_para_index": 5, "title": "转"},
            {"start_para_index": 7, "title": "合"},
            {"start_para_index": 9, "title": "尾"},
        ]
    }) + "\n```"
    client = StubClient(plan)
    result = run_chapter_titling(_config(), work_dir=work, client=client)
    assert result.used_fallback is False
    assert result.chapter_count == 5


# ============================================================ fallback paths


def test_fallback_on_invalid_json(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=12)
    client = StubClient("not valid json at all")
    result = run_chapter_titling(_config(), work_dir=work, client=client)
    assert result.used_fallback is True
    assert result.chapter_count >= 5
    assert result.chaptered_path.exists()
    # All paragraphs still preserved
    content = result.chaptered_path.read_text(encoding="utf-8")
    for i in range(12):
        assert f"段落{i}的内容" in content


def test_fallback_when_chapter_count_too_small(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=12)
    plan = json.dumps({
        "chapters": [
            {"start_para_index": 0, "title": "唯一章"},
        ]
    })
    client = StubClient(plan)
    result = run_chapter_titling(_config(), work_dir=work, client=client)
    assert result.used_fallback is True


def test_fallback_when_first_chapter_not_at_zero(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=12)
    plan = json.dumps({
        "chapters": [
            {"start_para_index": 2, "title": "起"},
            {"start_para_index": 4, "title": "承"},
            {"start_para_index": 6, "title": "转"},
            {"start_para_index": 8, "title": "合"},
            {"start_para_index": 10, "title": "尾"},
        ]
    })
    client = StubClient(plan)
    result = run_chapter_titling(_config(), work_dir=work, client=client)
    assert result.used_fallback is True


def test_fallback_when_start_index_out_of_range(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=8)
    plan = json.dumps({
        "chapters": [
            {"start_para_index": 0, "title": "起"},
            {"start_para_index": 2, "title": "承"},
            {"start_para_index": 4, "title": "转"},
            {"start_para_index": 6, "title": "合"},
            {"start_para_index": 99, "title": "越界"},
        ]
    })
    client = StubClient(plan)
    result = run_chapter_titling(_config(), work_dir=work, client=client)
    assert result.used_fallback is True


def test_disabled_via_config_uses_fallback(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, paragraphs=10)
    # Even with valid plan, disabled config skips LLM and uses fallback
    plan = json.dumps({"chapters": [{"start_para_index": 0, "title": "x"}]})
    client = StubClient(plan)
    result = run_chapter_titling(_config(enabled=False), work_dir=work, client=client)
    assert result.used_fallback is True
    assert client.calls == []  # LLM never called


# ============================================================ render purity


def test_render_chapters_preserves_paragraphs_verbatim() -> None:
    paragraphs = [f"段落{i}内容" for i in range(6)]
    chapters = [
        Chapter(start_para_index=0, title="一"),
        Chapter(start_para_index=3, title="二"),
    ]
    out = render_chapters(paragraphs, chapters)
    for p in paragraphs:
        assert p in out
    assert out.startswith("第一章 一")
    assert "第二章 二" in out


def test_render_chapters_handles_empty_chapters_list() -> None:
    paragraphs = ["a", "b", "c"]
    out = render_chapters(paragraphs, [])
    assert out == "a\n\nb\n\nc"


def test_render_chapters_sorts_by_start_index() -> None:
    paragraphs = [f"P{i}" for i in range(6)]
    chapters = [
        Chapter(start_para_index=3, title="二"),
        Chapter(start_para_index=0, title="一"),
    ]
    out = render_chapters(paragraphs, chapters)
    # Header for "一" must precede "P0"; "二" must precede "P3"
    assert out.index("第一章 一") < out.index("P0")
    assert out.index("第二章 二") < out.index("P3")


# ============================================================ helpers


@pytest.mark.parametrize(
    "n,expected",
    [(1, "一"), (2, "二"), (5, "五"), (10, "十"), (12, "十二"), (20, "二十"), (21, "21")],
)
def test_chinese_numeral(n: int, expected: str) -> None:
    assert _chinese_numeral(n) == expected


# ============================================================ error cases


def test_raises_when_phase5_output_missing(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    client = StubClient("{}")
    with pytest.raises(PhaseChapterError, match="missing"):
        run_chapter_titling(_config(), work_dir=work, client=client)


def test_raises_when_phase5_output_empty(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "5_最终稿.md").write_text("   \n  \n", encoding="utf-8")
    client = StubClient("{}")
    with pytest.raises(PhaseChapterError, match="no paragraphs"):
        run_chapter_titling(_config(), work_dir=work, client=client)


# ============================================================ prompt content


def test_prompt_includes_numbered_paragraphs_and_constraints() -> None:
    msgs = build_phase6_prompt(
        paragraphs=["第一段", "第二段", "第三段"],
        min_chapters=5,
        max_chapters=10,
        title_min_chars=3,
        title_max_chars=8,
    )
    user = msgs[1]["content"]
    assert "[0] 第一段" in user
    assert "[1] 第二段" in user
    assert "[2] 第三段" in user
    assert "5 到 10 章" in user
    assert "start_para_index" in user
