"""Web detect action — wraps zhuque_client for AI detection in custom steps."""

from __future__ import annotations

import logging
from typing import Any

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction

logger = logging.getLogger(__name__)


class WebDetectAction(BaseAction):
    action_name = "web_detect"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        text = ctx.resolve_v2(str(params.get("text") or ""))
        output_var = str(params.get("output_var") or "detect_result")

        if not text.strip():
            return ActionResult(ok=False, message="web_detect: text is empty")

        try:
            from generator.c_pipeline.zhuque_client import ZhuqueClient

            client = ZhuqueClient()
            result = client.detect(text)
            data = {
                "label": result.label.value,
                "ai_rate": result.ai_probability if result.ai_probability is not None else None,
                "anomaly": (result.anomaly.value if result.anomaly else None),
                "message": result.message,
            }
            # also set top-level vars for convenience in conditional expressions
            ctx.set("label", data["label"])
            ctx.set("ai_rate", data["ai_rate"])
            ctx.set(output_var, data)
            logger.info("web_detect: label=%s ai_rate=%s", data["label"], data["ai_rate"])
            return ActionResult(ok=result.ok, outputs={output_var: data, "label": data["label"], "ai_rate": data["ai_rate"]}, message=data["message"])
        except Exception as exc:
            logger.exception("web_detect failed")
            return ActionResult(ok=False, message=f"web_detect: {exc}")


__all__ = ["WebDetectAction"]
