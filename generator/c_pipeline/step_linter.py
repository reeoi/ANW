"""Step linter — static checks on step manifests. Phase 5.1."""

from __future__ import annotations

import ast as _ast
import logging
from dataclasses import dataclass, field
from typing import Any

from generator.c_pipeline.actions.runner import _ACTION_REGISTRY
from generator.c_pipeline.contract import StepManifest

logger = logging.getLogger(__name__)


@dataclass
class LintIssue:
    level: str  # "error" | "warning"
    field: str
    message: str
    fix_hint: str = ""


def lint(manifest: StepManifest) -> list[LintIssue]:
    """Run all lint checks on a manifest. Returns empty list if clean."""
    issues: list[LintIssue] = []

    # 1. Schema validity (pydantic already checked, but wrap)
    try:
        manifest.validate_self()
    except ValueError as exc:
        issues.append(LintIssue("error", "schema", str(exc)))

    # 2. Check executor.ref points to real module
    if manifest.executor.kind.value == "builtin" and manifest.executor.ref:
        issues.extend(_check_ref(manifest.executor.ref))

    # 3. Check action names in action_chain
    if manifest.executor.kind.value == "action_chain" and manifest.executor.actions:
        for a in manifest.executor.actions:
            aname = a.get("action") if isinstance(a, dict) else None
            if aname and aname not in _ACTION_REGISTRY:
                issues.append(LintIssue(
                    "error", f"executor.actions.{aname}",
                    f"unknown action '{aname}'",
                    f"valid: {sorted(_ACTION_REGISTRY)}",
                ))

    # 4. python_snippet syntax check
    for a in (manifest.executor.actions or []):
        if isinstance(a, dict) and a.get("action") == "python_snippet":
            code = a.get("code", "")
            if code:
                try:
                    _ast.parse(code)
                except SyntaxError as exc:
                    issues.append(LintIssue(
                        "error", "actions.code",
                        f"Python syntax error: {exc}",
                    ))

    # 5. Permissions vs operations consistency
    perms = set(manifest.permissions or [])
    if manifest.executor.actions:
        for a in manifest.executor.actions:
            if isinstance(a, dict):
                aname = a.get("action", "")
                if aname == "write_file" and "fs.write" not in perms:
                    issues.append(LintIssue("warning", "permissions",
                        "write_file action used but fs.write not declared"))
                if aname == "read_file" and "fs.read" not in perms:
                    issues.append(LintIssue("warning", "permissions",
                        "read_file action used but fs.read not declared"))
                if aname == "web_detect" and "browser.cdp" not in perms:
                    issues.append(LintIssue("warning", "permissions",
                        "web_detect action used but browser.cdp not declared"))
                if aname == "http_request" and "net.http" not in perms:
                    issues.append(LintIssue("warning", "permissions",
                        "http_request action used but net.http not declared"))

    return issues


def _check_ref(ref: str) -> list[LintIssue]:
    """Check if a builtin ref actually imports."""
    import importlib
    try:
        module_path, func_name = ref.rsplit(".", 1)
        module = importlib.import_module(f"generator.c_pipeline.{module_path}")
        if not hasattr(module, func_name):
            return [LintIssue("error", "executor.ref", f"module {module_path} has no function {func_name}")]
    except ImportError:
        return [LintIssue("warning", "executor.ref", f"Cannot import {ref} (may be lazy-loaded)")]
    return []


__all__ = ["LintIssue", "lint"]
