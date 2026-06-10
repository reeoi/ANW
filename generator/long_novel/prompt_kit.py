"""长篇流水线共享的 prompt 模板工具。

``_PromptValues`` + ``_render_prompt_template`` + ``_load_prompt_template`` 此前
在 api.py / l0_book_setup.py / l2_chapter_write.py / l4_review.py 四处逐字重复
（仅日志前缀不同）。本模块是唯一实现；各模块以旧私有名做别名引入，调用点
零改动（待 api.py 拆分时再彻底归位）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def prompt_file_text(name: str | None) -> str:
    """读取 ``prompts/`` 下指定文件全文；name 为空或文件不存在返回空串。"""
    if not name:
        return ""
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_prompt_template(name: str, fallback: str) -> str:
    """优先读 ``prompts/`` 文件（strip 后非空才算数），否则用内置 fallback。"""
    text = prompt_file_text(name).strip()
    return text or fallback


class PromptValues(dict):
    """``format_map`` 的宽容载体：缺失键原样保留 ``{key}`` 占位符。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_prompt_template(template: str, values: dict[str, Any]) -> str:
    """宽容渲染：None → 空串、缺失键保留占位符、渲染异常时原样返回模板。"""
    try:
        return template.format_map(PromptValues({k: "" if v is None else v for k, v in values.items()}))
    except Exception as exc:
        logger.warning("prompt template render failed: %s", exc)
        return template


__all__ = ["PROMPTS_DIR", "PromptValues", "load_prompt_template", "prompt_file_text", "render_prompt_template"]
