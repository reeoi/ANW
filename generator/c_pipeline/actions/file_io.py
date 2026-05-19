"""File read/write actions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction

logger = logging.getLogger(__name__)


class ReadFileAction(BaseAction):
    action_name = "read_file"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        raw_path = ctx.resolve_v2(str(params.get("path") or ""))
        output_var = str(params.get("output_var") or "file_content")
        path = Path(raw_path)
        if not path.exists():
            return ActionResult(ok=False, message=f"read_file: {raw_path} not found")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return ActionResult(ok=False, message=f"read_file: {exc}")
        ctx.set(output_var, content)
        return ActionResult(ok=True, outputs={output_var: content}, message=f"read {len(content)} chars from {path.name}")


class WriteFileAction(BaseAction):
    action_name = "write_file"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        raw_path = ctx.resolve_v2(str(params.get("path") or ""))
        content = ctx.resolve_v2(str(params.get("content") or ""))
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ActionResult(ok=False, message=f"write_file: {exc}")
        return ActionResult(ok=True, message=f"wrote {len(content)} chars to {path.name}")


__all__ = ["ReadFileAction", "WriteFileAction"]
