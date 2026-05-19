"""L2 — Chapter writing pipeline with context assembly and continuity check.

Flow: context_load → draft → expand → polish → deslop → continuity → update_state
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from generator.api_client import DeepSeekClient

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    p = _PROMPTS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _save_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _llm(client: DeepSeekClient, system: str, user: str, thinking: bool = False) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.content if hasattr(completion, "content") else str(completion)


def count_chinese_chars(text: str) -> int:
    import re
    return len(re.sub(r'[\s\n\r　]', '', text))


# ── Context Assembly ──────────────────────────────────────────────────


def assemble_context(
    work_dir: Path,
    chapter_number: int,
    chapter_title: str = "",
    target_words: int = 3000,
) -> dict[str, str]:
    """Assemble all available context for writing a chapter."""
    ctx: dict[str, str] = {}

    # Chapter outline
    outline_path = work_dir / "大纲" / f"细纲_第{chapter_number:03d}章.md"
    if outline_path.exists():
        ctx["outline"] = _read_file(outline_path)

    # Previous chapter (full text)
    if chapter_number > 1:
        # Try common filename patterns
        for pattern in [f"第{chapter_number - 1:03d}章", f"第{chapter_number - 1}章"]:
            prev_dir = work_dir / "正文"
            if prev_dir.exists():
                for f in prev_dir.iterdir():
                    if f.stem.startswith(pattern):
                        prev_text = _read_file(f)
                        ctx["prev_chapter_summary"] = prev_text[:600]
                        ctx["prev_chapter_last_paras"] = prev_text[-400:]
                        ctx["prev_chapter_full"] = prev_text
                        break

    # Foreshadowing
    foreshadow_path = work_dir / "追踪" / "伏笔.md"
    if foreshadow_path.exists():
        ctx["foreshadowing"] = _read_file(foreshadow_path)

    # Character states
    char_state_path = work_dir / "追踪" / "角色状态.md"
    if char_state_path.exists():
        ctx["character_states"] = _read_file(char_state_path)

    # Timeline
    timeline_path = work_dir / "追踪" / "时间线.md"
    if timeline_path.exists():
        ctx["timeline"] = _read_file(timeline_path)

    # Book premise
    premise_path = work_dir / "设定" / "题材定位.md"
    if premise_path.exists():
        ctx["premise"] = _read_file(premise_path)[:1000]

    return ctx


# ── Phase: Draft ──────────────────────────────────────────────────────


def run_draft(
    client: DeepSeekClient,
    work_dir: Path,
    chapter_number: int,
    chapter_title: str = "",
    target_words: int = 3000,
) -> str:
    """Generate the first draft of a chapter."""
    ctx = assemble_context(work_dir, chapter_number, chapter_title, target_words)

    system = _load_prompt("l2_draft_system.txt") or (
        "你是一位专业的网络小说作者。根据章纲和上下文，撰写一章高质量的正文。"
        "要求：节奏感强、钩子到位、爽点清晰、文字流畅自然。"
        "只输出正文内容，不要输出任何解释或元信息。"
    )

    user_parts = [f"请撰写第{chapter_number}章的正文。目标字数：{target_words}字。章节标题：{chapter_title or '（待定）'}"]

    if ctx.get("outline"):
        user_parts.append(f"\n## 本章细纲\n{ctx['outline']}")
    if ctx.get("prev_chapter_last_paras"):
        user_parts.append(f"\n## 上一章结尾（需要衔接）\n{ctx['prev_chapter_last_paras']}")
    if ctx.get("foreshadowing"):
        user_parts.append(f"\n## 当前伏笔状态（注意回收和埋设）\n{ctx['foreshadowing'][:1500]}")
    if ctx.get("character_states"):
        user_parts.append(f"\n## 角色当前状态\n{ctx['character_states'][:1000]}")
    if ctx.get("premise"):
        user_parts.append(f"\n## 全书基调\n{ctx['premise']}")

    user_parts.append("\n请直接输出正文，只输出小说内容，不要任何说明。")

    draft = _llm(client, system, "\n".join(user_parts), thinking=True)
    return draft.strip()


# ── Phase: Expand ─────────────────────────────────────────────────────


def run_expand(
    client: DeepSeekClient,
    draft: str,
    target_words: int = 3000,
) -> str:
    """Expand the draft if it's too short."""
    current_words = count_chinese_chars(draft)
    if current_words >= target_words * 0.9:
        return draft  # Already long enough

    shortfall = target_words - current_words
    system = "你是一位网文编辑。在保持原文风格和节奏的前提下，扩充章节内容。增加细节描写、对话、心理活动，但不要注水。只输出扩充后的完整正文。"
    user = f"""以下章节需要从{current_words}字扩充到约{target_words}字（需增加约{shortfall}字）。

原文：
{draft}

请扩充本章，增加场景细节、角色互动、内心独白等内容。保持原有的情节结构和爽点节奏。
只输出扩充后的完整正文。"""
    expanded = _llm(client, system, user)
    return expanded.strip()


# ── Phase: Polish ─────────────────────────────────────────────────────


def run_polish(
    client: DeepSeekClient,
    draft: str,
) -> str:
    """Polish the draft for language quality."""
    system = "你是一位资深网文编辑。精修以下章节，提升语言流畅度和文学质感。保持原意和风格，只做润色。只输出精修后的正文。"
    user = f"""请精修以下章节：

{draft}

润色要点：
1. 修正语病和不通顺的句子
2. 让段落节奏更流畅
3. 优化对话自然度
4. 增强画面感（用具体画面替代抽象描述）
5. 保持原有的情节结构和字数

只输出精修后的完整正文。"""
    polished = _llm(client, system, user)
    return polished.strip()


