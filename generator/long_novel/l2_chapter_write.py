"""L2 — Chapter writing pipeline with context assembly and continuity check.

Flow: context_load → draft → expand → polish → deslop → continuity → update_state
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from generator.api_client import DeepSeekClient

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_DEAI_SKILL_ENV = "LONG_NOVEL_DEAI_SKILL_DIR"
_DEAI_SKILL_DEFAULT_DIR = _PROMPTS_DIR / "deai_skills"
_DEAI_SKILL_FILES = ("anti-ai-writing.md", "banned-words.md", "story-deslop.md")


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
        logger.warning("prompt template render failed: %s", exc)
        return template


_TRACKING_MEMORY_SYSTEM_FALLBACK = (
    "你是长篇网文的连续性记录员。你的任务是从已定稿正文中提取长期记忆，"
    "用于后续章节续写。只输出 JSON，不要解释。"
)

_TRACKING_MEMORY_USER_FALLBACK = """请为第{chapter_number}章提取追踪长期记忆。

已有追踪记录（节选）：
{tracking_context}

第{chapter_number}章定稿正文：
{chapter_text}

必须输出 JSON：
{{
  "summary_short": "120-220字章节摘要",
  "summary_long": "300-600字详细摘要，包含冲突、选择、结果、章尾状态",
  "timeline_events": ["按发生顺序列出3-8条事件"],
  "character_updates": ["角色名：位置/身份/关系/伤势/能力/秘密/情绪状态变化"],
  "foreshadowing_updates": ["新增/推进/回收/悬置的伏笔，说明状态"],
  "continuation_constraints": ["下一章必须遵守的续写约束"],
  "key_entities": ["后续要保持一致的地名/组织/道具/术语"]
}}"""

_CONTINUITY_SYSTEM_FALLBACK = (
    "你是一位小说连续性检查专家。对比前后章节与设定，找出矛盾之处。"
    "只输出找到的问题；没有问题就输出“无问题”。"
)

_CONTINUITY_USER_FALLBACK = """请检查以下新章节与前文的连续性：

前文（上一章结尾）：
{previous_chapter}

新章节：
{chapter_text}

角色设定：
{character_profiles}

世界观/大纲：
{book_outline}
{volume_outline}

检查项目：
1. 角色状态是否一致（位置、能力、受伤状态等）
2. 时间线是否衔接
3. 是否有明显的设定矛盾
4. 伏笔是否合理推进

