"""Preset loader — read/validate/merge YAML preset files with inheritance."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PRESETS_DIR = Path(__file__).resolve().parents[2] / "presets"
VALID_PHASES = frozenset({
    "phase_0", "phase_1", "phase_2", "phase_3",
    "phase_4", "phase_5", "phase_6",
})
VALID_ACTIONS = frozenset({
    "llm_call", "read_file", "write_file", "text_template",
    "conditional", "loop", "http_request", "python_snippet",
})


def load_preset(name: str, presets_dir: Path | None = None) -> dict[str, Any]:
    """Load a preset YAML file, resolve inheritance, normalize to v2, and return dict.

    Raises ValueError if the file is missing, malformed, or has invalid references.
    """
    import yaml

    base = presets_dir or DEFAULT_PRESETS_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        raise ValueError(f"预设文件不存在: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"预设 YAML 顶层必须是 dict: {path}")

    # Resolve inheritance
    inherits = raw.get("inherits")
    if inherits and isinstance(inherits, str) and inherits.strip():
        parent = load_preset(inherits.strip(), presets_dir=base)
        raw = _merge_presets(parent, raw)

    raw.setdefault("name", name)
    raw.setdefault("steps", [])
    raw.setdefault("variables", {})
    raw = _v2_normalize(raw)
    _validate(raw, path)
    return raw


# ── v2 schema compat ────────────────────────────────────────────────

# Old step id → v2 uses mapping
_V1_TO_V2_BUILTIN: dict[str, str] = {
    "phase_0": "phase_0_select",
    "phase_1": "phase_1_framework",
    "phase_2": "phase_2_outline",
    "phase_3": "phase_3_sections",
    "phase_4": "phase_4_polish",
    "phase_5": "phase_5_deslop",
    "phase_6": "phase_6_chapter_title",
}


def _v2_normalize(preset: dict) -> dict:
    """Convert old v1 preset format to v2 schema in-memory."""
    version = preset.get("version", 1)
    if isinstance(version, str):
        try:
            version = int(version)
        except ValueError:
            version = 1
    if version >= 2:
        # Already v2 — ensure each step has uses field
        for s in preset.get("steps", []):
            if "uses" not in s and s.get("id"):
                s["uses"] = s["id"] + "@v1"
        return preset

    # v1 → v2 compat
    import logging
    _logger = logging.getLogger(__name__)
    _logger.warning("preset '%s' uses v1 schema, compat layer active (expires 2026-09-01)", preset.get("name", "?"))

    new_steps = []
    for s in preset.get("steps", []):
        sid = s.get("id", "")
        stype = s.get("type", "builtin")
        if stype == "builtin" and sid in _V1_TO_V2_BUILTIN:
            new_s = {
                "id": sid,
                "uses": _V1_TO_V2_BUILTIN[sid] + "@v1",
                "enabled": s.get("enabled", True),
                "pause_after": s.get("pause_after", False),
                "bindings": {},
                "params": {},
            }
            # Wire up sequential bindings based on known phase order
            prev_map = {
                "phase_1": "phase_0", "phase_2": "phase_1", "phase_3": "phase_2",
                "phase_4": "phase_3", "phase_5": "phase_4",
                "phase_6": "phase_5",
            }
            prev = prev_map.get(sid)
            if prev:
                prev_output = _V1_TO_V2_BUILTIN.get(prev, prev)
                port_map = {
                    "phase_1": "selection", "phase_2": "framework", "phase_3": "outline",
                    "phase_4": "draft", "phase_5": "polished",
                    "phase_6": "final",
                }
                port = port_map.get(sid, "default")
                new_s["bindings"] = {port: f"{{steps.{prev_output}.{port}}}"}
            new_steps.append(new_s)
        elif stype == "custom":
            new_s = {
                "id": sid,
                "uses": None,  # inline custom — keep actions in preset
                "enabled": s.get("enabled", True),
                "pause_after": s.get("pause_after", False),
                "bindings": {},
                "params": {},
                "actions": s.get("actions", []),
                "label": s.get("label", sid),
                "description": s.get("description", ""),
                "input": s.get("input", ""),
                "output": s.get("output", ""),
                "_v1_compat": True,
            }
            new_steps.append(new_s)
        else:
            new_steps.append(s)

    preset["steps"] = new_steps
    preset["version"] = 2
    return preset


def _merge_presets(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge child into parent. Child steps override parent by id; new steps appended."""
    merged = dict(parent)
    if "name" in child:
        merged["name"] = child["name"]
    if "variables" in child and isinstance(child["variables"], dict):
        merged.setdefault("variables", {})
        merged["variables"].update(child["variables"])
    # Merge steps
    p_steps = {s.get("id"): s for s in merged.get("steps", []) if isinstance(s, dict)}
    c_steps = {s.get("id"): s for s in child.get("steps", []) if isinstance(s, dict)}
    p_steps.update(c_steps)
    # Preserve order: parent steps, then child-only steps
    ordered_ids: list[str] = []
    for s in merged.get("steps", []):
        sid = s.get("id") if isinstance(s, dict) else None
        if sid and sid not in ordered_ids:
            ordered_ids.append(sid)
    for s in child.get("steps", []):
        sid = s.get("id") if isinstance(s, dict) else None
        if sid and sid not in ordered_ids:
            ordered_ids.append(sid)
    merged["steps"] = [p_steps[sid] for sid in ordered_ids if sid in p_steps]
    return merged


def _validate(preset: dict, path: Path) -> None:
    steps = preset.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError(f"预设 steps 必须是 list: {path}")
    seen_ids: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"step[{i}] 必须是 dict: {path}")
        sid = step.get("id")
        if not sid or not isinstance(sid, str):
            raise ValueError(f"step[{i}] 缺少 id 字段: {path}")
        if sid in seen_ids:
            raise ValueError(f"step id 重复: {sid}: {path}")
        seen_ids.add(sid)
        stype = step.get("type", "")
        uses = step.get("uses")
        actions = step.get("actions")
        # v2: uses field required for builtin steps
        if uses and not isinstance(uses, str):
            raise ValueError(f"step[{i}] ({sid}) uses 必须是字符串: {path}")
        # v1 compat: type builtin/custom
        if stype == "custom" or (not uses and actions):
            actions = actions or []
            if not isinstance(actions, list):
                raise ValueError(f"step[{i}] ({sid}) actions 必须是 list: {path}")
            for j, a in enumerate(actions):
                aname = a.get("action") if isinstance(a, dict) else None
                if aname not in VALID_ACTIONS:
                    raise ValueError(f"step[{i}] ({sid}) action[{j}] 未知或缺失: {aname}: {path}")


def list_presets(presets_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all .yaml preset files with metadata."""
    base = presets_dir or DEFAULT_PRESETS_DIR
    if not base.exists():
        return []
    result: list[dict[str, Any]] = []
    for fpath in sorted(base.glob("*.yaml")):
        import yaml
        try:
            raw = yaml.safe_load(fpath.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        result.append({
            "name": fpath.stem,
            "label": raw.get("name", fpath.stem) if isinstance(raw, dict) else fpath.stem,
            "inherits": raw.get("inherits") if isinstance(raw, dict) else None,
            "step_count": len(raw.get("steps", [])) if isinstance(raw, dict) else 0,
            "path": str(fpath),
        })
    return result


__all__ = ["DEFAULT_PRESETS_DIR", "VALID_ACTIONS", "VALID_PHASES", "list_presets", "load_preset"]
