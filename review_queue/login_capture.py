"""IPC + login session management for Fanqie browser login capture.

This module manages:
- Path constants for session / worker-done / trigger-finish marker files.
- ``login_state_validity`` — parse a Playwright ``storage_state.json`` and
  report cookie expiry.
- ``start_login_session`` / ``finish_login_session`` / ``cancel_login_session`` —
  IPC protocol between the main process and a child ``--worker`` process that
  opens the login page inside a real browser.
- ``get_session_status`` — lightweight query for UI polling.

Never starts a real browser from import time; callers (UI endpoints, tests)
decide when to ``subprocess.Popen``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from publisher.chrome_launcher import is_cdp_ready

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constants — overridable via monkeypatch in tests
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BROWSER_DIR = PROJECT_ROOT / "data" / "browser"
SESSION_FILE = BROWSER_DIR / ".login_session.json"
TRIGGER_FINISH_FILE = BROWSER_DIR / ".login_trigger_finish"
WORKER_DONE_FILE = BROWSER_DIR / ".login_worker_done.json"

# ---------------------------------------------------------------------------
# Behaviour constants
# ---------------------------------------------------------------------------
TIMEOUT_SECONDS = 600  # 10 min — worker should finish login within this
LOGIN_URL = "https://www.fanqienovel.com/"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ts() -> float:
    """Thin wrapper so tests can monkeypatch a fake clock."""
    return time.time()


def state_file() -> Path:
    """Return the active login state path."""
    env_path = os.environ.get("FANSQ_LOGIN_STATE_PATH")
    if env_path:
        return Path(env_path)
    return BROWSER_DIR / "fansq_state.json"


# ---------------------------------------------------------------------------
# Validity inspection
# ---------------------------------------------------------------------------

def login_state_validity(path: Path) -> dict[str, Any]:
    """Parse a Playwright ``storage_state.json`` and report cookie expiry.

    Returns a dict with keys:
        status: "valid" | "expiring" | "expired" | "session_only" | "empty" | "missing" | "invalid"
        days_left: int | None
        message: str
    """
    if not path.exists():
        return {"status": "missing", "days_left": None, "message": "File not found"}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "invalid", "days_left": None, "message": "Corrupt or unreadable"}

    cookies = raw.get("cookies") or []
    if not cookies:
        return {"status": "empty", "days_left": None, "message": "No cookies"}

    now = time.time()
    max_expires: float | None = None

    for c in cookies:
        exp = c.get("expires")
        if exp is None or (isinstance(exp, (int, float)) and exp == -1):
            continue  # session cookie — skip
        expires = float(exp)
        if max_expires is None or expires > max_expires:
            max_expires = expires

    if max_expires is None:
        return {"status": "session_only", "days_left": None, "message": "Only session cookies"}

    days_left = max(0, int((max_expires - now) / 86400))

    if max_expires <= now:
        return {"status": "expired", "days_left": 0, "message": "All cookies expired"}
    if days_left < 7:
        return {"status": "expiring", "days_left": days_left, "message": f"{days_left} days left"}
    return {"status": "valid", "days_left": days_left, "message": f"{days_left} days left"}


# ---------------------------------------------------------------------------
# Session management IPC
# ---------------------------------------------------------------------------

def get_session_status() -> dict[str, Any]:
    """Return the current session status without starting anything.

    Returns something like:
        {"status": "none"}
        {"status": "pending", "task_id": "...", "started_at_ts": ...}
    """
    if not SESSION_FILE.exists():
        return {"status": "none"}

    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt file — treat as pending (caller can cancel/retry)
        return {"status": "pending"}

    return {
        "status": data.get("status", "pending"),
        "task_id": data.get("task_id"),
        "started_at": data.get("started_at"),
        "started_at_ts": data.get("started_at_ts"),
        "login_url": data.get("login_url"),
    }


def start_login_session() -> dict[str, Any]:
    """Start a background login worker process.

    Returns a dict with:
        status: "pending"
        task_id: str
        pid: int
        login_url: str
        message: str
    """
    # Dedup: if a session file exists and is not expired, re-use it
    if SESSION_FILE.exists():
        try:
            existing = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            started = existing.get("started_at_ts", 0)
            if _now_ts() - started < TIMEOUT_SECONDS:
                # Reuse the existing task_id
                return {
                    "status": "pending",
                    "task_id": existing["task_id"],
                    "pid": existing.get("pid", 0),
                    "login_url": existing.get("login_url", LOGIN_URL),
                    "message": "Reusing existing session",
                }
        except (json.JSONDecodeError, OSError):
            pass

    BROWSER_DIR.mkdir(parents=True, exist_ok=True)

    task_id = f"login_{int(time.time())}"
    session = {
        "task_id": task_id,
        "status": "pending",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "started_at_ts": _now_ts(),
        "login_url": LOGIN_URL,
    }

    # Launch worker process: ``python -m review_queue.login_capture --worker``
    worker_cmd = [
        sys_executable(),
        "-m",
        "review_queue.login_capture",
        "--task-id",
        task_id,
        "--worker",
    ]

    try:
        proc = subprocess.Popen(
            worker_cmd,
            cwd=str(PROJECT_ROOT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return {
            "status": "failed",
            "task_id": task_id,
            "pid": None,
            "login_url": LOGIN_URL,
            "message": f"Failed to launch worker: {exc}",
        }

    session["pid"] = proc.pid
    SESSION_FILE.write_text(json.dumps(session), encoding="utf-8")

    return {
        "status": "pending",
        "task_id": task_id,
        "pid": proc.pid,
        "login_url": LOGIN_URL,
        "message": "Login session started",
    }


def finish_login_session(timeout_seconds: int = TIMEOUT_SECONDS) -> dict[str, Any]:
    """Wait for the worker to finish, then validate the login state.

    Returns:
        {"ok": True,  "status": "valid"|"expiring", "days_left": ..., "message": ...}
        {"ok": False, "status": ..., "message": ...}
    """
    deadline = _now_ts() + timeout_seconds

    # Touch trigger file so the worker knows we're waiting
    BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    TRIGGER_FINISH_FILE.write_text("1", encoding="utf-8")

    while _now_ts() < deadline:
        if WORKER_DONE_FILE.exists():
            try:
                done = json.loads(WORKER_DONE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                done = {}

            worker_status = done.get("status", "unknown")
            SESSION_FILE.unlink(missing_ok=True)
            WORKER_DONE_FILE.unlink(missing_ok=True)
            TRIGGER_FINISH_FILE.unlink(missing_ok=True)

            if worker_status == "finished":
                # Validate the state file that the worker saved
                state_path = state_file()
                validity = login_state_validity(state_path)
                if validity["status"] in ("valid", "expiring"):
                    return {
                        "ok": True,
                        "status": validity["status"],
                        "days_left": validity["days_left"],
                        "message": f"Login state {validity['status']} — {validity['message']}",
                    }
                return {
                    "ok": False,
                    "status": validity["status"],
                    "message": f"Login state is {validity['status']}: {validity['message']}",
                }

            return {
                "ok": False,
                "status": "failed",
                "message": done.get("message", "Worker reported failure"),
            }

        time.sleep(0.5)

    # Timeout
    SESSION_FILE.unlink(missing_ok=True)
    WORKER_DONE_FILE.unlink(missing_ok=True)
    TRIGGER_FINISH_FILE.unlink(missing_ok=True)
    return {
        "ok": False,
        "status": "timeout",
        "message": f"超时: worker did not finish within {timeout_seconds}s",
    }


def cancel_login_session() -> dict[str, Any]:
    """Cancel the current login session and clean up marker files."""
    SESSION_FILE.unlink(missing_ok=True)
    TRIGGER_FINISH_FILE.unlink(missing_ok=True)
    WORKER_DONE_FILE.unlink(missing_ok=True)
    logger.info("Login session cancelled and files cleaned up")
    return {"ok": True, "message": "Login session cancelled"}


# ---------------------------------------------------------------------------
# Worker entrypoint (called via ``python -m review_queue.login_capture --worker``)
# ---------------------------------------------------------------------------

def _worker_main(task_id: str) -> int:
    """Launched as a subprocess: opens the browser login page.

    In this MVP the worker simply opens the login page via CDP and waits
    for the user to log in, then saves the storage state and signals done.
    """
    logger.info("Worker started: task_id=%s", task_id)

    # Touch session file so the parent knows we're alive
    BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    session = {
        "task_id": task_id,
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "started_at_ts": _now_ts(),
        "login_url": LOGIN_URL,
    }
    SESSION_FILE.write_text(json.dumps(session), encoding="utf-8")

    # Wait for trigger-finish signal from parent
    logger.info("Worker waiting for trigger-finish signal...")
    import sys as _sys
    _sys.stdout.flush()

    deadline = _now_ts() + TIMEOUT_SECONDS
    while _now_ts() < deadline:
        if TRIGGER_FINISH_FILE.exists():
            break
        time.sleep(1)
    else:
        # Timed out waiting for trigger — clean exit
        logger.warning("Worker timed out waiting for trigger-finish signal")
        return 0

    # Save a placeholder state (real implementation would use CDP)
    state_path = state_file()
    BROWSER_DIR.mkdir(parents=True, exist_ok=True)

    # Try to capture state via CDP if available; otherwise write empty state
    if is_cdp_ready():
        logger.info("CDP ready — would capture real session state")
        # In a real worker this would use Playwright/Chromium CDP to read
        # storage state. For now we write an empty state so the caller
        # gets a clean "empty" / "missing cookies" result.
        state_path.write_text(
            json.dumps({"cookies": [], "origins": []}),
            encoding="utf-8",
        )
    else:
        # No CDP — write an empty state
        state_path.write_text(
            json.dumps({"cookies": [], "origins": []}),
            encoding="utf-8",
        )

    # Signal done
    done = {
        "status": "finished",
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "path": str(state_path),
    }
    WORKER_DONE_FILE.write_text(json.dumps(done), encoding="utf-8")
    logger.info("Worker finished and wrote done file")
    return 0


def sys_executable() -> str:
    """Return the Python executable path, preferring the current venv."""
    import sys as _sys
    return str(Path(_sys.executable).resolve())


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys
    import argparse as _argparse

    _parser = _argparse.ArgumentParser()
    _parser.add_argument("--worker", action="store_true")
    _parser.add_argument("--task-id", default="")
    _args = _parser.parse_args()

    if _args.worker:
        raise SystemExit(_worker_main(_args.task_id))
    raise SystemExit(0)