请列出每条问题，格式：- [严重度: 高/中/低] 问题描述。无问题则输出：无问题。"""


def _load_deai_skill_pack() -> tuple[str, list[dict[str, Any]]]:
    """Load the local de-AI writing references that should be sent to the LLM."""
    skill_dir = Path(os.environ.get(_DEAI_SKILL_ENV) or _DEAI_SKILL_DEFAULT_DIR)
    sections: list[str] = []
    files: list[dict[str, Any]] = []
    for filename in _DEAI_SKILL_FILES:
        path = skill_dir / filename
        item: dict[str, Any] = {
            "name": filename,
            "path": str(path),
            "present": path.exists(),
            "chars": 0,
        }
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8").strip()
                item["chars"] = len(text)
                sections.append(f"## {filename}\n{text}")
            except Exception as exc:
                item["error"] = str(exc)
        files.append(item)
    pack = "\n\n".join(sections).strip()
    return pack, files


def _save_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── Per-chapter folder layout ─────────────────────────────────────────
# Each chapter lives at `<work_dir>/正文/第NNN章_标题/` and contains:
#   初稿.md, 扩写.md, 润色.md, 去AI.md, 正文.md, 审查.json
# Reads fall back to the legacy flat-file layout (`正文/第NNN章_标题.md`)
# so books written before this change keep working.

_FORBIDDEN_DIR_CHARS = re.compile(r'[<>:"/\\|?*]+')

CHAPTER_STEP_FILES: dict[str, str] = {
    "draft": "初稿.md",
    "expand": "扩写.md",
    "polish": "润色.md",
    "deslop": "去AI.md",
    "review": "审查.json",
}

CHAPTER_FINAL_FILENAME = "正文.md"


def _chapter_prefix(chapter_number: int) -> str:
    return f"第{chapter_number:03d}章"


def chapter_dir(work_dir: Path, chapter_number: int, chapter_title: str = "") -> Path:
    """Return (and create) the per-chapter folder under `正文/`.

    Looks up an existing folder matching `第NNN章*`; otherwise creates a new
    `第NNN章_<title>` folder. Does NOT rename existing folders even if title changes.
    """
    text_dir = Path(work_dir) / "正文"
    text_dir.mkdir(parents=True, exist_ok=True)
    prefix = _chapter_prefix(chapter_number)
    if text_dir.exists():
        for p in text_dir.iterdir():
            if p.is_dir() and p.name.startswith(prefix):
                return p
    safe_title = _FORBIDDEN_DIR_CHARS.sub("_", (chapter_title or "").strip()).strip("_")
    name = f"{prefix}_{safe_title}" if safe_title else prefix
    folder = text_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def chapter_final_path(work_dir: Path, chapter_number: int, chapter_title: str = "") -> Path:
    """Path to the final `正文.md` inside the chapter folder."""
    return chapter_dir(work_dir, chapter_number, chapter_title) / CHAPTER_FINAL_FILENAME


def find_chapter_text(work_dir: Path, chapter_number: int) -> Path | None:
    """Locate a chapter's final text file. New layout first, then legacy flat file."""
    text_dir = Path(work_dir) / "正文"
    if not text_dir.exists():
        return None
    prefix = _chapter_prefix(chapter_number)
    legacy_prefix_short = f"第{chapter_number}章"
    # New layout: 正文/第NNN章_*/正文.md
    for p in text_dir.iterdir():
        if p.is_dir() and (p.name.startswith(prefix) or p.name.startswith(legacy_prefix_short)):
            final = p / CHAPTER_FINAL_FILENAME
            if final.exists():
                return final
    # Legacy flat layout: 正文/第NNN章_*.md
    for p in text_dir.iterdir():
        if p.is_file() and p.suffix == ".md" and (p.stem.startswith(prefix) or p.stem.startswith(legacy_prefix_short)):
            return p
    return None


def _tracking_templates(target_chapters: int = 0) -> dict[str, str]:
    total = target_chapters or "待定"
    return {
        "全书进展.md": (
            "## 全书进展\n\n"
            "- 当前进度：第0章（尚未开始写作）\n"
            f"- 计划总章数：{total}\n"
            "- 当前阶段：开篇准备\n\n"
            "## 最近章节摘要\n\n"
            "（写完章节后自动更新）\n"
        ),
        "角色状态.md": (
            "## 角色状态\n\n"
            "| 角色 | 当前身份/位置 | 关系变化 | 伤势/能力/秘密 | 情绪状态 | 最后更新 |\n"
            "|---|---|---|---|---|---|\n\n"
            "## 章节更新\n\n"
            "（写完章节后自动补充，必要时可人工编辑）\n"
        ),
        "伏笔.md": (
            "## 伏笔状态表\n\n"
            "| ID | 内容 | 埋设章节 | 预计回收 | 状态 | 重要度 |\n"
            "|---|---|---|---|---|---|\n\n"
            "## 章节更新\n\n"
            "（写完章节后自动补充，必要时可人工整理为上方表格）\n"
        ),
        "时间线.md": (
            "## 事件时间线\n\n"
            "| 顺序 | 章节 | 事件 | 备注 |\n"
            "|---|---|---|---|\n\n"
            "## 章节更新\n\n"
        ),
        "续写约束.md": (
            "## 续写约束\n\n"
            "- 不能改名：核心角色姓名、身份、阵营需沿用设定文件。\n"
            "- 不能推翻设定：世界观规则、力量体系、既定关系不能重置。\n"
            "- 不能让已死亡角色无解释复活；如需反转，必须提前埋伏笔。\n"
            "- 不能忽略已写正文：下一章必须承接上一章结尾和当前角色状态。\n"
            "- 新增人物只能作为临时配角，不能替换已设定的核心人物功能。\n"
        ),
        "上下文.md": (
            "## 写作上下文\n\n"
            "- 当前进度：第0章（尚未开始写作）\n"
            f"- 计划总章数：{total}\n"
            "- 下一章：第1章\n"
        ),
    }


