"""YAML serialization for StepManifest (contract ↔ disk)."""

from __future__ import annotations

from pathlib import Path

import yaml

from generator.c_pipeline.contract import StepManifest


def load_manifest(path: str | Path) -> StepManifest:
    """Load a StepManifest from a YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return StepManifest.model_validate(raw)


def dump_manifest(manifest: StepManifest, path: str | Path) -> None:
    """Write a StepManifest to a YAML file."""
    d = dump_to_dict(manifest)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(d, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def dump_to_dict(manifest: StepManifest) -> dict:
    """Serialize StepManifest to a plain dict (for API responses)."""
    return manifest.model_dump(mode="json", exclude_none=True)


__all__ = ["dump_manifest", "dump_to_dict", "load_manifest"]
