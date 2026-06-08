"""测试 ``auto_start`` 写 / 删 Windows Startup 文件夹快捷方式。

测试不污染真实 Startup 目录：用 ``startup_dir`` 参数指向 ``tmp_path``。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auto_start


def test_status_default(tmp_path: Path) -> None:
    info = auto_start.status(startup_dir=tmp_path)
    assert info["enabled"] is False
    assert info["shortcut_path"].endswith("ANW_AutoStart.bat")


def test_enable_creates_bat(tmp_path: Path) -> None:
    target = auto_start.enable(
        project_root=tmp_path / "project",
        pythonw=tmp_path / "project" / "py.exe",
        tray_script=tmp_path / "project" / "tray_app.py",
        startup_dir=tmp_path,
    )
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    # 必须用 pythonw 而非 python (黑窗口要消失)
    assert "py.exe" in text
    # 等 10 秒
    assert "timeout /t 10" in text
    # cd /d
    assert "cd /d" in text


def test_disable_removes_bat(tmp_path: Path) -> None:
    auto_start.enable(
        project_root=tmp_path / "project",
        pythonw=tmp_path / "project" / "py.exe",
        tray_script=tmp_path / "project" / "tray_app.py",
        startup_dir=tmp_path,
    )
    assert auto_start.disable(startup_dir=tmp_path) is True
    assert auto_start.disable(startup_dir=tmp_path) is False
    assert not (tmp_path / "ANW_AutoStart.bat").exists()


def test_enable_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "Startup"
    target = auto_start.enable(
        project_root=tmp_path,
        pythonw=tmp_path / "py.exe",
        tray_script=tmp_path / "tray.py",
        startup_dir=nested,
    )
    assert target.exists()
    assert nested.exists()


def test_status_after_enable(tmp_path: Path) -> None:
    auto_start.enable(
        project_root=tmp_path,
        pythonw=tmp_path / "py.exe",
        tray_script=tmp_path / "tray.py",
        startup_dir=tmp_path,
    )
    info = auto_start.status(startup_dir=tmp_path)
    assert info["enabled"] is True


def test_enable_without_startup_dir_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_start, "is_windows", lambda: False)
    with pytest.raises(RuntimeError):
        auto_start.enable()


def test_startup_folder_returns_path() -> None:
    assert "Startup" in str(auto_start.startup_folder())


def test_shortcut_path_uses_launcher_name() -> None:
    assert auto_start.shortcut_path().name == auto_start.LAUNCHER_NAME
