"""L4 — 4-dimension AI review for long novel chapters.

Dimensions:
1. 故事架构 (story-architect) — hooks, pacing, structure, emotional rhythm
2. 角色对话 (character-designer) — character voice, dialogue quality, arc consistency
3. 文字质量 (narrative-writer) — banned words, AI traces, format compliance
4. 事实一致 (consistency-checker) — character attributes, world rules, foreshadowing

Each dimension outputs: APPROVE / CONCERNS / REJECT + findings + recommendations
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from generator.api_client import DeepSeekClient

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    p = _PROMPTS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_prompt_template(name: str, fallback: str) -> str:
    text = _load_prompt(name).strip()
    return text or fallback


class _PromptValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_prompt_template(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format_map(_PromptValues({k: "" if v is None else v for k, v in values.items()}))
    except Exception as exc:
        logger.warning("review prompt template render failed: %s", exc)
        return template


def _llm(client: DeepSeekClient, system: str, user: str, thinking: bool = False) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.text if hasattr(completion, "text") else str(completion)


# ── Dimension 1: Story Architecture ───────────────────────────────────


def review_architecture(
    client: DeepSeekClient,
    chapter_content: str,
    outline: str = "",
    chapter_number: int = 1,
) -> dict[str, Any]:
    """Review hooks, pacing, structure, emotional rhythm."""
    system = (
        "你是一位网文故事架构审查专家。从故事架构角度审查章节质量。"
        "输出JSON格式：{\"verdict\": \"APPROVE|CONCERNS|REJECT\", \"findings\": [\"问题1\", ...], \"recommendations\": [\"建议1\", ...]}"
    )
    user = f"""审查第{chapter_number}章的架构质量。

本章细纲：
{outline[:1000] if outline else '（无）'}

正文（前2000字）：
{chapter_content[:2000]}

检查项目：
1. 章首钩子：前300字是否有冲突/悬念/钩子吸引读者？
2. 爽点设计：本章爽点是否清晰？爽感是否到位？
3. 章尾钩子：结尾是否有翻页动力？
4. 情绪节奏：情绪起伏是否合理？有无连续多段无变化？
5. 结构完整性：起承转合是否完整？

请输出JSON格式审查结果。"""
    return _parse_review_result(_llm(client, system, user))


# ── Dimension 2: Character & Dialogue ─────────────────────────────────


def review_characters(
    client: DeepSeekClient,
    chapter_content: str,
    character_profiles: str = "",
) -> dict[str, Any]:
    """Review character voice consistency and dialogue quality."""
    system = (
        "你是一位角色与对话审查专家。从角色塑造和对话质量角度审查章节。"
        "输出JSON格式：{\"verdict\": \"APPROVE|CONCERNS|REJECT\", \"findings\": [\"问题1\", ...], \"recommendations\": [\"建议1\", ...]}"
    )
    user = f"""审查章节的角色和对话质量。

角色设定：
{character_profiles[:1500] if character_profiles else '（无设定文件）'}

正文：
{chapter_content[:2500]}

检查项目：
1. 角色语言风格一致性：不同角色说话方式是否区分明显？
2. 对话质量：对话是否自然？有无AI味的千篇一律？
3. 角色行为动机：角色行为是否符合其设定动机？
4. 人物弧线：本章是否推进了角色成长？
5. 对话信息控制：对话是否有效传递信息而非纯说明？

请输出JSON格式审查结果。"""
    return _parse_review_result(_llm(client, system, user))


# ── Dimension 3: Writing Quality ──────────────────────────────────────


def review_writing_quality(
    client: DeepSeekClient,
    chapter_content: str,
) -> dict[str, Any]:
    """Review banned words, AI traces, format compliance."""
    system = (
        "你是一位网文文字质量审查专家。从文字质量和格式角度审查章节。"
        "输出JSON格式：{\"verdict\": \"APPROVE|CONCERNS|REJECT\", \"findings\": [\"问题1\", ...], \"recommendations\": [\"建议1\", ...], \"ai_level\": \"none|mild|moderate|severe\"}"
    )
    user = f"""审查章节的文字质量。

正文：
{chapter_content[:3000]}

检查项目：
1. AI味检测：是否存在AI高频词（仿佛/似乎/不禁/微微/淡淡/一丝/心中一动/眼中闪过/嘴角勾起等）？
2. 格式合规：段落是否一段一句？有无过长段落？
3. 节奏均匀度：有无连续多段无情绪变化？
4. 禁用词扫描：是否存在套话/陈词滥调？
5. AI味分级：无/轻度/中度/重度

