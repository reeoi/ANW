"""HTTP API tests for review_queue/scan_plan_api.py.

Covers the four endpoints surfaced for the Web UI:
- GET  /api/scan/status
- POST /api/scan/run     (with dry_run=true so no DeepSeek call)
- GET  /api/plan/today
- POST /api/plan/run
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_queue.human_review import app


def _config_yaml(tmp_path: Path) -> Path:
    """Write a minimal config.yaml that points to tmp_path-based files.

    ``runtime.project_root`` is set to tmp_path so the scan module writes
    its theme_pool under tmp_path/data/. We pre-copy the real seeds file
    so the dry-run client can build a synthetic pool.
    """
    db_path = tmp_path / "anw.sqlite3"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Copy real seeds.yaml so dry-run scan can synthesize a pool.
    shutil.copy(ROOT / "data" / "scan_seeds.yaml", data_dir / "scan_seeds.yaml")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n"
        "  api_key: \"\"\n"
        "  mock: true\n"
        "runtime:\n"
        "  mode: \"semi-auto\"\n"
        "  dry_run: true\n"
        "  project_root: \"" + str(tmp_path).replace("\\", "/") + "\"\n"
        "scheduler:\n"
        "  enabled: false\n"
        "scan:\n"
        "  pool_size: 100\n"
        "  on_failure: \"fallback_or_block\"\n"
        "  seed_file: \"data/scan_seeds.yaml\"\n"
        "publisher:\n"
        "  daily_count_min: 0\n"
        "  daily_count_max: 3\n"
        "  operating_hours: [\"09:00\", \"22:00\"]\n"
        "  slot_min_gap_minutes: 30\n"
        "database:\n"
        "  sqlite_path: \"" + str(db_path).replace("\\", "/") + "\"\n",
        encoding="utf-8",
    )
    return cfg


@pytest.fixture()
def env_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = _config_yaml(tmp_path)
    monkeypatch.setenv("ANW_CONFIG", str(cfg))
    monkeypatch.setenv("ANW_DOTENV", str(tmp_path / ".env"))
    return {
        "cfg": cfg,
        "tmp": tmp_path,
        "pool_path": tmp_path / "data" / "theme_pool.json",
        "db_path": tmp_path / "anw.sqlite3",
    }


def _request(
    method: str, path: str, json_body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """ASGI request helper (same shape as test_control_api)."""
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
        body = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        return {"status": status, "body": body.decode("utf-8")}

    return anyio.run(run)


# ============================================================ scan/status


def test_scan_status_when_pool_missing(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/scan/status")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["exists"] is False
    assert body["item_count"] == 0
    assert body["weekly_topics"] == []


def test_scan_status_reads_existing_pool(env_setup: dict[str, Path]) -> None:
    pool = env_setup["pool_path"]
    pool.write_text(
        json.dumps({
            "iso_week": "2026-W19",
            "generated_at": "2026-05-08T03:00:00Z",
            "weekly_topics": ["学区房", "婆媳", "出轨"],
            "used_fallback": False,
            "items": [{"id": f"tp_{i}"} for i in range(100)],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    r = _request("GET", "/api/scan/status")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["exists"] is True
    assert body["item_count"] == 100
    assert body["iso_week"] == "2026-W19"
    assert body["weekly_topics"] == ["学区房", "婆媳", "出轨"]
    assert body["used_fallback"] is False


def test_scan_status_handles_corrupted_pool_file(
    env_setup: dict[str, Path],
) -> None:
    env_setup["pool_path"].write_text("{ not valid json", encoding="utf-8")
    r = _request("GET", "/api/scan/status")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["exists"] is True
    assert body["item_count"] == 0
    assert "error" in body


# ============================================================ scan/run


def test_scan_run_dry_run_creates_pool(env_setup: dict[str, Path]) -> None:
    pool = env_setup["pool_path"]
    assert not pool.exists()

    r = _request("POST", "/api/scan/run", {"dry_run": True, "force": True})
    assert r["status"] == 200, r["body"]
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["item_count"] == 100
    assert pool.exists()
    # Subsequent status reflects new pool
    r2 = _request("GET", "/api/scan/status")
    body2 = json.loads(r2["body"])
    assert body2["exists"] is True
    assert body2["item_count"] == 100


def test_scan_run_accepts_empty_body(env_setup: dict[str, Path]) -> None:
    """Empty body falls back to defaults (force=False, dry_run=False).

    Without DeepSeek key, the live path uses DeepSeekClient mock — which
    returns a non-JSON placeholder so run_weekly_scan falls back to the
    previous pool. With no previous pool we expect a 409 BLOCKED.
    """
    r = _request("POST", "/api/scan/run", None)
    assert r["status"] in (200, 409)


# ============================================================ router mounted


def test_scan_plan_router_is_mounted_on_app() -> None:
    """Sanity: the routes exist on the FastAPI app's router table."""
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/scan/status" in paths
    assert "/api/scan/run" in paths
    # /api/plan/today and /api/plan/run were removed alongside the scheduler.
    assert "/api/plan/today" not in paths
    assert "/api/plan/run" not in paths
