"""长篇提示词注册表与查看/编辑/回滚路由。

`_PHASE_PROMPT_INFO` / `_CHAPTER_PROMPT_INFO` 描述每个阶段可编辑的
system/user 模板文件与必要占位符；文件读写委托 ``prompt_kit``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from generator.long_novel import deps, prompt_kit
from generator.long_novel.deps import _json_payload

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPTS_DIR = prompt_kit.PROMPTS_DIR

_PHASE_PROMPT_INFO = {
    "premise": {
        "label": "题材定位",
        "system_file": "l0_premise_system.txt",
        "user_file": "l0_premise_user.txt",
        "placeholders": ["title", "genre", "genre_note", "premise"],
        "user_template": """请为以下长篇小说撰写题材定位文档：

书名：{title}
题材：{genre}
一句话梗概：{premise}

请按以下结构输出（Markdown格式）：

## 题材定位
- 核心梗概（三分法：表层/中层/深层）
- 目标读者画像
- 题材竞争力分析

## 卖点设计
- 核心卖点（至少3个）
- 情绪卖点
- 创新点

## 注意事项
- 该题材常见坑点
- 规避建议""",
    },
    "world": {
        "label": "世界观",
        "system_file": "l0_world_system.txt",
        "user_file": "l0_world_user.txt",
        "placeholders": ["title", "genre", "section_name", "section_focus", "premise_text"],
        "user_template": """请为以下长篇小说设计世界观：

书名：{title}
题材：{genre}
题材定位参考：{premise_summary}

请生成以下文件内容：

## 背景设定（设定/世界观/背景设定.md）
- 时代背景（古代/现代/架空）
- 地理版图（主要区域及特征）
- 历史大事件（影响当前格局的关键事件）

## 力量体系（设定/世界观/力量体系.md）
- 修炼/能力等级体系（如有）
- 核心规则与限制
- 特殊设定（如有）

## 势力分布（设定/势力/主要势力.md）
- 各大势力的名称、定位、关系
- 势力间的冲突与平衡""",
    },
    "characters": {
        "label": "角色设计",
        "system_file": "l0_characters_roster_system.txt",
        "user_file": "l0_characters_roster_user.txt",
        "placeholders": ["title", "genre", "premise_text"],
        "related_prompts": ["characters_detail"],
        "user_template": """请为以下长篇小说设计主要角色：

书名：{title}
题材：{genre}
已有设定：{settings_summary}

请设计3-5个核心角色，每个角色包含：

## 主角：[角色名]
- 身份背景（出身/职业/秘密）
- 性格特质（3个核心特质+1个缺陷）
- 核心动机（想要什么/害怕什么）
- 成长弧线（起点→终点）
- 关键关系（与其他角色的关系）
- 语言风格（说话方式/口头禅）
- 能力/技能（如有）

## 反派：[角色名]
- 同上结构

## 配角（1-3个）
- 简化版角色卡

## 角色关系图
描述角色之间的核心关系网络。""",
    },
    "factions": {
        "label": "势力",
        "system_file": "l0_factions_roster_system.txt",
        "user_file": "l0_factions_roster_user.txt",
        "placeholders": ["title", "genre", "context_text"],
        "related_prompts": ["factions_detail"],
        "user_template": """两阶段生成势力档案。

阶段1（pro+thinking）：让 LLM 返回 JSON 清单 [{name,type,brief}, ...] 共 3-6 个势力。
阶段2（flash 并发）：对每个势力分别详写 设定/势力/{name}.md。

阶段1 prompt 上下文：
- 题材定位（首 1500 字）
- 世界观/背景设定（首 1500 字）
- 世界观/力量体系（首 1500 字）
- 角色/_角色索引（首 1500 字）

阶段2 每项 prompt 模板：
「为《{title}》撰写势力「{name}」的完整档案。
结构：起源历史/组织架构/核心人物/势力范围/资源底牌/与其他势力关系/在剧情中的作用。600-1200 字。」
""",
    },
    "relations": {
        "label": "关系",
        "system_file": "l0_relations_system.txt",
        "user_file": "l0_relations_user.txt",
        "placeholders": ["title", "genre", "char_list", "faction_list", "context_text"],
        "user_template": """单次调用生成 设定/关系.md。

