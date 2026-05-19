"""测试 ``tray_app`` 的非 GUI 部分（端口 / 子进程 / 健康回调 / 通知路由）。

测试不真启 pystray / Chromium。
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tray_app
from tray_app import (
    HealthClient,
    NotificationStreamClient,
    TrayApp,
    is_port_in_use,
    launch_uvicorn,
    stop_proc,
)


# ============================================================================
# 端口检测
# ============================================================================


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_is_port_in_use_returns_false_for_free_port() -> None:
    assert is_port_in_use("127.0.0.1", _free_port()) is False


def test_is_port_in_use_returns_true_when_listening() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert is_port_in_use("127.0.0.1", port) is True


# ============================================================================
# launch_uvicorn 行为
# ============================================================================


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 12345
        self.terminated = False
        self.killed = False
        self._wait_count = 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self._wait_count += 1
        return 0

    def kill(self) -> None:
        self.killed = True


def test_launch_uvicorn_skips_when_port_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tray_app, "is_port_in_use", lambda host, port, timeout=0.5: True)
    monkeypatch.setattr(
        tray_app.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("Should not Popen when port busy"),
    )
    assert launch_uvicorn("127.0.0.1", 12345) is None


def test_resolve_console_python_replaces_pythonw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """pythonw.exe 解释器应被替换成同目录的 python.exe（如果存在）。"""
    fake_pythonw = tmp_path / "pythonw.exe"
    fake_pythonw.write_text("")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("")
    monkeypatch.setattr(tray_app.sys, "executable", str(fake_pythonw))
    assert tray_app._resolve_console_python() == str(fake_python)


def test_resolve_console_python_falls_back_when_no_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_pythonw = tmp_path / "pythonw.exe"
    fake_pythonw.write_text("")
    # 没有 python.exe → 仍返回原 sys.executable
    monkeypatch.setattr(tray_app.sys, "executable", str(fake_pythonw))
    assert tray_app._resolve_console_python() == str(fake_pythonw)


def test_resolve_console_python_keeps_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("")
    monkeypatch.setattr(tray_app.sys, "executable", str(fake_python))
    assert tray_app._resolve_console_python() == str(fake_python)


def test_launch_uvicorn_invokes_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tray_app, "is_port_in_use", lambda host, port, timeout=0.5: False)
    captured: dict[str, Any] = {}

    def fake_popen(args, cwd=None, creationflags=0, stdout=None, stderr=None, stdin=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return _FakeProc()

    monkeypatch.setattr(tray_app.subprocess, "Popen", fake_popen)
    proc = launch_uvicorn("127.0.0.1", 18000, project_root=Path("."))
    assert isinstance(proc, _FakeProc)
    assert "uvicorn" in captured["args"]
    assert "review_queue.human_review:app" in captured["args"]
    # stdout/stderr 必须重定向 (避免 pythonw 下 uvicorn 写日志崩)
    assert captured["stdout"] is not None
    assert captured["stderr"] is not None


# ============================================================================
# stop_proc
# ============================================================================


def test_stop_proc_none_is_noop() -> None:
    stop_proc(None)


def test_stop_proc_calls_terminate() -> None:
    proc = _FakeProc()
    stop_proc(proc, label="test")
    assert proc.terminated is True


def test_stop_proc_falls_back_to_kill_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc()
    import subprocess

    call_count = {"n": 0}

    def fake_wait(self, timeout: float | None = None) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise subprocess.TimeoutExpired(cmd=["x"], timeout=timeout or 0)
        return 0

    monkeypatch.setattr(_FakeProc, "wait", fake_wait, raising=False)
    stop_proc(proc, label="test")
    assert proc.killed is True


# ============================================================================
# HealthClient
# ============================================================================


def test_health_client_state_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict[str, Any]] = []
    client = HealthClient("http://127.0.0.1:18000", lambda s: received.append(s))

    class FakeResp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"status": "ok", "scheduler_running": True}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout=2.0: FakeResp())

    # 直接调一次 _loop 的内部 (不真起 thread)：手动触发一次 fetch
    client.on_state(httpx.get(client.base_url + "/api/health").json())
    assert received[-1] == {"status": "ok", "scheduler_running": True}


# ============================================================================
# TrayApp 状态回调 (不启 pystray)
# ============================================================================


@pytest.fixture()
def app_with_fake_icon(monkeypatch: pytest.MonkeyPatch) -> TrayApp:
    """构造 TrayApp 但用一个假的 icon 替代 pystray.Icon。"""
    app = TrayApp.__new__(TrayApp)
    app.host = "127.0.0.1"
    app.port = 18000
    app.base_url = "http://127.0.0.1:18000"
    app.uvicorn_proc = None
    app.health = None  # type: ignore[assignment]
    app.notif = None   # type: ignore[assignment]
    app._auto_running = False
    app._restart_lock = threading.Lock()

    class FakeIcon:
        def __init__(self) -> None:
            self.icon: Any = None
            self.title = ""
            self.notifies: list[tuple[str, str]] = []

        def notify(self, message: str, title: str) -> None:
            self.notifies.append((title, message))

        def stop(self) -> None: ...

    app.icon = FakeIcon()
    return app


def test_on_health_green_when_ok(app_with_fake_icon: TrayApp) -> None:
    app_with_fake_icon._on_health({"status": "ok"})
    assert "运行中" in app_with_fake_icon.icon.title


def test_on_health_yellow_when_degraded(app_with_fake_icon: TrayApp) -> None:
    app_with_fake_icon._on_health({"status": "degraded"})
    assert "警告" in app_with_fake_icon.icon.title


def test_on_health_red_when_unreachable(app_with_fake_icon: TrayApp) -> None:
    app_with_fake_icon._on_health({"status": "unreachable"})
    assert "连接断开" in app_with_fake_icon.icon.title


def test_on_notification_critical_triggers_notify(app_with_fake_icon: TrayApp) -> None:
    app_with_fake_icon._on_notification(
        {"severity": "critical", "title": "T", "message": "M"}
    )
    assert app_with_fake_icon.icon.notifies == [("T", "M")]


def test_on_notification_info_skipped(app_with_fake_icon: TrayApp) -> None:
    app_with_fake_icon._on_notification(
        {"severity": "info", "title": "T", "message": "M"}
    )
    assert app_with_fake_icon.icon.notifies == []


def test_on_notification_dismissed_skipped(app_with_fake_icon: TrayApp) -> None:
    app_with_fake_icon._on_notification(
        {"severity": "warning", "title": "T", "message": "M", "dismissed": True}
    )
    assert app_with_fake_icon.icon.notifies == []


def test_on_notification_restart_action_triggers_restart(
    app_with_fake_icon: TrayApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    triggered: list[bool] = []
    monkeypatch.setattr(
        app_with_fake_icon, "_restart_uvicorn", lambda: triggered.append(True)
    )
    app_with_fake_icon._on_notification(
        {
            "severity": "warning",
            "title": "T",
            "message": "M",
            "extras": {"action": "restart_uvicorn"},
        }
    )
    assert triggered == [True]


# ============================================================================
# 长连接客户端的 start / stop 行为 (不真发请求)
# ============================================================================


def test_health_client_start_stop_threadsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    states: list[dict] = []
    client = HealthClient("http://127.0.0.1:1", lambda s: states.append(s))

    # 用一个会立刻退出的 _loop 替代真实轮询
    def fake_loop(self) -> None:  # noqa: ANN001
        self._stop.set()

    monkeypatch.setattr(HealthClient, "_loop", fake_loop, raising=False)
    client.start()
    if client._thread:
        client._thread.join(timeout=1.0)
    assert client._stop.is_set()


def test_notification_stream_client_constructs() -> None:
    received: list[dict] = []
    client = NotificationStreamClient("http://x:1", lambda n: received.append(n))
    assert client.base_url == "http://x:1"
    client.stop()  # 没启动就 stop 也不应报错


# ============================================================================
# TrayApp._restart_uvicorn / _toggle_auto / _quit (mock pystray)
# ============================================================================


def test_restart_uvicorn_terminates_old_and_relaunches(
    app_with_fake_icon: TrayApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _FakeProc()
    new_proc = _FakeProc()
    app_with_fake_icon.uvicorn_proc = old
    monkeypatch.setattr(tray_app, "stop_proc", lambda p, label="x", timeout=5.0: setattr(p, "terminated", True))
    monkeypatch.setattr(tray_app, "launch_uvicorn", lambda host, port, project_root=None: new_proc)
    monkeypatch.setattr(tray_app.time, "sleep", lambda *_: None)
    app_with_fake_icon._restart_uvicorn()
    assert old.terminated is True
    assert app_with_fake_icon.uvicorn_proc is new_proc


def test_run_now_posts_to_api(app_with_fake_icon: TrayApp, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url: str, json=None, timeout=5.0):
        captured["url"] = url
        captured["json"] = json

        class R:
            status_code = 200

        return R()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    app_with_fake_icon._run_now()
    assert captured["url"].endswith("/api/console/run-now")


def test_quit_stops_subprocess_and_threads(
    app_with_fake_icon: TrayApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _FakeProc()
    app_with_fake_icon.uvicorn_proc = proc
    stops: list[str] = []

    class FakeChannel:
        def stop(self) -> None:
            stops.append("ch")

    app_with_fake_icon.health = FakeChannel()  # type: ignore[assignment]
    app_with_fake_icon.notif = FakeChannel()   # type: ignore[assignment]

    icon_stops: list[bool] = []

    class FakeIcon2:
        def stop(self) -> None:
            icon_stops.append(True)

    app_with_fake_icon.icon = FakeIcon2()
    monkeypatch.setattr(tray_app, "stop_proc", lambda p, label="x", timeout=5.0: setattr(p, "terminated", True))
    app_with_fake_icon._quit()
    assert proc.terminated is True
    assert stops == ["ch", "ch"]
    assert icon_stops == [True]


def test_open_folder_creates_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_open_folder` 在调用前必须 mkdir;实际的 startfile 用桩。"""
    app = TrayApp.__new__(TrayApp)
    app.icon = None  # type: ignore[assignment]
    monkeypatch.setattr(tray_app, "PROJECT_ROOT", tmp_path)
    if sys.platform == "win32":
        monkeypatch.setattr("os.startfile", lambda p: None, raising=False)
    else:
        monkeypatch.setattr(tray_app.subprocess, "Popen", lambda *a, **kw: None)
    app._open_folder("data")
    assert (tmp_path / "data").exists()
    app._open_folder("logs")
    assert (tmp_path / "logs").exists()
