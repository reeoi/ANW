"""Phase G.2 — concurrency observability endpoint tests.

Covers ``GET /api/monitor/concurrency``: max_concurrent / in_use /
available fields reflect the K2 ``PipelineSemaphore.stats()`` snapshot
across acquire/release cycles.
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

from generator.c_pipeline import concurrency as concurrency_module
from review_queue.human_review import app


def _request(method: str, path: str) -> dict[str, Any]:
    """Tiny ASGI driver — no httpx/TestClient dependency required."""
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
            "headers": [
                (b"host", b"t"),
                (b"content-type", b"application/json"),
                (b"content-length", b"0"),
            ],
            "client": ("t", 1),
            "server": ("t", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body_bytes = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        return {"status": status, "body": body_bytes.decode()}

    return anyio.run(run)


@pytest.fixture(autouse=True)
def _reset_global_semaphore() -> None:
    """Each test starts with a fresh process-global semaphore."""
    concurrency_module.reset_global_semaphore()
    yield
    concurrency_module.reset_global_semaphore()


@pytest.fixture()
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Minimal ANW_CONFIG so /api/monitor/concurrency can build a config."""
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "anw.sqlite3"
    cfg_path.write_text(
        f"""
deepseek:
  api_key: ""
runtime:
  mode: "semi-auto"
  dry_run: true
publisher:
  default_platform: "fansq"
  daily_count_min: 0
  daily_count_max: 5
  operating_hours: ["09:00", "22:00"]
  slot_min_gap_minutes: 30
scheduler:
  enabled: false
  timezone: "Asia/Shanghai"
database:
  sqlite_path: "{str(db_path).replace(chr(92), '/')}"
logging:
  file: "{str(tmp_path / 'anw.log').replace(chr(92), '/')}"
c_pipeline:
  max_concurrent_pipelines: 2
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANW_CONFIG", str(cfg_path))
    monkeypatch.setenv("ANW_SQLITE_PATH", str(db_path))
    return {"cfg": cfg_path, "db": db_path}


def test_concurrency_endpoint_idle_state(env: dict[str, Path]) -> None:
    r = _request("GET", "/api/monitor/concurrency")
    assert r["status"] == 200, r["body"]
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["max_concurrent"] == 2
    assert body["in_use"] == 0
    assert body["available"] == 2


def test_concurrency_endpoint_reflects_acquired_slot(env: dict[str, Path]) -> None:
    # Force-build the global semaphore via the endpoint, then acquire a slot
    # and observe in_use=1.
    _request("GET", "/api/monitor/concurrency")  # priming call
    semaphore = concurrency_module.get_global_semaphore()
    with semaphore.acquire_slot():
        r = _request("GET", "/api/monitor/concurrency")
        body = json.loads(r["body"])
        assert body["in_use"] == 1
        assert body["available"] == 1
        assert body["max_concurrent"] == 2

    # After release, slot returns to idle.
    r2 = _request("GET", "/api/monitor/concurrency")
    body2 = json.loads(r2["body"])
    assert body2["in_use"] == 0
    assert body2["available"] == 2


def test_concurrency_endpoint_handles_full_capacity(env: dict[str, Path]) -> None:
    _request("GET", "/api/monitor/concurrency")  # priming
    semaphore = concurrency_module.get_global_semaphore()
    with semaphore.acquire_slot(), semaphore.acquire_slot():
        r = _request("GET", "/api/monitor/concurrency")
        body = json.loads(r["body"])
        assert body["in_use"] == 2
        assert body["available"] == 0
