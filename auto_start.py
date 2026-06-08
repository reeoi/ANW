"""Windows 开机自启 (Phase 2)。

写一个 ``.bat`` 快捷文件到当前用户的 ``shell:startup`` 文件夹。这种方式不需
要管理员权限,也不用 Task Scheduler / NSSM。

跨平台行为：

- Windows: 用 ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``。
- macOS / Linux: ``is_enabled()`` 始终返回 False；``enable()`` 抛
  ``RuntimeError`` 并提示仅 Windows 支持。
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LAUNCHER_NAME = "ANW_AutoStart.bat"


def is_windows() -> bool:
    return platform.system() == "Windows"


def startup_folder() -> Path:
    """返回当前用户的 Startup 文件夹路径。仅在 Windows 上有意义。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        # 非 Windows 给个占位（仍然是有效路径但永远空）
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_folder() / LAUNCHER_NAME


def is_enabled() -> bool:
    return shortcut_path().exists()


def _build_launcher_content(
    project_root: Path | None = None,
    pythonw: Path | None = None,
    tray_script: Path | None = None,
) -> str:
    root = (project_root or PROJECT_ROOT).resolve()
    pyw = (pythonw or root / ".venv" / "Scripts" / "pythonw.exe").resolve()
    tray = (tray_script or root / "tray_app.py").resolve()
    # 等 10 秒避免抢资源；用 start /B 隐藏后台进程窗口
    return (
        "@echo off\r\n"
        f"cd /d \"{root}\"\r\n"
        "timeout /t 10 /nobreak >nul\r\n"
        f"start \"\" /B \"{pyw}\" \"{tray}\"\r\n"
    )


def enable(
    project_root: Path | None = None,
    pythonw: Path | None = None,
    tray_script: Path | None = None,
    startup_dir: Path | None = None,
) -> Path:
    """写一个 ``ANW_AutoStart.bat`` 到 Startup 文件夹。

    Returns:
        快捷方式的绝对路径。

    Raises:
        RuntimeError: 在非 Windows 平台上调用。
    """
    if not is_windows() and startup_dir is None:
        raise RuntimeError("开机自启只支持 Windows")
    folder = startup_dir or startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    content = _build_launcher_content(project_root, pythonw, tray_script)
    target = folder / LAUNCHER_NAME
    target.write_text(content, encoding="utf-8")
    return target


def disable(startup_dir: Path | None = None) -> bool:
    """删除开机自启快捷方式。返回是否真的删除了文件。"""
    folder = startup_dir or startup_folder()
    target = folder / LAUNCHER_NAME
    if target.exists():
        target.unlink()
        return True
    return False


def status(
    project_root: Path | None = None,
    startup_dir: Path | None = None,
) -> dict[str, str | bool]:
    """返回当前自启状态,供 UI / API 显示。"""
    target = (startup_dir or startup_folder()) / LAUNCHER_NAME
    return {
        "enabled": target.exists(),
        "shortcut_path": str(target),
        "platform": platform.system(),
        "supported": is_windows() or startup_dir is not None,
    }


__all__ = [
    "LAUNCHER_NAME",
    "PROJECT_ROOT",
    "disable",
    "enable",
    "is_enabled",
    "is_windows",
    "shortcut_path",
    "startup_folder",
    "status",
]
