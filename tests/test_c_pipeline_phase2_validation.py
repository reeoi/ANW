"""Tests for generator/c_pipeline/phase2_outline.py (Phase C.5).

Coverage:
- happy path: well-formed markdown table parses to OutlineSection list
- hard validators: section count / per-section words / total ±10%
- retry behavior: 1st attempt fails → 2nd attempt passes
- fallback: mock mode synthesizes a valid outline after retries exhaust
- live mode: raises PhaseOutlineError after retries exhaust
- markdown parser tolerates separator rows + extra whitespace
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
from generator.c_pipeline.phase2_outline import (
    OutlineSection,
    Phase2Result,
    PhaseOutlineError,
    SECTION_CHARS_MAX,
    SECTION_CHARS_MIN,
    SECTION_COUNT_MAX,
    SECTION_COUNT_MIN,
    build_phase2_prompt,
    parse_outline_md,
    render_outline_md,
    run_outline,
)


class StubClient:
    """Returns a list of canned responses, one per call."""

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
            reasoning="(mock)" if thinking_mode else None,
            model=model or "deepseek-v4-pro",
            usage=ChatUsage(input_tokens=300, output_tokens=900),
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


def _make_workdir(tmp_path: Path, target_length: int = 10000) -> Path:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "0_选题.json").write_text(
        json.dumps(
            {
                "target_length": [int(target_length * 0.95), int(target_length * 1.05)],
                "genre_id": "xian_dai_fu_chou",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work / "1_设定.md").write_text("# 设定\n\n## final_title\n标题\n\n## summary\n摘要\n", encoding="utf-8")
    return work


def _good_outline_md(words_per_section: list[int]) -> str:
    rows = []
    arc = ["愤怒", "压抑", "推进", "转折", "积累", "爆发", "碾压", "余韵", "铺垫", "深入", "二次", "终局", "尾声", "回响", "完结"]
    for i, w in enumerate(words_per_section, start=1):
        rows.append(
            f"| {i:02d} | 节{i} 主事件 | 子A / 子B / 子C | {arc[i-1]} | 信息{i} | 钩子{i} | 物件{i} | 动 | 30% | {w} |"
        )
    table = "\n".join(rows)
    return f"""# 小节大纲

## 元信息
- target_length: {sum(words_per_section)}
- section_count: {len(words_per_section)}

## 大纲表

| 节号 | 主事件 | 子事件×3-5 | 情绪 | 读者新获知 | 章末钩子 | 伏笔/物件 | 动静 | 对话密度 | target_words |
|---|---|---|---|---|---|---|---|---|---|
{table}

