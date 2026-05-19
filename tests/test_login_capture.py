"""测试 ``review_queue.login_capture`` 的 IPC + 登录态有效期判定。

测试不真实启动 Chromium：用 ``monkeypatch`` 把 ``subprocess.Popen`` 替换为
桩，并直接构造 Playwright ``storage_state.json`` 来检查有效期。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from review_queue import login_capture


class _DummyProc:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid


@pytest.fixture(autouse=True)
def _isolate_browser_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把所有路径常量重定向到 tmp_path，避免污染真实 data/browser/。"""
    browser_dir = tmp_path / "data" / "browser"
    browser_dir.mkdir(parents=True)
    monkeypatch.setattr(login_capture, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(login_capture, "BROWSER_DIR", browser_dir)
    monkeypatch.setattr(login_capture, "SESSION_FILE", browser_dir / ".login_session.json")
    monkeypatch.setattr(login_capture, "TRIGGER_FINISH_FILE", browser_dir / ".login_trigger_finish")
    monkeypatch.setattr(login_capture, "WORKER_DONE_FILE", browser_dir / ".login_worker_done.json")
    monkeypatch.delenv("FANSQ_LOGIN_STATE_PATH", raising=False)
    return browser_dir


def test_state_file_default(tmp_path: Path) -> None:
    p = login_capture.state_file()
    assert p.name == "fansq_state.json"
    assert "browser" in str(p)


def test_state_file_respects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "alt" / "state.json"
    monkeypatch.setenv("FANSQ_LOGIN_STATE_PATH", str(target))
    assert login_capture.state_file() == target


def test_login_state_validity_missing(tmp_path: Path) -> None:
    info = login_capture.login_state_validity(tmp_path / "nope.json")
    assert info["status"] == "missing"
    assert info["days_left"] is None


def test_login_state_validity_empty_cookies(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    info = login_capture.login_state_validity(p)
    assert info["status"] == "empty"


def test_login_state_validity_session_only(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"cookies": [{"name": "x", "expires": -1}]}), encoding="utf-8")
    info = login_capture.login_state_validity(p)
    assert info["status"] == "session_only"


def test_login_state_validity_expired(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(
        json.dumps({"cookies": [{"name": "x", "expires": time.time() - 3600}]}),
        encoding="utf-8",
    )
    info = login_capture.login_state_validity(p)
    assert info["status"] == "expired"
    assert info["days_left"] == 0


def test_login_state_validity_expiring(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(
        json.dumps({"cookies": [{"name": "x", "expires": time.time() + 3 * 86400}]}),
        encoding="utf-8",
    )
    info = login_capture.login_state_validity(p)
    assert info["status"] == "expiring"
    assert 2 <= info["days_left"] <= 3


def test_login_state_validity_valid(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(
        json.dumps({"cookies": [{"name": "x", "expires": time.time() + 30 * 86400}]}),
        encoding="utf-8",
    )
    info = login_capture.login_state_validity(p)
    assert info["status"] == "valid"
    assert 28 <= info["days_left"] <= 30


def test_login_state_validity_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text("not json at all", encoding="utf-8")
    info = login_capture.login_state_validity(p)
    assert info["status"] == "invalid"


def test_get_session_status_none() -> None:
    assert login_capture.get_session_status() == {"status": "none"}


def test_start_login_session_writes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(cmd, cwd=None, creationflags=0):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return _DummyProc(pid=4242)

    monkeypatch.setattr(login_capture.subprocess, "Popen", fake_popen)
    session = login_capture.start_login_session()
    assert session["status"] == "pending"
    assert session["pid"] == 4242
    assert "fanqienovel" in session["login_url"]
    assert captured["cmd"][-1] == "--worker"


def test_start_login_session_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_capture.subprocess, "Popen", lambda *a, **kw: _DummyProc(pid=1))
    first = login_capture.start_login_session()
    second = login_capture.start_login_session()
    assert first["task_id"] == second["task_id"]


def test_start_login_session_expired_session_starts_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 写一份过期的 session 文件
    expired = {
        "task_id": "old",
        "status": "pending",
        "started_at": "2020-01-01T00:00:00+00:00",
        "started_at_ts": time.time() - login_capture.TIMEOUT_SECONDS - 60,
        "login_url": login_capture.LOGIN_URL,
    }
    login_capture.SESSION_FILE.write_text(json.dumps(expired), encoding="utf-8")
    monkeypatch.setattr(login_capture.subprocess, "Popen", lambda *a, **kw: _DummyProc(pid=99))
    new_session = login_capture.start_login_session()
    assert new_session["task_id"] != "old"
    assert new_session["status"] == "pending"


def test_finish_login_session_no_active_returns_failure() -> None:
    out = login_capture.finish_login_session(timeout_seconds=0)
    assert out["ok"] is False


def test_finish_login_session_picks_up_done_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 测试 storage_state 路径，需要隔离 CDP 探测（本机可能有 Chrome 在 9222）
    monkeypatch.setattr(login_capture, "is_cdp_ready", lambda *a, **kw: False)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"cookies": [{"name": "x", "expires": time.time() + 86400 * 30}]}),
        encoding="utf-8",
    )
    # 模拟 worker 已完成
    login_capture.SESSION_FILE.write_text(
        json.dumps({"task_id": "t1", "status": "pending", "started_at_ts": time.time()}),
        encoding="utf-8",
    )
    login_capture.WORKER_DONE_FILE.write_text(
        json.dumps({"status": "finished", "saved_at": "now", "path": str(state_path)}),
        encoding="utf-8",
    )
    # 让 login_state_validity 找到这个文件
    import os as _os

    _os.environ["FANSQ_LOGIN_STATE_PATH"] = str(state_path)
    try:
        result = login_capture.finish_login_session(timeout_seconds=2)
    finally:
        _os.environ.pop("FANSQ_LOGIN_STATE_PATH", None)
    assert result["ok"] is True
    assert result["status"] == "valid"


