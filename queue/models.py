"""Data models for queued novels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Story:
    """A generated story in the local queue."""

    title: str
    content: str
    status: str = "pending"
    id: int | None = None
