"""保留注释 / 空行 / 未知键的 ``.env`` 读写工具。

``python-dotenv`` 的 ``set_key`` 在写回时会重排键的顺序并丢失行注释，所以这里
手写一个最小的解析器，只支持 ANW 实际使用的写法 (``KEY=VALUE`` / 引号包裹 /
注释 / 空行)。任何无法识别的行 **原样保留**。

主要函数:

- :func:`read_env` —— 解析 ``.env`` 为 ``dict[str, str]``。
- :func:`write_env_field` —— 单字段就地替换或追加，原子写回。
- :func:`write_env_fields` —— 批量更新，单次 IO。

**安全要求 (来自 VISUAL_REBUILD_PLAN §3)**

- 写入时尽量收紧文件权限到所有者可读写 (``chmod 0600`` 等价)。
- 全部走 ``.tmp`` + ``Path.replace`` 原子写。
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Iterable

# 一行 KEY=VALUE 的样式：键必须以字母 / 下划线开头，等号前后允许空格。
_LINE_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")


def _strip_quotes(raw: str) -> str:
    """脱去外层 ``"..."`` 或 ``'...'``,内部内容原样返回。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    return raw


def _quote_if_needed(value: str) -> str:
    """根据 value 内容决定是否加双引号。

    - 空字符串返回 ``""`` (显式可见)。
    - 含空白 / ``#`` / ``=`` / ``"`` 的值用双引号包裹 (内部 ``"`` 转义成 ``\\"``)。
    - 普通 ASCII / 中文 / sk-xxx 这种 key 不加引号，与现有 ``.env`` 风格一致。
    """
    if value == "":
        return '""'
    needs_quote = any(c.isspace() or c in "#=\"'" for c in value)
    if not needs_quote:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_env(path: str | Path) -> dict[str, str]:
    """读取 ``.env`` 并返回键值字典。

    - 注释 / 空行被忽略。
    - 未知行 (无法匹配 KEY=VALUE) 也被忽略，不会抛错。
    - 重复 key 取最后一次出现的值。

    Args:
        path: ``.env`` 路径；不存在返回空字典。
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key = m.group("key")
        raw = m.group("value")
        # 行内 # 注释只在 value 不被引号包裹时才剥离
        if raw and raw[0] not in ('"', "'"):
            hash_idx = raw.find(" #")
            if hash_idx >= 0:
                raw = raw[:hash_idx].rstrip()
        out[key] = _strip_quotes(raw)
    return out


def _restrict_permissions(p: Path) -> None:
    """尽力把文件权限收紧到 owner 可读写。出错时静默忽略 (Windows 上常见)。"""
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows / 受限环境无法 chmod，不阻塞流程。
        pass


def _atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    tmp.write_text(body, encoding="utf-8", newline="\n")
    tmp.replace(path)
    _restrict_permissions(path)


def _replace_or_append(lines: list[str], updates: dict[str, str]) -> list[str]:
    """在 ``lines`` 中替换已存在的 key 行，未出现的 key 追加到末尾。

    保留所有注释 / 空行 / 未知键的相对位置。
    """
    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        m = _LINE_RE.match(line)
        if m and m.group("key") in remaining:
            key = m.group("key")
            new_lines.append(f"{key}={_quote_if_needed(remaining.pop(key))}")
        else:
            new_lines.append(line)
    if remaining:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for key, value in remaining.items():
            new_lines.append(f"{key}={_quote_if_needed(value)}")
    return new_lines


def write_env_field(path: str | Path, key: str, value: str) -> None:
    """就地修改单个键，保留其他行 (注释 / 空行 / 未知键) 顺序。

    Args:
        path: ``.env`` 路径；不存在则新建。
        key: 环境变量名 (必须匹配 ``[A-Za-z_][A-Za-z0-9_]*``)。
        value: 值；空字符串会写成 ``KEY=""``。

    Raises:
        ValueError: 键名不合法。
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"非法环境变量名: {key!r}")
    p = Path(path)
    existing_lines = (
        p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    )
    new_lines = _replace_or_append(existing_lines, {key: value})
    _atomic_write(p, new_lines)


def write_env_fields(path: str | Path, updates: Iterable[tuple[str, str]]) -> None:
    """批量更新多个键，单次磁盘写。

    与 :func:`write_env_field` 行为一致，但只读 / 写一次文件。
    """
    pairs = dict(updates)
    if not pairs:
        return
    for key in pairs:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"非法环境变量名: {key!r}")
    p = Path(path)
    existing_lines = (
        p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    )
    new_lines = _replace_or_append(existing_lines, pairs)
    _atomic_write(p, new_lines)


__all__ = [
    "read_env",
    "write_env_field",
    "write_env_fields",
]
