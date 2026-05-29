"""Tests for scan/seed_evolver.py (Phase B).

Covers:
- load_seeds() YAML structure
- pick_weekly_topics determinism + season filtering + count override
- build_evolution_prompt template substitution + existing pool injection
- run_weekly_scan happy path: pool write + history backup + metadata
- run_weekly_scan output_schema validation (required fields + enum membership)
- run_weekly_scan diversity hard checks (genre share, reversal/opening/ending distinct,
  keyword overlap, dedup against existing pool)
- run_weekly_scan a+c failure handling (LLM raise / validation fail → fallback or block)
- run_weekly_scan atomic write + JSON tolerance + id normalization
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from scan import (
    WeeklyScanBlockedError,
    WeeklyScanResult,
    build_evolution_prompt,
    load_seeds,
    pick_weekly_topics,
    run_weekly_scan,
)

SEEDS_PATH = ROOT / "data" / "scan_seeds.yaml"


# ============================================================ helpers


EMOTION_DISTRIBUTION = (
    ["yi_nan_ping"] * 18
    + ["fan_zhuan_zhen_han"] * 15
    + ["shuang_gan_shi_fang"] * 30
    + ["zhi_yu_wen_nuan"] * 10
    + ["xi_si_ji_kong"] * 7
    + ["gong_ming_gan_dong"] * 20
)
PLATFORM_DISTRIBUTION = (
    ["番茄短篇"] * 50
    + ["七猫短篇"] * 15
    + ["黑岩短篇"] * 10
    + ["点众短篇"] * 15
    + ["知乎盐言"] * 10
)
GENRE_IDS = [
    "xian_dai_fu_chou",
    "gu_dai_zhai_dou",
    "nve_lian_fu_chou_linghun",
    "zong_cai_hao_men",
    "nian_dai_chong_sheng",
    "gong_wei_nve_fu",
    "xian_dai_xuan_yi",
    "jia_kong_xing_bie_fan_zhuan",
    "jia_qian_jin_xin_sheng",
    "chong_sheng_li_hun_ni_xi",
    "zhui_fu_huo_zang_chang",
    "nian_dai_yi_shu_fu_chou",
    "ling_hun_jia_ting_nve",
    "xi_jie_xian_suo_fu_chou",
    "fan_tao_lu_jia_huo_chong_sheng",
    "gong_kai_shen_pan_da_lian",
    "yin_ren_fu_hei",
    "jia_ting_fu_chou",
    "lao_tai_chong_sheng",
    "zhi_chang_ni_xi_xi_tong",
    "dian_zhong_qing_xi_ju",
]
REVERSAL_TYPES = [
    "shen_fen_fan_zhuan",
    "shi_jiao_fan_zhuan",
    "dong_ji_fan_zhuan",
    "shi_jian_xian_fan_zhuan",
    "xin_xi_cha_fan_zhuan",
]
OPENING_MODES = [
    "chong_tu_qian_zhi",
    "xin_xi_cha_gou",
    "fan_chang_xing_wei",
    "chong_sheng_fan_chang",
    "ling_hun_pang_guan",
    "bing_zhong_bei_bi",
    "si_wang_hui_su",
    "xuan_nian_ju",
    "dai_ru_shi_ti_wen",
    "ti_jia_bei_qi",
    "leng_xiao_fa_xian",
    "yi_chang_wu_jian",
]
ENDING_MODES = [
    "yu_yun_shi",
    "hu_ying_shi",
    "kai_fang_shi",
    "fan_zhuan_zai_fan_zhuan",
    "jin_ju_shi",
    "chong_sheng_nuan_xin",
    "yu_yun_dao_ju",
    "da_chang_jing_tou",
    "fan_feng_dui_wei",
]


def _synthetic_theme(i: int, *, seed: int = 0) -> str:
    """Build a 10-char theme using disjoint CJK slots so themes share zero bigrams.

    Each slot contributes one char drawn from a disjoint CJK code-point range,
    indexed by `i`. Different `seed` values shift the base into separate char
    ranges, so a `seed=0` pool and a `seed=2` pool share zero bigrams. This
    lets one test seed an existing pool that won't trigger dedup against the
    new pool by default — only an explicit override forces overlap.
    """
    base = 0x5400 + seed * 3000
    chars = [chr(base + slot * 200 + i) for slot in range(10)]
    return "".join(chars)


def _build_valid_pool_items(
    count: int = 100,
    iso_week: str = "2026W19",
    *,
    theme_seed: int = 0,
) -> list[dict[str, Any]]:
    """Build a `count`-item pool that satisfies every diversity constraint."""
    if count != 100:
        # Distribution targets are calibrated for pool_size=100.
        raise ValueError("test helper supports count=100 only")

    iso_week_lower = iso_week.lower()
    items: list[dict[str, Any]] = []
    for i in range(count):
        items.append(
            {
                "id": f"tp_{iso_week_lower}_{i + 1:03d}",
                "theme": _synthetic_theme(i, seed=theme_seed),
                "emotion": EMOTION_DISTRIBUTION[i],
                "genre": GENRE_IDS[i % len(GENRE_IDS)],
                "formula_used": "压抑→反击→碾压",
                "target_platform": PLATFORM_DISTRIBUTION[i],
                "target_length": [8000, 12000],
                "hint_title": f"标题样例{i + 1}",
                "title_pattern_used": "番茄主流",
                "opening_mode": OPENING_MODES[i % len(OPENING_MODES)],
                "ending_mode": ENDING_MODES[i % len(ENDING_MODES)],
                "reversal_type": REVERSAL_TYPES[i % len(REVERSAL_TYPES)],
                "expected_audience": "女频/25-35 都市",
                "seasonal_or_topic_seed": "彩礼纠纷",
                "consumed_count": 0,
                "created_at": "2026-05-06T03:00:00",
            }
        )
    return items


def _make_config(
    project_root: Path,
    *,
    pool_size: int = 100,
    on_failure: str = "fallback_or_block",
    api_key: str = "",
) -> LoadedConfig:
    """Construct a LoadedConfig pointing at `project_root` for sandboxed tests."""
    seeds_src = ROOT / "data" / "scan_seeds.yaml"
    seeds_dst = project_root / "data" / "scan_seeds.yaml"
    seeds_dst.parent.mkdir(parents=True, exist_ok=True)
    seeds_dst.write_text(seeds_src.read_text(encoding="utf-8"), encoding="utf-8")

    return LoadedConfig(
        data={
            "runtime": {"project_root": str(project_root), "dry_run": api_key == ""},
            "deepseek": {
                "api_key": api_key,
                "model": "deepseek-v4-pro",
                "flash_model": "deepseek-v4-flash",
                "thinking_mode": True,
                "prompt_cache_enabled": True,
                "timeout_seconds": 120,
                "max_retries": 3,
                "mock": api_key == "",
            },
            "scan": {
                "pool_size": pool_size,
                "on_failure": on_failure,
                "seed_file": "data/scan_seeds.yaml",
            },
        },
        path=Path("config.yaml"),
    )


class _FakeClient:
    """Stand-in for DeepSeekClient that returns a canned response or raises."""

    def __init__(
        self,
        *,
        response_text: str | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self.response_text = response_text
        self.raise_on_call = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def chat_completion(
        self,
        messages,
        *,
        thinking_mode=None,
        model=None,
        temperature=0.8,
        response_format=None,
        purpose="chat",
    ) -> ChatCompletion:
        self.calls.append(
            {
                "messages": list(messages),
                "thinking_mode": thinking_mode,
                "model": model,
                "response_format": response_format,
                "purpose": purpose,
            }
        )
        if self.raise_on_call is not None:
            raise self.raise_on_call
        text = self.response_text if self.response_text is not None else "[]"
        return ChatCompletion(
            text=text,
            reasoning=None,
            model=model or "deepseek-v4-pro",
            usage=ChatUsage(input_tokens=100, cached_tokens=0, output_tokens=200, raw={}),
            finish_reason="stop",
            cached=False,
        )

    def is_mock(self) -> bool:
        return False


def _seed_existing_pool(project_root: Path, *, iso_week: str = "2026W18") -> Path:
    """Write a valid 'last week' theme_pool.json under `project_root`.

    Uses theme_seed=2 so its themes are disjoint from the default new pool,
    avoiding incidental dedup hits in tests that need both pools to coexist.
    """
    pool_path = project_root / "data" / "theme_pool.json"
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text(
        json.dumps(
            {
                "version": 1,
                "iso_week": iso_week,
                "generated_at": "2026-04-29T03:00:00",
                "weekly_topics": ["旧风向"],
                "used_fallback": False,
                "items": _build_valid_pool_items(count=100, iso_week=iso_week, theme_seed=2),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pool_path


# ============================================================ load_seeds


def test_load_seeds_returns_dict_with_required_top_level_keys() -> None:
    seeds = load_seeds(SEEDS_PATH)
    for key in (
        "version",
        "target_platform",
        "emotion_types",
        "genres",
        "title_patterns",
        "opening_modes",
        "ending_modes",
        "reversal_types",
        "diversity_constraints",
        "time_seed_modifiers",
        "output_schema",
        "llm_evolution_prompt_template",
    ):
        assert key in seeds, f"missing top-level key: {key}"


def test_load_seeds_default_path_resolves_to_project_data() -> None:
    seeds = load_seeds()
    assert isinstance(seeds, dict)
    assert "emotion_types" in seeds


# ============================================================ pick_weekly_topics


def test_pick_weekly_topics_default_count_matches_seeds_value() -> None:
    seeds = load_seeds(SEEDS_PATH)
    expected = seeds["time_seed_modifiers"]["weekly_random_count"]
    picks = pick_weekly_topics(seeds, today=date(2026, 5, 6))
    assert len(picks) == expected


def test_pick_weekly_topics_deterministic_within_iso_week() -> None:
    seeds = load_seeds(SEEDS_PATH)
    a = pick_weekly_topics(seeds, today=date(2026, 5, 6))   # 2026W19 Wed
    b = pick_weekly_topics(seeds, today=date(2026, 5, 7))   # 2026W19 Thu
    c = pick_weekly_topics(seeds, today=date(2026, 5, 13))  # 2026W20 Wed
    assert a == b
    assert a != c


def test_pick_weekly_topics_only_returns_topics_in_pool() -> None:
    seeds = load_seeds(SEEDS_PATH)
    valid = set(seeds["time_seed_modifiers"]["current_topics_pool"])
    for season_topics in seeds["time_seed_modifiers"]["seasonal_topics"].values():
        valid.update(season_topics)
    picks = pick_weekly_topics(seeds, today=date(2026, 5, 6), count=10)
    for p in picks:
        assert p in valid


def test_pick_weekly_topics_count_override_works() -> None:
    seeds = load_seeds(SEEDS_PATH)
    picks = pick_weekly_topics(seeds, today=date(2026, 5, 6), count=3)
    assert len(picks) == 3


def test_pick_weekly_topics_excludes_other_season_topics_in_spring() -> None:
    """In May (spring), summer/autumn/winter-only topics must NOT appear."""
    seeds = load_seeds(SEEDS_PATH)
    spring_only = (
        set(seeds["time_seed_modifiers"]["seasonal_topics"]["summer"])
        | set(seeds["time_seed_modifiers"]["seasonal_topics"]["autumn"])
        | set(seeds["time_seed_modifiers"]["seasonal_topics"]["winter"])
    )
    # Force a large count so we'd see leakage if any
    eligible = (
        len(seeds["time_seed_modifiers"]["current_topics_pool"])
        + len(seeds["time_seed_modifiers"]["seasonal_topics"]["spring"])
    )
    picks = pick_weekly_topics(seeds, today=date(2026, 5, 6), count=eligible)
    for p in picks:
        assert p not in spring_only


# ============================================================ build_evolution_prompt


def test_build_evolution_prompt_returns_messages_list() -> None:
    seeds = load_seeds(SEEDS_PATH)
    msgs = build_evolution_prompt(
        seeds,
        weekly_topics=["彩礼纠纷"],
        existing_pool=[],
        pool_size=100,
    )
    assert isinstance(msgs, list)
    assert all(isinstance(m, dict) for m in msgs)
    assert msgs[-1]["role"] == "user"


def test_build_evolution_prompt_substitutes_template_variables() -> None:
    seeds = load_seeds(SEEDS_PATH)
    msgs = build_evolution_prompt(
        seeds,
        weekly_topics=["彩礼纠纷", "AI 替代人类岗位"],
        existing_pool=[],
        pool_size=42,
    )
    user_text = msgs[-1]["content"]
    assert "42" in user_text
    assert "彩礼纠纷" in user_text
    assert "AI 替代人类岗位" in user_text
    assert "${" not in user_text


def test_build_evolution_prompt_lists_every_emotion_id() -> None:
    seeds = load_seeds(SEEDS_PATH)
    msgs = build_evolution_prompt(seeds, weekly_topics=[], existing_pool=[], pool_size=10)
    user_text = msgs[-1]["content"]
    for emo in seeds["emotion_types"]:
        assert emo["id"] in user_text


def test_build_evolution_prompt_includes_existing_pool_themes() -> None:
    seeds = load_seeds(SEEDS_PATH)
    existing = [
        {"theme": "已存在的题材A_独特"},
        {"theme": "已存在的题材B_独特"},
    ]
    msgs = build_evolution_prompt(
        seeds,
        weekly_topics=[],
        existing_pool=existing,
        pool_size=10,
    )
    user_text = msgs[-1]["content"]
    assert "已存在的题材A_独特" in user_text
    assert "已存在的题材B_独特" in user_text


def test_build_evolution_prompt_handles_empty_existing_pool() -> None:
    seeds = load_seeds(SEEDS_PATH)
    msgs = build_evolution_prompt(
        seeds, weekly_topics=[], existing_pool=[], pool_size=10
    )
    user_text = msgs[-1]["content"]
    assert "${" not in user_text


# ============================================================ run_weekly_scan happy path


def test_run_weekly_scan_writes_pool_with_full_metadata(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    items = _build_valid_pool_items(count=100, iso_week="2026W19")
    fake = _FakeClient(response_text=json.dumps(items, ensure_ascii=False))

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    assert isinstance(result, WeeklyScanResult)
    assert result.iso_week == "2026W19"
    assert result.item_count == 100
    assert result.used_fallback is False

    pool_path = tmp_path / "data" / "theme_pool.json"
    assert pool_path.exists()
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    assert pool["iso_week"] == "2026W19"
    assert pool["used_fallback"] is False
    assert pool["weekly_topics"]
    assert "generated_at" in pool
    assert len(pool["items"]) == 100


def test_run_weekly_scan_calls_client_with_pro_thinking_off_and_json_format(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    items = _build_valid_pool_items()
    fake = _FakeClient(response_text=json.dumps(items, ensure_ascii=False))

    run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["thinking_mode"] is False
    assert call["response_format"] == {"type": "json_object"}


def test_run_weekly_scan_records_weekly_topics_into_pool(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    items = _build_valid_pool_items()
    fake = _FakeClient(response_text=json.dumps(items, ensure_ascii=False))

    run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    pool = json.loads((tmp_path / "data" / "theme_pool.json").read_text(encoding="utf-8"))
    seeds = load_seeds(SEEDS_PATH)
    expected = pick_weekly_topics(seeds, today=date(2026, 5, 6))
    assert pool["weekly_topics"] == expected


def test_run_weekly_scan_history_backup_uses_previous_iso_week(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _seed_existing_pool(tmp_path, iso_week="2026W18")

    new_items = _build_valid_pool_items(count=100, iso_week="2026W19")
    fake = _FakeClient(response_text=json.dumps(new_items, ensure_ascii=False))

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    backup_path = tmp_path / "data" / "theme_pool.history" / "2026W18.json"
    assert backup_path.exists()
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup["iso_week"] == "2026W18"
    assert result.backed_up_to == backup_path


def test_run_weekly_scan_normalizes_ids_to_current_iso_week(tmp_path: Path) -> None:
    """LLM may emit ids with the wrong week; we own canonical id format."""
    config = _make_config(tmp_path)
    items = _build_valid_pool_items(count=100, iso_week="2025W01")  # wrong week
    fake = _FakeClient(response_text=json.dumps(items, ensure_ascii=False))

    run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    pool = json.loads((tmp_path / "data" / "theme_pool.json").read_text(encoding="utf-8"))
    for idx, item in enumerate(pool["items"], start=1):
        assert item["id"] == f"tp_2026w19_{idx:03d}"


def test_run_weekly_scan_strips_markdown_code_fence(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    items = _build_valid_pool_items()
    text = "```json\n" + json.dumps(items, ensure_ascii=False) + "\n```"
    fake = _FakeClient(response_text=text)

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)
    assert result.item_count == 100


# ============================================================ schema validation


def test_run_weekly_scan_rejects_unknown_emotion(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    bad[0]["emotion"] = "unknown_emotion_xxx"
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_rejects_missing_required_field(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    del bad[0]["hint_title"]
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_rejects_unknown_genre(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    bad[0]["genre"] = "no_such_genre"
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_rejects_pool_with_wrong_size(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()[:50]  # only 50, not pool_size=100
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


# ============================================================ diversity hard checks


def test_run_weekly_scan_rejects_too_few_reversal_types(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    only_three = REVERSAL_TYPES[:3]
    for i, it in enumerate(bad):
        it["reversal_type"] = only_three[i % 3]
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_rejects_too_few_opening_modes(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    only_seven = OPENING_MODES[:7]  # min_distinct = 8 → 7 fails
    for i, it in enumerate(bad):
        it["opening_mode"] = only_seven[i % 7]
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_rejects_genre_max_share_violation(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    # Force 30 items to a single genre — exceeds 15% cap
    for i in range(30):
        bad[i]["genre"] = "xian_dai_fu_chou"
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_rejects_high_keyword_overlap(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    # Force two themes to share substantial CJK substring (≫ 3 bigrams)
    bad[0]["theme"] = "彩礼纠纷家族遗产复仇逆袭独家版本一"
    bad[1]["theme"] = "彩礼纠纷家族遗产复仇逆袭独家版本二"
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


def test_run_weekly_scan_dedup_against_existing_pool_falls_back(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    # Seed last-week pool (uses theme_seed=2 so it's normally disjoint)
    _seed_existing_pool(tmp_path, iso_week="2026W18")

    new_items = _build_valid_pool_items(count=100, iso_week="2026W19")
    # Force a new theme to clash with an existing seed=2 theme
    new_items[0]["theme"] = _synthetic_theme(0, seed=2)
    fake = _FakeClient(response_text=json.dumps(new_items, ensure_ascii=False))

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)
    assert result.used_fallback is True


# ============================================================ atomic write


def test_run_weekly_scan_no_partial_pool_after_failure(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    fake = _FakeClient(response_text="not valid json {[}")

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    pool_path = tmp_path / "data" / "theme_pool.json"
    tmp_pool = tmp_path / "data" / "theme_pool.json.tmp"
    assert not pool_path.exists()
    assert not tmp_pool.exists()


# ============================================================ a+c fallback


def test_run_weekly_scan_falls_back_when_llm_raises_and_pool_exists(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    pool_path = _seed_existing_pool(tmp_path, iso_week="2026W18")
    fake = _FakeClient(raise_on_call=RuntimeError("DeepSeek down"))

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    assert result.used_fallback is True
    assert result.iso_week == "2026W19"
    assert result.warnings
    # Existing pool stays — we don't overwrite during fallback
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    assert pool["iso_week"] == "2026W18"


def test_run_weekly_scan_falls_back_when_validation_fails_and_pool_exists(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    _seed_existing_pool(tmp_path, iso_week="2026W18")

    bad = _build_valid_pool_items()
    bad[0]["emotion"] = "unknown"
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)
    assert result.used_fallback is True


def test_run_weekly_scan_blocks_when_llm_fails_and_no_existing_pool(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    fake = _FakeClient(raise_on_call=RuntimeError("DeepSeek down"))

    with pytest.raises(WeeklyScanBlockedError) as excinfo:
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)
    msg = str(excinfo.value).lower()
    assert "fallback" in msg or "pool" in msg or "block" in msg


def test_run_weekly_scan_blocks_when_validation_fails_and_no_pool(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bad = _build_valid_pool_items()
    bad[0]["emotion"] = "unknown"
    fake = _FakeClient(response_text=json.dumps(bad, ensure_ascii=False))

    with pytest.raises(WeeklyScanBlockedError):
        run_weekly_scan(config, today=date(2026, 5, 6), client=fake)


# ============================================================ force / skip


def test_run_weekly_scan_skips_when_current_week_pool_already_exists(
    tmp_path: Path,
) -> None:
    """If theme_pool.json already represents this ISO week, scan is a no-op."""
    config = _make_config(tmp_path)
    _seed_existing_pool(tmp_path, iso_week="2026W19")  # current week
    fake = _FakeClient(raise_on_call=AssertionError("client must not be called"))

    result = run_weekly_scan(config, today=date(2026, 5, 6), client=fake)

    assert result.iso_week == "2026W19"
    assert result.used_fallback is False
    assert len(fake.calls) == 0
    assert result.warnings  # explanation populated


def test_run_weekly_scan_force_reruns_even_when_current_week_pool_exists(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    _seed_existing_pool(tmp_path, iso_week="2026W19")
    items = _build_valid_pool_items(count=100, iso_week="2026W19")
    fake = _FakeClient(response_text=json.dumps(items, ensure_ascii=False))

    result = run_weekly_scan(
        config, today=date(2026, 5, 6), client=fake, force=True
    )
    assert result.used_fallback is False
    assert len(fake.calls) == 1
