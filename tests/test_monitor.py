"""Tests for the /api/monitor endpoint and metrics tracking."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue.db import initialize_database, insert_story
from review_queue.human_review import app
from review_queue.metrics import (
    ensure_metrics_schema,
    estimate_cost_cny,
    query_overview,
    record_api_usage,
    record_pipeline_event,
)
from review_queue.models import Story


def _prepare_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "monitor.sqlite3"
    config = LoadedConfig(data={"database": {"sqlite_path": str(db_path)}}, path=Path("config.yaml"))
    initialize_database(config)
    return db_path


def test_metrics_helpers_round_trip(tmp_path: Path) -> None:
    db_path = _prepare_db(tmp_path)
    ensure_metrics_schema(db_path)
    record_api_usage(
        db_path,
        provider="deepseek",
        model="deepseek-chat",
        purpose="generate",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_cny=estimate_cost_cny(1000, 500),
    )
    record_pipeline_event(db_path, kind="generate", status="success", story_id=1, message="hello")

    overview = query_overview(db_path)
    assert overview["usage"]["d1"]["calls"] == 1
    assert overview["usage"]["d1"]["total_tokens"] == 1500
    assert overview["usage"]["d1"]["cost_cny"] > 0
    assert overview["events"]["d1"]["generate"]["success"] == 1
    assert any(event["kind"] == "generate" for event in overview["recent_events"])


def test_monitor_endpoint_returns_aggregates(tmp_path: Path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    insert_story(db_path, Story(title="样例", content="内容", status="pending"))
    record_api_usage(
        db_path,
        provider="deepseek",
        model="deepseek-chat",
        purpose="generate",
        prompt_tokens=200,
        completion_tokens=100,
        cost_cny=0.001,
    )
    record_pipeline_event(db_path, kind="generate", status="success", story_id=1)
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))

    response = _request("GET", "/api/monitor")

    assert response["status"] == 200
    body = json.loads(response["body"])
    assert body["ok"] is True
    assert "usage" in body and "events" in body
    assert body["limits"]["monthly_budget_cny"] >= 0
    assert body["health"]["db_path"]
    assert body["schedule"]["timezone"]


def test_health_endpoint(tmp_path: Path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    response = _request("GET", "/api/health")
    assert response["status"] == 200
    body = json.loads(response["body"])
    assert body["ok"] is True
    assert body["database"].endswith("monitor.sqlite3")


def test_favicon_returns_204() -> None:
    response = _request("GET", "/favicon.ico")
    assert response["status"] == 204


def _request(method: str, path: str) -> dict[str, object]:
    """Tiny ASGI test client to avoid starlette/httpx version coupling."""
    import anyio

    async def run() -> dict[str, object]:
        messages = [{"type": "http.request", "body": b"", "more_body": False}]
        sent: list[dict[str, object]] = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
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
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return {"status": status, "body": body.decode("utf-8")}

    return anyio.run(run)
