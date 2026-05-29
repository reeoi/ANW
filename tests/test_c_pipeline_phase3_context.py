"""Tests for generator/c_pipeline/phase3_sections.py (Phase C.6).

Coverage:
- C3 full prior context: each section's prompt receives all previously
  written sections (verified via prompt inspection)
- per-section hard validators (length / paragraph / slop) pass / fail
- section-level retries: 1st attempt fails → 2nd passes
- mock fallback after retries exhaust → synthesized validator-passing text
- live-mode behavior: section flagged needs_human (not raised)
- combined draft 3_正文_合稿.md is written and contains every section
- per-section files 3_正文_第 NN 节.md exist
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from generator.c_pipeline.phase2_outline import OutlineSection, render_outline_md
from generator.c_pipeline.phase3_sections import (
    SECTION_MIN_CHARS,
    Phase3Result,
    PhaseSectionsError,
    run_sections,
    validate_section_text,
)
from generator.c_pipeline.validators import count_chinese_chars


class StubClient:
    """Returns canned responses keyed by call index."""

    def __init__(self, responses: list[str], *, mock: bool = True) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._mock = mock

    def is_mock(self) -> bool:
        return self._mock

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
            {
                "messages": messages,
                "thinking_mode": thinking_mode,
                "purpose": purpose,
            }
        )
        text = self.responses.pop(0) if self.responses else ""
        return ChatCompletion(
            text=text,
            reasoning=None,
            model="deepseek-v4-pro",
            usage=ChatUsage(input_tokens=500, output_tokens=1000),
            finish_reason="stop",
            cached=False,
        )


def _config(mock: bool = True) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": mock, "project_root": str(ROOT)},
            "deepseek": {"api_key": "" if mock else "sk-x", "mock": mock},
        },
        path=Path("config.yaml"),
    )


def _make_workdir(tmp_path: Path, n_sections: int = 3) -> Path:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    framework = "# 设定\n\n## final_title\n标题\n\n## summary\n摘要" + "测试" * 60
    (work / "1_设定.md").write_text(framework, encoding="utf-8")

    sections = [
        OutlineSection(
            index=i,
            main_event=f"主事件{i}",
            sub_events=["a", "b", "c"],
            emotion="冷静" if i % 2 else "爆发",
            new_info=f"信息{i}",
            hook=f"钩子{i}",
            foreshadowing="物件A" if i in (1, 3) else "",
            static_dynamic="动",
            dialogue_ratio="30%",
            target_words=1000,
        )
        for i in range(1, n_sections + 1)
    ]
    target_length = sum(s.target_words for s in sections)
    md = render_outline_md(sections, target_length=target_length)
    (work / "2_小节大纲.md").write_text(md, encoding="utf-8")
    return work


def _good_section(content_seed: str, *, target_chars: int = 850) -> str:
    """Build a section with paragraphs ≤60 chars and ≥800 chinese chars,
    free of any blacklist word."""
    paras = [
        f"她{content_seed}转身把钥匙放在桌上。",
        f"门外{content_seed}传来一阵脚步声。",
        "我把外套挂好,袖口还在滴水。",
        "客厅吊灯坏了一只灯泡,半边是亮的。",
        "她终于开口:'你回来了。'",
        "我点头,把账本翻到下一页。",
        "窗外开始下雨,盆栽被打歪。",
        "我数到第三声铃响才接起电话。",
    ]
    out: list[str] = []
    n = 0
    i = 0
    while n < target_chars:
        para = paras[i % len(paras)]
        out.append(para)
        n += count_chinese_chars(para)
        i += 1
    return "\n".join(out)


# ============================================================ validators


def test_validate_section_text_passes_clean_text() -> None:
    text = _good_section("缓缓")
    results = validate_section_text(text, blacklist=["顿时", "瞬间"])
    assert all(r.ok for r in results.values())


def test_validate_section_text_fails_too_short() -> None:
    text = "短句一段。\n短句二段。"
    results = validate_section_text(text, blacklist=[])
    assert not results["length"].ok


def test_validate_section_text_fails_paragraph_too_long() -> None:
    long_para = "字" * 70
    text = long_para + "\n" + _good_section("缓")
    results = validate_section_text(text, blacklist=[])
    assert not results["paragraph"].ok


def test_validate_section_text_fails_slop_hit() -> None:
    text = _good_section("缓缓") + "\n顿时她明白了一切。"
    results = validate_section_text(text, blacklist=["顿时"])
    assert not results["slop"].ok


# ============================================================ happy path


def test_run_sections_writes_per_section_and_combined(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=3)
    responses = [_good_section(f"节{i}") for i in range(1, 4)]
    client = StubClient(responses)

    result = run_sections(_config(), work_dir=work, client=client)
    assert isinstance(result, Phase3Result)
    assert len(result.sections) == 3
    assert all(not s.needs_human for s in result.sections)
    assert all(not s.used_fallback for s in result.sections)
    # per-section files exist
    for i in range(1, 4):
        assert (work / f"3_正文_第 {i:02d} 节.md").exists()
    # combined file exists and contains all section text
    assert result.combined_path == work / "3_正文_合稿.md"
    combined = result.combined_path.read_text(encoding="utf-8")
    for i in range(1, 4):
        assert f"节{i}" in combined
    assert result.total_chars >= 800 * 3


def test_run_sections_passes_full_prior_context(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=3)
    responses = [_good_section(f"节{i}") for i in range(1, 4)]
    client = StubClient(responses)

    run_sections(_config(), work_dir=work, client=client)

    # Section 1 prompt: prior context = "(本节为第一节,尚无前文)"
    msg1 = client.calls[0]["messages"][1]["content"]
    assert "(本节为第一节,尚无前文)" in msg1

    # Section 2 prompt: prior context = section 1 text
    msg2 = client.calls[1]["messages"][1]["content"]
    assert "节1" in msg2

    # Section 3 prompt: prior context = sections 1 + 2
    msg3 = client.calls[2]["messages"][1]["content"]
    assert "节1" in msg3
    assert "节2" in msg3


def test_run_sections_thinking_mode_off(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=2)
    client = StubClient([_good_section("a"), _good_section("b")])
    run_sections(_config(), work_dir=work, client=client)
    assert all(call["thinking_mode"] is False for call in client.calls)
    assert all(call["purpose"].startswith("phase_3_section_") for call in client.calls)


# ============================================================ retries


def test_run_sections_retries_on_validator_fail(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=1)
    short_bad = "字" * 100  # 100 < 800 chars → fails length
    good = _good_section("修订后")
    client = StubClient([short_bad, good])

    result = run_sections(_config(), work_dir=work, client=client, max_section_retries=2)
    assert len(result.sections) == 1
    s = result.sections[0]
    assert s.attempts == 2
    assert not s.needs_human
    assert not s.used_fallback


def test_run_sections_retry_message_appended_on_failure(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=1)
    bad = "字" * 100
    good = _good_section("修订")
    client = StubClient([bad, good])
    run_sections(_config(), work_dir=work, client=client)
    assert len(client.calls) == 2
    second_msgs = client.calls[1]["messages"]
    # The retry user message must reference 未通过硬校验.
    assert any("未通过硬校验" in m["content"] for m in second_msgs)


def test_run_sections_slop_failure_triggers_retry(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=1)
    # Build the project's real blacklist to make sure the test exercises the live path.
    bad = _good_section("a") + "\n顿时她明白了一切。"
    good = _good_section("修订")
    client = StubClient([bad, good])
    result = run_sections(_config(), work_dir=work, client=client)
    assert result.sections[0].attempts == 2


# ============================================================ fallback


def test_run_sections_falls_back_in_mock_after_retries(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=2)
    client = StubClient(
        ["[mock] 占位"] * 6  # always too short → exhausts retries on every section
    )
    result = run_sections(_config(mock=True), work_dir=work, client=client, max_section_retries=2)
    assert len(result.sections) == 2
    for s in result.sections:
        assert s.used_fallback is True
        assert s.char_count >= SECTION_MIN_CHARS
        # fallback must pass all three validators
        assert all(v.ok for v in s.validations.values())
    assert result.used_fallback is True
    assert not result.needs_human


def test_run_sections_live_mode_marks_needs_human_after_retries(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, n_sections=1)
    client = StubClient(["bad"] * 5, mock=False)
    result = run_sections(
        _config(mock=False), work_dir=work, client=client, max_section_retries=2
    )
    assert len(result.sections) == 1
    assert result.sections[0].needs_human is True
    assert result.sections[0].used_fallback is False
    assert result.needs_human is True


# ============================================================ error path


def test_run_sections_missing_framework_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "2_小节大纲.md").write_text("placeholder", encoding="utf-8")
    with pytest.raises(PhaseSectionsError):
        run_sections(_config(), work_dir=work, client=StubClient([]))


def test_run_sections_missing_outline_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "1_设定.md").write_text("# settings", encoding="utf-8")
    with pytest.raises(PhaseSectionsError):
        run_sections(_config(), work_dir=work, client=StubClient([]))


def test_run_sections_unparseable_outline_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "1_设定.md").write_text("# settings", encoding="utf-8")
    (work / "2_小节大纲.md").write_text("no table here", encoding="utf-8")
    with pytest.raises(PhaseSectionsError):
        run_sections(_config(), work_dir=work, client=StubClient([]))


# ============================================================ uses real blacklist


def test_run_sections_uses_real_project_blacklist(tmp_path: Path) -> None:
    """If we don't override blacklist_path, the real ai_slop_blacklist.json is loaded."""
    work = _make_workdir(tmp_path, n_sections=1)
    # Section text that hits a real blacklist term.
    bad = _good_section("a") + "\n他不禁笑了。"  # "不禁" is in the real blacklist
    good = _good_section("修订后")
    client = StubClient([bad, good])
    result = run_sections(_config(), work_dir=work, client=client)
    assert result.sections[0].attempts == 2  # retried once because of the slop hit