请输出JSON格式审查结果（包含ai_level字段）。"""
    return _parse_review_result(_llm(client, system, user))


# ── Dimension 4: Consistency ──────────────────────────────────────────


def review_consistency(
    client: DeepSeekClient,
    chapter_content: str,
    work_dir: Path,
    chapter_number: int,
) -> dict[str, Any]:
    """Check character attribute consistency, world rules, foreshadowing, timeline."""
    # Collect context for comparison
    context_parts = []
    prev_chapter_text = ""
    if chapter_number > 1:
        from generator.long_novel.l2_chapter_write import find_chapter_text
        prev_path = find_chapter_text(work_dir, chapter_number - 1)
        if prev_path is not None:
            prev_chapter_text = prev_path.read_text(encoding="utf-8")[-1500:]
            context_parts.append(f"前章结尾：{prev_chapter_text}")

    for check_file in ["追踪/伏笔.md", "追踪/时间线.md", "追踪/角色状态.md", "设定/角色/角色设定.md"]:
        p = work_dir / check_file
        if p.exists():
            text = p.read_text(encoding="utf-8")
            context_parts.append(f"{check_file}：{text[:1000]}")

    system = (
        "你是一位小说一致性审查专家。使用grep思维逐条对比前后设定，查找事实冲突。"
        "输出JSON格式：{\"verdict\": \"APPROVE|CONCERNS|REJECT\", \"findings\": [\"问题1\", ...], \"recommendations\": [\"建议1\", ...]}"
    )
    user = f"""审查第{chapter_number}章的事实一致性。

已有设定和上下文：
{chr(10).join(context_parts)}

本章正文：
{chapter_content[:2000]}

检查项目：
1. 角色属性一致：本章角色的位置/能力/关系是否与前文一致？
2. 世界规则：本章是否违反已建立的世界观规则？
3. 伏笔：本章埋设/回收的伏笔是否与伏笔表一致？
4. 时间线自洽：事件时间是否与时间线记录一致？
5. 事实冲突：是否存在明显的自相矛盾？

