"""AI review skeleton with deterministic dry-run scoring."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewResult:
    """AI review result placeholder."""

    score: int
    passed: bool
    issues: list[str]


def mock_review(content: str, threshold: int = 85) -> ReviewResult:
    """Return a deterministic mock review for dry-run workflows."""
    score = 90 if len(content.strip()) >= 100 else 60
    issues = [] if score >= threshold else ["内容过短，需要扩写。"]
    return ReviewResult(score=score, passed=score >= threshold, issues=issues)