# ── Phase: De-AI ──────────────────────────────────────────────────────


def run_deslop(
    client: DeepSeekClient,
    draft: str,
) -> str:
    """Remove AI writing traces from the draft."""
    system = _load_prompt("l2_deslop_system.txt") or (
        "你是一位网文去AI味专家。清除文本中的AI写作痕迹，让文字读起来像真人写的网文。"
        "重点：删除'仿佛/似乎/不禁/微微/淡淡'等AI高频词、打破工整句式、增加口语化表达、"
        "用具体动作替代抽象心理描写。只输出去AI味后的正文。"
    )
    user = f"""请去除以下章节的AI味：

{draft}

要求：
1. 删除所有AI高频词（仿佛/似乎/不禁/微微/淡淡/一丝/闪过/心中一动等）
2. 打破工整的三段式/排比句式
3. 对话口语化，每个人说话方式不同
4. 用身体反应替代心理描写（"他紧张"→"他的手在抖"）
5. 缩短过长的段落（每段不超过3句话）

只输出去AI味后的完整正文。"""
    deslopped = _llm(client, system, user)
    return deslopped.strip()


# ── Phase: Continuity Check ───────────────────────────────────────────


def run_continuity_check(
    client: DeepSeekClient,
    work_dir: Path,
    chapter_number: int,
    draft: str,
) -> dict[str, Any]:
    """Check chapter for continuity issues with previous content."""
    ctx = assemble_context(work_dir, chapter_number)

    issues: list[dict[str, str]] = []

    # Check against previous chapter
    if ctx.get("prev_chapter_full"):
        system = "你是一位小说连续性检查专家。对比前后章节找出矛盾之处。只输出找到的问题，没有就输出'无问题'。"
        user = f"""请检查以下新章节与前文的连续性：

前文（上一章结尾）：
{ctx["prev_chapter_full"][-1000:]}

新章节：
{draft[:1500]}

检查项目：
1. 角色状态是否一致（位置、能力、受伤状态等）
2. 时间线是否衔接
3. 是否有明显的设定矛盾
4. 伏笔是否合理推进

请列出每条问题，格式：- [严重度: 高/中/低] 问题描述。无问题则输出"无问题"."""
        result = _llm(client, system, user)
        if "无问题" not in result:
            for line in result.split("\n"):
                if line.strip().startswith("-"):
                    issues.append({"type": "continuity", "detail": line.strip()})

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "issue_count": len(issues),
    }


# ── State Update ──────────────────────────────────────────────────────


def update_tracking_files(
    work_dir: Path,
    chapter_number: int,
    draft: str,
) -> None:
    """Update foreshadowing, timeline, character state, and context after writing."""
    tracking_dir = work_dir / "追踪"
    tracking_dir.mkdir(parents=True, exist_ok=True)

    # Update context
    context_path = tracking_dir / "上下文.md"
    summary = draft[:300].replace("\n", " ")
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_file(
        context_path,
        f"## 写作上下文\n\n"
        f"- 当前进度：第{chapter_number}章已完成\n"
        f"- 字数：{count_chinese_chars(draft)}字\n"
        f"- 上一章摘要：{summary}...\n"
        f"- 上次更新时间：{now}\n"
        f"- 下一章：第{chapter_number + 1}章\n",
    )

    # Update timeline
    timeline_path = tracking_dir / "时间线.md"
    existing = _read_file(timeline_path)
    _save_file(timeline_path, existing + f"\n- 第{chapter_number}章：{now}")

    logger.info("Tracking files updated for chapter %d", chapter_number)


# ── Full Pipeline ─────────────────────────────────────────────────────


def run_full_chapter(
    client: DeepSeekClient,
    work_dir: Path,
    chapter_number: int,
    chapter_title: str = "",
    target_words: int = 3000,
    skip_continuity: bool = False,
) -> dict[str, Any]:
    """Run the complete chapter writing pipeline."""
    logger.info("Starting chapter %d: %s", chapter_number, chapter_title)

    # 1. Draft
    draft = run_draft(client, work_dir, chapter_number, chapter_title, target_words)
    draft_words = count_chinese_chars(draft)
    logger.info("Draft: %d words", draft_words)

    # 2. Expand if needed
    if draft_words < target_words * 0.9:
        draft = run_expand(client, draft, target_words)
        draft_words = count_chinese_chars(draft)
        logger.info("Expanded: %d words", draft_words)

    # 3. Polish
    polished = run_polish(client, draft)
    polished_words = count_chinese_chars(polished)

    # 4. De-AI
    final = run_deslop(client, polished)
    final_words = count_chinese_chars(final)

    # 5. Continuity check
    continuity = None
    if not skip_continuity and chapter_number > 1:
        continuity = run_continuity_check(client, work_dir, chapter_number, final)

    # 6. Save
    text_dir = work_dir / "正文"
    text_dir.mkdir(parents=True, exist_ok=True)
    safe_title = chapter_title or f"第{chapter_number}章"
    draft_path = text_dir / f"第{chapter_number:03d}章_{safe_title}.md"
    _save_file(draft_path, final)

    # 7. Update tracking
    update_tracking_files(work_dir, chapter_number, final)

    return {
        "chapter_number": chapter_number,
        "draft_words": draft_words,
        "polished_words": polished_words,
        "final_words": final_words,
        "draft_path": str(draft_path),
        "continuity": continuity,
        "status": "draft",
    }


__all__ = [
    "assemble_context",
    "count_chinese_chars",
    "run_full_chapter",
    "run_draft",
    "run_expand",
    "run_polish",
    "run_deslop",
    "run_continuity_check",
    "update_tracking_files",
]
