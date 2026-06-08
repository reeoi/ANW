"""保留注释 / 顺序 / 缩进的 ``config.yaml`` 读写工具。

ANW 的 :mod:`config_loader` 用 ``pyyaml`` 只读地解析 ``config.yaml``。本模块在
**写回** 端保留 YAML 注释、键顺序、缩进风格，避免 UI 的"⚙️ 设置"小节把整份
配置文件的注释和顺序破坏掉。

主要函数:

- :func:`load_yaml` —— 读取并返回 ``ruamel.yaml`` 的 ``CommentedMap``，可原地
  改后再写回。
- :func:`save_yaml` —— 原子写回 (``.tmp`` + ``rename``)。
- :func:`update_yaml_field` —— 用 ``"scheduler.generate_cron"`` 这种点路径设
  置单个字段。
- :func:`update_yaml_fields` —— 批量更新多字段，用一次磁盘 IO。

**设计要点**

* 写回必须用 ``ruamel.yaml`` 的 round-trip 模式；不能换成 ``yaml.safe_dump``
  (会丢注释和顺序)。
* 所有写入操作都先写 ``.tmp`` 再 ``Path.replace``,避免半途崩溃损坏配置。
* 缺失的中间节点会被自动创建为 ``CommentedMap``，与现有配置风格一致。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _build_yaml() -> YAML:
    """构造一个保留注释 / 缩进的 ``ruamel.yaml`` 实例。"""
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


# ruamel.yaml 的 YAML() 实例不是线程安全的 — 其内部 scanner/parser/composer
# 持有可变状态。设置页 4 个并发 GET 共用同一个实例会导致 parser state 错乱，
# 抛出 IndexError: string index out of range。这里用锁保护所有 load/dump。
_yaml = _build_yaml()
_yaml_lock = threading.Lock()


def load_yaml(path: str | Path) -> CommentedMap:
    """读取 YAML 文件并返回可原地修改的映射。

    Args:
        path: YAML 文件路径。

    Returns:
        ``ruamel.yaml`` 的 ``CommentedMap`` (空文件返回空 ``CommentedMap``)。

    Raises:
        FileNotFoundError: 路径不存在。
        TypeError: 文件根节点不是映射。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"YAML 文件不存在: {p}")
    with _yaml_lock:
        with p.open("r", encoding="utf-8") as f:
            data = _yaml.load(f)
    if data is None:
        return CommentedMap()
    if not isinstance(data, Mapping):
        raise TypeError(f"YAML 根节点必须是映射: {p}")
    return data  # type: ignore[return-value]


def save_yaml(path: str | Path, data: Any) -> None:
    """原子写回 YAML 文件，保留注释 / 顺序 / 缩进。

    Args:
        path: 目标文件路径。
        data: 待写入的数据，通常是从 :func:`load_yaml` 拿到再修改后的对象。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _yaml_lock:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            _yaml.dump(data, f)
    tmp.replace(p)


def _split_path(dotted_key: str) -> list[str]:
    keys = [k for k in dotted_key.split(".") if k]
    if not keys:
        raise ValueError("dotted_key 不能为空")
    return keys


def _ensure_path(root: Any, keys: list[str]) -> Any:
    current = root
    for key in keys[:-1]:
        nxt = None
        if isinstance(current, Mapping):
            nxt = current.get(key)
        if not isinstance(nxt, Mapping):
            new_map = CommentedMap()
            current[key] = new_map  # type: ignore[index]
            nxt = new_map
        current = nxt
    return current


def update_yaml_field(path: str | Path, dotted_key: str, value: Any) -> None:
    """读 + 改单字段 + 写回。

    Args:
        path: YAML 文件路径。
        dotted_key: 例如 ``"scheduler.generate_cron"``。
        value: 新值；缺失中间节点会自动创建为映射。
    """
    keys = _split_path(dotted_key)
    data = load_yaml(path)
    parent = _ensure_path(data, keys)
    parent[keys[-1]] = value
    save_yaml(path, data)


def update_yaml_fields(path: str | Path, updates: Iterable[tuple[str, Any]]) -> None:
    """一次性批量更新多个字段，仅一次磁盘读 + 一次写。

    Args:
        path: YAML 文件路径。
        updates: ``(dotted_key, value)`` 二元组列表。
    """
    pairs = list(updates)
    if not pairs:
        return
    data = load_yaml(path)
    for dotted_key, value in pairs:
        keys = _split_path(dotted_key)
        parent = _ensure_path(data, keys)
        parent[keys[-1]] = value
    save_yaml(path, data)


__all__ = [
    "CommentedMap",
    "load_yaml",
    "save_yaml",
    "update_yaml_field",
    "update_yaml_fields",
]
