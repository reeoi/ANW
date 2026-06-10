"""进程级缓存的配置 / 数据库路径解析（long-novel API 专用）。

旧实现把 ``load_from_environment()``（每次读盘解析 config.yaml）和
``initialize_database()``（每次执行全量建表 DDL）放进 ``_db_path()``，而该函数
在 api.py 内有上百处调用点，单个 HTTP 请求会触发数十次重复解析与 DDL，写事务
还会与后台写作线程争抢 SQLite 写锁。

本模块以「相关环境变量 + 配置文件 mtime」作为缓存键：

- 生产稳态：键不变 → 全命中，配置解析与 DDL 进程内只执行一次；
- 设置页写回 config.yaml：文件 mtime 变化 → 键变化 → 自动重新解析，行为与
  旧实现（每次现读）保持一致；
- 测试：monkeypatch ``ANW_CONFIG`` / ``ANW_SQLITE_PATH`` 指向各自 tmp_path →
  键不同 → 测试间天然隔离，无需 reset 钩子（``reset_cache()`` 仍可显式重置）。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from config_loader import DEFAULT_CONFIG_PATH, DEFAULT_DOTENV_PATH, load_from_environment
from storage.schema import initialize_database

_ENV_KEYS = ("ANW_CONFIG", "ANW_DOTENV", "ANW_SQLITE_PATH")


def _mtime_ns(path: Path) -> int:
    """文件 mtime（纳秒）；不存在或不可访问时返回 -1。"""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _cache_key() -> tuple[str, ...]:
    config_path = Path(os.getenv("ANW_CONFIG") or DEFAULT_CONFIG_PATH)
    dotenv_path = Path(os.getenv("ANW_DOTENV") or DEFAULT_DOTENV_PATH)
    env_values = tuple(os.getenv(key) or "" for key in _ENV_KEYS)
    return env_values + (str(config_path), str(_mtime_ns(config_path)), str(dotenv_path), str(_mtime_ns(dotenv_path)))


@lru_cache(maxsize=8)
def _resolve(cache_key: tuple[str, ...]) -> tuple[Path, Path]:
    config = load_from_environment()
    db = initialize_database(config) or Path("data/anw.sqlite3")
    root = Path(str(config.data.get("runtime", {}).get("project_root") or ".")).resolve()
    return (db, root)


def db_path() -> Path:
    """返回已初始化 schema 的 SQLite 路径（进程级缓存，见模块说明）。"""
    return _resolve(_cache_key())[0]


def project_root() -> Path:
    """返回 config ``runtime.project_root``（进程级缓存，见模块说明）。"""
    return _resolve(_cache_key())[1]


def reset_cache() -> None:
    """清空解析缓存（测试或运行时显式刷新用）。"""
    _resolve.cache_clear()


__all__ = ["db_path", "project_root", "reset_cache"]
