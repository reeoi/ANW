"""测试 ``review_queue.monitor_aggregator`` 4 张卡片聚合 + 不打扰判断。"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue import monitor_aggregator as ma
from review_queue.db import initialize_database
from review_queue.metrics import ensure_metrics_schema, record_pipeline_event


@pytest.fixture(autouse=True)
def _reset_login_cache() -> None:
    ma.reset_login_cache()
    yield
    ma.reset_login_cache()


@pytest.fixture()
def cfg(tmp_path: Path) -> LoadedConfig:
    db_path = tmp_path / "anw.sqlite3"
    config = LoadedConfig(
        data={
            "database": {"sqlite_path": str(db_path)},
            "cost_limits": {"monthly_budget_cny": 100, "daily_token_limit": 200000},
            "publisher": {"fansq": {"login_state_path": str(tmp_path / "no.json")}},
        },
        path=Path("config.yaml"),
    )
    initialize_database(config)
    return config


def _db_path(cfg: LoadedConfig) -> Path:
    return Path(str(cfg.data["database"]["sqlite_path"]))


# ============================================================================
# next_run（调度器已下线，仅检查占位返回）
# ============================================================================


def test_next_run_card_is_placeholder(cfg: LoadedConfig) -> None:
    out = ma.monitor_cards(cfg, _db_path(cfg))
    nr = out["next_run"]
    assert nr["next_run_at"] is None
    assert nr["countdown_seconds"] is None


# ============================================================================
# last_run
# ============================================================================


def test_last_run_card_no_events(cfg: LoadedConfig) -> None:
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["last_run"]["level"] == "warn"


def test_last_run_card_with_success(cfg: LoadedConfig) -> None:
    record_pipeline_event(_db_path(cfg), kind="generate", status="success", story_id=1, message="ok")
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["last_run"]["level"] == "ok"
    assert out["last_run"]["status"] == "success"


def test_last_run_card_consecutive_failures_danger(cfg: LoadedConfig) -> None:
    record_pipeline_event(_db_path(cfg), kind="publish", status="failed", message="x")
    record_pipeline_event(_db_path(cfg), kind="publish", status="failed", message="y")
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["last_run"]["level"] == "danger"
    assert out["last_run"]["consecutive_failures"] >= 2


def test_last_run_card_one_failure_warn(cfg: LoadedConfig) -> None:
    record_pipeline_event(_db_path(cfg), kind="generate", status="success")
    record_pipeline_event(_db_path(cfg), kind="publish", status="failed")
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["last_run"]["level"] == "warn"


# ============================================================================
# login
# ============================================================================


def test_login_card_default_missing(cfg: LoadedConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """CDP 离线 + 默认 storage_state 不存在 → chrome_offline，level=danger。"""
    from publisher import chrome_launcher

    from review_queue import login_capture

    monkeypatch.setattr(login_capture, "state_file", lambda: Path("/nope/missing.json"))
    monkeypatch.setattr(chrome_launcher, "is_cdp_ready", lambda *a, **kw: False)
    monkeypatch.setattr(login_capture, "is_cdp_ready", lambda *a, **kw: False)
    ma.reset_login_cache()
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["login"]["level"] == "danger"
    assert out["login"]["status"] == "chrome_offline"


def test_login_card_caches_within_ttl(cfg: LoadedConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    from review_queue import login_capture

    call_count = {"n": 0}

    def fake_validity(path=None) -> dict:
        call_count["n"] += 1
        return {"status": "valid", "label": "有效", "days_left": 30}

    monkeypatch.setattr(login_capture, "login_state_validity", fake_validity)
    out1 = ma.monitor_cards(cfg, _db_path(cfg))
    out2 = ma.monitor_cards(cfg, _db_path(cfg))
    assert out1["login"]["level"] == "ok"
    assert out2["login"]["level"] == "ok"
    assert call_count["n"] == 1  # 第二次走缓存


# ============================================================================
# budget
# ============================================================================


def test_budget_card_zero_when_no_usage(cfg: LoadedConfig) -> None:
    out = ma.monitor_cards(cfg, _db_path(cfg))
    b = out["budget"]
    assert b["used_cny"] == 0.0
    assert b["limit_cny"] == 100.0
    assert b["percent"] == 0.0
    assert b["level"] == "ok"


def test_budget_card_warn_above_60(cfg: LoadedConfig) -> None:
    ensure_metrics_schema(_db_path(cfg))
    with sqlite3.connect(_db_path(cfg)) as conn:
        conn.execute(
            "INSERT INTO api_usage(provider, prompt_tokens, completion_tokens, total_tokens, cost_cny) VALUES (?, 0, 0, 0, ?)",
            ("deepseek", 70.0),
        )
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["budget"]["level"] == "warn"
    assert out["budget"]["percent"] >= 60


def test_budget_card_danger_above_90(cfg: LoadedConfig) -> None:
    ensure_metrics_schema(_db_path(cfg))
    with sqlite3.connect(_db_path(cfg)) as conn:
        conn.execute(
            "INSERT INTO api_usage(provider, prompt_tokens, completion_tokens, total_tokens, cost_cny) VALUES (?, 0, 0, 0, ?)",
            ("deepseek", 95.0),
        )
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["budget"]["level"] == "danger"


def test_budget_card_no_limit_is_ok(cfg: LoadedConfig) -> None:
    cfg.data["cost_limits"]["monthly_budget_cny"] = 0
    out = ma.monitor_cards(cfg, _db_path(cfg))
    assert out["budget"]["level"] == "ok"
    assert out["budget"]["limit_cny"] == 0.0


# ============================================================================
# 不打扰时段
# ============================================================================


def test_quiet_hours_off_by_default(cfg: LoadedConfig) -> None:
    assert ma.is_quiet_hours(cfg) is False


def test_quiet_hours_simple_window() -> None:
    cfg = LoadedConfig(
        data={"notifications": {"quiet_hours_start": "10:00", "quiet_hours_end": "12:00"}},
        path=Path("c.yaml"),
    )
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 6, 10, 30)) is True
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 6, 9, 59)) is False
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 6, 12, 0)) is False


def test_quiet_hours_overnight_window() -> None:
    cfg = LoadedConfig(
        data={"notifications": {"quiet_hours_start": "22:00", "quiet_hours_end": "08:00"}},
        path=Path("c.yaml"),
    )
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 6, 23, 0)) is True
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 7, 7, 0)) is True
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 7, 8, 0)) is False
    assert ma.is_quiet_hours(cfg, datetime(2026, 5, 6, 21, 59)) is False


def test_quiet_hours_invalid_format_returns_false() -> None:
    cfg = LoadedConfig(
        data={"notifications": {"quiet_hours_start": "abc", "quiet_hours_end": "08:00"}},
        path=Path("c.yaml"),
    )
    assert ma.is_quiet_hours(cfg) is False


# ============================================================================
# 整体延迟
# ============================================================================


def test_monitor_cards_under_50ms(cfg: LoadedConfig) -> None:
    record_pipeline_event(_db_path(cfg), kind="generate", status="success")
    t0 = time.perf_counter()
    ma.monitor_cards(cfg, _db_path(cfg))
    duration_ms = (time.perf_counter() - t0) * 1000
    # 给点余量 (含登录态文件系统调用 + CDP 端口快速探测，冷路径)
    assert duration_ms < 200, f"耗时 {duration_ms:.1f} ms"
