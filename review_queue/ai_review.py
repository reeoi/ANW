"""AI review and bounded rewrite workflow for c_pipeline stories.

Phase E note:
    Decision #31 (R2): when the 7-dimension review falls below the
    approval threshold (decision T2 → 90), retry by re-running Phase 4
    (polish) and Phase 5 (deslop) only — handled by
    ``generator.c_pipeline.rewrite.rerun_phase_4_5``. Up to
    ``audit.max_rewrite_attempts`` (=3) reruns are attempted; each
    attempt re-reads the freshly written ``final_content_path`` before
    re-reviewing. After all attempts fail the story is escalated to
    ``status='needs_human'`` for human review.
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
from review_queue.db import (
    get_story,
    list_reviewable_stories,
    update_story_ai_review,
    update_story_status,
)


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

    approval_threshold: int = 90
    max_rewrite_attempts: int = 3
    rewrite_strategy: str = "phase_4_5_only"
    model: str = "deepseek-v4-pro"
    temperature: float = 0.3
    timeout_seconds: int = 60
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    mock: bool = True
    dimension_weights: dict[str, float] | None = None
    metrics_db_path: str | None = None

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
        return self.total_score

    @property
    def passed(self) -> bool:
        return self.decision == "approved"

    def to_json(self) -> str:
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
    """Load AI-review thresholds, rewrite limit, and model params."""

    if config is None:
        config = load_from_environment()
    audit = config.data.get("audit", {}) if isinstance(config.data.get("audit", {}), dict) else {}
    deepseek = config.data.get("deepseek", {}) if isinstance(config.data.get("deepseek", {}), dict) else {}

    weights = _normalize_weights(audit.get("dimensions"))
    api_key = str(deepseek.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or "")
    mock = bool(deepseek.get("mock") or config.is_dry_run or not api_key)

    db_path: str | None = None
    try:
        from review_queue.db import get_database_path

        db_path = str(get_database_path(config))
    except Exception:  # pragma: no cover - never block review
        db_path = None

    return AIReviewSettings(
        approval_threshold=_env_int("ANP_AI_REVIEW_THRESHOLD", int(audit.get("approval_threshold") or 90)),
        max_rewrite_attempts=max(0, _env_int("ANP_MAX_REWRITE_ATTEMPTS", int(audit.get("max_rewrite_attempts") or 3))),
        rewrite_strategy=str(
            os.getenv("ANP_AI_REVIEW_REWRITE_STRATEGY")
            or audit.get("rewrite_strategy")
            or "phase_4_5_only"
        ),
        model=os.getenv("ANP_AI_REVIEW_MODEL") or str(audit.get("model") or deepseek.get("model") or "deepseek-v4-pro"),
        temperature=_env_float("ANP_AI_REVIEW_TEMPERATURE", float(audit.get("temperature") or 0.3)),
        timeout_seconds=_env_int(
            "ANP_AI_REVIEW_TIMEOUT_SECONDS",
            int(audit.get("timeout_seconds") or deepseek.get("timeout_seconds") or 120),
        ),
        api_key=api_key,
        base_url=str(deepseek.get("base_url") or "https://api.deepseek.com").rstrip("/"),
        mock=mock,
        dimension_weights=weights,
        metrics_db_path=db_path,
    )


def review_story(story, config: LoadedConfig | None = None, settings: AIReviewSettings | None = None) -> ReviewResult:
    """Review a story and return a parseable JSON-compatible 7-dimension result."""

    settings = settings or load_ai_review_settings(config)
    title, content = _extract_review_inputs(story)
    if settings.mock:
        return _mock_review(title, content, settings)
    try:
        return _live_review(title, content, settings)
    except Exception as exc:  # keep auto mode non-blocking if remote review fails
        result = _mock_review(title, content, settings)
        return ReviewResult(
            total_score=result.total_score,
            dimension_scores=result.dimension_scores,
            issues=result.issues + [f"DeepSeek 审核调用失败，已回退 mock：{exc}"],
            suggestions=result.suggestions,
            decision=result.decision,
        )


def review_story_in_database(
    db_path: str | Path,
    story_id: int,
    config: LoadedConfig | None = None,
) -> StoryReviewSummary:
    """Review one queued story and persist final state into c_pipeline schema.

    Phase E (decision #31, R2):
    - Run the 7-dimension review against the manuscript at
      ``final_content_path``.
    - If the decision is below threshold, rerun Phase 4-5 only via
      ``rerun_phase_4_5`` (lighter than ``run_pipeline(resume_from='phase_4')``)
      and re-review against the freshly written final manuscript. Loop
      up to ``settings.max_rewrite_attempts`` (default 3) attempts.
    - On approval at any attempt, persist ``status='approved'``.
    - On all attempts failing (or rewrite exception), persist
      ``status='needs_human'`` with the failure reason.
    """

    settings = load_ai_review_settings(config)
    story = get_story(db_path, story_id)
    if story is None:
        return StoryReviewSummary(story_id, "failed", 0, 0, [], [], "story not found")

    base_attempts = int(story.ai_review_attempts or 0)
    final_review = review_story(story, config=config, settings=settings)
    extra_attempts = 0
    rewrite_failure: str | None = None

    while final_review.decision != "approved" and extra_attempts < settings.max_rewrite_attempts:
        if settings.rewrite_strategy != "phase_4_5_only":
            rewrite_failure = (
                f"unsupported rewrite_strategy={settings.rewrite_strategy!r}; "
                "only 'phase_4_5_only' (R2) is implemented"
            )
            break
        try:
            _do_rewrite_phase_4_5(story_id, config=config)
        except Exception as exc:  # RewriteError or upstream LLM/IO failure
            rewrite_failure = (
                f"Phase 4-5 rerun #{extra_attempts + 1} failed: "
                f"{exc.__class__.__name__}: {exc}"
            )
            extra_attempts += 1
            break
        # Re-fetch and re-review against the regenerated final manuscript.
        story = get_story(db_path, story_id)
        if story is None:
            rewrite_failure = "story disappeared after rerun"
            extra_attempts += 1
            break
        extra_attempts += 1
        final_review = review_story(story, config=config, settings=settings)

    total_attempts = base_attempts + extra_attempts
    if final_review.decision == "approved":
        update_story_ai_review(db_path, story_id, final_review.total_score, total_attempts, status="approved")
        update_story_status(db_path, story_id, "approved", summary="AI 审核通过：" + final_review.to_json())
        return StoryReviewSummary(
            story_id=story_id,
            decision="approved",
            attempts=extra_attempts,
            final_score=final_review.total_score,
            issues=final_review.issues,
            suggestions=final_review.suggestions,
        )

    update_story_ai_review(db_path, story_id, final_review.total_score, total_attempts, status="needs_human")
    update_story_status(
        db_path,
        story_id,
        "needs_human",
        summary="AI 审核未通过，转人工复查：" + final_review.to_json(),
    )
    failure_text = rewrite_failure or ("; ".join(final_review.issues) or "score below threshold")
    return StoryReviewSummary(
        story_id=story_id,
        decision="needs_human",
        attempts=extra_attempts,
        final_score=final_review.total_score,
        issues=final_review.issues,
        suggestions=final_review.suggestions,
        failure_reason=failure_text,
    )


def _do_rewrite_phase_4_5(story_id: int, *, config: LoadedConfig | None) -> None:
    """Indirection for tests: monkeypatch this to stub the c_pipeline rerun."""

    from generator.c_pipeline.rewrite import rerun_phase_4_5

    if config is None:
        config = load_from_environment()
    rerun_phase_4_5(story_id, config=config)


def mock_review(content: str, threshold: int = 90) -> ReviewResult:
    """Backward-compatible deterministic mock review for dry-run workflows."""

    settings = AIReviewSettings(approval_threshold=threshold, mock=True)
    return _mock_review("未命名", content, settings)


def run_review_batch(
    db_path: str | Path,
    threshold: int | None = None,
    limit: int = 20,
    config: LoadedConfig | None = None,
) -> BatchReviewResult:
    """Review pending stories and update SQLite with c_pipeline AI review state."""

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


def _extract_review_inputs(story) -> tuple[str, str]:
    """Return (title, content_text) from either inline content or final manuscript file."""

    title = str(getattr(story, "title", "") or "")
    content = getattr(story, "content", None)
    if not content:
        reader = getattr(story, "read_final_content", None)
        if callable(reader):
            content = reader() or ""
    return title, str(content or "")


def _mock_review(title: str, content: str, settings: AIReviewSettings) -> ReviewResult:
    text = (content or "").strip()
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
        _record_review_usage(settings, response_data)
        return str(response_data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, ValueError, HTTPError, URLError, TimeoutError) as exc:
        _record_review_failure(settings, str(exc))
        raise RuntimeError(f"DeepSeek AI review request failed: {exc}") from exc


def _record_review_usage(settings: "AIReviewSettings", response_data: dict[str, Any]) -> None:
    try:
        from review_queue.metrics import estimate_cost_cny, record_api_usage

        db_path = getattr(settings, "metrics_db_path", None)
        if not db_path:
            return
        usage = response_data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        record_api_usage(
            db_path,
            provider="deepseek",
            model=settings.model,
            purpose="ai_review",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_cny=estimate_cost_cny(prompt_tokens, completion_tokens),
            success=True,
        )
    except Exception:  # pragma: no cover - never break review
        pass


def _record_review_failure(settings: "AIReviewSettings", error: str) -> None:
    try:
        from review_queue.metrics import record_api_usage

        db_path = getattr(settings, "metrics_db_path", None)
        if not db_path:
            return
        record_api_usage(
            db_path,
            provider="deepseek",
            model=settings.model,
            purpose="ai_review",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_cny=0.0,
            success=False,
            error=error[:500],
        )
    except Exception:  # pragma: no cover
        pass


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
    "run_review_batch",
]
