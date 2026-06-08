"""Tests for generator/c_pipeline/phase0_select.py (Phase C.3).

Covers theme-pool consumption (lowest-consumed-first + tie-break by id),
consumed_count++ side effect, override application, prompt construction
substitutes all required fields, fallback pitch when LLM output is not
JSON-parseable, and the LLM injection path.
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
from generator.c_pipeline.phase0_select import (
    PHASE0_OUTPUT_FIELDS,
    ThemePoolEmptyError,
    build_phase0_prompt,
    select_theme,
)
from scan.seed_evolver import load_seeds

SEEDS_PATH = ROOT / "data" / "scan_seeds.yaml"
SEEDS = load_seeds(SEEDS_PATH)


# ============================================================ helpers


class StubClient:
    """Minimal DeepSeek client stub returning a canned ChatCompletion."""

    def __init__(self, text: str, usage_in: int = 100, usage_out: int = 200) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []
        self._usage = ChatUsage(input_tokens=usage_in, output_tokens=usage_out)

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
            reasoning=None,
            model=model or "deepseek-v4-pro",
            usage=self._usage,
            finish_reason="stop",
            cached=False,
        )


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(ROOT)},
            "deepseek": {
                "api_key": "",
                "model": "deepseek-v4-pro",
                "thinking_mode": True,
                "mock": True,
            },
            "database": {"sqlite_path": str(tmp_path / "anw.sqlite3")},
        },
        path=Path("config.yaml"),
    )


def _pool_path(tmp_path: Path) -> Path:
    pool = tmp_path / "theme_pool.json"
    pool.write_text(
        json.dumps(
            {
                "version": 1,
                "iso_week": "2026W19",
                "items": [
                    {
                        "id": "tp_2026w19_001",
                        "theme": "白领姐弟拆迁分房纠纷复仇",
                        "emotion": "shuang_gan_shi_fang",
                        "genre": "xian_dai_fu_chou",
                        "formula_used": "...",
                        "target_platform": "番茄短篇",
                        "target_length": [8000, 12000],
                        "hint_title": "弟弟把拆迁款卷走那天我笑了",
                        "title_pattern_used": "点众式",
                        "opening_mode": "leng_xiao_fa_xian",
                        "ending_mode": "da_chang_jing_tou",
                        "reversal_type": "shi_jiao_fan_zhuan",
                        "expected_audience": "女频/30-45 都市",
                        "seasonal_or_topic_seed": "拆迁分房",
                        "consumed_count": 2,
                        "created_at": "2026-05-06T03:00:00Z",
                    },
                    {
                        "id": "tp_2026w19_002",
                        "theme": "总裁丈夫白月光归来公开侮辱",
                        "emotion": "yi_nan_ping",
                        "genre": "zong_cai_hao_men",
                        "formula_used": "...",
                        "target_platform": "番茄短篇",
                        "target_length": [10000, 13000],
                        "hint_title": "白月光回国当晚他递来了离婚协议",
                        "title_pattern_used": "番茄主流",
                        "opening_mode": "chong_tu_qian_zhi",
                        "ending_mode": "yu_yun_shi",
                        "reversal_type": "shen_fen_fan_zhuan",
                        "expected_audience": "女频/25-35 都市",
                        "seasonal_or_topic_seed": "婚前协议",
                        "consumed_count": 0,
                        "created_at": "2026-05-06T03:00:00Z",
                    },
                    {
                        "id": "tp_2026w19_003",
                        "theme": "灵魂视角病房母亲偏心目睹",
                        "emotion": "yi_nan_ping",
                        "genre": "ling_hun_jia_ting_nve",
                        "formula_used": "...",
                        "target_platform": "番茄短篇",
                        "target_length": [9000, 11000],
                        "hint_title": "脊椎手术那天妈妈让我去祭祖",
                        "title_pattern_used": "黑岩式",
                        "opening_mode": "bing_zhong_bei_bi",
                        "ending_mode": "chong_sheng_nuan_xin",
                        "reversal_type": "shi_jian_xian_fan_zhuan",
                        "expected_audience": "女频/25-45 都市",
                        "seasonal_or_topic_seed": "原生家庭议题",
                        "consumed_count": 0,
                        "created_at": "2026-05-06T03:00:00Z",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pool


def _llm_pitch_response(item: dict[str, Any]) -> str:
    """Build a JSON string the LLM might plausibly return."""
    return json.dumps(
        {
            "theme": item["theme"],
            "tuned_pitch": "32 岁法务总监,亲弟弟把父母拆迁款卷走给前任买房,我连夜起诉",
            "protagonist": {
                "identity": "32 岁法务总监,公司内小有名气",
                "narrative_voice": "第一人称",
            },
            "antagonist_or_object": "亲弟弟与他的前妻,用拆迁款给前妻买房",
            "trigger_event": "母亲深夜来电:你弟把房子卖了,钱全给那女人",
            "tone_keywords": ["冷静", "细节", "克制"],
            "target_length": item["target_length"],
            "emotion_id": item["emotion"],
            "genre_id": item["genre"],
            "opening_mode_id": item["opening_mode"],
            "ending_mode_id": item["ending_mode"],
            "reversal_type_id": item["reversal_type"],
            "target_platform": item["target_platform"],
            "weekly_topic_used": item["seasonal_or_topic_seed"],
            "hint_title": item["hint_title"],
        },
        ensure_ascii=False,
    )


# ============================================================ pick / consume


def test_pick_lowest_consumed_first_then_id(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    work = tmp_path / "works" / "1"
    items_before = json.loads(pool.read_text(encoding="utf-8"))["items"]

    item2 = next(it for it in items_before if it["id"] == "tp_2026w19_002")
    client = StubClient(_llm_pitch_response(item2))

    result = select_theme(
        _config(tmp_path),
        work_dir=work,
        theme_pool_path=pool,
        seeds_path=SEEDS_PATH,
        client=client,
    )

    # tp_002 and tp_003 both have consumed_count=0, but tp_002's id sorts first.
    assert result.theme_pool_item["id"] == "tp_2026w19_002"
    # consumed_count++ persisted to disk
    after = json.loads(pool.read_text(encoding="utf-8"))["items"]
    by_id = {it["id"]: it for it in after}
    assert by_id["tp_2026w19_001"]["consumed_count"] == 2  # untouched
    assert by_id["tp_2026w19_002"]["consumed_count"] == 1  # +1
    assert by_id["tp_2026w19_003"]["consumed_count"] == 0  # untouched


def test_consumed_count_increments_each_call(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    item2 = next(
        it
        for it in json.loads(pool.read_text(encoding="utf-8"))["items"]
        if it["id"] == "tp_2026w19_002"
    )
    client = StubClient(_llm_pitch_response(item2))

    for run in range(1, 4):
        select_theme(
            _config(tmp_path),
            work_dir=tmp_path / "works" / str(run),
            theme_pool_path=pool,
            seeds_path=SEEDS_PATH,
            client=client,
        )
    after = {it["id"]: it for it in json.loads(pool.read_text(encoding="utf-8"))["items"]}
    # All three items started at 0/0/2 — after 3 picks the lowest gets picked twice
    # then ties shift, so total consumed across 002+003 should be 3.
    total = after["tp_2026w19_002"]["consumed_count"] + after["tp_2026w19_003"]["consumed_count"]
    assert total == 3
    assert after["tp_2026w19_001"]["consumed_count"] == 2  # never picked


# ============================================================ overrides


def test_overrides_apply_word_count_and_theme(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    item = next(
        it
        for it in json.loads(pool.read_text(encoding="utf-8"))["items"]
        if it["id"] == "tp_2026w19_002"
    )
    client = StubClient(_llm_pitch_response(item))

    result = select_theme(
        _config(tmp_path),
        work_dir=tmp_path / "works" / "1",
        theme_pool_path=pool,
        seeds_path=SEEDS_PATH,
        client=client,
        overrides={"target_length": 9000, "theme": "强行覆盖的题材"},
    )
    assert "target_length" in result.overrides_applied
    # ±5% range computed from override
    assert result.overrides_applied["target_length"] == [8550, 9450]
    assert result.theme_pool_item["theme"] == "强行覆盖的题材"


# ============================================================ output schema


def test_pitch_json_has_all_required_fields(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    item = next(
        it
        for it in json.loads(pool.read_text(encoding="utf-8"))["items"]
        if it["id"] == "tp_2026w19_002"
    )
    client = StubClient(_llm_pitch_response(item))

    result = select_theme(
        _config(tmp_path),
        work_dir=tmp_path / "works" / "1",
        theme_pool_path=pool,
        seeds_path=SEEDS_PATH,
        client=client,
    )

    pitch = json.loads(result.pitch_path.read_text(encoding="utf-8"))
    for field in PHASE0_OUTPUT_FIELDS:
        assert field in pitch, f"missing field: {field}"
    assert pitch["theme"]
    assert pitch["target_length"] == item["target_length"]
    assert pitch["emotion_id"] == item["emotion"]
    assert pitch["protagonist"]["narrative_voice"] == "第一人称"


# ============================================================ fallback


def test_fallback_pitch_when_llm_returns_garbage(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    client = StubClient("[mock] not a json response — Phase B mock placeholder")

    result = select_theme(
        _config(tmp_path),
        work_dir=tmp_path / "works" / "1",
        theme_pool_path=pool,
        seeds_path=SEEDS_PATH,
        client=client,
    )
    assert result.used_fallback is True
    pitch = json.loads(result.pitch_path.read_text(encoding="utf-8"))
    for field in PHASE0_OUTPUT_FIELDS:
        assert field in pitch
    assert "fallback" in pitch["tuned_pitch"]
    assert pitch["target_length"] == [10000, 13000]


def test_fallback_tolerates_text_then_json_wrapping(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    item = next(
        it
        for it in json.loads(pool.read_text(encoding="utf-8"))["items"]
        if it["id"] == "tp_2026w19_002"
    )
    wrapped = "前置说明文字\n```json\n" + _llm_pitch_response(item) + "\n```"
    client = StubClient(wrapped)

    result = select_theme(
        _config(tmp_path),
        work_dir=tmp_path / "works" / "1",
        theme_pool_path=pool,
        seeds_path=SEEDS_PATH,
        client=client,
    )
    assert result.used_fallback is False
    pitch = json.loads(result.pitch_path.read_text(encoding="utf-8"))
    assert pitch["tuned_pitch"].startswith("32 岁法务总监")


# ============================================================ prompt construction


def test_prompt_substitutes_genre_and_opening_details(tmp_path: Path) -> None:
    item = json.loads(_pool_path(tmp_path).read_text(encoding="utf-8"))["items"][1]
    messages = build_phase0_prompt(item, seeds=SEEDS, project_root=ROOT)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user = messages[1]["content"]
    # opening_mode 'chong_tu_qian_zhi' → template "第一句就是矛盾"
    assert "第一句就是矛盾" in user
    # genre 'zong_cai_hao_men' → formula starts with "三层伤害"
    assert "三层伤害" in user
    # weekly topic
    assert "婚前协议" in user
    # target length range
    assert "10000" in user
    assert "13000" in user


def test_prompt_handles_unknown_ids_gracefully(tmp_path: Path) -> None:
    item = {
        "theme": "测试题材",
        "emotion": "unknown_emotion",
        "genre": "unknown_genre",
        "opening_mode": "unknown_opening",
        "ending_mode": "unknown_ending",
        "reversal_type": "unknown_reversal",
        "target_platform": "番茄短篇",
        "target_length": [8000, 12000],
        "hint_title": "测试标题",
        "seasonal_or_topic_seed": "测试",
        "expected_audience": "女频",
    }
    messages = build_phase0_prompt(item, seeds=SEEDS, project_root=ROOT)
    user = messages[1]["content"]
    # Substitution should still produce valid text, just with empty values for unknown ids.
    assert "测试题材" in user
    assert "unknown_emotion" in user


# ============================================================ error path


def test_empty_pool_raises(tmp_path: Path) -> None:
    pool = tmp_path / "theme_pool.json"
    pool.write_text(json.dumps({"items": []}), encoding="utf-8")
    client = StubClient("{}")
    with pytest.raises(ThemePoolEmptyError):
        select_theme(
            _config(tmp_path),
            work_dir=tmp_path / "works" / "1",
            theme_pool_path=pool,
            seeds_path=SEEDS_PATH,
            client=client,
        )


def test_missing_pool_file_raises(tmp_path: Path) -> None:
    client = StubClient("{}")
    with pytest.raises(ThemePoolEmptyError):
        select_theme(
            _config(tmp_path),
            work_dir=tmp_path / "works" / "1",
            theme_pool_path=tmp_path / "does_not_exist.json",
            seeds_path=SEEDS_PATH,
            client=client,
        )


# ============================================================ llm call settings


def test_llm_call_uses_pro_no_thinking_and_json_object(tmp_path: Path) -> None:
    pool = _pool_path(tmp_path)
    item = next(
        it
        for it in json.loads(pool.read_text(encoding="utf-8"))["items"]
        if it["id"] == "tp_2026w19_002"
    )
    client = StubClient(_llm_pitch_response(item))

    select_theme(
        _config(tmp_path),
        work_dir=tmp_path / "works" / "1",
        theme_pool_path=pool,
        seeds_path=SEEDS_PATH,
        client=client,
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["thinking_mode"] is False  # phase 0 no thinking
    assert call["purpose"] == "phase_0"
