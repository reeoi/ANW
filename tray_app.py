"""ANP 系统托盘主程序 (Phase 2)。

职责：

- 启动并维护 ``uvicorn`` 子进程 (FastAPI Web UI)。
- 5 秒轮询 ``/api/health`` 决定托盘图标颜色 (绿/红/灰)。
- 通过 SSE (``/api/notifications/stream``) 监听通知;``critical`` / ``warning``
  弹 Windows 桌面气泡。
- 右键菜单：打开界面 / 立即生成一篇 / 软重启 / 打开数据 / 打开日志 / 退出。
- 优雅退出：终止子进程并清理临时文件。

CLI:

    python tray_app.py        # 调试模式 (主进程留在前台)
    pythonw tray_app.py       # 生产模式 (无控制台,被 start_anp.bat 调用)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("ANP_REVIEW_PORT", "18000") or 18000)

logger = logging.getLogger(__name__)


def _setup_tray_log_file() -> Path:
    """配置文件日志,确保 ``pythonw.exe`` 启动时也能看到错误堆栈。"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    tray_log = log_dir / "tray.log"
    handler = logging.FileHandler(tray_log, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    if not any(getattr(h, "_anp_tray", False) for h in root.handlers):
        handler._anp_tray = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
    return tray_log


# ============================================================================
# Web 服务子进程管理
# ============================================================================


def is_port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def _resolve_console_python() -> str:
    """挑一个有 stdio 的 Python 解释器去启 uvicorn 子进程。

    托盘自身用 ``pythonw.exe`` 启动 (无控制台),但 uvicorn 子进程必须用
    ``python.exe``,否则 logging 写 stderr 时 ``sys.stderr is None`` 会让 uvicorn
    启动崩溃 (子进程立刻退出,端口永远不监听)。
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return sys.executable


def launch_uvicorn(host: str, port: int, project_root: Path | None = None) -> subprocess.Popen | None:
    """在子进程里启 uvicorn,如端口已占用则返回 None。

    子进程的 stdout / stderr 重定向到 ``logs/uvicorn.log``,即使父进程是
    ``pythonw.exe`` 也能看到 uvicorn 的启动错误。
    """
    if is_port_in_use(host, port):
        logger.info("Port %s already in use; assuming external uvicorn is running", port)
        return None
    cwd = project_root or PROJECT_ROOT
    log_dir = cwd / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "uvicorn.log"
    args = [
        _resolve_console_python(),
        "-m",
        "uvicorn",
        "review_queue.human_review:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    creation = 0
    if sys.platform == "win32":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_handle = log_path.open("ab", buffering=0)
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        creationflags=creation,
        stdout=log_handle,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
    )


def stop_proc(proc: subprocess.Popen | None, label: str = "process", timeout: float = 5.0) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    except Exception as exc:  # pragma: no cover - subprocess teardown is OS-specific
        logger.warning("Failed to stop %s: %s", label, exc)


# ============================================================================
# HTTP / SSE 客户端
# ============================================================================


class HealthClient:
    """5 秒轮询 ``/api/health``,把结果传给上层。"""

    def __init__(self, base_url: str, on_state: Callable[[dict[str, Any]], None]) -> None:
        self.base_url = base_url.rstrip("/")
        self.on_state = on_state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="anp-health")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return
        while not self._stop.is_set():
            try:
                resp = httpx.get(f"{self.base_url}/api/health", timeout=2.0)
                payload = resp.json() if resp.status_code == 200 else {"status": "down"}
                self.on_state(payload)
            except Exception:
                self.on_state({"status": "unreachable"})
            self._stop.wait(5.0)


class NotificationStreamClient:
    """SSE 客户端：从 ``/api/notifications/stream`` 拉通知。"""

    def __init__(self, base_url: str, on_notification: Callable[[dict[str, Any]], None]) -> None:
        self.base_url = base_url.rstrip("/")
        self.on_notification = on_notification
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="anp-sse")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:  # pragma: no cover - 长连接难以稳定单测
        try:
            import httpx
        except ImportError:
            return
        url = f"{self.base_url}/api/notifications/stream"
        while not self._stop.is_set():
            try:
                with httpx.stream("GET", url, timeout=None) as resp:
                    for line in resp.iter_lines():
                        if self._stop.is_set():
                            break
                        if not line or not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        self.on_notification(data)
            except Exception:
                self._stop.wait(3.0)


# ============================================================================
# 托盘 UI
# ============================================================================


class TrayApp:
    """主托盘应用。Windows 上用 pystray。"""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.uvicorn_proc: subprocess.Popen | None = None
        self.icon: Any = None
        self.health = HealthClient(self.base_url, self._on_health)
        self.notif = NotificationStreamClient(self.base_url, self._on_notification)
        self._restart_lock = threading.Lock()

    def start(self) -> None:
        logger.info("TrayApp.start launching uvicorn on %s:%s", self.host, self.port)
        try:
            self.uvicorn_proc = launch_uvicorn(self.host, self.port)
        except Exception:
            logger.exception("launch_uvicorn raised")
            raise
        logger.info("uvicorn proc=%s, waiting for port", self.uvicorn_proc.pid if self.uvicorn_proc else None)
        # 等服务可达
        for _ in range(40):
            if is_port_in_use(self.host, self.port):
                logger.info("port reachable")
                break
            time.sleep(0.25)
        else:
            logger.warning("port did not become reachable within 10s; check logs/uvicorn.log")
        # 启动监听
        self.health.start()
        self.notif.start()
        logger.info("building tray icon")
        try:
            self._build_icon()
        except Exception:
            logger.exception("build_icon failed")
            raise
        # 默认开浏览器
        try:
            webbrowser.open(self.base_url)
        except Exception as exc:
            logger.warning("webbrowser.open failed: %s", exc)
        # 后台异步拉起 Chrome CDP 端口（A1：anp 启动即确保 Chrome 在线）
        threading.Thread(
            target=self._ensure_chrome_background,
            daemon=True,
            name="anp-chrome-launcher",
        ).start()
        logger.info("entering icon.run loop")
        self.icon.run()

    # -- pystray 集成 --

    def _build_icon(self) -> None:
        import pystray

        from tray_icons import make_icon

        menu = pystray.Menu(
            pystray.MenuItem(
                "📊 打开管理界面",
                lambda: self._open_browser(),
                default=True,
            ),
            pystray.MenuItem("🚀 立即生成一篇", lambda: self._run_now()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 重启服务", lambda: self._restart_uvicorn()),
            pystray.MenuItem("📁 打开数据文件夹", lambda: self._open_folder("data")),
            pystray.MenuItem("📄 打开日志", lambda: self._open_folder("logs")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 退出", lambda: self._quit()),
        )
        self.icon = pystray.Icon(
            "ANP",
            icon=make_icon("gray"),
            title="ANP（启动中…）",
            menu=menu,
        )

    def _open_browser(self) -> None:
        try:
            webbrowser.open(self.base_url)
        except Exception as exc:  # pragma: no cover
            logger.warning("Open browser failed: %s", exc)

    def _open_folder(self, kind: str) -> None:
        target = PROJECT_ROOT / ("data" if kind == "data" else "logs")
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(target.resolve()))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:  # pragma: no cover
            logger.warning("Open folder failed: %s", exc)

    def _run_now(self) -> None:
        """触发一次 generate→AI review 流水线（发布需在 UI 中手动确认）。"""
        try:
            import httpx

            httpx.post(f"{self.base_url}/api/console/run-now", timeout=5.0)
        except Exception as exc:  # pragma: no cover
            logger.warning("Run-now failed: %s", exc)

    def _restart_uvicorn(self) -> None:
        with self._restart_lock:
            stop_proc(self.uvicorn_proc, "uvicorn")
            time.sleep(1.0)
            self.uvicorn_proc = launch_uvicorn(self.host, self.port)

    @staticmethod
    def _ensure_chrome_background() -> None:
        """A1：anp 启动时后台尝试拉起带 CDP 端口的 Chrome。

        失败不阻塞 anp 主流程（用户可在 UI 上手动触发重试）。
        最多重试 5 次（每次间隔 15s），覆盖 Chrome 冷启动慢的场景。
        """

        if is_cdp_ready():
            logger.info("chrome_cdp already online, skipping background launch")
            return
        for attempt in range(1, 6):
            logger.info("chrome_background_launch attempt=%s", attempt)
            endpoint = ensure_chrome(wait_seconds=12.0)
            if endpoint is not None:
                logger.info("chrome_cdp online via background launch endpoint=%s", endpoint.http_url)
                return
            if attempt < 5:
                time.sleep(15.0)
        logger.warning("chrome_background_launch failed after 5 attempts — user can retry from UI")

    def _quit(self) -> None:
        self.health.stop()
        self.notif.stop()
        stop_proc(self.uvicorn_proc, "uvicorn")
        if self.icon is not None:
            self.icon.stop()

    # -- callbacks --

    def _on_health(self, payload: dict[str, Any]) -> None:
        from tray_icons import make_icon

        status = str(payload.get("status") or "").lower()
        if status == "ok":
            color, title = "green", "ANP（运行中）"
        elif status == "degraded":
            color, title = "yellow", "ANP（运行中，有警告）"
        elif status in {"down", "unreachable"}:
            color, title = "red", "ANP（连接断开）"
        else:
            color, title = "gray", "ANP（启动中…）"
        if self.icon is not None:
            self.icon.icon = make_icon(color)
            self.icon.title = title

    def _on_notification(self, payload: dict[str, Any]) -> None:
        if payload.get("dismissed"):
            return
        sev = str(payload.get("severity") or "info")
        if sev not in {"critical", "warning"}:
            return
        if self.icon is not None:
            try:
                self.icon.notify(payload.get("message") or "", payload.get("title") or "ANP")
            except Exception:  # pragma: no cover - GUI dependent
                pass
        # 处理重启请求
        if str(payload.get("extras", {}).get("action")) == "restart_uvicorn":
            self._restart_uvicorn()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ANP 托盘程序")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> int:
    log_path = _setup_tray_log_file()
    logger.info("tray_app starting; sys.executable=%s host=%s port=%s log=%s",
                sys.executable, DEFAULT_HOST, DEFAULT_PORT, log_path)
    try:
        args = _parse_args()
        app = TrayApp(host=args.host, port=args.port)
        try:
            app.start()
        except KeyboardInterrupt:
            app._quit()
        return 0
    except Exception:
        logger.exception("tray_app crashed before icon.run")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "HealthClient",
    "NotificationStreamClient",
    "TrayApp",
    "is_port_in_use",
    "launch_uvicorn",
    "stop_proc",
]
