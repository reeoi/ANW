"""Data models for the c_pipeline story queue.

Schema is defined in docs/c_pipeline_plan.md §3.2 and corresponds to the
``stories`` table managed by ``review_queue.db``. Old fields (score,
retry_count, review_notes, is_dry_run, published_at) are removed; the legacy
``content`` column stays as a nullable compatibility field but new rows leave
it NULL — full-text reads go through ``final_content_path``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Story:
    """A c_pipeline story record."""

    title: str
    status: str = "pending"
    pipeline_version: str = "c1"
    preset_name: str = ""
    work_dir: str = ""
    current_phase: str = "phase_0"
    final_content_path: str | None = None
    pipeline_cost_cny: float = 0.0
    target_length: int | None = None
    emotion: str | None = None
    genre: str | None = None
    hint_title: str | None = None
    summary: str | None = None
    ai_review_score: float | None = None
    ai_review_attempts: int = 0
    content: str | None = None
    cancel_requested: int = 0
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def read_final_content(self) -> str | None:
        """Return the final manuscript text from ``final_content_path`` if present."""
        if not self.final_content_path:
            return None
        path = Path(self.final_content_path)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None


@dataclass(frozen=True)
class DailyPublishPlan:
    """A daily publish plan row."""

    date: str
    planned_count: int
    slots_json: str
    created_at: str | None = None


@dataclass(frozen=True)
class PipelineCostLogEntry:
    """A single pipeline cost log entry."""

    story_id: int | None
    phase: str
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_cny: float = 0.0
    id: int | None = None
    occurred_at: str | None = None


__all__ = ["Story", "DailyPublishPlan", "PipelineCostLogEntry"]
