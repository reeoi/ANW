"""Tests for ``/api/console/*`` endpoints (UI rebuild §三).

Mirrors the minimal-ASGI client style of ``test_settings_api.py``.
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

from review_queue.human_review import app
from review_queue import atomic_runner
from review_queue.atomic_runner import AtomicResult


SAMPLE_CONFIG = """\
deepseek:
  api_key: ""
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  mock: true

runtime:
  mode: "semi-auto"
  dry_run: true
  project_root: "."

audit:
  approval_threshold: 90

publisher:
  default_platform: "fansq"
  daily_count_min: 0
  daily_count_max: 5
  operating_hours: ["09:00", "22:00"]
  slot_min_gap_minutes: 30
  fansq:
    enabled: true
    login_state_path: "data/browser/fansq_state.json"

scheduler:
  enabled: false
  timezone: "Asia/Shanghai"

logging:
  level: "INFO"
  file: "logs/anp.log"

database:
  sqlite_path: "data/anp.sqlite3"
  backup_dir: "data/backups"

cost_limits:
  monthly_budget_cny: 100
"""


@pytest.fixture()
def env_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("ANP_CONFIG", str(cfg))
    monkeypatch.setenv("ANP_DOTENV", str(env))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(tmp_path / "anp.sqlite3"))

    # Reset atomic runner global state between tests.
    atomic_runner.state.clear_current()
    atomic_runner.state.reset_publish_fail_streak()
    while atomic_runner.state.is_busy():
        atomic_runner.state.release()

    return {"cfg": cfg, "env": env, "tmp": tmp_path}


def _request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import anyio

    async def run() -> dict[str, Any]:
        body_bytes = b""
        headers: list[tuple[bytes, bytes]] = []
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            headers.append((b"content-type", b"application/json"))
        messages = [{"type": "http.request", "body": body_bytes, "more_body": False}]
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers + [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return {"status": status, "body": body.decode("utf-8")}

    return anyio.run(run)


# ============================================================================
# /api/console/status
# ============================================================================


def test_console_status_idle(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/console/status")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["current_task"] is None
    assert body["busy"] is False
    assert "scheduler_running" not in body  # 已下线
    assert "today" not in body  # daily plan 已下线
    assert "login_state" in body
    assert body["theme_pool_count"] >= 0
    assert body["publish_fail_streak"] == 0


def test_console_status_reflects_current_task(env_setup: dict[str, Path]) -> None:
    atomic_runner.state.try_acquire()
    try:
        atomic_runner.state.set_current(99, "generate#1")
        r = _request("GET", "/api/console/status")
        assert r["status"] == 200
        body = json.loads(r["body"])
        assert body["busy"] is True
        cur = body["current_task"]
        assert cur is not None
        assert cur["story_id"] == 99
        assert cur["phase"] == "generate#1"
    finally:
        atomic_runner.state.clear_current()
        atomic_runner.state.release()


# ============================================================================
# /api/console/run-now
# ============================================================================


def test_run_now_kicks_async_returns_story_id(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_kick(config: Any) -> int:
        captured["called"] = True
        return 7

    import review_queue.console_api as ca

    monkeypatch.setattr(ca, "kick_off_async", fake_kick)
    r = _request("POST", "/api/console/run-now", json_body={})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["story_id"] == 7
    assert captured.get("called") is True


def test_run_now_returns_409_when_busy(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import review_queue.console_api as ca

    def boom(config: Any) -> int:
        raise RuntimeError("另一个原子任务正在运行")

    monkeypatch.setattr(ca, "kick_off_async", boom)
    r = _request("POST", "/api/console/run-now", json_body={})
    assert r["status"] == 409


# ============================================================================
# /api/console/cancel
# ============================================================================


def test_cancel_no_active_task_returns_404(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/console/cancel", json_body={})
    assert r["status"] == 404


def test_cancel_explicit_story_id_sets_flag(env_setup: dict[str, Path]) -> None:
    from config_loader import load_from_environment
    from review_queue.db import (
        initialize_database,
        insert_story,
        is_cancel_requested,
    )
    from review_queue.models import Story

    config = load_from_environment()
    db = initialize_database(config)
    sid = insert_story(
        db,
        Story(title="T", status="pending", current_phase="phase_2_running"),
    )
    r = _request("POST", "/api/console/cancel", json_body={"story_id": sid})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert is_cancel_requested(db, sid) is True


def test_cancel_published_story_rejected(env_setup: dict[str, Path]) -> None:
    from config_loader import load_from_environment
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    config = load_from_environment()
    db = initialize_database(config)
    sid = insert_story(
        db,
        Story(title="P", status="published", current_phase="phase_6_done"),
    )
    r = _request("POST", "/api/console/cancel", json_body={"story_id": sid})
    assert r["status"] == 400
