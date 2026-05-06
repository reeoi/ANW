"""Tests for phase4_polish.py + phase5_deslop.py (Phase C.7).

Coverage:
- Phase 4 happy path: writes 4_精修稿.md, thinking_mode=True
- Phase 4 prompt enforces "保留架构不变,只改语言" constraint
- Phase 4 mock fallback preserves Phase 3 content
- Phase 5 happy path: writes 5_最终稿.md, thinking_mode=False
- Phase 5 prompt embeds full blacklist
- Phase 5 mock fallback locally strips blacklist words
- Phase 5 reports residual slop hits as warnings (not errors)
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
from generator.c_pipeline.phase4_polish import (
    Phase4Result,
    PhasePolishError,
    build_phase4_prompt,
    run_polish,
)
from generator.c_pipeline.phase5_deslop import (
    Phase5Result,
    PhaseDeSlopError,
    build_phase5_prompt,
    run_deslop,
)


class StubClient:
    def __init__(self, text: str, *, mock: bool = True) -> None:
        self.text = text
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
            {"messages": messages, "thinking_mode": thinking_mode, "purpose": purpose}
        )
        return ChatCompletion(
            text=self.text,
            reasoning="(mock)" if thinking_mode else None,
            model="deepseek-v4-pro",
            usage=ChatUsage(input_tokens=1000, output_tokens=2000),
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


def _make_polish_workdir(tmp_path: Path) -> Path:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    body = ("她把钥匙放在桌上,门外的脚步声停了一下。\n" * 80)
    (work / "3_正文_合稿.md").write_text(body, encoding="utf-8")
    return work


def _long_polished(prefix: str = "精修") -> str:
    body = ("我盯着银行短信,手指在桌沿上抠出一道白印。\n" * 80)
    return prefix + "\n\n" + body


# ============================================================ phase 4


def test_phase4_writes_polished_md(tmp_path: Path) -> None:
    work = _make_polish_workdir(tmp_path)
    client = StubClient(_long_polished())
    result = run_polish(_config(), work_dir=work, client=client)
    assert isinstance(result, Phase4Result)
    assert result.polished_path == work / "4_精修稿.md"
    assert result.polished_path.exists()
    assert result.char_count > 800
    assert client.calls[0]["thinking_mode"] is True
    assert client.calls[0]["purpose"] == "phase_4"
    assert result.used_fallback is False


def test_phase4_prompt_keeps_architecture_constraint() -> None:
    msgs = build_phase4_prompt(combined_md="原文……", project_root=ROOT)
    user = msgs[1]["content"]
    assert "保留架构完全不变" in user
    assert "节数 / 节顺序 / 主反转位置" in user
    assert "原文……" in user


def test_phase4_mock_fallback_preserves_phase3_content(tmp_path: Path) -> None:
    work = _make_polish_workdir(tmp_path)
    # Mock-style placeholder is too short → fallback path kicks in
    client = StubClient("[mock] phase4 placeholder")
    result = run_polish(_config(mock=True), work_dir=work, client=client)
    assert result.used_fallback is True
    polished = result.polished_path.read_text(encoding="utf-8")
    # Original Phase 3 text must survive into 4_精修稿.md
    assert "她把钥匙放在桌上" in polished
    assert "phase4 mock fallback" in polished


def test_phase4_missing_input_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    with pytest.raises(PhasePolishError):
        run_polish(_config(), work_dir=work, client=StubClient(""))


# ============================================================ phase 5


def _make_deslop_workdir(tmp_path: Path) -> Path:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    polished = "她把钥匙放在桌上,门外的脚步声停了一下。\n" * 80
    (work / "4_精修稿.md").write_text(polished, encoding="utf-8")
    return work


def test_phase5_writes_final_md(tmp_path: Path) -> None:
    work = _make_deslop_workdir(tmp_path)
    final_text = "她把钥匙放在桌上。\n" * 80
    client = StubClient(final_text)
    result = run_deslop(_config(), work_dir=work, client=client)
    assert isinstance(result, Phase5Result)
    assert result.final_path == work / "5_最终稿.md"
    assert result.final_path.exists()
    assert client.calls[0]["thinking_mode"] is False
    assert client.calls[0]["purpose"] == "phase_5"


def test_phase5_prompt_embeds_full_blacklist(tmp_path: Path) -> None:
    blacklist = ["顿时", "瞬间", "如同潮水般"]
    msgs = build_phase5_prompt(
        polished_md="原文……", blacklist=blacklist, project_root=ROOT
    )
    user = msgs[1]["content"]
    assert "原文……" in user
    for w in blacklist:
        assert w in user


def test_phase5_reports_residual_slop_in_warnings(tmp_path: Path) -> None:
    work = _make_deslop_workdir(tmp_path)
    # Use a custom blacklist file so we can force a remaining hit.
    bl_path = tmp_path / "bl.json"
    bl_path.write_text(json.dumps(["残留词"], ensure_ascii=False), encoding="utf-8")
    final_text = "她把钥匙放在桌上。\n残留词依然在文中。\n" * 60
    client = StubClient(final_text)
    result = run_deslop(
        _config(),
        work_dir=work,
        blacklist_path=bl_path,
        client=client,
    )
    assert not result.slop_check.ok
    assert any("slop residue" in w for w in result.warnings)


def test_phase5_mock_fallback_strips_blacklist_words(tmp_path: Path) -> None:
    work = _make_deslop_workdir(tmp_path)
    # Inject blacklist words into the polished input so the local stripper is
    # actually doing work in the fallback path.
    polished = (
        "她顿时愣住了。\n瞬间一切都明白了。\n"
        "门外的脚步声停了一下。\n" * 60
    )
    (work / "4_精修稿.md").write_text(polished, encoding="utf-8")

    bl_path = tmp_path / "bl.json"
    bl_path.write_text(json.dumps(["顿时", "瞬间"], ensure_ascii=False), encoding="utf-8")

    client = StubClient("[mock] short placeholder")
    result = run_deslop(
        _config(mock=True),
        work_dir=work,
        blacklist_path=bl_path,
        client=client,
    )
    assert result.used_fallback is True
    final = result.final_path.read_text(encoding="utf-8")
    assert "顿时" not in final
    assert "瞬间" not in final
    assert "门外的脚步声停了一下" in final  # untouched content survives
    # Final slop check should now pass.
    assert result.slop_check.ok


def test_phase5_missing_input_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    with pytest.raises(PhaseDeSlopError):
        run_deslop(_config(), work_dir=work, client=StubClient(""))


def test_phase5_uses_real_blacklist_when_path_unset(tmp_path: Path) -> None:
    work = _make_deslop_workdir(tmp_path)
    final_text = "她把钥匙放在桌上。\n" * 80
    client = StubClient(final_text)
    result = run_deslop(_config(), work_dir=work, client=client)
    # The real blacklist is non-empty; final_text intentionally contains zero of those words.
    assert result.slop_check.ok