def ensure_tracking_files(work_dir: Path, target_chapters: int = 0) -> None:
    """Create missing long-memory tracking files without overwriting edits."""
    tracking_dir = work_dir / "追踪"
    tracking_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in _tracking_templates(target_chapters).items():
        path = tracking_dir / filename
        if not path.exists():
            _save_file(path, content)


def _upsert_section(path: Path, heading: str, body: str) -> None:
    existing = _read_file(path)
    section = f"{heading}\n\n{body.strip()}\n"
    pattern = re.compile(
        rf"^{re.escape(heading)}\n.*?(?=^## |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(section, existing).rstrip() + "\n"
    else:
        updated = existing.rstrip() + "\n\n" + section
    _save_file(path, updated)


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    return {}


def _extract_tracking_memory(
    client: DeepSeekClient | None,
    work_dir: Path,
    chapter_number: int,
    chapter_text: str,
) -> dict[str, Any]:
    fallback_summary = chapter_text[:500].replace("\n", " ").strip()
    fallback = {
        "summary_short": fallback_summary[:220],
        "summary_long": fallback_summary,
        "timeline_events": [fallback_summary[:160]] if fallback_summary else [],
        "character_updates": [],
        "foreshadowing_updates": [],
        "continuation_constraints": ["下一章必须承接本章结尾、角色状态与已暴露信息。"],
        "key_entities": [],
    }
    if client is None:
        return fallback

    tracking_dir = work_dir / "追踪"
    context_parts: list[str] = []
    for name in ("全书进展.md", "角色状态.md", "伏笔.md", "时间线.md", "续写约束.md"):
        path = tracking_dir / name
        if path.exists():
            context_parts.append(f"## 追踪/{name}\n{_read_file(path)[-1800:]}")

    system = _load_prompt_template("l2_tracking_memory_system.txt", _TRACKING_MEMORY_SYSTEM_FALLBACK)
    user_template = _load_prompt_template("l2_tracking_memory_user.txt", _TRACKING_MEMORY_USER_FALLBACK)
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "tracking_context": chr(10).join(context_parts)[:6000] or "（暂无）",
        "chapter_text": chapter_text[:9000],
    })
    try:
        data = _parse_json_object(_llm(client, system, user, thinking=True))
    except Exception:
        logger.exception("tracking_memory_extract_failed chapter=%s", chapter_number)
        return fallback

    result = dict(fallback)
    for key in result:
        value = data.get(key)
        if isinstance(result[key], list):
            result[key] = [str(x).strip() for x in (value if isinstance(value, list) else []) if str(x).strip()] or result[key]
        elif isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _llm(client: DeepSeekClient, system: str, user: str, thinking: bool = False) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.text if hasattr(completion, "text") else str(completion)


def count_chinese_chars(text: str) -> int:
    import re
    return len(re.sub(r'[\s\n\r　]', '', text))


_CHAPTER_HEADING_RE = re.compile(r"^\s*#{1,6}\s*第\s*\d+\s*章[^\n]*(?:\r?\n)+")


