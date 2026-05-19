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


def _llm(client: DeepSeekClient, system: str, user: str, thinking: bool = False) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.content if hasattr(completion, "content") else str(completion)


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
        text_dir = work_dir / "正文"
        for pattern in [f"第{chapter_number - 1:03d}章", f"第{chapter_number - 1}章"]:
            if text_dir.exists():
                for f in text_dir.iterdir():
                    if f.stem.startswith(pattern):
                        prev_chapter_text = f.read_text(encoding="utf-8")[-1500:]
                        context_parts.append(f"前章结尾：{prev_chapter_text}")
                        break

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

    worst = max(verdicts, key=lambda v: _verdict_score(v))
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


__all__ = [
    "review_architecture",
    "review_characters",
    "review_writing_quality",
    "review_consistency",
    "run_full_review",
]
