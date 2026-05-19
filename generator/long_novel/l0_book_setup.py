"""L0 — Book creation pipeline.

Phases:
- L0_benchmark: 对标分析 (optional, runs story-long-analyze style breakdown)
- L0_premise: 选题定位 → 设定/题材定位.md
- L0_world: 世界观 + 势力 → 设定/世界观/*.md + 设定/势力/*.md
- L0_characters: 角色设计 → 设定/角色/*.md + 设定/关系.md
- L0_outline: 全书梗概 + 卷纲 + 细纲(30章)

All phases are auto-run but each can be paused for human review.
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
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _save_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _llm(client: DeepSeekClient, system: str, user: str, thinking: bool = True) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.content if hasattr(completion, "content") else str(completion)


def _select_genre_prompt(genre: str) -> str:
    """Select genre-specific prompt supplement based on genre."""
    mapping = {
        "玄幻": "玄幻修仙类，注重修炼体系、境界突破、世界观宏大",
        "仙侠": "仙侠类，注重道心、因果、法宝、门派势力",
        "都市": "都市类，注重现实感、职场、情感、逆袭爽感",
        "科幻": "科幻类，注重科技设定、未来世界观、文明冲突",
        "历史": "历史类，注重时代背景、权谋、战争、人物命运",
        "悬疑": "悬疑类，注重谜题设计、线索铺设、反转揭露",
        "言情": "言情类，注重情感拉扯、人物关系、甜虐节奏",
        "游戏": "游戏类，注重系统设定、数值成长、副本设计",
        "末世": "末世类，注重生存压力、资源争夺、人性考验",
        "无限流": "无限流类，注重副本设计、规则设定、轮回揭秘",
    }
    return mapping.get(genre, "综合类，注重故事性和爽点设计")


# ── Public API ────────────────────────────────────────────────────────


def run_l0_premise(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    premise: str,
    benchmark_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate 题材定位.md with core premise and benchmark analysis."""
    benchmark_text = ""
    if benchmark_dir and benchmark_dir.exists():
        report = benchmark_dir / "拆文报告.md"
        if report.exists():
            benchmark_text = report.read_text(encoding="utf-8")[:3000]

    system = _load_prompt("l0_premise_system.txt") or (
        "你是一位资深的网络小说编辑和故事架构师。"
        "你的任务是根据用户提供的题材和梗概，撰写一份完整的题材定位文档。"
    )
    genre_note = _select_genre_prompt(genre)
    user = f"""请为以下长篇小说撰写题材定位文档：

书名：{title}
题材：{genre}（{genre_note}）
一句话梗概：{premise}
{f'对标作品分析参考：{benchmark_text}' if benchmark_text else ''}

请按以下结构输出（Markdown格式）：

## 题材定位
- 核心梗概（三分法：表层/中层/深层）
- 目标读者画像
- 题材竞争力分析

## 对标分析
- 同题材爆款模式
- 差异化切入点
- 可借鉴套路

## 卖点设计
- 核心卖点（至少3个）
- 情绪卖点
- 创新点

## 注意事项
- 该题材常见坑点
- 规避建议"""
    result = _llm(client, system, user)
    _save_file(work_dir / "设定" / "题材定位.md", result)
    return {"phase": "l0_premise", "output": "设定/题材定位.md"}


def run_l0_world(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
) -> dict[str, Any]:
    """Generate world-building documents."""
    premise_text = ""
    p = work_dir / "设定" / "题材定位.md"
    if p.exists():
        premise_text = p.read_text(encoding="utf-8")[:2000]

    system = _load_prompt("l0_world_system.txt") or (
        "你是一位世界观架构师，擅长为网络小说设计自洽且有深度的世界背景。"
    )
    user = f"""请为以下长篇小说设计世界观：

书名：{title}
题材：{genre}
题材定位参考：{premise_text}

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
- 势力间的冲突与平衡"""
    result = _llm(client, system, user)

    # Parse sections and save to files
    world_dir = work_dir / "设定" / "世界观"
    world_dir.mkdir(parents=True, exist_ok=True)
    faction_dir = work_dir / "设定" / "势力"
    faction_dir.mkdir(parents=True, exist_ok=True)

    # Save as combined file, individual files can be split later
    _save_file(world_dir / "背景设定.md", result)
    _save_file(faction_dir / "主要势力.md", "（待从世界观设定中拆分）")
    return {"phase": "l0_world", "output": "设定/世界观/背景设定.md"}


def run_l0_characters(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
) -> dict[str, Any]:
    """Generate character profiles and relationship map."""
    premise_text = ""
    for f in ["设定/题材定位.md", "设定/世界观/背景设定.md"]:
        p = work_dir / f
        if p.exists():
            premise_text += p.read_text(encoding="utf-8")[:1000] + "\n"

    system = _load_prompt("l0_characters_system.txt") or (
        "你是一位角色设计师，擅长为网络小说创建有深度、有弧线的角色。"
    )
    user = f"""请为以下长篇小说设计主要角色：

书名：{title}
题材：{genre}
已有设定：{premise_text}

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
描述角色之间的核心关系网络。

请为每个主要角色生成独立的角色卡，最后附上关系图。"""
    result = _llm(client, system, user)
    chars_dir = work_dir / "设定" / "角色"
    chars_dir.mkdir(parents=True, exist_ok=True)
    _save_file(chars_dir / "角色设定.md", result)
    _save_file(work_dir / "设定" / "关系.md", "（角色关系详见 设定/角色/角色设定.md）")
    return {"phase": "l0_characters", "output": "设定/角色/角色设定.md"}


