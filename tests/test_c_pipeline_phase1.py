"""Tests for generator/c_pipeline/phase1_framework.py (Phase C.4).

Mock-LLM path verifies:
- thinking_mode=True is passed to chat_completion
- well-formed Markdown round-trips through final_title / summary extraction
- summary word range is platform-aware (番茄 → 150-300 / 知乎 → 200-500)
- mock fallback synthesizes a Phase 2-ready 1_设定.md
- live-mode (mock disabled) raises when LLM output is missing required sections
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
from generator.c_pipeline.phase1_framework import (
    Phase1Result,
    PhaseFrameworkError,
    build_phase1_prompt,
    run_framework,
)
from scan.seed_evolver import load_seeds


SEEDS_PATH = ROOT / "data" / "scan_seeds.yaml"
SEEDS = load_seeds(SEEDS_PATH)


# ============================================================ helpers


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
            {
                "messages": messages,
                "thinking_mode": thinking_mode,
                "model": model,
                "purpose": purpose,
            }
        )
        return ChatCompletion(
            text=self.text,
            reasoning="[mock-thinking]" if thinking_mode else None,
            model=model or "deepseek-v4-pro",
            usage=ChatUsage(input_tokens=200, output_tokens=600),
            finish_reason="stop",
            cached=False,
        )


def _config(mock: bool = True) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": mock, "project_root": str(ROOT)},
            "deepseek": {
                "api_key": "" if mock else "sk-test",
                "model": "deepseek-v4-pro",
                "thinking_mode": True,
                "mock": mock,
            },
        },
        path=Path("config.yaml"),
    )


def _pitch(**overrides: Any) -> dict[str, Any]:
    base = {
        "theme": "白领姐弟拆迁分房纠纷复仇",
        "tuned_pitch": "32 岁法务总监,弟弟把父母拆迁款卷走给前任买房,我连夜起诉",
        "protagonist": {
            "identity": "32 岁法务总监",
            "narrative_voice": "第一人称",
        },
        "antagonist_or_object": "亲弟弟与他的前妻",
        "trigger_event": "母亲深夜来电:你弟把房子卖了",
        "tone_keywords": ["冷静", "细节"],
        "target_length": [10000, 12000],
        "emotion_id": "shuang_gan_shi_fang",
        "genre_id": "xian_dai_fu_chou",
        "opening_mode_id": "leng_xiao_fa_xian",
        "ending_mode_id": "da_chang_jing_tou",
        "reversal_type_id": "shi_jiao_fan_zhuan",
        "target_platform": "番茄短篇",
        "weekly_topic_used": "拆迁分房",
        "hint_title": "弟弟把拆迁款卷走那天我笑了",
    }
    base.update(overrides)
    return base


def _good_framework_md(title: str = "弟弟把拆迁款卷走那天我笑了", summary_chars: int = 200) -> str:
    summary_text = "我盯着银行短信,母亲在电话里哭。" * 8
    summary_text = summary_text[:summary_chars]
    return f"""# 故事设定

## final_title
{title}

## summary
{summary_text}

## 一句话核心
法务总监姐姐用证据链追回弟弟卷走的拆迁款。

## 主角
- 身份:32 岁法务总监
- 标志性动作:端起水杯
- 内心驱动力:守住父母的房

## 核心反派
- 身份:弟弟和他的前妻
- 标志性动作:伸手要钱时低头
- 动机:对父母资源的理所当然

## 关键配角
- 母亲:作用 / 接电话时颤抖

## 反转设计
- 主反转(第 6 节):弟弟的前妻其实早已知情
- 小反转 1:转账记录被备份
- 小反转 2:弟弟妻子也被骗

## 结构物件(物件三现)
- 物件 1:房产证
  - 一现:第 1 节
  - 二现:第 4 节
  - 三现:第 8 节

## 钩子设计
- 开篇钩子:母亲深夜来电
- 章末钩子原则:每节末埋一个数字
- 收尾设计:电梯门关

