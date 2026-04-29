"""Tests for Sprint 3 FastAPI human review workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]

from config_loader import LoadedConfig
from queue.db import get_story, initialize_database, insert_story
from queue.human_review import app
from queue.models import Story


def _prepare_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "human_review.sqlite3"
    config = LoadedConfig(data={"database": {"sqlite_path": str(db_path)}}, path=Path("config.yaml"))
    initialize_database(config)
    return db_path


def test_home_lists_pending_and_needs_human_stories(tmp_path: Path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    insert_story(
        db_path,
        Story(
            title="<待审标题>",
            content="这是一篇待审核小说内容。",
            status="pending",
            score=72,
            retry_count=1,
            review_notes="需要人工确认",
        ),
    )
    insert_story(db_path, Story(title="已批准标题", content="不应出现在待审列表", status="approved"))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))

    response = _request("GET", "/")

    assert response["status"] == 200
    assert "&lt;待审标题&gt;" in response["body"]
    assert "pending" in response["body"]
    assert "72" in response["body"]
    assert "已批准标题" not in response["body"]


def test_approve_endpoint_updates_sqlite_status(tmp_path: Path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    story_id = insert_story(db_path, Story(title="待批准", content="足够长的内容", status="pending"))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))

    response = _request("POST", f"/stories/{story_id}/approve")

    assert response["status"] == 303
    stored = get_story(db_path, story_id)
    assert stored is not None
    assert stored.status == "approved"
    assert "人工批准" in (stored.review_notes or "")


def test_edit_endpoint_validates_and_saves_content(tmp_path: Path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    story_id = insert_story(db_path, Story(title="旧标题", content="旧内容", status="needs_human"))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))

    bad_response = _request("POST", f"/stories/{story_id}/edit", {"title": " ", "content": "新内容"})
    assert bad_response["status"] == 400

    response = _request(
        "POST",
        f"/stories/{story_id}/edit",
        {"title": "新标题", "content": "新内容 <script>", "review_notes": "已手动润色"},
    )

    assert response["status"] == 303
    stored = get_story(db_path, story_id)
    assert stored is not None
    assert stored.title == "新标题"
    assert stored.content == "新内容 <script>"
    assert stored.status == "needs_human"
    assert stored.review_notes == "已手动润色"


def test_run_ai_review_batch_handles_empty_queue(tmp_path: Path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))

    response = _request("POST", "/ai-review/run")

    assert response["status"] == 200
    assert "没有可审核数据" in response["body"]


def _request(method: str, path: str, data: dict[str, str] | None = None) -> dict[str, object]:
    """Tiny ASGI test client to avoid version coupling in starlette/httpx TestClient."""
    import anyio

    async def run() -> dict[str, object]:
        body = ""
        headers = []
        if data is not None:
            from urllib.parse import urlencode

            body = urlencode(data)
            headers.append((b"content-type", b"application/x-www-form-urlencoded"))
        body_bytes = body.encode()
        messages = [{"type": "http.request", "body": body_bytes, "more_body": False}]
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
            "headers": headers + [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        response_body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return {"status": status, "body": response_body.decode("utf-8")}

    return anyio.run(run)
