"""Python snippet action — escape hatch for custom logic in presets.

The builtins whitelist and import allowlist below are a *convenience guard*
against preset-author mistakes, NOT a security boundary: CPython ``exec``
sandboxes are escapable via introspection, and the whitelisted ``pathlib``
already grants full file-system access. Presets are trusted local files —
**review any ``python_snippet`` block manually before running a preset that
came from somewhere else.**

Snippet receives ``input_text`` and ``ctx`` (dict), and must return a dict
that gets merged into the variable pool (via a ``run()`` function or an
``output`` variable).
"""

from __future__ import annotations

import logging
from typing import Any

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction

logger = logging.getLogger(__name__)

_ALLOWED_IMPORTS = frozenset({
    "re", "json", "time", "datetime", "pathlib", "math", "random",
    "collections", "itertools", "textwrap", "string", "hashlib",
})


class PythonSnippetAction(BaseAction):
    action_name = "python_snippet"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        code = str(params.get("code") or "").strip()
        if not code:
            return ActionResult(ok=False, message="python_snippet: code is empty")

        # Build safe globals with whitelisted imports
        safe_globals: dict[str, Any] = {"__builtins__": {
            "print": print,
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sorted": sorted,
            "reversed": reversed,
            "min": min,
            "max": max,
            "sum": sum,
            "abs": abs,
            "round": round,
            "isinstance": isinstance,
            "type": type,
            "Exception": Exception,
            "ValueError": ValueError,
            "TypeError": TypeError,
            "KeyError": KeyError,
            "IndexError": IndexError,
        }}
        for mod_name in _ALLOWED_IMPORTS:
            try:
                safe_globals[mod_name] = __import__(mod_name)
            except ImportError:
                pass

        # Input data
        safe_globals["input_text"] = ctx.get("prev_output", "")
        safe_globals["ctx"] = ctx.snapshot()

        try:
            exec(code, safe_globals)
            # Call the snippet's main function or get the result
            result_fn = safe_globals.get("run")
            if callable(result_fn):
                result = result_fn(safe_globals["input_text"], safe_globals["ctx"])
            else:
                # Look for 'output' variable as fallback
                result = safe_globals.get("output")
        except Exception as exc:
            logger.exception("python_snippet execution failed")
            return ActionResult(ok=False, message=f"python_snippet: {exc}")

        if not isinstance(result, dict):
            return ActionResult(ok=False, message=f"python_snippet: expected dict result, got {type(result).__name__}")

        for k, v in result.items():
            ctx.set(k, v)
        return ActionResult(ok=True, outputs=result, message=f"python_snippet returned {len(result)} keys")


__all__ = ["PythonSnippetAction"]
