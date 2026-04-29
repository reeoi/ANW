"""AI review helpers with deterministic dry-run scoring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from queue.db import list_reviewable_stories, update_story_status


@dataclass(frozen=True)
class ReviewResult:
    """AI review result placeholder."""

    score: int
    passed: bool
    issues: list[str]


@dataclass(frozen=True)
class BatchReviewResult:
    """Summary of a local AI review batch."""

    reviewed: int
    approved: int
    needs_human: int
    message: str


def mock_review(content: str, threshold: int = 85) -> ReviewResult:
    """Return a deterministic mock review for dry-run workflows."""
    score = 90 if len(content.strip()) >= 100 else 60
    issues = [] if score >= threshold else ["内容过短，需要扩写。"]
    return ReviewResult(score=score, passed=score >= threshold, issues=issues)


def run_review_batch(db_path: str | Path, threshold: int = 85, limit: int = 20) -> BatchReviewResult:
    """Review pending stories with the current dry-run AI logic and update SQLite.

    This function is intentionally deterministic for Sprint 3 so the FastAPI UI can
    call the same batch-processing seam that Sprint 4 will expand with live model
    scoring and rewrite attempts.
    """
    candidates = [story for story in list_reviewable_stories(db_path) if story.status == "pending"]
    if not candidates:
        return BatchReviewResult(0, 0, 0, "没有可审核数据：当前没有 pending 作品可运行 AI 审核。")

    approved = 0
    needs_human = 0
    for story in candidates[:limit]:
        if story.id is None:
            continue
        result = mock_review(story.content, threshold=threshold)
        if result.passed:
            approved += 1
            update_story_status(
                db_path,
                story.id,
                "approved",
                review_notes="AI 审核通过（dry-run/mock）。",
                score=result.score,
            )
        else:
            needs_human += 1
            update_story_status(
                db_path,
                story.id,
                "needs_human",
                review_notes="AI 审核需人工复查：" + "；".join(result.issues),
                score=result.score,
            )

    reviewed = approved + needs_human
    return BatchReviewResult(
        reviewed=reviewed,
        approved=approved,
        needs_human=needs_human,
        message=f"AI 审核批次完成：审核 {reviewed} 篇，通过 {approved} 篇，转人工 {needs_human} 篇。",
    )
