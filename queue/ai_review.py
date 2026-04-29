"""AI review, diagnosis, and bounded rewrite workflow for queued stories.

The module is intentionally safe in dry-run/mock mode: when no DeepSeek API key is
configured, scoring and rewriting are deterministic local operations so batch
review never waits on an external service and never loops forever.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config_loader import LoadedConfig, load_from_environment
from queue.db import get_story, list_reviewable_stories, update_story_status


DIMENSIONS: tuple[str, ...] = (
    "plot",
    "character",
    "pacing",
    "language",
    "originality",
    "safety",
    "platform_fit",
)

DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    "plot": 0.20,
    "character": 0.15,
    "pacing": 0.15,
    "language": 0.15,
    "originality": 0.15,
    "safety": 0.10,
    "platform_fit": 0.10,
}

UNSAFE_TERMS = ("色情", "暴力", "血腥", "赌博", "自杀", "仇恨", "违规")


@dataclass(frozen=True)
class AIReviewSettings:
    """Runtime controls for AI review and rewrite."""

    approval_threshold: int = 80
    max_rewrite_attempts: int = 3
    model: str = "deepseek-chat"
    temperature: float = 0.3
    timeout_seconds: int = 60
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    mock: bool = True
    dimension_weights: dict[str, float] | None = None

    @property
    def weights(self) -> dict[str, float]:
        return self.dimension_weights or DEFAULT_DIMENSION_WEIGHTS


@dataclass(frozen=True)
class ReviewResult:
    """Structured, JSON-serializable result for the 7-dimension review."""

    total_score: int
    dimension_scores: dict[str, int]
    issues: list[str]
    suggestions: list[str]
    decision: str

    @property
    def score(self) -> int:
        """Backward-compatible alias used by older Sprint 3 callers."""
        return self.total_score

    @property
    def passed(self) -> bool:
        """Backward-compatible pass flag."""
        return self.decision == "approved"

    def to_json(self) -> str:
        """Return parseable JSON with stable Chinese-safe encoding."""
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class StoryReviewSummary:
    """Summary for a single story processed through review/rewrite attempts."""

    story_id: int
    decision: str
    attempts: int
    final_score: int
    issues: list[str]
    suggestions: list[str]
    failure_reason: str | None = None


@dataclass(frozen=True)
class BatchReviewResult:
    """Summary of an AI review batch."""

    reviewed: int
    approved: int
    needs_human: int
    failed: int = 0
    failure_reasons: list[str] | None = None
    message: str = ""


def load_ai_review_settings(config: LoadedConfig | None = None) -> AIReviewSettings:
    """Load AI-review thresholds, rewrite limit, and model params.

    Values come from ``config.yaml`` via ``LoadedConfig`` and may be overridden by
    environment variables:

    - ``ANP_AI_REVIEW_THRESHOLD``
    - ``ANP_MAX_REWRITE_ATTEMPTS``
    - ``ANP_AI_REVIEW_MODEL``
    - ``ANP_AI_REVIEW_TEMPERATURE``
    - ``ANP_AI_REVIEW_TIMEOUT_SECONDS``
    """

    if config is None:
        config = load_from_environment()
    audit = config.data.get("audit", {}) if isinstance(config.data.get("audit", {}), dict) else {}
    deepseek = config.data.get("deepseek", {}) if isinstance(config.data.get("deepseek", {}), dict) else {}

    weights = _normalize_weights(audit.get("dimensions"))
    api_key = str(deepseek.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or "")
    mock = bool(deepseek.get("mock") or config.is_dry_run or not api_key)

    return AIReviewSettings(
        approval_threshold=_env_int("ANP_AI_REVIEW_THRESHOLD", int(audit.get("approval_threshold") or 80)),
        max_rewrite_attempts=max(0, _env_int("ANP_MAX_REWRITE_ATTEMPTS", int(audit.get("max_rewrite_attempts") or 3))),
        model=os.getenv("ANP_AI_REVIEW_MODEL") or str(audit.get("model") or deepseek.get("model") or "deepseek-chat"),
        temperature=_env_float("ANP_AI_REVIEW_TEMPERATURE", float(audit.get("temperature") or 0.3)),
        timeout_seconds=_env_int(
            "ANP_AI_REVIEW_TIMEOUT_SECONDS",
            int(audit.get("timeout_seconds") or deepseek.get("timeout_seconds") or 60),
        ),
        api_key=api_key,
        base_url=str(deepseek.get("base_url") or "https://api.deepseek.com").rstrip("/"),
        mock=mock,
        dimension_weights=weights,
    )


def review_story(story, config: LoadedConfig | None = None, settings: AIReviewSettings | None = None) -> ReviewResult:
    """Review a story and return a parseable JSON-compatible 7-dimension result."""

    settings = settings or load_ai_review_settings(config)
    if settings.mock:
        return _mock_review(story.title, story.content, settings)
    try:
        return _live_review(story.title, story.content, settings)
    except Exception as exc:  # keep auto mode non-blocking if remote review fails
        result = _mock_review(story.title, story.content, settings)
        return ReviewResult(
            total_score=result.total_score,
            dimension_scores=result.dimension_scores,
            issues=result.issues + [f"DeepSeek 审核调用失败，已回退 mock：{exc}"],
            suggestions=result.suggestions,
            decision=result.decision,
        )


def rewrite_story(
    title: str,
    content: str,
    review: ReviewResult,
    config: LoadedConfig | None = None,
    settings: AIReviewSettings | None = None,
) -> str:
    """Rewrite one low-scoring story draft using DeepSeek or deterministic mock."""

    settings = settings or load_ai_review_settings(config)
    if settings.mock:
        return _mock_rewrite(title, content, review)
    try:
        return _live_rewrite(title, content, review, settings)
    except Exception:
        return _mock_rewrite(title, content, review)


def review_story_in_database(
    db_path: str | Path,
    story_id: int,
    config: LoadedConfig | None = None,
) -> StoryReviewSummary:
    """Review one queued story, rewrite at most N times, and persist final state."""

    settings = load_ai_review_settings(config)
    story = get_story(db_path, story_id)
    if story is None:
        return StoryReviewSummary(story_id, "failed", 0, 0, [], [], "story not found")

    attempts = 0
    current_title = story.title
    current_content = story.content
    current_retry_count = int(story.retry_count or 0)
    final_review = review_story(story, config=config, settings=settings)

    while final_review.decision != "approved" and attempts < settings.max_rewrite_attempts:
        attempts += 1
        current_retry_count += 1
        current_content = rewrite_story(current_title, current_content, final_review, config=config, settings=settings)
        _persist_review_attempt(
            db_path,
            story_id,
            title=current_title,
            content=current_content,
            retry_count=current_retry_count,
            score=final_review.total_score,
            status="pending",
            review_notes=f"AI 第 {attempts} 次重写；上轮问题：{json.dumps(final_review.issues, ensure_ascii=False)}",
        )
        refreshed = get_story(db_path, story_id)
        final_review = review_story(refreshed or story, config=config, settings=settings)

    if final_review.decision == "approved":
        notes = "AI 审核通过：" + final_review.to_json()
        _persist_review_attempt(
            db_path,
            story_id,
            title=current_title,
            content=current_content,
            retry_count=current_retry_count,
            score=final_review.total_score,
            status="approved",
            review_notes=notes,
        )
        return StoryReviewSummary(
            story_id=story_id,
            decision="approved",
            attempts=attempts,
            final_score=final_review.total_score,
            issues=final_review.issues,
            suggestions=final_review.suggestions,
        )

    notes = "AI 审核未通过，转人工复查：" + final_review.to_json()
    _persist_review_attempt(
        db_path,
        story_id,
        title=current_title,
        content=current_content,
        retry_count=current_retry_count,
        score=final_review.total_score,
        status="needs_human",
        review_notes=notes,
    )
    return StoryReviewSummary(
        story_id=story_id,
        decision="needs_human",
        attempts=attempts,
        final_score=final_review.total_score,
        issues=final_review.issues,
        suggestions=final_review.suggestions,
        failure_reason="; ".join(final_review.issues) or "score below threshold",
    )


def mock_review(content: str, threshold: int = 80) -> ReviewResult:
    """Backward-compatible deterministic mock review for dry-run workflows."""

    settings = AIReviewSettings(approval_threshold=threshold, mock=True)
    return _mock_review("未命名", content, settings)


def run_review_batch(
    db_path: str | Path,
    threshold: int | None = None,
    limit: int = 20,
    config: LoadedConfig | None = None,
) -> BatchReviewResult:
    """Review pending stories, auto-rewrite low scores, and update SQLite."""

    config = config or load_from_environment()
    if threshold is not None:
        config = _config_with_threshold(config, threshold)

    candidates = [story for story in list_reviewable_stories(db_path) if story.status == "pending"]
    if not candidates:
        return BatchReviewResult(0, 0, 0, 0, [], "没有可审核数据：当前没有 pending 作品可运行 AI 审核。")

    approved = 0
    needs_human = 0
    failed = 0
    failure_reasons: list[str] = []
    reviewed = 0
    for story in candidates[:limit]:
        if story.id is None:
            failed += 1
            failure_reasons.append("story id missing")
            continue
        summary = review_story_in_database(db_path, story.id, config=config)
        reviewed += 1
        if summary.decision == "approved":
            approved += 1
        elif summary.decision == "needs_human":
            needs_human += 1
            if summary.failure_reason:
                failure_reasons.append(f"id={story.id}: {summary.failure_reason}")
        else:
            failed += 1
            failure_reasons.append(f"id={story.id}: {summary.failure_reason or 'unknown failure'}")

    message = (
        f"AI 审核批次完成：审核 {reviewed} 篇，通过 {approved} 篇，"
        f"转人工 {needs_human} 篇，失败 {failed} 篇。"
    )
    if failure_reasons:
        message += " 失败原因：" + "；".join(failure_reasons)
    return BatchReviewResult(reviewed, approved, needs_human, failed, failure_reasons, message)


def _mock_review(title: str, content: str, settings: AIReviewSettings) -> ReviewResult:
    text = content.strip()
    length = len(text)
    unsafe_hits = [term for term in UNSAFE_TERMS if term in text]

    scores = {
        "plot": _bounded(55 + min(length // 8, 35)),
        "character": _bounded(58 + min(length // 10, 32)),
        "pacing": _bounded(62 + min(length // 14, 25)),
        "language": _bounded(60 + min(length // 12, 30)),
        "originality": _bounded(64 + (7 if title and title not in {"未命名", "过短"} else 0) + min(length // 20, 18)),
        "safety": 35 if unsafe_hits else 92,
        "platform_fit": 45 if unsafe_hits else _bounded(65 + min(length // 15, 25)),
    }

    issues: list[str] = []
    suggestions: list[str] = []
    if length < 100:
        issues.append("篇幅过短，情节、人物与场景展开不足。")
        suggestions.append("扩写开端、转折和结尾，补充人物动机与环境细节。")
    if scores["plot"] < 80:
        issues.append("情节完整度不足，冲突或结尾回收不够清晰。")
        suggestions.append("强化核心冲突，并在结尾呼应开头意象。")
    if scores["character"] < 80:
        issues.append("人物动机和情感变化不够具体。")
        suggestions.append("增加关键动作、对白和选择来呈现人物弧光。")
    if unsafe_hits:
        issues.append("安全与平台适配存在风险词：" + "、".join(unsafe_hits))
        suggestions.append("删除或改写高风险表达，避免色情、血腥、仇恨或违规内容。")

    total = round(sum(scores[name] * settings.weights[name] for name in DIMENSIONS))
    decision = "approved" if total >= settings.approval_threshold and scores["safety"] >= 80 else "rewrite"
    return ReviewResult(total, scores, issues, suggestions, decision)


def _mock_rewrite(title: str, content: str, review: ReviewResult) -> str:
    if any("安全" in issue or "风险词" in issue for issue in review.issues):
        # Deterministically leave unresolved risk text in place so dry-run can
        # exercise the needs_human path after the configured retry limit.
        return content + "\n\n【AI重写尝试】已尝试调整表达，但仍需人工判断高风险内容是否可保留。"

    clean_title = title.strip("《》") or "雨夜归人"
    return (
        f"《{clean_title}》\n\n"
        "雨停在凌晨两点，老街尽头的灯牌还亮着。主人公攥着一封没有寄出的信，"
        "回到阔别多年的旧书店。书店老板没有追问，只把热茶推到他面前，像早就"
        "知道这个夜晚需要一点安静的善意。\n\n"
        "随着一张夹在书页里的旧车票被发现，他终于明白家人当年的沉默并非责备，"
        "而是给他保留重新开始的位置。故事在雨声、茶香和未说出口的歉意中推进，"
        "人物从逃避走向承担，冲突也由误解转为和解。\n\n"
        "天亮时，他帮老板修好漏风的窗，又把门口积水扫向街边。第一束光落在门牌上，"
        "他决定留下来，把错过的日子一点点补回。这个结尾呼应开头的雨夜，也让人物"
        "完成清晰而温暖的转变。"
    )


def _live_review(title: str, content: str, settings: AIReviewSettings) -> ReviewResult:
    prompt = (
        "请从 plot、character、pacing、language、originality、safety、platform_fit "
        "7 个维度审核以下中文短篇小说。只返回 JSON，字段必须包含 total_score、"
        "dimension_scores、issues、suggestions、decision。decision 只能是 approved 或 rewrite。"
        f"通过阈值：{settings.approval_threshold}/100。\n标题：{title}\n正文：{content}"
    )
    raw = _call_deepseek(prompt, settings)
    data = _extract_json(raw)
    return _review_result_from_mapping(data, settings)


def _live_rewrite(title: str, content: str, review: ReviewResult, settings: AIReviewSettings) -> str:
    prompt = (
        "请根据审核问题重写中文短篇小说，保留标题核心意象，提高情节、人物、节奏、语言、原创性、"
        "安全和平台适配。只返回重写后的小说正文。\n"
        f"标题：{title}\n问题：{json.dumps(review.issues, ensure_ascii=False)}\n"
        f"建议：{json.dumps(review.suggestions, ensure_ascii=False)}\n原文：{content}"
    )
    return _call_deepseek(prompt, settings).strip()


def _call_deepseek(prompt: str, settings: AIReviewSettings) -> str:
    url = f"{settings.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": "你是严谨的中文小说编辑和内容安全审核员。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": settings.temperature,
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.timeout_seconds) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        return str(response_data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, ValueError, HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"DeepSeek AI review request failed: {exc}") from exc


def _extract_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        raise ValueError("AI review response did not contain JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI review JSON root must be an object")
    return data


def _review_result_from_mapping(data: dict[str, Any], settings: AIReviewSettings) -> ReviewResult:
    raw_scores = data.get("dimension_scores") or {}
    if not isinstance(raw_scores, dict):
        raw_scores = {}
    scores = {name: _bounded(int(raw_scores.get(name, 0))) for name in DIMENSIONS}
    total = int(data.get("total_score") or round(sum(scores[name] * settings.weights[name] for name in DIMENSIONS)))
    issues = [str(item) for item in data.get("issues", []) if str(item).strip()]
    suggestions = [str(item) for item in data.get("suggestions", []) if str(item).strip()]
    decision = str(data.get("decision") or "rewrite")
    if decision not in {"approved", "rewrite", "needs_human"}:
        decision = "approved" if total >= settings.approval_threshold else "rewrite"
    if total < settings.approval_threshold and decision == "approved":
        decision = "rewrite"
    return ReviewResult(_bounded(total), scores, issues, suggestions, decision)


def _persist_review_attempt(
    db_path: str | Path,
    story_id: int,
    *,
    title: str,
    content: str,
    retry_count: int,
    score: int,
    status: str,
    review_notes: str,
) -> None:
    import sqlite3

    with sqlite3.connect(Path(db_path)) as connection:
        connection.execute(
            """
            UPDATE stories
            SET title = ?,
                content = ?,
                status = ?,
                retry_count = ?,
                score = ?,
                review_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, content, status, retry_count, score, review_notes, story_id),
        )


def _normalize_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return DEFAULT_DIMENSION_WEIGHTS.copy()
    weights = DEFAULT_DIMENSION_WEIGHTS.copy()
    for name in DIMENSIONS:
        try:
            weights[name] = float(raw.get(name, weights[name]))
        except (TypeError, ValueError):
            pass
    total = sum(weights.values()) or 1.0
    return {name: weights[name] / total for name in DIMENSIONS}


def _config_with_threshold(config: LoadedConfig, threshold: int) -> LoadedConfig:
    data = dict(config.data)
    audit = dict(data.get("audit", {}))
    audit["approval_threshold"] = threshold
    data["audit"] = audit
    return LoadedConfig(data=data, path=config.path, warnings=config.warnings)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bounded(value: int) -> int:
    return max(0, min(100, int(value)))


__all__ = [
    "AIReviewSettings",
    "BatchReviewResult",
    "DEFAULT_DIMENSION_WEIGHTS",
    "DIMENSIONS",
    "ReviewResult",
    "StoryReviewSummary",
    "load_ai_review_settings",
    "mock_review",
    "review_story",
    "review_story_in_database",
    "rewrite_story",
    "run_review_batch",
]