def ensure_chapter_heading(text: str, chapter_number: int) -> str:
    """Ensure saved prose starts with the requested markdown chapter heading."""
    body = str(text or "").lstrip("\ufeff \t\r\n")
    heading = f"# 第{int(chapter_number)}章"
    if _CHAPTER_HEADING_RE.match(body):
        body = _CHAPTER_HEADING_RE.sub(heading + "\n\n", body, count=1)
    else:
        body = heading + "\n\n" + body
    return body.rstrip() + "\n"


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

    # Higher-level outline context keeps chapter writing aligned with the plan.
    book_outline_path = work_dir / "大纲" / "大纲.md"
    if book_outline_path.exists():
        ctx["book_outline"] = _read_file(book_outline_path)[:2500]

    volume_outline_parts: list[str] = []
    outline_dir = work_dir / "大纲"
    if outline_dir.exists():
        try:
            from generator.long_novel.l0_book_setup import ensure_volume_outlines_split
            ensure_volume_outlines_split(work_dir)
        except Exception:
            pass
        for volume_path in sorted(outline_dir.glob("卷纲_*.md")):
            volume_outline_parts.append(f"--- 大纲/{volume_path.name} ---\n{_read_file(volume_path)[:1800]}")
    if volume_outline_parts:
        ctx["volume_outline"] = "\n\n".join(volume_outline_parts)[:5000]

    # Previous chapter (full text) — works for both new folder layout and legacy flat files
    if chapter_number > 1:
        prev_path = find_chapter_text(work_dir, chapter_number - 1)
        if prev_path is not None:
            prev_text = _read_file(prev_path)
            ctx["prev_chapter_summary"] = prev_text[:600]
            ctx["prev_chapter_last_paras"] = prev_text[-400:]
            ctx["prev_chapter_full"] = prev_text

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

    progress_path = work_dir / "追踪" / "全书进展.md"
    if progress_path.exists():
        ctx["book_progress"] = _read_file(progress_path)[:3000]

    constraints_path = work_dir / "追踪" / "续写约束.md"
    if constraints_path.exists():
        ctx["continuation_constraints"] = _read_file(constraints_path)[:2000]

    # Book premise
    premise_path = work_dir / "设定" / "题材定位.md"
    if premise_path.exists():
        ctx["premise"] = _read_file(premise_path)[:1000]

    # Core setup files must travel with every chapter draft; otherwise later
    # generations tend to reinvent names, relationships, and rules.
    world_path = work_dir / "设定" / "世界观" / "背景设定.md"
    if world_path.exists():
        ctx["world"] = _read_file(world_path)[:2000]

    character_path = work_dir / "设定" / "角色" / "角色设定.md"
    if character_path.exists():
        ctx["character_profiles"] = _read_file(character_path)[:4000]

    relationship_path = work_dir / "设定" / "关系.md"
    if relationship_path.exists():
        ctx["relationships"] = _read_file(relationship_path)[:1000]

    return ctx


# ── Phase: Draft ──────────────────────────────────────────────────────


_DRAFT_SYSTEM_FALLBACK = (
    "你是一位专业的网络小说作者。根据章纲和上下文，撰写一章高质量的正文。"
    "要求：节奏感强、钩子到位、爽点清晰、文字流畅自然。"
    "只输出正文内容，不要输出任何解释或元信息。"
)

_DRAFT_USER_FALLBACK = """请撰写第{chapter_number}章的正文。目标字数：{target_words}字。章节标题：{chapter_title}

## 连续性硬约束
- 必须沿用角色设定中的人物名、身份、动机、关系和语言风格，不得改名或替换人物。
- 必须服从世界观、全书大纲、卷纲和本章细纲；不得另起世界观、另设主线或重置人物关系。
- 若本章需要路人/工具人，可以临时新增，但不能顶替已设定核心角色。

{context_sections}

请直接输出正文，只输出小说内容，不要任何说明。"""

_EXPAND_SYSTEM_FALLBACK = (
    "你是一位网文编辑。在保持原文风格和节奏的前提下，扩充章节内容。"
    "增加细节描写、对话、心理活动，但不要注水。只输出扩充后的完整正文。"
)

_EXPAND_USER_FALLBACK = """以下章节需要从{current_words}字扩充到约{target_words}字（需增加约{shortfall}字）。

原文：
{draft}

请扩充本章，增加场景细节、角色互动、内心独白等内容。保持原有的情节结构和爽点节奏。
只输出扩充后的完整正文。"""

_POLISH_SYSTEM_FALLBACK = "你是一位资深网文编辑。精修以下章节，提升语言流畅度和文学质感。保持原意和风格，只做润色。只输出精修后的正文。"

_POLISH_USER_FALLBACK = """请精修以下章节：

{draft}

润色要点：
1. 修正语病和不通顺的句子
2. 让段落节奏更流畅
3. 优化对话自然度
4. 增强画面感（用具体画面替代抽象描述）
5. 保持原有的情节结构和字数

只输出精修后的完整正文。"""

_DESLOP_SYSTEM_FALLBACK = (
    "你是一位网文去AI味专家。清除文本中的AI写作痕迹，让文字读起来像真人写的网文。"
    "重点：删除'仿佛/似乎/不禁/微微/淡淡'等AI高频词、打破工整句式、增加口语化表达、"
    "用具体动作替代抽象心理描写。只输出去AI味后的正文。"
)

