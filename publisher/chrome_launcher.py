"""Chrome launcher helpers for CDP-based browser automation.

Provides functions to ensure Chrome is running with remote debugging,
query CDP endpoint info, and check readiness.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222


def _default_chrome_path() -> str:
    """Guess the Chrome executable path for the current platform."""
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def ensure_chrome(
    *,
    cdp_host: str = DEFAULT_CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
    user_data_dir: str | None = None,
    headless: bool = False,
) -> dict[str, Any]:
    """Ensure Chrome is running with remote debugging enabled.

    Returns a dict with keys:
        ok: bool
        endpoint: str | None — the CDP ws endpoint if Chrome is running
        message: str
    """
    # First check if Chrome is already listening on the CDP port
    info = get_cdp_info(cdp_host=cdp_host, cdp_port=cdp_port)
    if info.get("ok"):
        return info

    chrome_path = _default_chrome_path()
    if not chrome_path:
        return {"ok": False, "message": "Chrome executable not found. Please install Google Chrome."}

    if user_data_dir is None:
        user_data_dir = str(Path.home() / ".anw" / "chrome-user-data")
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--remote-debugging-address={cdp_host}",
        f"--user-data-dir={user_data_dir}",
    ]
    if headless:
        cmd.append("--headless=new")

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {"ok": False, "message": f"Failed to launch Chrome: {exc}"}

    # Wait for Chrome to start listening
    for _attempt in range(30):
        time.sleep(0.5)
        info = get_cdp_info(cdp_host=cdp_host, cdp_port=cdp_port)
        if info.get("ok"):
            return info

    return {"ok": False, "message": "Chrome launched but CDP endpoint did not become available within 15s."}


def get_cdp_info(
    *,
    cdp_host: str = DEFAULT_CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
) -> dict[str, Any]:
    """Query the CDP /json/version endpoint and return connection info.

    Returns:
        ok: bool
        endpoint: str | None — ws://... endpoint for CDP
        browser: str | None — browser version string
        user_data_dir: str | None
        message: str
    """
    import json
    import urllib.request

    url = f"http://{cdp_host}:{cdp_port}/json/version"
    try:
        resp = urllib.request.urlopen(url, timeout=3)
        data = json.loads(resp.read().decode("utf-8"))
        ws_endpoint = data.get("webSocketDebuggerUrl", "")
        browser = data.get("Browser", "")
        return {
            "ok": True,
            "endpoint": ws_endpoint,
            "browser": browser,
            "message": f"Chrome CDP ready at {cdp_host}:{cdp_port}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "endpoint": None,
            "browser": None,
            "message": f"CDP not ready: {exc}",
        }


def is_cdp_ready(
    *,
    cdp_host: str = DEFAULT_CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
    timeout: float = 5.0,
) -> bool:
    """Check whether Chrome's CDP endpoint is responsive."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = get_cdp_info(cdp_host=cdp_host, cdp_port=cdp_port)
        if info.get("ok"):
            return True
        time.sleep(0.3)
    return False


__all__ = [
    "DEFAULT_CDP_HOST",
    "DEFAULT_CDP_PORT",
    "ensure_chrome",
    "get_cdp_info",
    "is_cdp_ready",
]
