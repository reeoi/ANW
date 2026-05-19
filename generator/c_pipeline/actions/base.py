"""Action base class + variable context + result types."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionContext:
    """Mutable variable pool shared across actions in one step.

    Loop bodies snapshot the pool on entry and discard their writes on exit,
    so loop-internal variables never leak to the outer scope.
    """

    vars: dict[str, Any] = field(default_factory=dict)

    def resolve_v2(self, text: str) -> str:
        """Replace ``{var_name}`` placeholders using pool values. Unknown keys kept as-is."""
        if not isinstance(text, str):
            return str(text)

        def replacer(m: re.Match) -> str:
            key = m.group(1)
            val = self.vars.get(key)
            if val is None:
                return m.group(0)
            if isinstance(val, (dict, list)):
                import json
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        return re.sub(r"\{(\w+)\}", replacer, text)

    def set(self, key: str, value: Any) -> None:
        self.vars[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.vars.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        return dict(self.vars)

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.vars = dict(snapshot)


@dataclass
class ActionResult:
    """Outcome of one action execution."""

    ok: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    stop_loop: bool = False  # signal to exit enclosing loop


class BaseAction:
    """Protocol for all actions. Subclasses override ``run``."""

    action_name: str = "base"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        raise NotImplementedError


@dataclass
class StepRunContext:
    """Named data bus shared across steps in a preset pipeline.

    Phase 3: replaces the implicit `{prev_output}` with named `{steps.X.Y}`,
    `{params.Z}`, `{globals.W}` references.
    """

    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    globals: dict[str, Any] = field(default_factory=dict)
    _last_step_output: str = ""  # compat: {prev_output} fallback

    def bind(self, template: str) -> Any:
        """Resolve `{steps.X.Y}`, `{params.Z}`, `{globals.W}` references.
        Unknown keys → empty string. Supports `| default("val")` fallback.
        """
        if not isinstance(template, str):
            return template
        # Shortcut: if it's a simple scalar template like "{steps.X.Y}", return the value as-is (not stringified)
        import re as _re
        simple = _re.match(r"^\{(\w+(?:\.\w+)*)\}$", template.strip())
        if simple:
            val = self._resolve_path(simple.group(1))
            return val if val is not None else ""

        def replacer(m: _re.Match) -> str:
            expr = m.group(1)
            default = None
            if "|" in expr:
                expr, fallback = expr.split("|", 1)
                fallback = fallback.strip()
                if fallback.startswith('default("') and fallback.endswith('")'):
                    default = fallback[9:-2]
                elif fallback.startswith("default('") and fallback.endswith("')"):
                    default = fallback[9:-2]
            val = self._resolve_path(expr.strip())
            if val is None:
                return default if default is not None else ""
            if isinstance(val, (dict, list)):
                import json
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        return _re.sub(r"\{((?:\w+(?:\.\w+)*)(?:\s*\|\s*default\([^)]*\))?)\}", replacer, template)

    def _resolve_path(self, path: str) -> Any | None:
        """Resolve a dotted path like 'steps.paste.article' or 'params.dry_run'."""
        parts = path.split(".", 1)
        namespace = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if namespace == "steps":
            if not rest:
                return None
            step_parts = rest.split(".", 1)
            step_id = step_parts[0]
            port = step_parts[1] if len(step_parts) > 1 else None
            if step_id not in self.steps:
                return None
            step_data = self.steps[step_id]
            if port is None:
                return step_data
            return step_data.get(port)
        elif namespace == "params":
            return self.params.get(rest) if rest else self.params
        elif namespace == "globals":
            return self.globals.get(rest) if rest else self.globals
        return None

    def write_step_output(self, step_id: str, outputs: dict[str, Any]) -> None:
        self.steps[step_id] = dict(outputs)
        # compat: maintain _last_step_output for old {prev_output} syntax
        for v in outputs.values():
            if isinstance(v, str) and len(v) > 20:
                self._last_step_output = v
                break


__all__ = ["ActionContext", "ActionResult", "BaseAction", "StepRunContext"]
