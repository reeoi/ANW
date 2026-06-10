"""ANW 系统托盘主程序 (Phase 2)。

职责：

- 启动并维护 ``uvicorn`` 子进程 (FastAPI Web UI)。
- 5 秒轮询 ``/api/health`` 决定托盘图标颜色 (绿/红/灰)。
- 通过 SSE (``/api/notifications/stream``) 监听通知;``critical`` / ``warning``
  弹 Windows 桌面气泡。
- 右键菜单：打开界面 / 立即生成一篇 / 软重启 / 打开数据 / 打开日志 / 退出。
- 优雅退出：终止子进程并清理临时文件。

CLI:

    python tray_app.py        # 调试模式 (主进程留在前台)
    pythonw tray_app.py       # 生产模式 (无控制台,被 start_anw.bat 调用)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

from config_loader import get_env

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(get_env("ANW_REVIEW_PORT", "18000") or 18000)

logger = logging.getLogger(__name__)


def _setup_tray_log_file() -> Path:
    """配置文件日志,确保 ``pythonw.exe`` 启动时也能看到错误堆栈。"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    tray_log = log_dir / "tray.log"
    handler = logging.handlers.RotatingFileHandler(
        tray_log,
        encoding="utf-8",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    if not any(getattr(h, "_anw_tray", False) for h in root.handlers):
        handler._anw_tray = True  # type: ignore[attr-defined]
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
    """在子进程里启 uvicorn;若端口已占用,先尝试清理孤儿 worker 后再启动。

    子进程的 stdout / stderr 重定向到 ``logs/uvicorn.log``,即使父进程是
    ``pythonw.exe`` 也能看到 uvicorn 的启动错误。
    """
    if is_port_in_use(host, port):
        logger.info("Port %s in use at launch; trying to release orphan worker", port)
        if not _force_release_port(host, port, timeout=8.0):
            logger.warning("Port %s still busy after cleanup; uvicorn launch may fail", port)
            return None
    cwd = project_root or PROJECT_ROOT
    log_dir = cwd / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "uvicorn.log"
    # Rotate uvicorn.log if it exceeds 10 MB to prevent unbounded growth
    if log_path.exists() and log_path.stat().st_size > 10 * 1024 * 1024:
        rotated = log_path.with_suffix(".log.1")
        log_path.replace(rotated)
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
    # Hot reload: 默认关闭。watchfiles 子进程在 socket inheritance 下
    # 容易残留孤儿(父挂掉但子还监听端口),让托盘重启失败。
    # 需要时显式设置 ANW_HOT_RELOAD=1。
    if get_env("ANW_HOT_RELOAD", "0").strip().lower() in ("1", "true", "yes"):
        args.append("--reload")
        logger.info("ANW_HOT_RELOAD enabled: uvicorn --reload is active")
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
        # On Windows, kill entire process tree (handles uvicorn --reload child workers)
        if sys.platform == "win32" and proc.pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10.0,
                    check=True,
                )
                proc.wait(timeout=timeout)
                return
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("taskkill failed for %s, falling back to terminate/kill: %s", label, exc)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    except Exception as exc:  # pragma: no cover - subprocess teardown is OS-specific
        logger.warning("Failed to stop %s: %s", label, exc)


