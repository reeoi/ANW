"""LLM call action."""

from __future__ import annotations

import logging
from typing import Any

from generator.api_client import DeepSeekClient
from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction

logger = logging.getLogger(__name__)


class LLMCallAction(BaseAction):
    action_name = "llm_call"

    def __init__(self, client: DeepSeekClient | None = None):
        self._client = client

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        prompt = ctx.resolve_v2(str(params.get("prompt") or ""))
        model = str(params.get("model") or "")
        thinking = bool(params.get("thinking_mode", False))
        output_var = str(params.get("output_var") or "response_text")

        if not prompt.strip():
            return ActionResult(ok=False, message="llm_call: prompt is empty")

        try:
            from config_loader import load_from_environment

            client = self._client or DeepSeekClient(load_from_environment())
            messages = [{"role": "user", "content": prompt}]
            completion = client.chat_completion(
                messages,
                thinking_mode=thinking,
                model=model if model else None,
                purpose="action_llm_call",
            )
            text = (completion.text or "").strip()
            logger.info("llm_call completed chars=%s model=%s", len(text), model or "default")
            ctx.set(output_var, text)
            return ActionResult(ok=True, outputs={output_var: text}, message=f"LLM returned {len(text)} chars")
        except Exception as exc:
            logger.exception("llm_call failed")
            return ActionResult(ok=False, message=f"llm_call: {exc}")


__all__ = ["LLMCallAction"]
