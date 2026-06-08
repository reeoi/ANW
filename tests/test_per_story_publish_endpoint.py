"""TDD: per-story publish endpoint that drives ``run_publish_only`` (Q6=B).

The dashboard's inbox lets the user click "立即发布" on an approved story.
That button hits ``POST /api/stories/{story_id}/publish`` which delegates
to ``atomic_runner.run_publish_only`` instead of the legacy
``/api/publish`` (which had no concept of choosing a specific story).
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

from review_queue import atomic_runner
from review_queue.human_review import app

SAMPLE_CONFIG = """\
deepseek:
  api_key: ""
  mock: true

runtime:
  mode: "semi-auto"
  dry_run: true
  project_root: "."

publisher:
  fansq:
    enabled: true
    login_state_path: "data/browser/fansq_state.json"

logging:
  level: "INFO"
  file: "logs/anw.log"

database:
  sqlite_path: "data/anw.sqlite3"
  backup_dir: "data/backups"
"""


@pytest.fixture()
def env_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("ANW_CONFIG", str(cfg))
    monkeypatch.setenv("ANW_DOTENV", str(env))
    monkeypatch.setenv("ANW_SQLITE_PATH", str(tmp_path / "anw.sqlite3"))

    while atomic_runner.state.is_busy():
        atomic_runner.state.release()
    atomic_runner.state.clear_current()
    atomic_runner.state.reset_publish_fail_streak()

    return {"cfg": cfg, "env": env, "tmp": tmp_path}


def _request(method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
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


def test_publish_endpoint_404_for_unknown_story(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/stories/9999/publish", json_body={})
    assert r["status"] == 404


def test_publish_endpoint_rejects_pending_status(env_setup: dict[str, Path]) -> None:
    """Only approved stories can be published manually."""
    from config_loader import load_from_environment
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    config = load_from_environment()
    db = initialize_database(config)
    sid = insert_story(db, Story(title="未审核", status="pending"))

    r = _request("POST", f"/api/stories/{sid}/publish", json_body={})
    assert r["status"] == 400, r["body"]
    body = json.loads(r["body"])
    assert "approved" in body.get("detail", "") or "批准" in body.get("detail", "")


def test_publish_endpoint_calls_run_publish_only(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approved story → endpoint delegates to run_publish_only."""
    from config_loader import load_from_environment
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    config = load_from_environment()
    db = initialize_database(config)
    sid = insert_story(db, Story(title="可发布", status="approved"))

    captured: dict[str, Any] = {}

    def fake_run_publish_only(cfg, story_id, **kwargs):
        captured["story_id"] = story_id
        return atomic_runner.AtomicResult(
            story_id=story_id,
            status="published",
            phase="publish",
            message="ok",
            publish_status="published",
        )

    monkeypatch.setattr(atomic_runner, "run_publish_only", fake_run_publish_only)
    # Patch the imported reference in human_review too, in case it imports
    # the function name into local scope.
    import review_queue.human_review as hr

    if hasattr(hr, "run_publish_only"):
        monkeypatch.setattr(hr, "run_publish_only", fake_run_publish_only)

    r = _request("POST", f"/api/stories/{sid}/publish", json_body={})
    assert r["status"] == 200, r["body"]
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["status"] == "published"
    assert captured["story_id"] == sid


def test_publish_endpoint_409_when_busy(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from config_loader import load_from_environment
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    config = load_from_environment()
    db = initialize_database(config)
    sid = insert_story(db, Story(title="可发布", status="approved"))

    def fake_busy(cfg, story_id, **kwargs):
        return atomic_runner.AtomicResult(
            story_id=story_id,
            status="busy",
            phase="busy",
            message="另一个原子任务正在运行",
        )

    monkeypatch.setattr(atomic_runner, "run_publish_only", fake_busy)
    import review_queue.human_review as hr

    if hasattr(hr, "run_publish_only"):
        monkeypatch.setattr(hr, "run_publish_only", fake_busy)

    r = _request("POST", f"/api/stories/{sid}/publish", json_body={})
    assert r["status"] == 409


def test_publish_endpoint_returns_paused_status(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from config_loader import load_from_environment
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    config = load_from_environment()
    db = initialize_database(config)
    sid = insert_story(db, Story(title="可发布", status="approved"))

    def fake_paused(cfg, story_id, **kwargs):
        return atomic_runner.AtomicResult(
            story_id=story_id,
            status="paused",
            phase="publish",
            message="风控触发",
            publish_status="paused",
        )

    monkeypatch.setattr(atomic_runner, "run_publish_only", fake_paused)
    import review_queue.human_review as hr

    if hasattr(hr, "run_publish_only"):
        monkeypatch.setattr(hr, "run_publish_only", fake_paused)

    r = _request("POST", f"/api/stories/{sid}/publish", json_body={})
    assert r["status"] == 200, r["body"]
    body = json.loads(r["body"])
    assert body["status"] == "paused"
