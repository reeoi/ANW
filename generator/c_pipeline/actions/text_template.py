"""Text template action — variable substitution without LLM call."""

from __future__ import annotations

from typing import Any

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction


class TextTemplateAction(BaseAction):
    action_name = "text_template"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        template = str(params.get("template") or "")
        output_var = str(params.get("output_var") or "text_output")
        result = ctx.resolve_v2(template)
        ctx.set(output_var, result)
        return ActionResult(ok=True, outputs={output_var: result}, message=f"template rendered {len(result)} chars")


__all__ = ["TextTemplateAction"]