_DESLOP_USER_FALLBACK = """请去除以下章节的AI味：

{draft}

已扫描到的重点风险词：
{hit_text}

执行要求：
1. 按“去泛化 -> 去书面化 -> 回人味”三遍法处理。
2. 删除或替换禁用词，但不要为了去词破坏剧情信息。
3. 段落以 1-3 句为主；紧张/打斗用短句，对话尽量口语。
4. 用动作、对白、身体反应、感官细节替代心理告知。
5. 打散三连排比、论文体、总结体、章尾升华。
6. 不改变剧情、人设、关系、伏笔、章节推进。
7. 不得整段删除；总删除量不得超过原文 15%。
8. 不连续复用原文中 12 字以上的原句。

只输出去AI味后的完整正文。"""


def _draft_context_sections(ctx: dict[str, str]) -> str:
    parts: list[str] = []
    if ctx.get("outline"):
        parts.append(f"\n## 本章细纲\n{ctx['outline']}")
    if ctx.get("book_outline"):
        parts.append(f"\n## 全书大纲（用于校准主线，不要偏离）\n{ctx['book_outline']}")
    if ctx.get("volume_outline"):
        parts.append(f"\n## 卷纲（用于校准本卷人物线与事件线）\n{ctx['volume_outline']}")
    if ctx.get("prev_chapter_last_paras"):
        parts.append(f"\n## 上一章结尾（需要衔接）\n{ctx['prev_chapter_last_paras']}")
    if ctx.get("foreshadowing"):
        parts.append(f"\n## 当前伏笔状态（注意回收和埋设）\n{ctx['foreshadowing'][:1500]}")
    if ctx.get("character_states"):
        parts.append(f"\n## 角色当前状态\n{ctx['character_states'][:1000]}")
    if ctx.get("book_progress"):
        parts.append(f"\n## 全书长期进展记忆（必须承接）\n{ctx['book_progress']}")
    if ctx.get("continuation_constraints"):
        parts.append(f"\n## 续写约束（不可违反）\n{ctx['continuation_constraints']}")
    if ctx.get("character_profiles"):
        parts.append(f"\n## 角色设定（人物唯一来源，必须严格沿用）\n{ctx['character_profiles']}")
    if ctx.get("relationships"):
        parts.append(f"\n## 角色关系\n{ctx['relationships']}")
    if ctx.get("world"):
        parts.append(f"\n## 世界观设定（规则不可改）\n{ctx['world']}")
    if ctx.get("premise"):
        parts.append(f"\n## 全书基调\n{ctx['premise']}")
    return "\n".join(parts).strip()


def build_draft_prompt(
    work_dir: Path,
    chapter_number: int,
    chapter_title: str = "",
    target_words: int = 3000,
) -> dict[str, Any]:
    ctx = assemble_context(work_dir, chapter_number, chapter_title, target_words)
    system = _load_prompt_template("l2_draft_system.txt", _DRAFT_SYSTEM_FALLBACK)
    user_template = _load_prompt_template("l2_draft_user.txt", _DRAFT_USER_FALLBACK)
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title or "（待定）",
        "target_words": target_words,
        "context_sections": _draft_context_sections(ctx),
    })
    return {"system": system, "user": user, "context": ctx}


def build_expand_prompt(draft: str, target_words: int = 3000) -> dict[str, Any]:
    current_words = count_chinese_chars(draft)
    shortfall = max(0, target_words - current_words)
    system = _load_prompt_template("l2_expand_system.txt", _EXPAND_SYSTEM_FALLBACK)
    user_template = _load_prompt_template("l2_expand_user.txt", _EXPAND_USER_FALLBACK)
    user = _render_prompt_template(user_template, {
        "draft": draft,
        "current_words": current_words,
        "target_words": target_words,
        "shortfall": shortfall,
    })
    return {"system": system, "user": user, "current_words": current_words, "shortfall": shortfall}