请输出JSON格式审查结果。"""
    return _parse_review_result(_llm(client, system, user))


# ── Helpers ───────────────────────────────────────────────────────────


def _parse_review_result(text: str) -> dict[str, Any]:
    """Parse LLM output into structured review result."""
    try:
        # Try direct JSON parse
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    try:
        # Try extracting JSON block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except json.JSONDecodeError:
        pass
    # Fallback
    verdict = "CONCERNS"
    if "APPROVE" in text:
        verdict = "APPROVE"
    elif "REJECT" in text:
        verdict = "REJECT"
    return {"verdict": verdict, "findings": [text[:500]], "recommendations": [], "raw": text}


def _verdict_score(verdict: str) -> int:
    return {"APPROVE": 0, "CONCERNS": 1, "REJECT": 2}.get(verdict, 1)


def _score_from_verdict(verdict: str) -> int:
    return {"APPROVE": 90, "CONCERNS": 72, "REJECT": 45}.get(str(verdict).upper(), 72)


def _coerce_score(value: Any, fallback: int) -> int:
    try:
        score = int(round(float(value)))
    except Exception:
        score = fallback
    return max(0, min(100, score))


def _looks_like_actionable_issue(text: Any) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    positive_markers = (
        "无明显", "未见明显", "未发现", "没有明显", "暂无", "较清晰", "清晰",
        "合理", "自然", "有效", "已经", "能够", "形成", "建立", "推进",
        "完成", "符合", "到位", "增强", "营造", "成功", "衔接顺畅",
    )
    problem_markers = (
        "问题", "不足", "缺少", "缺乏", "不够", "不清", "混乱", "冲突",
        "矛盾", "牵强", "突兀", "崩", "需要", "必须", "建议", "风险",
        "可能导致", "未能", "没有交代", "没有明确", "偏弱", "较弱",
        "不一致", "不统一", "遗漏", "断裂",
    )
    if any(p in s for p in positive_markers) and not any(p in s for p in problem_markers):
        return False
    return any(p in s for p in problem_markers)


def _looks_like_major_recommendation(text: Any) -> bool:
    s = str(text or "").strip()
    if not s or s in {"无", "暂无", "无建议", "无需修改"}:
        return False
    major_markers = (
        "必须", "需要", "应当", "统一", "修正", "补足", "补充交代",
        "删除", "替换", "避免", "不能", "冲突", "矛盾", "不一致",
        "不统一", "崩", "断裂", "重写",
    )
    minor_markers = ("可适当", "可以适当", "可进一步", "建议适当", "略微")
    if any(m in s for m in major_markers):
        return True
    return not any(m in s for m in minor_markers)


def _drop_unsupported_first_chapter_continuity(text: Any, has_previous_chapter: bool) -> bool:
    if has_previous_chapter:
        return False
    s = str(text or "")
    prior_terms = ("前文", "上一章", "前章", "长期记忆", "已写正文", "承接前")
    internal_terms = ("本章内部", "设定", "自相矛盾", "不一致", "不统一")
    return any(t in s for t in prior_terms) and not any(t in s for t in internal_terms)


def _calibrate_dimension_score(verdict: str, raw_score: Any, issue_count: int, minor_count: int = 0) -> int:
    verdict = str(verdict or "CONCERNS").upper()
    fallback = {"APPROVE": 90, "CONCERNS": 74, "REJECT": 45}.get(verdict, 74)
    score = _coerce_score(raw_score, fallback)
    if verdict == "APPROVE":
        score = max(80, min(100, score))
        if minor_count:
            score = min(score, 89) - min(5, minor_count)
    elif verdict == "CONCERNS":
        score = min(79, score) - min(10, issue_count * 2)
        score = max(68, score)
    else:
        score = min(59, score) - min(12, issue_count * 2)
        score = max(20, score)
    return max(0, min(100, int(score)))


def _normalize_story_review(
    parsed: dict[str, Any],
    required: list[str],
    chapter_number: int = 0,
    has_previous_chapter: bool = True,
) -> dict[str, Any]:
    dims = parsed.get("dimensions")
    if not isinstance(dims, dict):
        dims = {}

    for key in required:
        if key not in dims or not isinstance(dims[key], dict):
            dims[key] = {
                "verdict": parsed.get("verdict", "CONCERNS"),
                "findings": parsed.get("findings", [])[:2],
                "recommendations": parsed.get("recommendations", [])[:2],
            }
        raw_findings = [str(x).strip() for x in (dims[key].get("findings") or []) if str(x).strip()]
        raw_recs = [str(x).strip() for x in (dims[key].get("recommendations") or []) if str(x).strip()]
        if key == "continuity":
            raw_findings = [x for x in raw_findings if not _drop_unsupported_first_chapter_continuity(x, has_previous_chapter)]
            raw_recs = [x for x in raw_recs if not _drop_unsupported_first_chapter_continuity(x, has_previous_chapter)]
        issue_findings: list[str] = []
        strengths = [str(x).strip() for x in (dims[key].get("strengths") or []) if str(x).strip()]
        for item in raw_findings:
            if _looks_like_actionable_issue(item):
                issue_findings.append(item)
            else:
                strengths.append(item)
        major_recs = [x for x in raw_recs if _looks_like_major_recommendation(x)]
        minor_recs = [x for x in raw_recs if x not in major_recs]
        issue_count = len(issue_findings) + len(major_recs)
        raw_verdict = str(dims[key].get("verdict") or "CONCERNS").upper()
        if raw_verdict not in {"APPROVE", "CONCERNS", "REJECT"}:
            raw_verdict = "CONCERNS"
        if raw_verdict == "REJECT" and issue_count:
            verdict = "REJECT"
        elif issue_count:
            verdict = "CONCERNS"
        else:
            verdict = "APPROVE"
        dims[key]["findings"] = issue_findings
        dims[key]["strengths"] = strengths[:6]
        dims[key]["recommendations"] = raw_recs
        dims[key]["verdict"] = verdict
        score_source = dims[key].get("score") if raw_verdict == verdict else None
        dims[key]["score"] = _calibrate_dimension_score(verdict, score_source, issue_count, len(minor_recs))
        dims[key]["pass_score"] = int(dims[key].get("pass_score") or 80)
        dims[key]["passed"] = bool(
            dims[key]["score"] >= dims[key]["pass_score"] and verdict == "APPROVE"
        )

    scores = [int(dims[k]["score"]) for k in required]
    avg_score = round(sum(scores) / max(1, len(scores)))
    pass_score = int(parsed.get("pass_score") or 80)
    verdicts = [dims[k].get("verdict", "CONCERNS") for k in required]
    if "REJECT" in verdicts or min(scores) < 60:
        overall = "REJECT"
    elif "CONCERNS" in verdicts or min(scores) < pass_score:
        overall = "CONCERNS"
    else:
        overall = "APPROVE"
    score = avg_score
    if overall == "REJECT":
        score = min(score, 59)
    elif overall == "CONCERNS":
        score = min(score, 79)
    else:
        score = max(score, pass_score)
    passed = bool(score >= pass_score and overall == "APPROVE" and min(scores) >= pass_score)
    return {
        "overall": overall,
        "score": score,
        "pass_score": pass_score,
        "passed": passed,
        "dimensions": dims,
        "recommendations": parsed.get("recommendations", []),
        "summary": parsed.get("summary") or f"正文审查完成：{overall} / {score}分",
    }


# ── Full Review ───────────────────────────────────────────────────────


def run_full_review(
    client: DeepSeekClient,
    chapter_content: str,
    work_dir: Path,
    chapter_number: int,
    outline: str = "",
) -> dict[str, Any]:
    """Run all 4 review dimensions and aggregate results."""
    # Load character profiles
    char_profiles = ""
    chars_dir = work_dir / "设定" / "角色"
    if chars_dir.exists():
        for f in chars_dir.iterdir():
            if f.suffix == ".md":
                char_profiles += f.read_text(encoding="utf-8")[:2000]

    results = {}

    # D1: Architecture
    results["architecture"] = review_architecture(
        client, chapter_content, outline, chapter_number
    )

    # D2: Characters
    results["characters"] = review_characters(
        client, chapter_content, char_profiles
    )

    # D3: Writing quality
    results["writing_quality"] = review_writing_quality(
        client, chapter_content
    )

    # D4: Consistency
    results["consistency"] = review_consistency(
        client, chapter_content, work_dir, chapter_number
    )

    # Aggregate
    verdicts = [r.get("verdict", "CONCERNS") for r in results.values()]
    all_findings = []
    all_recommendations = []
    for dim, r in results.items():
        for f in r.get("findings", []):
            all_findings.append({f"[{dim}]": f})
        for rec in r.get("recommendations", []):
            all_recommendations.append(f"[{dim}] {rec}")

    if "REJECT" in verdicts:
        overall = "REJECT"
    elif verdicts.count("CONCERNS") >= 2:
        overall = "CONCERNS"
    elif "CONCERNS" in verdicts:
        overall = "CONCERNS"
    else:
        overall = "APPROVE"

    return {
        "overall": overall,
        "verdicts": {dim: r.get("verdict", "?") for dim, r in results.items()},
        "findings": all_findings,
        "recommendations": all_recommendations,
        "ai_level": results.get("writing_quality", {}).get("ai_level", "unknown"),
        "details": results,
        "summary": f"审查完成：{overall}（架构={results['architecture'].get('verdict')}, 角色={results['characters'].get('verdict')}, 文字={results['writing_quality'].get('verdict')}, 一致={results['consistency'].get('verdict')}）",
    }


def _run_story_review_legacy(
    client: DeepSeekClient,
    chapter_content: str,
    work_dir: Path,
    chapter_number: int,
    outline: str = "",
) -> dict[str, Any]:
    """Run the writing-workbench review used before de-AI/finalizing."""
    context_parts: list[str] = []
    for rel in [
        "追踪/全书进展.md",
        "追踪/角色状态.md",
        "追踪/伏笔.md",
        "追踪/时间线.md",
        "追踪/续写约束.md",
        "设定/世界观/背景设定.md",
        "设定/角色/角色设定.md",
        "设定/关系.md",
        "大纲/大纲.md",
    ]:
        path = work_dir / rel
        if path.exists():
            context_parts.append(f"## {rel}\n{path.read_text(encoding='utf-8')[:1600]}")

    system = (
        "你是长篇网文正文审查编辑。只输出 JSON，不要输出解释。"
        "每个维度给出 verdict(APPROVE|CONCERNS|REJECT)、findings、recommendations。"
    )
    user = f"""请审查第{chapter_number}章。