def test_cancel_login_session_clears_files() -> None:
    login_capture.SESSION_FILE.write_text("{}", encoding="utf-8")
    login_capture.TRIGGER_FINISH_FILE.write_text("", encoding="utf-8")
    login_capture.WORKER_DONE_FILE.write_text("{}", encoding="utf-8")
    out = login_capture.cancel_login_session()
    assert out["ok"] is True
    assert not login_capture.SESSION_FILE.exists()
    assert not login_capture.TRIGGER_FINISH_FILE.exists()
    assert not login_capture.WORKER_DONE_FILE.exists()


def test_finish_login_waits_then_picks_up_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟 worker 在 ``finish`` 调用后第一次 sleep 之后写入 done 文件。"""
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"cookies": [{"name": "x", "expires": time.time() + 86400 * 30}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FANSQ_LOGIN_STATE_PATH", str(state_path))

    login_capture.SESSION_FILE.write_text(
        json.dumps({"task_id": "t1", "status": "pending", "started_at_ts": time.time()}),
        encoding="utf-8",
    )

    real_sleep = login_capture.time.sleep
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        # 模拟在第一次 sleep 时 worker 把 done 文件写出来
        if not login_capture.WORKER_DONE_FILE.exists():
            login_capture.WORKER_DONE_FILE.write_text(
                json.dumps({"status": "finished", "saved_at": "now"}),
                encoding="utf-8",
            )

    monkeypatch.setattr(login_capture.time, "sleep", fake_sleep)
    try:
        result = login_capture.finish_login_session(timeout_seconds=2)
    finally:
        monkeypatch.setattr(login_capture.time, "sleep", real_sleep)

    assert result["ok"] is True
    assert sleep_calls, "expected at least one sleep iteration"
    assert not login_capture.SESSION_FILE.exists()


def test_finish_login_propagates_failure_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_capture.SESSION_FILE.write_text(
        json.dumps({"task_id": "t1", "status": "pending", "started_at_ts": time.time()}),
        encoding="utf-8",
    )

    def fake_sleep(seconds: float) -> None:
        login_capture.WORKER_DONE_FILE.write_text(
            json.dumps({"status": "failed", "message": "playwright crashed"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(login_capture.time, "sleep", fake_sleep)
    out = login_capture.finish_login_session(timeout_seconds=2)
    assert out["ok"] is False
    assert "playwright crashed" in out["message"]


def test_finish_login_timeout_when_worker_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    login_capture.SESSION_FILE.write_text(
        json.dumps({"task_id": "t1", "status": "pending", "started_at_ts": time.time()}),
        encoding="utf-8",
    )
    # 让 _now_ts 加速冲过 deadline
    times = iter([1000.0, 1000.0, 1000.0, 9999.0])

    def fake_now() -> float:
        try:
            return next(times)
        except StopIteration:
            return 9999.0

    monkeypatch.setattr(login_capture, "_now_ts", fake_now)
    out = login_capture.finish_login_session(timeout_seconds=1)
    assert out["ok"] is False
    assert "超时" in out["message"]


def test_finish_login_session_already_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(login_capture, "is_cdp_ready", lambda *a, **kw: False)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"cookies": [{"name": "x", "expires": time.time() + 86400 * 14}]}),
        encoding="utf-8",
    )
    import os as _os

    _os.environ["FANSQ_LOGIN_STATE_PATH"] = str(state_path)
    login_capture.WORKER_DONE_FILE.write_text(
        json.dumps({"status": "finished"}), encoding="utf-8"
    )
    try:
        out = login_capture.finish_login_session()
    finally:
        _os.environ.pop("FANSQ_LOGIN_STATE_PATH", None)
    assert out["ok"] is True
    assert out["status"] == "valid"


def test_get_session_status_with_corrupt_session_file() -> None:
    login_capture.SESSION_FILE.write_text("not json", encoding="utf-8")
    info = login_capture.get_session_status()
    # 损坏文件 → 返回空 dict 加默认 status
    assert info.get("status") == "pending"