def build_polish_prompt(draft: str) -> dict[str, Any]:
    system = _load_prompt_template("l2_polish_system.txt", _POLISH_SYSTEM_FALLBACK)
    user_template = _load_prompt_template("l2_polish_user.txt", _POLISH_USER_FALLBACK)
    user = _render_prompt_template(user_template, {"draft": draft})
    return {"system": system, "user": user}


def build_deslop_prompt(draft: str) -> dict[str, Any]:
    base_system = _load_prompt_template("l2_deslop_system.txt", _DESLOP_SYSTEM_FALLBACK)
    skill_pack, skill_files = _load_deai_skill_pack()
    system = base_system
    if skill_pack:
        system += (
            "\n\n以下是本项目本地去 AI 味 skills/参考资料，必须作为本轮改写依据。"
            "优先执行这些资料里的禁用词、反套路、句式打散和回人味方法：\n\n"
            f"{skill_pack}"
        )
    banned_terms = [
        "仿佛", "好像", "犹如", "宛若", "一丝", "一抹", "些许", "几分", "隐约",
        "深吸一口气", "缓缓", "不禁", "微微", "轻轻", "淡淡",
        "眼中闪过", "嘴角勾起", "眉头微皱", "眉眼低垂", "瞳孔微缩",
        "心中一动", "心头一震", "心下了然", "心中暗道", "心底泛起", "不由得",
        "不容置疑", "不易察觉", "显而易见", "毫无疑问", "不可否认",
        "不由自主", "情不自禁", "自然而然", "映入眼帘", "此时此刻", "沉声道", "说道",
    ]
    hits = [term for term in banned_terms if term in draft]
    hit_text = "、".join(hits[:30]) if hits else "未明显命中，但仍需检查工整句式、书面腔和升华结尾"
    user_template = _load_prompt_template("l2_deslop_user.txt", _DESLOP_USER_FALLBACK)
    user = _render_prompt_template(user_template, {"draft": draft, "hit_text": hit_text})
    return {"system": system, "user": user, "hits": hits, "skill_files": skill_files}


def run_draft(
    client: DeepSeekClient,
    work_dir: Path,
    chapter_number: int,
    chapter_title: str = "",
    target_words: int = 3000,
) -> str:
    """Generate the first draft of a chapter."""
    prompt = build_draft_prompt(work_dir, chapter_number, chapter_title, target_words)
    draft = _llm(client, prompt["system"], prompt["user"], thinking=True)
    return draft.strip()


_REWRITE_SYSTEM_FALLBACK = (
    "你是中文网文改稿编辑。你的任务是重写用户指定的已有章节，不是续写新章节。"
    "必须保持该章在全书中的原有位置、剧情范围和章节编号。"
    "只输出重写后的完整正文，不要解释，不要写下一章。"
)

_REWRITE_USER_FALLBACK = """请重写第{chapter_number}章。
章节标题：{chapter_title}
本章细纲：
{outline}

原正文：
{source}

要求：
1. 只重写第{chapter_number}章，不得续写后续章节。
2. 保持原正文的剧情位置、人物状态和主要事件。
3. 输出完整正文，开头必须写“# 第{chapter_number}章”。
4. 不要解释修改过程，不要附加下一章内容。"""


def rewrite_chapter_from_source(
    client: DeepSeekClient,
    source_text: str,
    chapter_number: int,
    chapter_title: str = "",
    outline: str = "",
) -> str:
    """Rewrite one existing chapter without loading later-book tracking state."""
    system = _load_prompt_template("l2_rewrite_system.txt", _REWRITE_SYSTEM_FALLBACK)
    user_template = _load_prompt_template("l2_rewrite_user.txt", _REWRITE_USER_FALLBACK)
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title or f"第{chapter_number}章",
        "outline": (outline or "（暂无细纲）")[:3000],
        "source": source_text,
    })
    rewritten = _llm(client, system, user, thinking=True)
    return ensure_chapter_heading(rewritten, chapter_number)


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

    prompt = build_expand_prompt(draft, target_words)
    expanded = _llm(client, prompt["system"], prompt["user"])
    return expanded.strip()


# ── Phase: Polish ─────────────────────────────────────────────────────