## 情绪曲线落点
- 节 1:愤怒
- 节 2:冷静
- 节 3:布局
- 节 4:推进
- 节 5:转折
- 节 6:爆发
- 节 7:碾压
- 节 8:余韵
"""


# ============================================================ happy path


def test_run_framework_extracts_title_and_summary(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "0_选题.json").write_text(
        json.dumps(_pitch(), ensure_ascii=False), encoding="utf-8"
    )
    md = _good_framework_md(title="弟弟把拆迁款卷走那天我笑了", summary_chars=180)
    client = StubClient(md)

    result = run_framework(
        _config(),
        work_dir=work,
        seeds_path=SEEDS_PATH,
        client=client,
    )
    assert isinstance(result, Phase1Result)
    assert result.final_title == "弟弟把拆迁款卷走那天我笑了"
    assert result.summary.startswith("我盯着银行短信")
    assert 100 <= len(result.summary) <= 300
    assert result.framework_path == work / "1_设定.md"
    assert result.framework_path.exists()
    assert result.used_fallback is False


def test_run_framework_passes_thinking_mode_true(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "0_选题.json").write_text(
        json.dumps(_pitch(), ensure_ascii=False), encoding="utf-8"
    )
    client = StubClient(_good_framework_md())
    run_framework(_config(), work_dir=work, seeds_path=SEEDS_PATH, client=client)
    assert client.calls[0]["thinking_mode"] is True
    assert client.calls[0]["purpose"] == "phase_1"


# ============================================================ summary range


def test_summary_word_range_zhihu_overrides_default(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    pitch = _pitch(target_platform="知乎盐言")
    (work / "0_选题.json").write_text(json.dumps(pitch, ensure_ascii=False), encoding="utf-8")
    client = StubClient(_good_framework_md(summary_chars=350))

    result = run_framework(
        _config(), work_dir=work, seeds_path=SEEDS_PATH, client=client
    )
    # 知乎金句式: word_range = [200, 500]
    assert result.summary_word_range == (200, 500)


def test_summary_word_range_fanqie_default_150_300(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "0_选题.json").write_text(
        json.dumps(_pitch(), ensure_ascii=False), encoding="utf-8"
    )
    client = StubClient(_good_framework_md(summary_chars=200))
    result = run_framework(_config(), work_dir=work, seeds_path=SEEDS_PATH, client=client)
    assert result.summary_word_range == (150, 300)


# ============================================================ fallback


def test_fallback_synthesizes_when_mock_returns_garbage(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "0_选题.json").write_text(
        json.dumps(_pitch(), ensure_ascii=False), encoding="utf-8"
    )
    # Mock-style placeholder that has no markdown structure.
    client = StubClient("[mock] DeepSeek 客户端运行在 mock 模式。")

    result = run_framework(
        _config(mock=True),
        work_dir=work,
        seeds_path=SEEDS_PATH,
        client=client,
    )
    assert result.used_fallback is True
    assert result.final_title  # non-empty
    assert len(result.summary) >= 150
    assert "fallback" in result.framework_md
    # Phase 2 should still see the markdown skeleton.
    assert "## final_title" in result.framework_md
    assert "## summary" in result.framework_md


def test_live_mode_raises_when_llm_output_missing_sections(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    (work / "0_选题.json").write_text(
        json.dumps(_pitch(), ensure_ascii=False), encoding="utf-8"
    )
    client = StubClient("内容缺失,只有正文段落而没有 markdown 章节。", mock=False)
    with pytest.raises(PhaseFrameworkError):
        run_framework(
            _config(mock=False),
            work_dir=work,
            seeds_path=SEEDS_PATH,
            client=client,
        )


def test_missing_pitch_file_raises(tmp_path: Path) -> None:
    work = tmp_path / "works" / "1"
    work.mkdir(parents=True)
    client = StubClient(_good_framework_md())
    with pytest.raises(PhaseFrameworkError):
        run_framework(
            _config(),
            work_dir=work,
            seeds_path=SEEDS_PATH,
            client=client,
        )


# ============================================================ prompt content


def test_prompt_includes_summary_formula_and_genre_refs() -> None:
    pitch = _pitch()
    msgs = build_phase1_prompt(pitch, seeds=SEEDS, project_root=ROOT)
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    # Pitch JSON is dumped wholesale into the prompt
    assert pitch["theme"] in user
    assert pitch["weekly_topic_used"] in user
    # 番茄_主推: structure starts with 情境设定
    assert "情境设定" in user
    # genre xian_dai_fu_chou: formula starts with 当众背叛
    assert "当众背叛" in user
    # opening leng_xiao_fa_xian → "冷发现" template
    assert "发现异常后第一反应" in user
    # summary range
    assert "150" in user
    assert "300" in user


def test_prompt_uses_zhihu_summary_for_zhihu_pitch() -> None:
    pitch = _pitch(target_platform="知乎盐言")
    msgs = build_phase1_prompt(pitch, seeds=SEEDS, project_root=ROOT)
    user = msgs[1]["content"]
    # 知乎金句式 has structure starting with "人物关系"
    assert "人物关系" in user
    assert "200" in user
    assert "500" in user