## 总字数核对
- 各节 target_words 之和 = {sum(words_per_section)}
"""


# ============================================================ parser


def test_parse_outline_md_extracts_8_sections() -> None:
    md = _good_outline_md([1100, 1200, 1100, 1100, 1300, 1200, 1100, 900])
    sections, warnings = parse_outline_md(md)
    assert len(sections) == 8
    assert sections[0].index == 1
    assert sections[0].main_event == "节1 主事件"
    assert sections[0].emotion == "愤怒"
    assert sections[7].target_words == 900
    assert sections[0].sub_events == ["子A", "子B", "子C"]
    assert warnings == []


def test_parse_outline_md_handles_missing_header() -> None:
    sections, warnings = parse_outline_md("# 小节大纲\n\n纯文字,没有表格。")
    assert sections == []
    assert any("table header" in w for w in warnings)


def test_parse_outline_md_skips_separator_row() -> None:
    md = _good_outline_md([1000] * 8)
    sections, _ = parse_outline_md(md)
    # The separator row should not appear as a section.
    assert all(s.index >= 1 and s.index <= 8 for s in sections)
    assert len(sections) == 8


def test_parse_outline_md_robust_to_extra_whitespace() -> None:
    md = _good_outline_md([1100] * 8).replace("|", "  |  ")
    sections, _ = parse_outline_md(md)
    assert len(sections) == 8


# ============================================================ render round-trip


def test_render_round_trip_preserves_section_count() -> None:
    sections = [
        OutlineSection(
            index=i,
            main_event=f"主{i}",
            sub_events=["x", "y", "z"],
            emotion="爽",
            new_info=f"info{i}",
            hook=f"hook{i}",
            foreshadowing="物件",
            static_dynamic="动",
            dialogue_ratio="30%",
            target_words=1100,
        )
        for i in range(1, 9)
    ]
    md = render_outline_md(sections, target_length=8800)
    parsed, _ = parse_outline_md(md)
    assert len(parsed) == 8
    assert parsed[0].main_event == "主1"
    assert parsed[7].target_words == 1100


# ============================================================ happy path


def test_run_outline_happy_path(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=8800)
    md = _good_outline_md([1100] * 8)  # total 8800 == target
    client = StubClient([md])
    result = run_outline(_config(), work_dir=work, client=client)
    assert isinstance(result, Phase2Result)
    assert result.attempts == 1
    assert result.used_fallback is False
    assert len(result.sections) == 8
    assert result.total_target_words == 8800
    assert result.outline_path == work / "2_小节大纲.md"
    assert result.outline_path.exists()
    assert client.calls[0]["thinking_mode"] is True


def test_run_outline_uses_pitch_target_length_midpoint(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=10000)
    # midpoint of [9500, 10500] is 10000; produce outline summing to 10000
    md = _good_outline_md([1250] * 8)
    result = run_outline(_config(), work_dir=work, client=StubClient([md]))
    assert result.target_length == 10000
    assert result.total_target_words == 10000


# ============================================================ retry


def test_run_outline_retries_after_validation_fail(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=10000)
    # First attempt: total = 16000 (way over ±10%) → fail
    bad = _good_outline_md([2000] * 8)
    # Second attempt: total = 10000 → pass
    good = _good_outline_md([1250] * 8)
    client = StubClient([bad, good])

    result = run_outline(_config(), work_dir=work, client=client, max_retries=2)
    assert result.attempts == 2
    assert result.used_fallback is False
    assert any("attempt 1" in w for w in result.warnings)
    # Retry message should be appended on attempt 2.
    assert len(client.calls) == 2
    last_msgs = client.calls[1]["messages"]
    assert any("未通过硬校验" in m["content"] for m in last_msgs)


def test_run_outline_section_count_too_few_triggers_retry(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=10000)
    # 7 sections — below SECTION_COUNT_MIN
    bad = _good_outline_md([1500] * 7)  # also wrong total but section count fails first
    good = _good_outline_md([1250] * 8)
    client = StubClient([bad, good])
    result = run_outline(_config(), work_dir=work, client=client)
    assert result.attempts == 2


def test_run_outline_section_chars_out_of_range_triggers_retry(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=10000)
    # 8 sections summing to ~10000 BUT one section is 700 (below 800 floor)
    bad = _good_outline_md([700, 1300, 1300, 1300, 1300, 1300, 1400, 1400])
    good = _good_outline_md([1250] * 8)
    client = StubClient([bad, good])
    result = run_outline(_config(), work_dir=work, client=client)
    assert result.attempts == 2


# ============================================================ fallback


def test_run_outline_falls_back_in_mock_after_retries(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=10000)
    client = StubClient(
        [
            "[mock] 没有表格的占位输出",
            "[mock] 还是没有表格",
            "[mock] 第三次依旧没有表格",
        ]
    )
    result = run_outline(_config(mock=True), work_dir=work, client=client, max_retries=2)
    assert result.used_fallback is True
    assert result.target_length == 10000
    # fallback synthesizes 8 sections
    assert len(result.sections) == 8
    # all section target_words inside [800, 1500]
    for s in result.sections:
        assert SECTION_CHARS_MIN <= s.target_words <= SECTION_CHARS_MAX
    # total within ±10%
    total = result.total_target_words
    assert 9000 <= total <= 11000


def test_run_outline_live_mode_raises_after_retries(tmp_path: Path) -> None:
    work = _make_workdir(tmp_path, target_length=10000)
    client = StubClient(["bad"] * 5, mock=False)
    with pytest.raises(PhaseOutlineError):
        run_outline(_config(mock=False), work_dir=work, client=client, max_retries=2)


def test_run_outline_missing_framework_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    with pytest.raises(PhaseOutlineError):
        run_outline(_config(), work_dir=work, client=StubClient([]))


# ============================================================ prompt


def test_build_phase2_prompt_substitutes_required_fields() -> None:
    msgs = build_phase2_prompt(
        framework_md="# 设定\n## final_title\n标题\n\n## summary\n摘要\n",
        target_length=10000,
        project_root=ROOT,
    )
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "# 设定" in user
    assert "10000" in user
    assert str(SECTION_COUNT_MIN) in user
    assert str(SECTION_COUNT_MAX) in user
    assert str(SECTION_CHARS_MIN) in user
    assert str(SECTION_CHARS_MAX) in user