def run_polish(
    client: DeepSeekClient,
    draft: str,
) -> str:
    """Polish the draft for language quality."""
    prompt = build_polish_prompt(draft)
    polished = _llm(client, prompt["system"], prompt["user"])
    return polished.strip()


# ── Phase: De-AI ──────────────────────────────────────────────────────


def run_deslop(
    client: DeepSeekClient,
    draft: str,
) -> str:
    """Remove AI writing traces from the draft."""
    prompt = build_deslop_prompt(draft)
    deslopped = _llm(client, prompt["system"], prompt["user"])
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
        system = _load_prompt_template("l2_continuity_system.txt", _CONTINUITY_SYSTEM_FALLBACK)
        user_template = _load_prompt_template("l2_continuity_user.txt", _CONTINUITY_USER_FALLBACK)
        user = _render_prompt_template(user_template, {
            "previous_chapter": ctx["prev_chapter_full"][-1000:],
            "chapter_text": draft[:1500],
            "character_profiles": ctx.get("character_profiles", "")[:1500],
            "book_outline": ctx.get("book_outline", "")[:1000],
            "volume_outline": ctx.get("volume_outline", "")[:1000],
        })
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


def refresh_tracking_head(
    work_dir: Path,
    chapter_number: int,
    draft: str,
    *,
    summary_short: str = "",
) -> None:
    """Refresh only the latest-progress summary used by subsequent chapters."""
    tracking_dir = work_dir / "追踪"
    ensure_tracking_files(work_dir)

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    words = count_chinese_chars(draft)
    summary = str(summary_short or draft[:220].replace("\n", " ")).strip()

    _save_file(
        tracking_dir / "上下文.md",
        f"## 写作上下文\n\n"
        f"- 当前进度：第{chapter_number}章已完成\n"
        f"- 字数：{words}字\n"
        f"- 本章摘要：{summary}\n"
        f"- 上次更新时间：{now}\n"
        f"- 下一章：第{chapter_number + 1}章\n",
    )
    _upsert_section(
        tracking_dir / "全书进展.md",
        "## 全书进展",
        f"- 当前进度：第{chapter_number}章已完成\n"
        f"- 最近更新：{now}\n"
        f"- 最新章节摘要：{summary}\n"
        f"- 下一章：第{chapter_number + 1}章\n",
    )


def update_tracking_files(
    work_dir: Path,
    chapter_number: int,
    draft: str,
    client: DeepSeekClient | None = None,
    *,
    advance_current: bool = True,
) -> None:
    """Update foreshadowing, timeline, character state, and context after writing."""
    tracking_dir = work_dir / "追踪"
    ensure_tracking_files(work_dir)

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    words = count_chinese_chars(draft)
    memory = _extract_tracking_memory(client, work_dir, chapter_number, draft)
    summary_short = str(memory.get("summary_short") or draft[:220].replace("\n", " "))
    summary_long = str(memory.get("summary_long") or summary_short)
    timeline_events = [str(x) for x in memory.get("timeline_events") or [] if str(x).strip()]
    character_updates = [str(x) for x in memory.get("character_updates") or [] if str(x).strip()]
    foreshadowing_updates = [str(x) for x in memory.get("foreshadowing_updates") or [] if str(x).strip()]
    continuation_constraints = [str(x) for x in memory.get("continuation_constraints") or [] if str(x).strip()]
    key_entities = [str(x) for x in memory.get("key_entities") or [] if str(x).strip()]

    section_heading = f"## 第{chapter_number}章"
    section_body = (
        f"- 更新时间：{now}\n"
        f"- 本章字数：{words}字\n"
        f"- 摘要：{summary_long}\n"
        + (f"- 关键实体：{'、'.join(key_entities)}\n" if key_entities else "")
    )

    if advance_current:
        refresh_tracking_head(work_dir, chapter_number, draft, summary_short=summary_short)
    _upsert_section(tracking_dir / "全书进展.md", section_heading, section_body)
    _upsert_section(
        tracking_dir / "时间线.md",
        section_heading,
        "\n".join(f"- {idx}. {event}" for idx, event in enumerate(timeline_events, 1))
        or f"- {summary_short}",
    )
    _upsert_section(
        tracking_dir / "角色状态.md",
        section_heading,
        "\n".join(f"- {item}" for item in character_updates)
        or f"- 本章未提取到明确角色状态变化；摘要参考：{summary_short}",
    )
    _upsert_section(
        tracking_dir / "伏笔.md",
        section_heading,
        "\n".join(f"- {item}" for item in foreshadowing_updates)
        or f"- 本章未提取到明确伏笔变化；摘要参考：{summary_short}",
    )
    _upsert_section(
        tracking_dir / "续写约束.md",
        f"## 第{chapter_number}章后续写约束",
        "\n".join(f"- {item}" for item in continuation_constraints)
        or "- 下一章必须承接本章结尾、角色状态与已暴露信息。",
    )

    logger.info("Tracking files updated for chapter %d", chapter_number)


