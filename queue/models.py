"""Data models for queued novels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Story:
    """A generated story in the local SQLite queue."""

    title: str
    content: str
    status: str = "pending"
    score: float | None = None
    retry_count: int = 0
    review_notes: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    published_at: str | None = None
