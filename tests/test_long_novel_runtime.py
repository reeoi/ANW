"""``generator.long_novel.runtime`` 的进程级缓存行为。

覆盖三个关键性质（对应改进清单 P0-1）：

1. 环境稳定时 ``initialize_database``（含全量 DDL）进程内只执行一次；
2. monkeypatch 切换 ``ANW_SQLITE_PATH``（测试隔离的方式）触发重新解析；
3. config.yaml mtime 变化（设置页写回场景）触发重新解析。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generator.long_novel import runtime


@pytest.fixture(autouse=True)
def _fresh_cache():
    runtime.reset_cache()
    yield
    runtime.reset_cache()


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("runtime:\n  project_root: .\n", encoding="utf-8")
    return cfg


def _point_env_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db_name: str = "anw.sqlite3") -> Path:
    cfg = _write_config(tmp_path)
    db_file = tmp_path / db_name
    monkeypatch.setenv("ANW_CONFIG", str(cfg))
    monkeypatch.setenv("ANW_DOTENV", str(tmp_path / "missing.env"))
    monkeypatch.setenv("ANW_SQLITE_PATH", str(db_file))
    return db_file


def _count_initialize_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real_initialize = runtime.initialize_database

    def counting_initialize(config):
        calls.append(1)
        return real_initialize(config)

    monkeypatch.setattr(runtime, "initialize_database", counting_initialize)
    return calls


def test_stable_environment_initializes_once(tmp_path, monkeypatch):
    db_file = _point_env_at(monkeypatch, tmp_path)
    calls = _count_initialize_calls(monkeypatch)

    first = runtime.db_path()
    second = runtime.db_path()
    third = runtime.db_path()

    assert first == second == third == db_file
    assert db_file.exists()
    assert len(calls) == 1


def test_env_change_triggers_fresh_resolution(tmp_path, monkeypatch):
    db_a = _point_env_at(monkeypatch, tmp_path, db_name="a.sqlite3")
    assert runtime.db_path() == db_a

    db_b = tmp_path / "b.sqlite3"
    monkeypatch.setenv("ANW_SQLITE_PATH", str(db_b))

    assert runtime.db_path() == db_b
    assert db_b.exists()


def test_config_mtime_change_triggers_fresh_resolution(tmp_path, monkeypatch):
    _point_env_at(monkeypatch, tmp_path)
    cfg = Path(os.environ["ANW_CONFIG"])
    os.utime(cfg, ns=(1_000_000_000, 1_000_000_000))
    calls = _count_initialize_calls(monkeypatch)

    runtime.db_path()
    runtime.db_path()
    assert len(calls) == 1

    os.utime(cfg, ns=(2_000_000_000, 2_000_000_000))
    runtime.db_path()
    assert len(calls) == 2


def test_project_root_resolved_from_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"runtime:\n  project_root: {tmp_path.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("ANW_CONFIG", str(cfg))
    monkeypatch.setenv("ANW_DOTENV", str(tmp_path / "missing.env"))
    monkeypatch.setenv("ANW_SQLITE_PATH", str(tmp_path / "anw.sqlite3"))

    assert runtime.project_root() == tmp_path.resolve()
