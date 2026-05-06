"""Tests for c_pipeline config.yaml + config_loader env overrides (Phase A4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import ConfigError, load_config


def test_default_config_yaml_parses_with_c_pipeline_fields() -> None:
    """The repo-level config.yaml must declare all PLAN §5.1 fields."""

    config = load_config(ROOT / "config.yaml")
    deepseek = config.data["deepseek"]
    assert deepseek["model"] == "deepseek-v4-pro"
    assert deepseek["flash_model"] == "deepseek-v4-flash"
    assert deepseek["thinking_mode"] is True
    assert deepseek["prompt_cache_enabled"] is True
    assert deepseek["timeout_seconds"] == 120

    audit = config.data["audit"]
    assert audit["approval_threshold"] == 90
    assert audit["rewrite_strategy"] == "phase_4_5_only"
    assert audit["max_rewrite_attempts"] == 3

    publisher = config.data["publisher"]
    assert publisher["daily_count_distribution"] == "uniform"
    assert publisher["daily_count_min"] == 0
    assert publisher["daily_count_max"] == 5
    assert publisher["operating_hours"] == ["09:00", "22:00"]
    assert publisher["slot_min_gap_minutes"] == 30

    scheduler = config.data["scheduler"]
    assert scheduler["weekly_scan_cron"] == "0 3 * * 1"
    assert scheduler["plan_today_cron"] == "0 3 * * *"
    assert scheduler["generate_cron"] == ""
    assert scheduler["publish_cron"] == ""

    scan = config.data["scan"]
    assert scan["pool_size"] == 100
    assert scan["on_failure"] == "fallback_or_block"
    assert scan["seed_file"] == "data/scan_seeds.yaml"

    c_pipeline = config.data["c_pipeline"]
    assert c_pipeline["max_concurrent_pipelines"] == 2
    assert c_pipeline["phase_2_max_retries"] == 2
    assert c_pipeline["phase_3_section_max_retries"] == 2

    cost_limits = config.data["cost_limits"]
    assert cost_limits["monthly_budget_cny"] == 100.0
    assert cost_limits["daily_token_limit"] == 800000
    assert cost_limits["on_budget_exceeded"] == "degrade"
    assert cost_limits["degrade_phases"] == ["phase_3", "phase_5", "ai_review", "weekly_scan"]


def test_default_config_drops_legacy_generation_block() -> None:
    """Old ``generation`` block (theme/word_count/style) is replaced by theme_pool."""

    config = load_config(ROOT / "config.yaml")
    assert "generation" not in config.data


def test_default_config_drops_legacy_stop_when_exceeded() -> None:
    """``cost_limits.stop_when_exceeded`` is replaced by ``on_budget_exceeded`` (B2)."""

    config = load_config(ROOT / "config.yaml")
    assert "stop_when_exceeded" not in config.data["cost_limits"]


def test_env_override_for_deepseek_thinking_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\n  thinking_mode: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_DEEPSEEK_THINKING_MODE", "false")
    config = load_config(cfg)
    assert config.data["deepseek"]["thinking_mode"] is False


def test_env_override_for_prompt_cache_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\n  prompt_cache_enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_DEEPSEEK_PROMPT_CACHE_ENABLED", "0")
    config = load_config(cfg)
    assert config.data["deepseek"]["prompt_cache_enabled"] is False


def test_env_override_for_flash_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\n  flash_model: \"deepseek-v4-flash\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash-staging")
    config = load_config(cfg)
    assert config.data["deepseek"]["flash_model"] == "deepseek-v4-flash-staging"


def test_env_override_for_pool_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\nscan:\n  pool_size: 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_SCAN_POOL_SIZE", "50")
    config = load_config(cfg)
    assert config.data["scan"]["pool_size"] == 50


def test_env_override_for_max_concurrent_pipelines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\nc_pipeline:\n  max_concurrent_pipelines: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_MAX_CONCURRENT_PIPELINES", "1")
    config = load_config(cfg)
    assert config.data["c_pipeline"]["max_concurrent_pipelines"] == 1


def test_env_override_for_on_budget_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\ncost_limits:\n  on_budget_exceeded: \"degrade\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_ON_BUDGET_EXCEEDED", "stop")
    config = load_config(cfg)
    assert config.data["cost_limits"]["on_budget_exceeded"] == "stop"


def test_env_override_for_publisher_slot_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"sk-test\"\npublisher:\n  slot_min_gap_minutes: 30\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_SLOT_MIN_GAP_MINUTES", "45")
    config = load_config(cfg)
    assert config.data["publisher"]["slot_min_gap_minutes"] == 45


def test_missing_api_key_forces_dry_run_and_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("deepseek:\n  api_key: \"\"\n", encoding="utf-8")
    # Isolate from the project .env which carries a real DEEPSEEK_API_KEY.
    monkeypatch.setenv("ANP_DOTENV", str(tmp_path / "missing.env"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = load_config(cfg)
    assert config.is_dry_run is True
    assert config.data["deepseek"]["mock"] is True
    assert any("DeepSeek API key" in w for w in config.warnings)


def test_missing_config_path_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")
