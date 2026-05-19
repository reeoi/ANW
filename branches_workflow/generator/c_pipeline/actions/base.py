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


__all__ = ["ActionContext", "ActionResult", "BaseAction"]
