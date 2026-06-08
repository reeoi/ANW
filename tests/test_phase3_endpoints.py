"""测试 c_pipeline schema 与 ``/api/monitor/cards`` 端点。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue.db import (
    initialize_database,
    insert_story,
    list_reviewable_stories,
)
from review_queue.human_review import app
from review_queue.models import Story

# ============================================================================
# c_pipeline schema 幂等
# ============================================================================


def test_initialize_creates_c_pipeline_schema(tmp_path: Path) -> None:
    """initialize_database 应创建 c_pipeline 三张表与索引。"""
    db = tmp_path / "fresh.sqlite3"
    config = LoadedConfig(data={"database": {"sqlite_path": str(db)}}, path=Path("c.yaml"))
    initialize_database(config)
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        story_cols = {row[1] for row in conn.execute("PRAGMA table_info(stories)")}
    assert {"stories", "daily_publish_plan", "pipeline_cost_log"}.issubset(tables)
    assert {"current_phase", "work_dir", "final_content_path", "ai_review_score"}.issubset(story_cols)


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    config = LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "x.sqlite3")}},
        path=Path("c.yaml"),
    )
    initialize_database(config)
    initialize_database(config)
    initialize_database(config)


def test_insert_story_with_c_pipeline_fields(tmp_path: Path) -> None:
    config = LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "f.sqlite3")}}, path=Path("c.yaml")
    )
    db = initialize_database(config)
    sid_a = insert_story(
        db,
        Story(title="A", work_dir="data/works/1", current_phase="phase_3_section_05", emotion="意难平"),
    )
    sid_b = insert_story(db, Story(title="B"))
    with sqlite3.connect(db) as conn:
        rows = {row[0]: row for row in conn.execute(
            "SELECT id, current_phase, work_dir, emotion FROM stories"
        )}
    assert rows[sid_a][1] == "phase_3_section_05"
    assert rows[sid_a][2] == "data/works/1"
    assert rows[sid_a][3] == "意难平"
    assert rows[sid_b][1] == "phase_0"


def test_list_reviewable_stories_returns_pending(tmp_path: Path) -> None:
    config = LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "g.sqlite3")}}, path=Path("c.yaml")
    )
    db = initialize_database(config)
    insert_story(db, Story(title="dry", status="pending", current_phase="phase_5_done"))
    insert_story(db, Story(title="real", status="approved", current_phase="phase_5_done"))
    titles = {s.title for s in list_reviewable_stories(db)}
    assert "dry" in titles
    assert "real" not in titles


# ============================================================================
# /api/monitor/cards endpoint
# ============================================================================


def _request(method: str, path: str) -> dict[str, Any]:
    import anyio

    async def run() -> dict[str, Any]:
        sent: list[dict[str, object]] = []
        messages = [{"type": "http.request", "body": b"", "more_body": False}]

        async def receive() -> dict[str, object]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(m: dict[str, object]) -> None:
            sent.append(m)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"t")],
            "client": ("t", 1),
            "server": ("t", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return {"status": status, "body": body.decode()}

    return anyio.run(run)


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"\"\n"
        "runtime:\n  mode: \"semi-auto\"\n  dry_run: true\n"
        "database:\n  sqlite_path: \"" + str(tmp_path / "anw.sqlite3").replace("\\", "/") + "\"\n"
        "cost_limits:\n  monthly_budget_cny: 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANW_CONFIG", str(cfg))
    monkeypatch.setenv("ANW_SQLITE_PATH", str(tmp_path / "anw.sqlite3"))
    return {"cfg": cfg, "db": tmp_path / "anw.sqlite3"}


def test_monitor_cards_endpoint_returns_4_cards(isolated_env: dict[str, Path]) -> None:
    r = _request("GET", "/api/monitor/cards")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    for key in ("next_run", "last_run", "login", "budget"):
        assert key in body
        assert "level" in body[key]


def test_monitor_cards_endpoint_when_scheduler_off(isolated_env: dict[str, Path]) -> None:
    """调度器已下线，next_run 永远是占位（无 next_run_at）。"""
    r = _request("GET", "/api/monitor/cards")
    body = json.loads(r["body"])
    assert body["next_run"]["next_run_at"] is None


def test_monitor_cards_endpoint_includes_generated_at(isolated_env: dict[str, Path]) -> None:
    r = _request("GET", "/api/monitor/cards")
    body = json.loads(r["body"])
    assert "generated_at" in body