# ── Review-driven rewrite ─────────────────────────────────────────────


_REVIEW_FIX_SYSTEM_FALLBACK = (
    "你是长篇网文改稿编辑。你的任务是逐条落实审查问题，不是笼统润色。"
    "保持原剧情目标、人物设定、世界观和章节细纲不变。只输出修改后的完整正文，不要解释。"
)

_REVIEW_FIX_USER_FALLBACK = """请根据审查建议修改第{chapter_number}章，保持原剧情目标、人物设定、世界观和章节细纲不变。

本章细纲：
{outline}

上一轮审查问题与建议（每一条都必须在正文里有具体修复）：
{suggestions}

原正文：
{source}

只输出修改后的完整正文。"""


def revise_chapter_once(
    client: DeepSeekClient,
    work_dir: Path,
    chapter_number: int,
    review: dict[str, Any],
    *,
    source_text: str,
    outline: str = "",
) -> tuple[str, dict[str, Any]]:
    """Rewrite a chapter once against its review findings, then re-review.

    Returns ``(revised_text, new_review)``. The rewrite addresses the concrete
    findings from ``review`` and is run back through the de-AI pass so it keeps
    the same quality bar as a freshly written chapter. ``run_story_review`` is
    imported lazily to avoid an l2 ↔ l4 import cycle.
    """
    from generator.long_novel.l4_review import (
        run_story_review,
        summarize_review_recommendations,
    )

    suggestions = summarize_review_recommendations(review)
    system = _load_prompt_template("l2_review_fix_system.txt", _REVIEW_FIX_SYSTEM_FALLBACK)
    user_template = _load_prompt_template("l2_review_fix_user.txt", _REVIEW_FIX_USER_FALLBACK)
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "outline": (outline or "")[:2000],
        "suggestions": suggestions or "请整体提升连续性、逻辑、剧情推进、人设、环境与共情。",
        "extra_prompt": "无",
        "source": source_text,
    })
    revised = _llm(client, system, user, thinking=True).strip()
    revised = ensure_chapter_heading(run_deslop(client, revised), chapter_number)
    new_review = run_story_review(client, revised, work_dir, chapter_number, outline)
    return revised, new_review


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
    final = ensure_chapter_heading(run_deslop(client, polished), chapter_number)
    final_words = count_chinese_chars(final)

    # 5. Continuity check
    continuity = None
    if not skip_continuity and chapter_number > 1:
        continuity = run_continuity_check(client, work_dir, chapter_number, final)

    # 6. Save — into per-chapter folder: 正文/第NNN章_标题/正文.md
    draft_path = chapter_final_path(work_dir, chapter_number, chapter_title)
    _save_file(draft_path, final)

    # 7. Update tracking
    update_tracking_files(work_dir, chapter_number, final, client)

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
    "ensure_chapter_heading",
    "run_full_chapter",
    "run_draft",
    "rewrite_chapter_from_source",
    "run_expand",
    "run_polish",
    "run_deslop",
    "build_draft_prompt",
    "build_expand_prompt",
    "build_polish_prompt",
    "build_deslop_prompt",
    "run_continuity_check",
    "revise_chapter_once",
    "update_tracking_files",
    "refresh_tracking_head",
    "ensure_tracking_files",
    "chapter_dir",
    "chapter_final_path",
    "find_chapter_text",
    "CHAPTER_STEP_FILES",
    "CHAPTER_FINAL_FILENAME",
]