def run_l0_outline(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    target_chapters: int = 30,
    words_per_chapter: int = 3000,
) -> dict[str, Any]:
    """Generate book outline, volume outline, and chapter outlines (30 chapters)."""
    all_settings = ""
    for f in ["设定/题材定位.md", "设定/世界观/背景设定.md", "设定/角色/角色设定.md"]:
        p = work_dir / f
        if p.exists():
            all_settings += f"\n--- {f} ---\n{p.read_text(encoding='utf-8')[:1500]}"

    # Step 1: Book outline
    system = _load_prompt("l0_outline_system.txt") or (
        "你是一位网文大纲架构师，擅长设计卷级结构和章节级细纲。"
    )
    user = f"""请为以下长篇小说设计全书大纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定：{all_settings}

请先输出全书卷级大纲：

## 全书大纲（大纲/大纲.md）
按卷划分，每卷包含：
- 卷名 + 字数范围 + 章数
- 核心事件（本卷最重要的一件事）
- 起始状态 → 结束状态（主角处境的变化）
- 本卷爽点类型
- 本卷伏笔（埋设什么/回收什么）

然后为第一卷生成前{target_chapters}章的细纲（每章一个文件 大纲/细纲_第XXX章.md）：

每章细纲包含：
- 核心事件（本章发生的最重要的事）
- 章首钩子（如何吸引读者继续读）
- 主要冲突（本章的对抗/矛盾）
- 爽点（本章的爽感来源）
- 章尾钩子（如何让读者翻下一章）
- 出场角色
- 埋设伏笔 / 回收伏笔（如有）
- 情绪目标（本章想让读者感受到的情绪）

请完整输出。"""
    result = _llm(client, system, user, thinking=True)

    # Save book outline
    outline_dir = work_dir / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    _save_file(outline_dir / "大纲.md", result[:5000])

    # Save volume 1 outline
    _save_file(outline_dir / "卷纲_第一卷.md", result[:3000])

    # Generate individual chapter outlines
    # First attempt: try to extract chapters from the LLM output
    chapter_outlines = _extract_chapter_outlines(result, target_chapters)

    for i, ch_text in enumerate(chapter_outlines):
        ch_num = i + 1
        _save_file(outline_dir / f"细纲_第{ch_num:03d}章.md", ch_text)

    # If LLM didn't produce enough chapters, generate remaining individually
    if len(chapter_outlines) < target_chapters:
        _fill_remaining_outlines(
            client, work_dir, title, genre, all_settings,
            len(chapter_outlines) + 1, target_chapters, words_per_chapter,
        )

    # Initialize tracking files
    tracking_dir = work_dir / "追踪"
    tracking_dir.mkdir(parents=True, exist_ok=True)
    _save_file(tracking_dir / "伏笔.md", "## 伏笔状态表\n\n| ID | 内容 | 埋设章节 | 预计回收 | 状态 | 重要度 |\n|---|---|---|---|---|---|\n")
    _save_file(tracking_dir / "时间线.md", f"## 故事时间线\n\n（第1章开始）\n")
    _save_file(tracking_dir / "角色状态.md", "## 角色初始状态\n\n（待第一章写完后更新）\n")
    _save_file(tracking_dir / "上下文.md", f"## 写作上下文\n\n- 当前进度：第0章（尚未开始写作）\n- 计划总章数：{target_chapters}\n- 上次更新时间：{_now_iso()}\n")

    return {"phase": "l0_outline", "chapters_generated": len(chapter_outlines)}


def _extract_chapter_outlines(text: str, max_chapters: int) -> list[str]:
    """Attempt to extract individual chapter outlines from the LLM output."""
    outlines = []
    for i in range(1, max_chapters + 1):
        patterns = [
            f"第{i}章", f"第{i:03d}章", f"第 {i} 章",
            f"### 第{i}章", f"## 第{i}章", f"**第{i}章",
        ]
        for j, pat in enumerate(patterns):
            idx = text.find(pat)
            if idx >= 0:
                # Find next chapter marker
                next_idx = len(text)
                for k in range(i + 1, max_chapters + 2):
                    for p2 in [f"第{k}章", f"第{k:03d}章", f"### 第{k}章", f"## 第{k}章"]:
                        n = text.find(p2, idx + len(pat))
                        if n >= 0 and n < next_idx:
                            next_idx = n
                outlines.append(text[idx:next_idx].strip())
                break
        else:
            break  # No more chapters found
    return outlines


def _fill_remaining_outlines(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    start_ch: int,
    end_ch: int,
    words_per_chapter: int,
) -> None:
    """Generate remaining chapter outlines in batches."""
    outline_dir = work_dir / "大纲"
    batch_size = 5
    for batch_start in range(start_ch, end_ch + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, end_ch)
        prev_outline = ""
        if batch_start > 1:
            prev_path = outline_dir / f"细纲_第{batch_start - 1:03d}章.md"
            if prev_path.exists():
                prev_outline = prev_path.read_text(encoding="utf-8")[:800]

        system = "你是一位网文细纲设计师。为指定章节生成细纲。"
        user = f"""为{title}（{genre}题材）生成第{batch_start}到第{batch_end}章的细纲。
每章约{words_per_chapter}字。
上一章细纲参考：{prev_outline}

每章细纲包含：核心事件、章首钩子、主要冲突、爽点、章尾钩子、出场角色、埋设/回收伏笔、情绪目标。
用"## 第N章"分隔每章。"""
        result = _llm(client, system, user)
        ch_outlines = _extract_chapter_outlines(result, batch_end)
        for j, ch_text in enumerate(ch_outlines):
            ch_num = batch_start + j
            if ch_num <= end_ch:
                _save_file(outline_dir / f"细纲_第{ch_num:03d}章.md", ch_text)
        logger.info("Generated outlines for chapters %d-%d", batch_start, batch_end)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "run_l0_premise",
    "run_l0_world",
    "run_l0_characters",
    "run_l0_outline",
]