输入：
- 设定/角色/_角色索引.md
- 设定/势力/_势力索引.md
- 设定/题材定位.md
- 角色文件列表（仅文件名）
- 势力文件列表（仅文件名）

输出结构：
## 一、人物关系
## 二、人物-势力归属
## 三、势力之间的关系
## 四、关系演化时间线
""",
    },
    "outline": {
        "label": "大纲",
        "system_file": "l0_outline_system.txt",
        "user_file": "l0_outline_user.txt",
        "placeholders": ["title", "genre", "target_chapters", "words_per_chapter", "all_settings"],
        "related_prompts": ["extend_chapters"],
        "user_template": """请为以下长篇小说设计全书大纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定（必须继承，尤其是角色名、身份、动机、关系、世界观规则）：{all_settings}

一致性硬约束：
- 人物只能沿用“设定/角色/角色设定.md”中的核心角色；不得改名、换身份、换动机或重新发明主角团。
- 临时配角必须标注为临时配角，不能替代既有核心角色。
- 事件推进必须服从题材定位、世界观背景、角色关系图，不能另起一套世界观或人物关系。

只输出全书级结构，不要写章节细纲。请包含：
- 全书核心主线
- 主要人物线与关系变化
- 核心矛盾升级
- 爽点/情绪曲线
- 重要伏笔与回收计划
- 按卷划分建议""",
    },
    "volume_outline": {
        "label": "卷纲",
        "system_file": "l0_volume_outline_system.txt",
        "user_file": "l0_volume_outline_user.txt",
        "placeholders": [
            "title", "genre", "volume_name", "target_chapters", "words_per_chapter", "vol_num", "ch_start",
            "ch_end", "chapter_count", "volume_words", "plan_title", "all_settings", "book_outline", "full_plan_brief",
        ],
        "user_template": """请基于已有设定和全书大纲生成卷纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定：{all_settings}
全书大纲：{book_outline}

一致性硬约束：
- 卷纲必须承接全书大纲，并继续沿用角色设计中的人物名、身份、动机和关系。
- 不得新增核心主角/反派替换已设计角色；不得把已设计人物改成另一套关系。
- 每卷的人物线必须说明这些既有角色的关系如何变化。

只输出卷级结构，不要写章节细纲。每卷包含：卷名、章节范围、核心事件、起始状态→结束状态、人物线、爽点、伏笔。

输出格式要求：
- 每一卷用二级标题分隔，例如 ## 第一卷：卷名、## 第二卷：卷名。
- 系统会按卷标题拆成 大纲/卷纲_第一卷.md、卷纲_第二卷.md 等独立文件。""",
    },
    "chapter_outlines": {
        "label": "章节细纲",
        "system_file": "l0_chapter_outlines_system.txt",
        "user_file": "l0_chapter_outlines_user.txt",
        "placeholders": ["title", "genre", "target_chapters", "words_per_chapter", "outline_context"],
        "related_prompts": ["chapter_outlines_fill"],
        "user_template": """请基于已有设定、全书大纲和卷纲生成章节细纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定：{all_settings}
全书大纲：{book_outline}
卷纲：{volume_outline}

一致性硬约束：
- 章节细纲只能使用角色设计、全书大纲、卷纲中已经确立的核心人物与关系。
- 每章“出场角色”必须优先从角色设定中选择，并保持身份、动机、说话方式、关系不变。
- 不得凭空替换人物名、阵营、情感线或世界观规则；确需新增路人/工具人时标注为临时配角。

