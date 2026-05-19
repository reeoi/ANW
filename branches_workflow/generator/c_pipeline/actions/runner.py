"""Action chain runner — executes a list of actions in sequence."""

from __future__ import annotations

import logging
from typing import Any

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.c_pipeline.actions.base import ActionContext, ActionResult
from generator.c_pipeline.actions.conditional import ConditionalAction
from generator.c_pipeline.actions.file_io import ReadFileAction, WriteFileAction
from generator.c_pipeline.actions.http_request import HttpRequestAction
from generator.c_pipeline.actions.llm_call import LLMCallAction
from generator.c_pipeline.actions.loop import LoopAction
from generator.c_pipeline.actions.python_snippet import PythonSnippetAction
from generator.c_pipeline.actions.text_template import TextTemplateAction
from generator.c_pipeline.actions.web_detect import WebDetectAction

logger = logging.getLogger(__name__)

_ACTION_REGISTRY: dict[str, type] = {
    "llm_call": LLMCallAction,
    "read_file": ReadFileAction,
    "write_file": WriteFileAction,
    "text_template": TextTemplateAction,
    "conditional": ConditionalAction,
    "loop": LoopAction,
    "web_detect": WebDetectAction,
    "http_request": HttpRequestAction,
    "python_snippet": PythonSnippetAction,
}


class ActionRunner:
    """Execute a list of action dicts in order within a variable context."""

    def __init__(self, config: LoadedConfig | None = None, client: DeepSeekClient | None = None):
        self._config = config
        self._client = client

    def run(
        self,
        actions: list[dict[str, Any]],
        ctx: ActionContext,
    ) -> ActionResult:
        """Run actions sequentially. First failure stops the chain."""
        for i, params in enumerate(actions):
            action_name = str(params.get("action") or "").strip()
            if not action_name:
                return ActionResult(ok=False, message=f"action[{i}] missing 'action' field")
            cls = _ACTION_REGISTRY.get(action_name)
            if cls is None:
                return ActionResult(ok=False, message=f"unknown action '{action_name}' (valid: {sorted(_ACTION_REGISTRY)})")
            instance = cls()
            # Inject client if action needs it
            if action_name == "llm_call" and self._client:
                instance._client = self._client  # type: ignore[attr-defined]
            try:
                result = instance.run(ctx, params)
            except Exception as exc:
                logger.exception("action[%s] %s crashed", i, action_name)
                return ActionResult(ok=False, message=f"action[{i}] {action_name}: {exc}")
            if not result.ok:
                logger.warning("action[%s] %s failed: %s", i, action_name, result.message)
                return result
            # Merge outputs into context
            for k, v in result.outputs.items():
                ctx.set(k, v)
            if result.stop_loop:
                return result
        return ActionResult(ok=True, message=f"{len(actions)} actions completed")


__all__ = ["ActionRunner", "_ACTION_REGISTRY"]
