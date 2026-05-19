"""测试 ``review_queue.control_api`` (Phase 2)。

autostart / notifications 的 API 端点都用桩做隔离。
（调度器 /api/control/auto 已下线，相关测试已移除。）
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
from review_queue import control_api
from review_queue.notification_bus import bus


@pytest.fixture()
def env_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "deepseek:\n  api_key: \"\"\nruntime:\n  mode: \"semi-auto\"\n  dry_run: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(cfg))
    monkeypatch.setenv("ANP_DOTENV", str(tmp_path / ".env"))
    bus.clear()
    return {"cfg": cfg}


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


# ============================================================================
# /api/control/restart
# ============================================================================


def test_post_restart_publishes_notification(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/control/restart")
    assert r["status"] == 200
    items = bus.list_recent()
    assert any("重启" in n.title for n in items)
    assert any(n.extras.get("action") == "restart_uvicorn" for n in items)


# ============================================================================
# /api/autostart
# ============================================================================


def test_get_autostart_status(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/autostart")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert "enabled" in body and "shortcut_path" in body


def test_post_autostart_disable_when_not_present(env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable 一个本来就没有的快捷方式应该返回 removed=False。"""
    import auto_start

    monkeypatch.setattr(auto_start, "disable", lambda startup_dir=None: False)
    r = _request("POST", "/api/autostart", json_body={"enabled": False})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["enabled"] is False
    assert body["removed"] is False


def test_post_autostart_enable_on_non_windows_returns_400(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control_api, "autostart_is_windows", lambda: False)
    r = _request("POST", "/api/autostart", json_body={"enabled": True})
    assert r["status"] == 400


def test_post_autostart_enable_calls_autostart_enable(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(control_api, "autostart_is_windows", lambda: True)
    captured: dict[str, Any] = {}

    def fake_enable(**kwargs: Any) -> Path:
        captured.update(kwargs)
        return tmp_path / "ANP_AutoStart.bat"

    monkeypatch.setattr(control_api, "autostart_enable", fake_enable)
    r = _request("POST", "/api/autostart", json_body={"enabled": True})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["enabled"] is True
    assert body["shortcut_path"].endswith("ANP_AutoStart.bat")
    assert "project_root" in captured


# ============================================================================
# /api/notifications
# ============================================================================


def test_list_notifications_empty(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/notifications")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["items"] == []


def test_list_notifications_with_some(env_setup: dict[str, Path]) -> None:
    bus.publish("warning", "X", "Y")
    bus.publish("info", "A", "B")
    r = _request("GET", "/api/notifications")
    body = json.loads(r["body"])
    assert len(body["items"]) == 2
    assert body["items"][0]["title"] in {"X", "A"}


def test_dismiss_specific_notification(env_setup: dict[str, Path]) -> None:
    n = bus.publish("info", "T", "M")
    r = _request("POST", f"/api/notifications/dismiss/{n.id}")
    assert r["status"] == 200
    r = _request("POST", f"/api/notifications/dismiss/{n.id}")
    # 第二次 dismiss 同一个还会返回 200 (id 仍存在,只是已 dismissed)
    # 注：当前实现 dismiss 已 dismissed 时返回 True,所以 200
    assert r["status"] == 200


def test_dismiss_unknown_returns_404(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/notifications/dismiss/non-existent")
    assert r["status"] == 404


def test_dismiss_all(env_setup: dict[str, Path]) -> None:
    bus.publish("info", "a", "b")
    bus.publish("warning", "c", "d")
    r = _request("POST", "/api/notifications/dismiss-all")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["dismissed"] >= 2


# ============================================================================
# /api/health
# ============================================================================


def test_health_basic_fields(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/health")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert "status" in body
    assert "scheduler_running" not in body  # 已下线