def _force_release_port(host: str, port: int, timeout: float = 10.0) -> bool:
    # netstat 的 OwningProcess 在 Windows 上是 socket 创建者的 PID,
    # 而 socket inheritance 后真正监听的可能是其子进程; 父进程死掉后
    # netstat 还显示死 PID,基于 netstat 的 taskkill 等于空操作。
    # 这里遍历所有 python 进程,直接查每个进程实际持有的 socket fd。
    if not is_port_in_use(host, port):
        return True
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available; cannot force-release port %s", port)
        return False

    current_pid = os.getpid()
    killed: list[int] = []
    for proc in psutil.process_iter():
        try:
            if proc.pid == current_pid:
                continue
            name = (proc.name() or "").lower()
            if "python" not in name:
                continue
            holds_port = False
            for c in proc.net_connections(kind="inet"):
                laddr = getattr(c, "laddr", None)
                if laddr and getattr(laddr, "port", None) == port:
                    holds_port = True
                    break
            if not holds_port:
                continue
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            proc.kill()
            killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if killed:
        logger.info("Force-released port %s by killing pids=%s", port, killed)
    else:
        logger.warning("No live python process found holding port %s", port)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_port_in_use(host, port):
            return True
        time.sleep(0.25)
    return not is_port_in_use(host, port)


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
        self._thread = threading.Thread(target=self._loop, daemon=True, name="anw-health")
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
        self._thread = threading.Thread(target=self._loop, daemon=True, name="anw-sse")
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
        # 后台异步拉起 Chrome CDP 端口（A1：anw 启动即确保 Chrome 在线）
        threading.Thread(
            target=self._ensure_chrome_background,
            daemon=True,
            name="anw-chrome-launcher",
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
            "ANW",
            icon=make_icon("gray"),
            title="ANW（启动中…）",
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
        """Stop uvicorn, force-release port (incl orphan workers), then relaunch."""
        with self._restart_lock:
            logger.info("Restart uvicorn requested")
            stop_proc(self.uvicorn_proc, "uvicorn")
            self.uvicorn_proc = None

            # 强制释放端口:覆盖 socket inheritance 留下的孤儿 worker
            if not _force_release_port(self.host, self.port, timeout=10.0):
                logger.error("Port %s could not be released; aborting restart", self.port)
                try:
                    self.icon.notify("端口无法释放，重启失败", "ANW")
                except Exception:
                    logger.debug("tray notify failed", exc_info=True)
                return

            for attempt in range(1, 4):
                try:
                    proc = launch_uvicorn(self.host, self.port, project_root=PROJECT_ROOT)
                    if proc is not None:
                        self.uvicorn_proc = proc
                        logger.info("Uvicorn relaunched (attempt %d, pid=%s)", attempt, self.uvicorn_proc.pid)
                        try:
                            self.icon.notify("服务已重启", "ANW")
                        except Exception:
                            logger.debug("tray notify failed", exc_info=True)
                        return
                    logger.warning("Uvicorn launch returned no process (attempt %d)", attempt)
                except Exception as exc:
                    logger.error("Launch attempt %d failed: %s", attempt, exc)
                time.sleep(2.0)

            logger.error("Failed to relaunch uvicorn after 3 attempts")
            try:
                self.icon.notify("重启失败", "请手动启动服务")
            except Exception:
                logger.debug("tray notify failed", exc_info=True)

    @staticmethod
    def _ensure_chrome_background() -> None:
        """Chrome CDP — disabled (publishing removed)."""
        pass

    def _quit(self) -> None:
        # pystray 菜单回调跑在 GUI 主线程,如果在这里同步做 stop_proc(等 5s)、
        # httpx 关闭等阻塞操作,托盘看上去"点了没反应"。所以这里只触发后台
        # 清理,主线程立即停掉 icon,然后用守护线程在 5 秒后兜底 os._exit。
        logger.info("Quit requested")

        def _shutdown() -> None:
            try:
                self.health.stop()
            except Exception:
                logger.exception("health.stop failed")
            try:
                self.notif.stop()
            except Exception:
                logger.exception("notif.stop failed")
            try:
                stop_proc(self.uvicorn_proc, "uvicorn", timeout=3.0)
            except Exception:
                logger.exception("stop_proc uvicorn failed")
            logger.info("Quit shutdown complete")
            # 兜底:某些后台线程(SSE httpx.stream timeout=None)在 daemon 退出
            # 时偶发卡 logging.shutdown,直接结束进程。
            try:
                logging.shutdown()
            except Exception:
                pass  # 马上 os._exit；logging 已不可靠，无处可记。
            os._exit(0)

        threading.Thread(target=_shutdown, daemon=True, name="anw-shutdown").start()

        if self.icon is not None:
            try:
                self.icon.visible = False
            except Exception:
                logger.debug("icon hide failed", exc_info=True)
            try:
                self.icon.stop()
            except Exception:
                logger.exception("icon.stop failed")

        # 5 秒后强制退出,防止任何路径 hang 住
        def _hard_exit() -> None:
            time.sleep(5.0)
            logger.warning("Quit watchdog: forcing os._exit(0)")
            os._exit(0)

        threading.Thread(target=_hard_exit, daemon=True, name="anw-quit-watchdog").start()

    # -- callbacks --

    def _on_health(self, payload: dict[str, Any]) -> None:
        from tray_icons import make_icon

        status = str(payload.get("status") or "").lower()
        if status == "ok":
            color, title = "green", "ANW（运行中）"
        elif status == "degraded":
            color, title = "yellow", "ANW（运行中，有警告）"
        elif status in {"down", "unreachable"}:
            color, title = "red", "ANW（连接断开）"
        else:
            color, title = "gray", "ANW（启动中…）"
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
                self.icon.notify(payload.get("message") or "", payload.get("title") or "ANW")
            except Exception:  # pragma: no cover - GUI dependent
                pass
        # 处理重启请求
        if str(payload.get("extras", {}).get("action")) == "restart_uvicorn":
            self._restart_uvicorn()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ANW 托盘程序")
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
    "_force_release_port",
    "is_port_in_use",
    "launch_uvicorn",
    "stop_proc",
]