每章：核心事件、章首钩子、主要冲突、爽点、章尾钩子、出场角色、伏笔、情绪目标。
用"## 第N章"分隔每章。""",
    },
}

_CHAPTER_PROMPT_INFO = {
    "characters_detail": {
        "label": "角色详情卡",
        "system_file": "l0_characters_detail_system.txt",
        "user_file": "l0_characters_detail_user.txt",
        "placeholders": ["title", "genre", "name", "role", "brief", "premise_text"],
    },
    "factions_detail": {
        "label": "势力详情档案",
        "system_file": "l0_factions_detail_system.txt",
        "user_file": "l0_factions_detail_user.txt",
        "placeholders": ["title", "genre", "name", "ftype", "brief", "context_text"],
    },
    "chapter_outlines_fill": {
        "label": "补全章节细纲",
        "system_file": "l0_chapter_outlines_fill_system.txt",
        "user_file": "l0_chapter_outlines_fill_user.txt",
        "placeholders": ["title", "genre", "batch_start", "batch_end", "words_per_chapter", "all_settings", "prev_outline"],
    },
    "extend_chapters": {
        "label": "追加章节规划",
        "system_file": "l0_extend_chapters_system.txt",
        "user_file": "l0_extend_chapters_user.txt",
        "placeholders": [
            "title", "genre", "start_ch", "end_ch", "old_target_chapters",
            "new_target_chapters", "words_per_chapter", "extension_context",
        ],
    },
    "draft": {
        "label": "正文初稿",
        "system_file": "l2_draft_system.txt",
        "user_file": "l2_draft_user.txt",
        "placeholders": ["chapter_number", "chapter_title", "target_words", "context_sections"],
    },
    "expand": {
        "label": "扩写",
        "system_file": "l2_expand_system.txt",
        "user_file": "l2_expand_user.txt",
        "placeholders": ["draft", "current_words", "target_words", "shortfall"],
    },
    "polish": {
        "label": "润色",
        "system_file": "l2_polish_system.txt",
        "user_file": "l2_polish_user.txt",
        "placeholders": ["draft"],
    },
    "deslop": {
        "label": "去 AI",
        "system_file": "l2_deslop_system.txt",
        "user_file": "l2_deslop_user.txt",
        "placeholders": ["draft", "hit_text"],
        "related_prompts": ["deslop_fix"],
    },
    "review": {
        "label": "审查",
        "system_file": "l4_story_review_system.txt",
        "user_file": "l4_story_review_user.txt",
        "placeholders": ["chapter_number", "outline", "context", "chapter_text", "continuity_rule"],
        "related_prompts": ["review_fix"],
    },
    "finalize": {
        "label": "成稿/长期记忆",
        "system_file": "l2_tracking_memory_system.txt",
        "user_file": "l2_tracking_memory_user.txt",
        "placeholders": ["chapter_number", "tracking_context", "chapter_text"],
    },
    "continuity": {
        "label": "连续性检查",
        "system_file": "l2_continuity_system.txt",
        "user_file": "l2_continuity_user.txt",
        "placeholders": ["previous_chapter", "chapter_text", "character_profiles", "book_outline", "volume_outline"],
    },
    "review_fix": {
        "label": "按审查建议修改",
        "system_file": "l2_review_fix_system.txt",
        "user_file": "l2_review_fix_user.txt",
        "placeholders": ["chapter_number", "outline", "suggestions", "extra_prompt", "source"],
    },
    "deslop_fix": {
        "label": "继续降低 AI 味",
        "system_file": "l2_deslop_fix_system.txt",
        "user_file": "l2_deslop_fix_user.txt",
        "placeholders": ["chapter_number", "suggestions", "extra_prompt", "source"],
    },
}


# 共享实现见 prompt_kit；保留旧私有名，调用点零改动。
_prompt_file_text = prompt_kit.prompt_file_text
_render_prompt_template = prompt_kit.render_prompt_template
_load_prompt_template = prompt_kit.load_prompt_template


def _missing_prompt_placeholders(content: str, placeholders: list[str]) -> list[str]:
    """Return required template placeholders that are absent from content."""
    return [p for p in placeholders if "{" + p + "}" not in content]


_PROMPT_THINKING_MODE = {
    "expand": False,
    "polish": False,
    "deslop": False,
    "continuity": False,
}


def _prompt_call_parameters(phase: str) -> dict[str, Any]:
    """Return the effective default parameters used by a prompt phase."""
    settings = deps._deepseek_client().settings
    thinking_mode = _PROMPT_THINKING_MODE.get(phase, True)
    return {
        "model": settings.model,
        "thinking_mode": thinking_mode,
        "temperature": 0.8,
        "max_output_tokens": settings.max_output_tokens,
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }


def _save_prompt_file(filename: str, content: str) -> str:
    path = _PROMPTS_DIR / filename
    if path.suffix.lower() != ".txt":
        raise HTTPException(status_code=400, detail="只支持编辑 .txt 格式的 prompt 文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    return backup.name


@router.get("/prompts/{phase}")
def api_get_phase_prompt(phase: str) -> dict[str, Any]:
    """Return system and user prompt templates for a setup phase or chapter step."""
    info = _PHASE_PROMPT_INFO.get(phase)
    kind = "setup"
    if not info:
        info = _CHAPTER_PROMPT_INFO.get(phase)
        kind = "chapter"
    if not info:
        raise HTTPException(status_code=404, detail=f"未知阶段：{phase}")

    system_file = str(info.get("system_file") or "")
    user_file = str(info.get("user_file") or "")
    system_prompt = _prompt_file_text(system_file)
    user_template = _prompt_file_text(user_file) if user_file else str(info.get("user_template") or "")

    return {
        "ok": True,
        "phase": phase,
        "kind": kind,
        "label": info["label"],
        "system_file": system_file,
        "user_file": user_file,
        "editable_system": bool(system_file),
        "editable_user": bool(user_file),
        "placeholders": list(info.get("placeholders") or []),
        "related_prompts": list(info.get("related_prompts") or []),
        "call_parameters": _prompt_call_parameters(phase),
        "system_prompt": system_prompt,
        "user_template": user_template,
    }


@router.post("/prompts/{phase}")
async def api_save_phase_prompt(phase: str, request: Request) -> dict[str, Any]:
    """Save editable long-novel prompt templates."""
    info = _PHASE_PROMPT_INFO.get(phase) or _CHAPTER_PROMPT_INFO.get(phase)
    if not info:
        raise HTTPException(status_code=404, detail=f"未知阶段：{phase}")
    payload = await _json_payload(request)
    saved: list[str] = []
    backups: list[str] = []
    if "system_prompt" in payload:
        filename = str(info.get("system_file") or "")
        if not filename:
            raise HTTPException(status_code=400, detail="该阶段没有可编辑的 system prompt 文件")
        content = str(payload.get("system_prompt") or "")
        if not content.strip():
            raise HTTPException(status_code=400, detail="system prompt 不能为空")
        backups.append(_save_prompt_file(filename, content))
        saved.append(filename)
    if "user_template" in payload:
        filename = str(info.get("user_file") or "")
        if not filename:
            raise HTTPException(status_code=400, detail="该阶段的 user prompt 仍由源码拼装，暂不能保存为文件")
        content = str(payload.get("user_template") or "")
        if not content.strip():
            raise HTTPException(status_code=400, detail="user prompt 不能为空")
        missing = _missing_prompt_placeholders(content, list(info.get("placeholders") or []))
        if missing:
            missing_text = "、".join("{" + p + "}" for p in missing)
            raise HTTPException(status_code=400, detail=f"user prompt 缺少必要变量：{missing_text}")
        backups.append(_save_prompt_file(filename, content))
        saved.append(filename)
    if not saved:
        raise HTTPException(status_code=400, detail="没有可保存的提示词内容")
    logger.info("long novel prompts saved phase=%s files=%s", phase, saved)
    return {"ok": True, "phase": phase, "saved": saved, "backups": backups, "message": "提示词已保存，下一次运行会使用新内容"}


@router.post("/prompts/{phase}/revert")
def api_revert_phase_prompt(phase: str) -> dict[str, Any]:
    """Restore editable long-novel prompt templates from .bak files."""
    info = _PHASE_PROMPT_INFO.get(phase) or _CHAPTER_PROMPT_INFO.get(phase)
    if not info:
        raise HTTPException(status_code=404, detail=f"未知阶段：{phase}")
    restored: list[str] = []
    for key in ("system_file", "user_file"):
        filename = str(info.get(key) or "")
        if not filename:
            continue
        path = _PROMPTS_DIR / filename
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append(filename)
    if not restored:
        raise HTTPException(status_code=404, detail="没有找到可恢复的提示词备份")
    return {"ok": True, "phase": phase, "restored": restored, "message": "已恢复上一版提示词"}