本章细纲：
{outline[:2000] if outline else "（无）"}

设定与长期记忆：
{chr(10).join(context_parts)[:7000]}

正文：
{chapter_content[:6000]}

必须按以下六项审查：
1. continuity：连续性检查，是否承接前文和长期记忆。
2. logic：逻辑是否自洽，因果是否成立。
3. plot_progress：剧情是否推进，有没有原地踏步。
4. character_integrity：人设是否崩塌，人物语言、动机、关系是否沿用设定。
5. environment：环境是否恰当，世界观、场景、时代与力量体系是否冲突。
6. empathy：读者是否能共情，情绪铺垫、爽点、痛点是否成立。

输出 JSON 格式：
{{
  "overall": "APPROVE|CONCERNS|REJECT",
  "dimensions": {{
    "continuity": {{"verdict": "...", "findings": [], "recommendations": []}},
    "logic": {{"verdict": "...", "findings": [], "recommendations": []}},
    "plot_progress": {{"verdict": "...", "findings": [], "recommendations": []}},
    "character_integrity": {{"verdict": "...", "findings": [], "recommendations": []}},
    "environment": {{"verdict": "...", "findings": [], "recommendations": []}},
    "empathy": {{"verdict": "...", "findings": [], "recommendations": []}}
  }},
  "recommendations": []
}}"""
    parsed = _parse_review_result(_llm(client, system, user, thinking=True))
    dims = parsed.get("dimensions")
    required = [
        "continuity",
        "logic",
        "plot_progress",
        "character_integrity",
        "environment",
        "empathy",
    ]
    if not isinstance(dims, dict):
        dims = {}
    for key in required:
        if key not in dims or not isinstance(dims[key], dict):
            dims[key] = {
                "verdict": parsed.get("verdict", "CONCERNS"),
                "findings": parsed.get("findings", [])[:2],
                "recommendations": parsed.get("recommendations", [])[:2],
            }
    verdicts = [dims[k].get("verdict", "CONCERNS") for k in required]
    if "REJECT" in verdicts:
        overall = "REJECT"
    elif "CONCERNS" in verdicts:
        overall = "CONCERNS"
    else:
        overall = "APPROVE"
    return {
        "overall": parsed.get("overall") or overall,
        "dimensions": dims,
        "recommendations": parsed.get("recommendations", []),
        "summary": f"正文审查完成：{parsed.get('overall') or overall}",
    }

def run_story_review(
    client: DeepSeekClient,
    chapter_content: str,
    work_dir: Path,
    chapter_number: int,
    outline: str = "",
) -> dict[str, Any]:
    """Run the writing-workbench review used before de-AI/finalizing.

    This later definition intentionally normalizes older verdict-only review
    JSON into a scored quality gate so saved chapters and new chapters share
    the same frontend contract.
    """
    context_parts: list[str] = []
    context_roots = [
        work_dir / "追踪",
        work_dir / "设定",
        work_dir / "大纲",
        work_dir / "杩借釜",
        work_dir / "璁惧畾",
        work_dir / "澶х翰",
    ]
    seen: set[Path] = set()
    for root in context_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md"))[:40]:
            if path in seen:
                continue
            seen.add(path)
            try:
                rel = path.relative_to(work_dir)
            except ValueError:
                rel = path
            try:
                context_parts.append(f"## {rel}\n{path.read_text(encoding='utf-8')[:1400]}")
            except Exception:
                continue
    has_previous_chapter = False
    if chapter_number > 1:
        try:
            from generator.long_novel.l2_chapter_write import find_chapter_text
            has_previous_chapter = find_chapter_text(work_dir, chapter_number - 1) is not None
        except Exception:
            has_previous_chapter = chapter_number > 1

    system = _load_prompt_template("l4_story_review_system.txt", "You are the chief editor for a long serialized Chinese web novel. Return JSON only.")
    user_template = _load_prompt_template("l4_story_review_user.txt", "Review chapter {chapter_number}.\n\n{chapter_text}")
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "outline": outline[:2000] if outline else "(none)",
        "context": chr(10).join(context_parts)[:9000],
        "chapter_text": chapter_content[:7000],
        "continuity_rule": (
            "chapter 1: only check internal consistency and consistency with settings; do not penalize lack of previous chapters."
            if not has_previous_chapter else
            "connects with prior chapters and long memory."
        ),
    })
    parsed = _parse_review_result(_llm(client, system, user, thinking=True))
    required = [
        "continuity",
        "logic",
        "plot_progress",
        "character_integrity",
        "environment",
        "empathy",
    ]
    return _normalize_story_review(
        parsed,
        required,
        chapter_number=chapter_number,
        has_previous_chapter=has_previous_chapter,
    )


__all__ = [
    "review_architecture",
    "review_characters",
    "review_writing_quality",
    "review_consistency",
    "run_full_review",
    "run_story_review",
]
