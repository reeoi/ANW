"""审查质量门：去 AI 评分、story review 归一化、审查文本摘要辅助。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_EXPAND_AUTO_SKIP_WORDS = 3000

_REVIEW_DIM_LABELS = {
    "continuity": "连续性",
    "logic": "逻辑",
    "plot_progress": "剧情推进",
    "character_integrity": "人物一致性",
    "environment": "环境/设定",
    "empathy": "共情",
    "architecture": "故事架构",
    "characters": "角色对话",
    "writing_quality": "文字质量",
    "consistency": "事实一致性",
}


def _score_deai_result(text: str) -> dict[str, Any]:
    """Build a local quality gate for the de-AI step."""
    def _local_char_count(value: str) -> int:
        return sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")

    pass_score = 82
    score = 88
    findings: list[str] = ["已完成本地去 AI 改写，并通过文本特征质量门评估。"]
    recommendations: list[str] = []
    ai_phrases = [
        "仿佛", "似乎", "微微", "淡淡", "不由得", "心中一动", "眼中闪过",
        "嘴角勾起", "一股", "某种", "复杂的情绪",
    ]
    hits = [phrase for phrase in ai_phrases if phrase in text]
    if hits:
        score -= min(18, len(hits) * 3)
        findings.append("仍有高频 AI 味表达：" + " / ".join(hits[:8]))
        recommendations.append("用具体动作、对白和感官细节替换泛泛的情绪标签。")

    paragraph_count = max(1, len([p for p in text.splitlines() if p.strip()]))
    avg_paragraph_len = _local_char_count(text) / paragraph_count
    if avg_paragraph_len > 260:
        score -= 6
        findings.append("部分段落偏长，阅读节奏可能显得整齐或说明感过强。")
        recommendations.append("拆分长段，穿插动作、环境反馈和短对白。")
    if text.count("。") and text.count("，") / max(1, text.count("。")) > 5:
        score -= 5
        findings.append("长句比例偏高，句式层次容易显得机械。")
        recommendations.append("把连续说明句改成短句、停顿和人物反应。")

    score = max(0, min(100, int(score)))
    if score >= pass_score:
        verdict = "APPROVE"
    else:
        verdict = "CONCERNS" if score >= 65 else "REJECT"
    return {
        "verdict": verdict,
        "score": score,
        "pass_score": pass_score,
        "passed": score >= pass_score,
        "pending": False,
        "source": "local_text_quality",
        "findings": findings,
        "recommendations": recommendations,
        "summary": f"去 AI 质量门：{verdict} / 本地文本特征 / {score}分",
    }


def _normalize_review_gate(review: dict[str, Any], chapter_number: int = 0) -> dict[str, Any]:
    required = ["continuity", "logic", "plot_progress", "character_integrity", "environment", "empathy"]
    try:
        from generator.long_novel.l4_review import _normalize_story_review
        has_previous_chapter = chapter_number > 1
        return _normalize_story_review(
            review,
            required,
            chapter_number=chapter_number,
            has_previous_chapter=has_previous_chapter,
        )
    except Exception:
        logger.exception("normalize_review_gate_fallback chapter=%s", chapter_number)
    dims = review.get("dimensions") if isinstance(review.get("dimensions"), dict) else {}
    verdict_score = {"APPROVE": 88, "CONCERNS": 70, "REJECT": 45}
    for key in required:
        dim = dims.get(key) if isinstance(dims.get(key), dict) else {}
        verdict = str(dim.get("verdict") or "CONCERNS").upper()
        if verdict not in verdict_score:
            verdict = "CONCERNS"
        issue_count = len(dim.get("findings") or []) + len(dim.get("recommendations") or [])
        try:
            score = int(round(float(dim.get("score"))))
        except Exception:
            score = verdict_score[verdict]
        if verdict == "APPROVE":
            score = max(80, min(100, score))
            if issue_count:
                score = min(score, 89) - min(8, issue_count * 2)
        elif verdict == "CONCERNS":
            score = min(79, score) - min(10, issue_count * 2)
            score = max(60, score)
        else:
            score = min(59, score) - min(12, issue_count * 2)
            score = max(20, score)
        dim["score"] = max(0, min(100, score))
        dim["verdict"] = verdict
        dim["pass_score"] = int(dim.get("pass_score") or 80)
        dim["passed"] = bool(int(dim.get("score") or 0) >= dim["pass_score"] and verdict == "APPROVE")
        dims[key] = dim
    scores = [int((dims.get(k) or {}).get("score") or 72) for k in required]
    verdicts = [str((dims.get(k) or {}).get("verdict") or "CONCERNS").upper() for k in required]
    pass_score = int(review.get("pass_score") or 80)
    if "REJECT" in verdicts or min(scores) < 60:
        overall = "REJECT"
    elif "CONCERNS" in verdicts or min(scores) < pass_score:
        overall = "CONCERNS"
    else:
        overall = "APPROVE"
    score = round(sum(scores) / max(1, len(scores)))
    if overall == "REJECT":
        score = min(score, 59)
    elif overall == "CONCERNS":
        score = min(score, 79)
    else:
        score = max(score, pass_score)
    review["overall"] = overall
    review["dimensions"] = dims
    review["pass_score"] = pass_score
    review["score"] = int(score)
    review["passed"] = bool(review["score"] >= pass_score and overall == "APPROVE" and min(scores) >= pass_score)
    return review


def _review_recommendation_text(review: dict[str, Any]) -> str:
    lines: list[str] = []
    for rec in review.get("recommendations") or []:
        lines.append(str(rec))
    for name, dim in (review.get("dimensions") or {}).items():
        for item in dim.get("findings") or []:
            lines.append(f"[{name} 问题] {item}")
        for item in dim.get("recommendations") or []:
            lines.append(f"[{name} 建议] {item}")
    return "\n".join(f"- {line}" for line in lines if str(line).strip())[:6000]


def _review_issue_count(review: dict[str, Any]) -> int:
    count = len([x for x in review.get("recommendations") or [] if str(x).strip()])
    for dim in (review.get("dimensions") or {}).values():
        if not isinstance(dim, dict):
            continue
        count += len([x for x in dim.get("findings") or [] if str(x).strip()])
        count += len([x for x in dim.get("recommendations") or [] if str(x).strip()])
    return count


def _expand_skip_threshold(target_words: Any) -> int:
    """Return the configured expansion threshold, defaulting to 3000 words."""
    try:
        threshold = int(target_words or _EXPAND_AUTO_SKIP_WORDS)
    except (TypeError, ValueError):
        threshold = _EXPAND_AUTO_SKIP_WORDS
    return max(1, threshold)


def _short_review_text(value: Any, *, limit: int = 90) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _review_blocking_reasons(review: dict[str, Any], *, limit: int = 3) -> list[str]:
    """Return concise reasons explaining why review/rewrite is blocked."""
    reasons: list[str] = []
    dims = review.get("dimensions") if isinstance(review, dict) else {}
    if isinstance(dims, dict):
        for key, dim in dims.items():
            if not isinstance(dim, dict):
                continue
            verdict = str(dim.get("verdict") or "").upper()
            score = int(dim.get("score") or 0)
            pass_score = int(dim.get("pass_score") or review.get("pass_score") or 80)
            passed = bool(dim.get("passed"))
            if passed and verdict == "APPROVE" and score >= pass_score:
                continue
            label = _REVIEW_DIM_LABELS.get(str(key), str(key))
            detail = ""
            for item in (dim.get("findings") or []) + (dim.get("recommendations") or []):
                detail = _short_review_text(item)
                if detail:
                    break
            score_text = f"{score}/{pass_score}" if score else f"{verdict or '未通过'}"
            reasons.append(f"{label}{score_text}：{detail or (verdict or '未通过')}")
            if len(reasons) >= limit:
                return reasons

    for item in review.get("recommendations") or []:
        detail = _short_review_text(item)
        if detail:
            reasons.append(detail)
        if len(reasons) >= limit:
            return reasons

    summary = _short_review_text(review.get("summary") or review.get("overall") or "审查未通过")
    return reasons or [summary]


def _review_rewrite_reason(review: dict[str, Any]) -> str:
    return "；".join(_review_blocking_reasons(review))
