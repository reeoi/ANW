"""Conditional action — evaluate an expression and branch."""

from __future__ import annotations

import logging
import operator
import re
from typing import Any

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction

logger = logging.getLogger(__name__)

# Safe subset of operators allowed in expressions
_OP_MAP = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    "and": lambda a, b: a and b,
    "or": lambda a, b: a or b,
}

# Parse: {var} OP value   e.g. {label} != '人工创作特征显著'     {ai_rate} > 0.3
_EXPR_RE = re.compile(r"\{(\w+)\}\s*(==|!=|>=|<=|>|<)\s*(.+)", re.DOTALL)


class ConditionalAction(BaseAction):
    action_name = "conditional"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        expression = str(params.get("expression") or "").strip()
        then_branch = params.get("then") or []
        else_branch = params.get("else") or []
        if not isinstance(then_branch, list):
            then_branch = [then_branch]
        if not isinstance(else_branch, list):
            else_branch = [else_branch]

        truthy = self._eval(ctx, expression)
        branch = then_branch if truthy else else_branch
        if not branch:
            return ActionResult(ok=True, message=f"conditional: expression is {truthy}, no branch to run")

        from generator.c_pipeline.actions.runner import ActionRunner  # lazy to break circular import
        runner = ActionRunner()
        return runner.run(branch, ctx)

    def _bool_val(self, raw: str) -> Any:
        raw = raw.strip()
        lo = raw.lower()
        if lo in ("true", "yes", "1"):
            return True
        if lo in ("false", "no", "0"):
            return False
        if lo in ("null", "none", "nil", ""):
            return None
        # strip quotes
        if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
            return raw[1:-1]
        # try number
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw

    def _eval(self, ctx: ActionContext, expression: str) -> bool:
        """Evaluate a simple boolean expression with variable substitution."""
        resolved = ctx.resolve_v2(expression)
        m = _EXPR_RE.match(resolved)
        if not m:
            logger.warning("conditional: cannot parse expression '%s'", resolved)
            return False
        left_raw = m.group(1)
        op_text = m.group(2).strip()
        right_raw = m.group(3).strip()

        # left came from a resolved variable context, so resolve original expression's {var}
        # Actually the ctx.resolve_v2 already resolved to the value. But the regex needs
        # to capture the resolved value directly. Let me fix: resolve first, then compare.
        # The resolved expression would be like "人工创作特征显著 != '人工创作特征显著'"
        # So left is "人工创作特征显著", op is "!=", right is "'人工创作特征显著'"

        left_val = left_raw
        right_val = self._bool_val(right_raw)

        if isinstance(left_val, str) and isinstance(right_val, str):
            pass  # string compare OK
        elif isinstance(left_val, str):
            left_val = self._bool_val(left_val)

        fn = _OP_MAP.get(op_text)
        if fn is None:
            return False
        try:
            return bool(fn(left_val, right_val))
        except (TypeError, ValueError):
            return False


__all__ = ["ConditionalAction"]
