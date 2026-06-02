"""L0 — Book creation pipeline.

Phases:
- L0_benchmark: 对标分析 (optional, runs story-long-analyze style breakdown)
- L0_premise: 选题定位 → 设定/题材定位.md
- L0_world: 世界观 + 势力 → 设定/世界观/*.md + 设定/势力/*.md
- L0_characters: 角色设计 → 设定/角色/*.md + 设定/关系.md
- L0_outline: 全书大纲
- L0_volume_outline: 卷纲
- L0_chapter_outlines: 章节细纲

All phases are auto-run but each can be paused for human review.
"""

from __future__ import annotations

import json
import json as _json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
        logger.warning("setup prompt template render failed: %s", exc)
        return template


def _save_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Setup-progress/trace file location ─────────────────────────────────
# All `_setup_*.json` (phase progress + LLM traces) live in `<work_dir>/.setup/`
# instead of being scattered at the book root. Reads fall back to the legacy
# root location so we don't break books whose files haven't been migrated yet.

SETUP_DIR_NAME = ".setup"


def setup_dir(work_dir: Path) -> Path:
    """Return `<work_dir>/.setup`, creating the directory if needed."""
    d = Path(work_dir) / SETUP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_file_read(work_dir: Path, filename: str) -> Path:
    """Resolve a setup file for reading: prefer `.setup/`, fall back to root."""
    new_path = Path(work_dir) / SETUP_DIR_NAME / filename
    if new_path.exists():
        return new_path
    return Path(work_dir) / filename


def setup_glob(work_dir: Path, pattern: str) -> list[Path]:
    """Glob setup files in `.setup/` plus legacy root location (deduped by name)."""
    root = Path(work_dir)
    seen: dict[str, Path] = {}
    new_dir = root / SETUP_DIR_NAME
    if new_dir.exists():
        for p in new_dir.glob(pattern):
            seen[p.name] = p
    for p in root.glob(pattern):
        seen.setdefault(p.name, p)
    return sorted(seen.values(), key=lambda p: p.name)


def _with_additional_prompt(user: str, additional_prompt: str | None = None) -> str:
    extra = (additional_prompt or "").strip()
    if not extra:
        return user
    return f"{user}\n\n用户本次补充要求（优先遵守，但不得破坏前后设定一致性）：\n{extra}"


def _serialize_usage(usage: Any) -> dict[str, Any]:
    if not usage:
        return {}
    try:
        return {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "cached_tokens": getattr(usage, "cached_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_hit_ratio": getattr(usage, "cache_hit_ratio", 0.0),
        }
    except Exception:
        return {}


def _write_trace(
    work_dir: Path,
    phase: str,
    *,
    system: str,
    user: str,
    completion: Any,
    thinking: bool,
    temperature: float,
    inputs: list[dict[str, Any]],
    started: datetime,
    ended: datetime,
    suffix: str = "",
    outputs: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Write _setup_{phase}{suffix}_trace.json next to existing _setup_*.json files."""
    trace = {
        "phase": phase,
        "model": getattr(completion, "model", "unknown") if completion else "unknown",
        "thinking_mode": thinking,
        "temperature": temperature,
        "system_prompt": system,
        "user_prompt": user,
        "output_text": getattr(completion, "text", "") if completion else "",
        "reasoning": getattr(completion, "reasoning", None) if completion else None,
        "finish_reason": getattr(completion, "finish_reason", None) if completion else None,
        "cached": getattr(completion, "cached", False) if completion else False,
        "usage": _serialize_usage(getattr(completion, "usage", None) if completion else None),
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%S"),
        "ended_at": ended.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_seconds": round((ended - started).total_seconds(), 2),
        "inputs": inputs,
        "outputs": outputs or [],
        "error": error,
    }
    fname = f"_setup_{phase}{suffix}_trace.json"
    try:
        (setup_dir(work_dir) / fname).write_text(
            _json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to write trace %s: %s", fname, e)


def _llm_traced(
    client: DeepSeekClient,
    work_dir: Path,
    phase: str,
    system: str,
    user: str,
    *,
    thinking: bool = True,
    temperature: float = 0.8,
    inputs: list[dict[str, Any]] | None = None,
    trace_suffix: str = "",
    outputs: list[str] | None = None,
) -> str:
    """Run LLM call and persist a trace JSON next to phase progress files."""
    started = datetime.now()
    purpose = f"long_novel_{phase}"
    if trace_suffix == "_stage1_roster":
        purpose += "_roster"
    elif trace_suffix.startswith("_fallback"):
        purpose += "_fallback"
    try:
        completion = client.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            thinking_mode=thinking,
            temperature=temperature,
            purpose=purpose,
        )
        ended = datetime.now()
        _write_trace(
            work_dir, phase,
            system=system, user=user, completion=completion,
            thinking=thinking, temperature=temperature,
            inputs=inputs or [], started=started, ended=ended,
            suffix=trace_suffix, outputs=outputs,
        )
        return completion.text if hasattr(completion, "text") else str(completion)
    except Exception as e:
        ended = datetime.now()
        _write_trace(
            work_dir, phase,
            system=system, user=user, completion=None,
            thinking=thinking, temperature=temperature,
            inputs=inputs or [], started=started, ended=ended,
            suffix=trace_suffix, outputs=outputs, error=str(e)[:500],
        )
        raise


def _file_input(work_dir: Path, rel_path: str, bytes_used: int, label: str = "") -> dict[str, Any]:
    """Helper to build an 'inputs' entry for a file upstream."""
    p = work_dir / rel_path
    return {
        "kind": "file",
        "path": rel_path,
        "label": label or rel_path,
        "bytes_used": bytes_used,
        "bytes_total": p.stat().st_size if p.exists() else 0,
        "exists": p.exists(),
    }


def _param_input(name: str, value: Any) -> dict[str, Any]:
    """Helper to build an 'inputs' entry for a scalar parameter."""
    text = str(value) if value is not None else ""
    return {"kind": "param", "name": name, "value": text, "bytes_used": len(text.encode("utf-8"))}


# ── Multi-item phase helpers ──────────────────────────────────────────


_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\s]+')


def _sanitize_slug(name: Any) -> str:
    """Make a string safe to use as Windows filename / trace suffix."""
    s = str(name).strip()
    s = _FORBIDDEN_FILENAME_CHARS.sub("_", s).strip("_")
    return s or "unnamed"


def _write_overview_trace(
    work_dir: Path,
    phase: str,
    *,
    items: list[str],
    inputs: list[dict[str, Any]],
    expected_outputs: list[str],
    note: str = "",
) -> None:
    """Write a coordinator-style main trace for multi-item phases."""
    now = datetime.now()
    body = (
        "（multi-item phase 协调器；本身不调用 LLM，"
        "详见每个 item 的 sub-trace）\n\n"
        f"子项清单：\n{json.dumps(items, ensure_ascii=False, indent=2)}\n\n{note}"
    )
    trace = {
        "phase": phase,
        "is_multi_item": True,
        "items": items,
        "model": "—",
        "thinking_mode": False,
        "temperature": 0,
        "system_prompt": "（multi-item coordinator）",
        "user_prompt": body,
        "output_text": "",
        "reasoning": None,
        "finish_reason": None,
        "cached": False,
        "usage": {},
        "started_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "ended_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_seconds": 0,
        "inputs": inputs,
        "outputs": expected_outputs,
        "error": None,
    }
    fname = f"_setup_{phase}_trace.json"
    try:
        (setup_dir(work_dir) / fname).write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to write overview trace %s: %s", fname, e)


def _llm_item_call(
    client: DeepSeekClient,
    work_dir: Path,
    phase: str,
    item_slug: str,
    system: str,
    user: str,
    *,
    use_flash: bool = False,
    thinking: bool = False,
    temperature: float = 0.8,
    inputs: list[dict[str, Any]] | None = None,
    outputs: list[str] | None = None,
) -> str:
    """One LLM call for a sub-item of a multi-item phase.

    Writes _setup_{phase}_item_{slug}_trace.json. Picks the flash model when
    ``use_flash`` is True so concurrent batches stay cheap.
    """
    started = datetime.now()
    model_name = client.settings.flash_model if use_flash else client.settings.model
    suffix = f"_item_{_sanitize_slug(item_slug)}"
    try:
        completion = client.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            thinking_mode=thinking,
            temperature=temperature,
            model=model_name,
            purpose=f"long_novel_{phase}_detail",
        )
        ended = datetime.now()
        _write_trace(
            work_dir, phase,
            system=system, user=user, completion=completion,
            thinking=thinking, temperature=temperature,
            inputs=inputs or [], started=started, ended=ended,
            suffix=suffix, outputs=outputs,
        )
        return completion.text if hasattr(completion, "text") else str(completion)
    except Exception as e:
        ended = datetime.now()
        _write_trace(
            work_dir, phase,
            system=system, user=user, completion=None,
            thinking=thinking, temperature=temperature,
            inputs=inputs or [], started=started, ended=ended,
            suffix=suffix, outputs=outputs, error=str(e)[:500],
        )
        raise


def _run_items_concurrent(
    client: DeepSeekClient,
    work_dir: Path,
    phase: str,
    items: list[dict[str, Any]],
    *,
    max_workers: int = 5,
) -> tuple[dict[str, str], dict[str, str]]:
    """Run a batch of item-level LLM calls concurrently.

    Each item dict: {slug, system, user, use_flash?, thinking?, inputs?, outputs?}
    Returns ({slug: text}, {slug: error_message}).
    """
    results: dict[str, str] = {}
    errors: dict[str, str] = {}
    if not items:
        return results, errors

    workers = min(max_workers, len(items))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {
            ex.submit(
                _llm_item_call, client, work_dir, phase, it["slug"],
                it["system"], it["user"],
                use_flash=it.get("use_flash", False),
                thinking=it.get("thinking", False),
                temperature=it.get("temperature", 0.8),
                inputs=it.get("inputs", []),
                outputs=it.get("outputs", []),
            ): it["slug"]
            for it in items
        }
        for fut in as_completed(future_map):
            slug = future_map[fut]
            try:
                results[slug] = fut.result()
            except Exception as e:
                errors[slug] = str(e)[:500]
                results[slug] = ""
    return results, errors


def _parse_json_list(text: str) -> list[dict[str, Any]]:
    """Robustly extract a JSON array from an LLM response.

    Handles: pure JSON, JSON wrapped in ```json fences, JSON embedded in text.
    """
    if not text:
        return []
    # strip code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # find first [...] block
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = re.search(r"\[\s*\{.*?\}\s*\]", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return []


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
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Generate 题材定位.md with core premise and benchmark analysis."""
    benchmark_text = ""
    benchmark_path_label = None
    if benchmark_dir and benchmark_dir.exists():
        report = benchmark_dir / "拆文报告.md"
        if report.exists():
            benchmark_text = report.read_text(encoding="utf-8")[:3000]
            benchmark_path_label = str(report)

    system = _load_prompt("l0_premise_system.txt") or (
        "你是一位资深的网络小说编辑和故事架构师。"
        "你的任务是根据用户提供的题材和梗概，撰写一份完整的题材定位文档。"
    )
    genre_note = _select_genre_prompt(genre)
    user_template = _load_prompt_template("l0_premise_user.txt", """请为以下长篇小说撰写题材定位文档：

书名：{title}
题材：{genre}（{genre_note}）
一句话梗概：{premise}
{benchmark_section}

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
    )
    user = _render_prompt_template(user_template, {
        "title": title,
        "genre": genre,
        "genre_note": genre_note,
        "premise": premise,
        "benchmark_text": benchmark_text,
        "benchmark_section": f"对标作品分析参考：{benchmark_text}" if benchmark_text else "",
    })
    user = _with_additional_prompt(user, additional_prompt)
    inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        _param_input("premise", premise),
        _param_input("genre_note", genre_note),
    ]
    if benchmark_path_label:
        inputs.append({
            "kind": "file",
            "path": benchmark_path_label,
            "label": "对标拆文报告",
            "bytes_used": len(benchmark_text.encode("utf-8")),
            "bytes_total": Path(benchmark_path_label).stat().st_size,
            "exists": True,
        })
    result = _llm_traced(
        client, work_dir, "premise", system, user,
        thinking=True, inputs=inputs,
        outputs=["设定/题材定位.md"],
    )
    _save_file(work_dir / "设定" / "题材定位.md", result)
    return {"phase": "l0_premise", "output": "设定/题材定位.md"}


def run_l0_world(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Generate world-building documents — one file per topic, run concurrently."""
    premise_text = _read_rel(work_dir, "设定/题材定位.md", limit=2000)

    system_base = _load_prompt("l0_world_system.txt") or (
        "你是一位世界观架构师，擅长为网络小说设计自洽且有深度的世界背景。"
    )

    topics = [
        {
            "name": "背景设定",
            "focus": (
                "时代背景（古代/现代/架空）、世界的核心氛围、与现实的关系、"
                "整体基调。要求 600-1500 字，分点输出。"
            ),
        },
        {
            "name": "力量体系",
            "focus": (
                "修炼/能力/科技等级体系、核心规则与限制、特殊设定、"
                "战力天花板说明。要求 600-1500 字，包含等级或机制表。"
            ),
        },
        {
            "name": "时代地理",
            "focus": (
                "主要地理区域及特征、文化分布、关键场景（至少 3 个具体地点）、"
                "区域之间的关系。要求 600-1500 字。"
            ),
        },
        {
            "name": "历史大事件",
            "focus": (
                "影响当前格局的关键历史事件，按时间线排列，至少 5 个事件；"
                "每个事件标注影响。要求 600-1500 字。"
            ),
        },
    ]

    common_inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        _file_input(work_dir, "设定/题材定位.md", len(premise_text.encode("utf-8")), "题材定位（首2000字）"),
    ]
    expected_outputs = [f"设定/世界观/{t['name']}.md" for t in topics]

    _write_overview_trace(
        work_dir, "world",
        items=[t["name"] for t in topics],
        inputs=common_inputs,
        expected_outputs=expected_outputs,
        note="world phase 把世界观按 4 个主题并发生成（flash 模型）。",
    )

    items = []
    world_user_template = _load_prompt_template("l0_world_user.txt", """请为长篇小说《{title}》（{genre}题材）撰写「{section_name}」一节。

题材定位参考：
{premise_text}

本节要求：
{section_focus}

只输出该节的 markdown 正文，不要写 ## 文件标题（系统会自动加），不要重复其他主题。"""
    )
    for t in topics:
        user = _render_prompt_template(world_user_template, {
            "title": title,
            "genre": genre,
            "section_name": t["name"],
            "section_focus": t["focus"],
            "premise_text": premise_text,
        })
        user = _with_additional_prompt(user, additional_prompt)
        items.append({
            "slug": t["name"],
            "system": system_base,
            "user": user,
            "use_flash": True,
            "thinking": False,
            "inputs": common_inputs + [_param_input("subtopic", t["name"])],
            "outputs": [f"设定/世界观/{t['name']}.md"],
        })

    results, errors = _run_items_concurrent(client, work_dir, "world", items, max_workers=4)

    world_dir = work_dir / "设定" / "世界观"
    world_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in topics:
        slug = t["name"]
        text = results.get(slug, "")
        if not text:
            continue
        content = f"# {slug}\n\n{text.strip()}\n"
        _save_file(world_dir / f"{slug}.md", content)
        written.append(f"设定/世界观/{slug}.md")

    if errors and not written:
        raise RuntimeError(f"world 全部子主题失败：{errors}")

    return {"phase": "l0_world", "outputs": written, "errors": errors}


def run_l0_characters(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Two-stage character design: roster JSON (pro) + per-character detail (flash, concurrent)."""
    premise_text = ""
    upstream_files: list[dict[str, Any]] = []
    for f in ["设定/题材定位.md", "设定/世界观/背景设定.md"]:
        p = work_dir / f
        if p.exists():
            chunk = p.read_text(encoding="utf-8")[:1500]
            premise_text += f"\n--- {f} ---\n{chunk}\n"
            upstream_files.append(_file_input(work_dir, f, len(chunk.encode("utf-8"))))

    # ── Stage 1: roster (pro, thinking) ──────────────────────────────────
    roster_system = _load_prompt_template("l0_characters_roster_system.txt", (
        "你是一位网文角色设计师。第一步只输出角色清单，不要写完整角色卡。\n"
        "严格按 JSON 数组输出，不要有任何额外文字。每个角色对象包含："
        "{\"name\":\"中文角色名\",\"role\":\"主角|反派|配角\",\"brief\":\"一句话简介\"}\n"
        "请设计 4-6 个核心角色（1 主角、1-2 反派、2-3 配角）。"
    ))
    roster_user_template = _load_prompt_template("l0_characters_roster_user.txt", """为长篇小说《{title}》（{genre}题材）设计角色清单。

已有设定：{premise_text}

只输出 JSON 数组（4-6 个角色），不要 markdown 代码块，不要任何解释。
示例：[{{"name":"林萧","role":"主角","brief":"重生归来的天才少年"}}, ...]
"""
    )
    roster_user = _render_prompt_template(roster_user_template, {
        "title": title,
        "genre": genre,
        "premise_text": premise_text,
    })
    roster_user = _with_additional_prompt(roster_user, additional_prompt)
    stage1_inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        *upstream_files,
        _param_input("stage", "1/2 roster"),
    ]
    roster_text = _llm_traced(
        client, work_dir, "characters",
        roster_system, roster_user,
        thinking=True, inputs=stage1_inputs,
        trace_suffix="_stage1_roster",
        outputs=["(roster JSON, 进入 stage 2)"],
    )
    roster = _parse_json_list(roster_text)
    if not roster:
        # Fallback: produce a one-shot single file like before so phase doesn't dead-end
        logger.warning("characters roster JSON parse failed, fallback to single-file mode")
        fallback_system = "你是一位角色设计师。请把所有角色写在一个 markdown 文件里。"
        fb = _llm_traced(
            client, work_dir, "characters",
            fallback_system, roster_user + "\n\n（清单解析失败，请直接用 markdown 输出全部角色，不必 JSON）",
            thinking=True, inputs=stage1_inputs + [_param_input("fallback", "true")],
            trace_suffix="_fallback_single",
            outputs=["设定/角色/角色设定.md"],
        )
        chars_dir = work_dir / "设定" / "角色"
        chars_dir.mkdir(parents=True, exist_ok=True)
        _save_file(chars_dir / "角色设定.md", fb)
        return {"phase": "l0_characters", "outputs": ["设定/角色/角色设定.md"], "fallback": True}

    # ── Overview trace (coordinator) ─────────────────────────────────────
    char_names = [c.get("name", f"角色{i}") for i, c in enumerate(roster)]
    expected_outputs = [f"设定/角色/{_sanitize_slug(n)}.md" for n in char_names]
    _write_overview_trace(
        work_dir, "characters",
        items=char_names,
        inputs=stage1_inputs,
        expected_outputs=expected_outputs,
        note=(
            "characters 两阶段：阶段1 pro+thinking 出清单（见 _stage1_roster trace），"
            "阶段2 flash 并发为每个角色详写。"
        ),
    )

    # ── Stage 2: per-character detail (flash, concurrent) ────────────────
    detail_system = _load_prompt_template("l0_characters_detail_system.txt", (
        "你是一位角色设计师。请为指定角色撰写一份完整的角色卡。\n"
        "结构（markdown）：身份背景 / 性格特质（3核心+1缺陷）/ 核心动机 / "
        "成长弧线 / 关键关系 / 语言风格 / 能力技能 / 出场标志。"
    ))
    items = []
    detail_user_template = _load_prompt_template("l0_characters_detail_user.txt", """为长篇小说《{title}》（{genre}题材）撰写角色「{name}」（{role}）的完整角色卡。

一句话简介：{brief}

世界观与题材参考：{premise_text}

要求：
- 800-1500 字 markdown
- 第一行用 `# {name}`
- 后续段落用 `## 身份背景` / `## 性格特质` 等二级标题
- 不要写其他角色的内容
- 不得改名，名字必须是「{name}」
"""
    )
    for c in roster:
        name = str(c.get("name", "")).strip() or "未命名角色"
        role = str(c.get("role", "配角")).strip()
        brief = str(c.get("brief", "")).strip()
        user = _render_prompt_template(detail_user_template, {
            "title": title,
            "genre": genre,
            "name": name,
            "role": role,
            "brief": brief,
            "premise_text": premise_text,
        })
        user = _with_additional_prompt(user, additional_prompt)
        items.append({
            "slug": name,
            "system": detail_system,
            "user": user,
            "use_flash": True,
            "thinking": False,
            "inputs": stage1_inputs + [
                _param_input("character_name", name),
                _param_input("character_role", role),
                _param_input("character_brief", brief),
                _param_input("stage", "2/2 detail"),
            ],
            "outputs": [f"设定/角色/{_sanitize_slug(name)}.md"],
        })

    results, errors = _run_items_concurrent(client, work_dir, "characters", items, max_workers=5)

    chars_dir = work_dir / "设定" / "角色"
    chars_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for c in roster:
        name = str(c.get("name", "")).strip() or "未命名角色"
        text = results.get(name, "")
        if not text:
            continue
        if not text.strip().startswith("#"):
            text = f"# {name}\n\n{text.strip()}\n"
        _save_file(chars_dir / f"{_sanitize_slug(name)}.md", text)
        written.append(f"设定/角色/{_sanitize_slug(name)}.md")

    # Roster index
    index_lines = [f"# 角色索引\n\n共 {len(roster)} 个角色\n"]
    for c in roster:
        index_lines.append(
            f"- **{c.get('name','?')}**（{c.get('role','?')}）— {c.get('brief','')}"
        )
    _save_file(chars_dir / "_角色索引.md", "\n".join(index_lines))
    written.insert(0, "设定/角色/_角色索引.md")

    if errors and len(written) <= 1:
        raise RuntimeError(f"characters 全部角色失败：{errors}")

    return {"phase": "l0_characters", "outputs": written, "errors": errors, "roster": roster}


def run_l0_factions(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Two-stage faction design: roster JSON (pro) + per-faction detail (flash, concurrent)."""
    upstream_files: list[dict[str, Any]] = []
    context_text = ""
    for f in [
        "设定/题材定位.md",
        "设定/世界观/背景设定.md",
        "设定/世界观/力量体系.md",
        "设定/角色/_角色索引.md",
    ]:
        p = work_dir / f
        if p.exists():
            chunk = p.read_text(encoding="utf-8")[:1500]
            context_text += f"\n--- {f} ---\n{chunk}\n"
            upstream_files.append(_file_input(work_dir, f, len(chunk.encode("utf-8"))))

    # Stage 1: roster
    roster_system = _load_prompt_template("l0_factions_roster_system.txt", (
        "你是一位网文世界观架构师。第一步只输出势力/组织清单。\n"
        "严格按 JSON 数组输出。每项 {\"name\":\"势力名\",\"type\":\"门派|公司|国家|帮派|组织|其他\",\"brief\":\"一句话简介\"}\n"
        "请设计 3-6 个核心势力，覆盖主角阵营、反派阵营、中立阵营。"
    ))
    roster_user_template = _load_prompt_template("l0_factions_roster_user.txt", """为长篇小说《{title}》（{genre}题材）设计势力清单。

参考资料：{context_text}

只输出 JSON 数组（3-6 项），不要 markdown 代码块。
示例：[{{"name":"青云宗","type":"门派","brief":"主角所在的正道大派"}}, ...]
"""
    )
    roster_user = _render_prompt_template(roster_user_template, {
        "title": title,
        "genre": genre,
        "context_text": context_text,
    })
    roster_user = _with_additional_prompt(roster_user, additional_prompt)
    stage1_inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        *upstream_files,
        _param_input("stage", "1/2 roster"),
    ]
    roster_text = _llm_traced(
        client, work_dir, "factions",
        roster_system, roster_user,
        thinking=True, inputs=stage1_inputs,
        trace_suffix="_stage1_roster",
        outputs=["(roster JSON, 进入 stage 2)"],
    )
    roster = _parse_json_list(roster_text)
    if not roster:
        logger.warning("factions roster JSON parse failed, fallback to single file")
        fb = _llm_traced(
            client, work_dir, "factions",
            "你是势力设计师。请把所有势力写在一个 markdown 文件里。",
            roster_user + "\n\n（清单解析失败，请直接 markdown 输出全部势力）",
            thinking=True, inputs=stage1_inputs + [_param_input("fallback", "true")],
            trace_suffix="_fallback_single",
            outputs=["设定/势力/主要势力.md"],
        )
        faction_dir = work_dir / "设定" / "势力"
        faction_dir.mkdir(parents=True, exist_ok=True)
        _save_file(faction_dir / "主要势力.md", fb)
        return {"phase": "l0_factions", "outputs": ["设定/势力/主要势力.md"], "fallback": True}

    names = [r.get("name", f"势力{i}") for i, r in enumerate(roster)]
    expected_outputs = [f"设定/势力/{_sanitize_slug(n)}.md" for n in names]
    _write_overview_trace(
        work_dir, "factions",
        items=names,
        inputs=stage1_inputs,
        expected_outputs=expected_outputs,
        note="factions 两阶段：阶段1 pro 出清单，阶段2 flash 并发详写。",
    )

    detail_system = _load_prompt_template("l0_factions_detail_system.txt", (
        "你是网文世界观架构师。请为指定势力撰写完整的势力档案。\n"
        "结构（markdown）：名称定位 / 起源历史 / 组织架构 / 核心人物 / "
        "势力范围 / 资源与底牌 / 与其他势力的关系 / 在剧情中的作用。"
    ))
    items = []
    detail_user_template = _load_prompt_template("l0_factions_detail_user.txt", """为长篇小说《{title}》（{genre}题材）撰写势力「{name}」（{ftype}）的完整档案。

一句话简介：{brief}

参考资料：{context_text}

要求：
- 600-1200 字 markdown
- 第一行 `# {name}`
- 用 `## 起源历史` / `## 组织架构` 等二级标题
- 不要写其他势力的内容
- 不得改名
"""
    )
    for r in roster:
        name = str(r.get("name", "")).strip() or "未命名势力"
        ftype = str(r.get("type", "组织")).strip()
        brief = str(r.get("brief", "")).strip()
        user = _render_prompt_template(detail_user_template, {
            "title": title,
            "genre": genre,
            "name": name,
            "ftype": ftype,
            "brief": brief,
            "context_text": context_text,
        })
        user = _with_additional_prompt(user, additional_prompt)
        items.append({
            "slug": name,
            "system": detail_system,
            "user": user,
            "use_flash": True,
            "thinking": False,
            "inputs": stage1_inputs + [
                _param_input("faction_name", name),
                _param_input("faction_type", ftype),
                _param_input("faction_brief", brief),
                _param_input("stage", "2/2 detail"),
            ],
            "outputs": [f"设定/势力/{_sanitize_slug(name)}.md"],
        })

    results, errors = _run_items_concurrent(client, work_dir, "factions", items, max_workers=5)

    faction_dir = work_dir / "设定" / "势力"
    faction_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in roster:
        name = str(r.get("name", "")).strip() or "未命名势力"
        text = results.get(name, "")
        if not text:
            continue
        if not text.strip().startswith("#"):
            text = f"# {name}\n\n{text.strip()}\n"
        _save_file(faction_dir / f"{_sanitize_slug(name)}.md", text)
        written.append(f"设定/势力/{_sanitize_slug(name)}.md")

    index_lines = [f"# 势力索引\n\n共 {len(roster)} 个势力\n"]
    for r in roster:
        index_lines.append(
            f"- **{r.get('name','?')}**（{r.get('type','?')}）— {r.get('brief','')}"
        )
    _save_file(faction_dir / "_势力索引.md", "\n".join(index_lines))
    written.insert(0, "设定/势力/_势力索引.md")

    if errors and len(written) <= 1:
        raise RuntimeError(f"factions 全部失败：{errors}")
    return {"phase": "l0_factions", "outputs": written, "errors": errors, "roster": roster}


def run_l0_relations(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Single LLM call to produce the cross-character + cross-faction relationship map."""
    upstream_files: list[dict[str, Any]] = []
    context_text = ""
    for f in [
        "设定/角色/_角色索引.md",
        "设定/势力/_势力索引.md",
        "设定/题材定位.md",
    ]:
        p = work_dir / f
        if p.exists():
            chunk = p.read_text(encoding="utf-8")[:2500]
            context_text += f"\n--- {f} ---\n{chunk}\n"
            upstream_files.append(_file_input(work_dir, f, len(chunk.encode("utf-8"))))

    # Also enumerate per-character / per-faction files (just paths for context)
    chars_dir = work_dir / "设定" / "角色"
    factions_dir = work_dir / "设定" / "势力"
    char_list = sorted([p.stem for p in chars_dir.glob("*.md") if not p.name.startswith("_")]) if chars_dir.exists() else []
    faction_list = sorted([p.stem for p in factions_dir.glob("*.md") if not p.name.startswith("_")]) if factions_dir.exists() else []

    system = _load_prompt_template("l0_relations_system.txt", (
        "你是一位网文关系设计师，擅长把角色与势力之间的关系网络写成可视化清单。"
    ))
    user_template = _load_prompt_template("l0_relations_user.txt", """为长篇小说《{title}》（{genre}题材）梳理关系网络。

已设计的角色：{char_list}
已设计的势力：{faction_list}

参考资料：{context_text}

请输出一份 markdown 关系总图，包含：

## 一、人物关系
- 用列表方式描述每对核心人物之间的关系（亲情/友情/爱情/敌对/师徒/竞争/...）
- 标注关系强度（强/中/弱）和是否会发生变化（贯穿/转折/破裂）

## 二、人物-势力归属
- 每个核心人物所属/敌对的势力

## 三、势力之间的关系
- 势力两两之间的关系（同盟/竞争/敌对/中立）
- 标注关键冲突点

## 四、关系演化时间线
- 哪些关系会在故事中发生重要变化，触发节点是什么

要求：800-1500 字，清晰可查；只用已列出的角色和势力名，不得新增。
"""
    )
    user = _render_prompt_template(user_template, {
        "title": title,
        "genre": genre,
        "char_list": ", ".join(char_list) or "（无）",
        "faction_list": ", ".join(faction_list) or "（无）",
        "context_text": context_text,
    })
    user = _with_additional_prompt(user, additional_prompt)
    inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        _param_input("char_count", len(char_list)),
        _param_input("faction_count", len(faction_list)),
        *upstream_files,
    ]
    result = _llm_traced(
        client, work_dir, "relations", system, user,
        thinking=True, inputs=inputs,
        outputs=["设定/关系.md"],
    )
    _save_file(work_dir / "设定" / "关系.md", result)
    return {"phase": "l0_relations", "output": "设定/关系.md"}


def run_l0_outline(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    target_chapters: int = 30,
    words_per_chapter: int = 3000,
) -> dict[str, Any]:
    """Compatibility wrapper: run outline, volume outline, and chapter outlines."""

    run_l0_book_outline(client, work_dir, title, genre, target_chapters, words_per_chapter)
    run_l0_volume_outline(client, work_dir, title, genre, target_chapters, words_per_chapter)
    return run_l0_chapter_outlines(client, work_dir, title, genre, target_chapters, words_per_chapter)


def run_l0_book_outline(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    target_chapters: int = 30,
    words_per_chapter: int = 3000,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Generate only the whole-book outline into 大纲/大纲.md."""

    all_settings, upstream = _collect_setup_settings_traced(work_dir)

    system = _load_prompt("l0_outline_system.txt") or (
        "你是一位网文大纲架构师，擅长设计全书级故事结构。"
    )
    user_template = _load_prompt_template("l0_outline_user.txt", """请为以下长篇小说设计全书大纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定（已读取题材定位、关系，以及世界观/角色/势力目录下的多个 md 文件；必须完整继承，尤其是角色名、身份、动机、关系、世界观规则）：{all_settings}

一致性硬约束：
- 人物只能沿用“设定/角色/”目录中已经生成的人物文件；不得把角色改名、换身份、换动机或重新发明主角团。
- 如需临时配角，必须标注为“临时配角”，不能替代既有核心角色的位置。
- 事件推进必须服从题材定位、世界观背景、角色关系图，不能另起一套世界观或人物关系。

只输出全书级结构，不要写章节细纲。请包含：
- 全书核心主线（一句话 + 三幕/多幕推进）
- 主要人物线与关系变化
- 核心矛盾如何逐步升级
- 爽点/情绪曲线
- 重要伏笔与回收计划
- 按卷划分建议（只给卷名、章数范围、核心任务）

输出保存为 大纲/大纲.md。"""
    )
    user = _render_prompt_template(user_template, {
        "title": title,
        "genre": genre,
        "target_chapters": target_chapters,
        "words_per_chapter": words_per_chapter,
        "all_settings": all_settings,
    })
    user = _with_additional_prompt(user, additional_prompt)
    inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        _param_input("target_chapters", target_chapters),
        _param_input("words_per_chapter", words_per_chapter),
        *upstream,
    ]
    result = _llm_traced(
        client, work_dir, "outline", system, user,
        thinking=True, inputs=inputs,
        outputs=["大纲/大纲.md"],
    )

    outline_dir = work_dir / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    _save_file(outline_dir / "大纲.md", result)
    return {"phase": "l0_outline", "output": "大纲/大纲.md"}


def _chinese_number(n: int) -> str:
    digits = "零一二三四五六七八九"
    if n <= 0:
        return str(n)
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    return str(n)


def _volume_filename(volume_number: int) -> str:
    return f"卷纲_第{_chinese_number(volume_number)}卷.md"


def _volume_number_from_heading(heading: str) -> int | None:
    m = re.search(r"(?:第|卷)\s*(\d+)\s*卷?", heading)
    if m:
        return int(m.group(1))
    chinese_digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    m = re.search(r"(?:第|卷)?\s*([零〇一二两三四五六七八九十]+)\s*卷", heading)
    if not m:
        return None
    text = m.group(1)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = chinese_digits.get(left, 1) if left else 1
        ones = chinese_digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return chinese_digits.get(text)


def _extract_volume_outlines(text: str) -> list[tuple[int, str]]:
    """Split a combined volume outline into one markdown body per volume."""
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, int | None]] = []
    legacy_line_re = re.compile(r"^(第?[零〇一二两三四五六七八九十\d]+卷|卷[零〇一二两三四五六七八九十\d]+)[:：、\s]")
    for i, line in enumerate(lines):
        stripped = line.strip()
        plain = stripped.lstrip("#").strip()
        is_h1 = stripped.startswith("#") and not stripped.startswith("##")
        is_h2 = stripped.startswith("##") and not stripped.startswith("###")

        # Only H1 / H2 markdown headings are eligible as volume splitters.
        # H3+ headings (e.g. '### 悬置的伏笔（从第一卷承接）') are sub-sections
        # within a volume — treating their incidental "第N卷" mentions as
        # splits explodes one volume into many bogus files.
        if not is_h1 and not is_h2 and not legacy_line_re.match(plain):
            continue
        if "本卷" in plain:
            continue

        if is_h1:
            # Exclude generic doc titles ('# 全书卷纲') and pseudo file-name
            # titles ('# 大纲/卷纲_第一卷.md') by requiring a parseable 卷
            # number and rejecting path-like text.
            looks_like_path = ".md" in plain or "/" in plain or "\\" in plain
            parsed = None if looks_like_path else _volume_number_from_heading(plain)
            if parsed is None:
                continue
            starts.append((i, parsed))
            continue

        if is_h2:
            if "卷" not in plain:
                continue
            parsed = _volume_number_from_heading(plain)
            if parsed is None:
                continue
            starts.append((i, parsed))
            continue

        # Legacy line-start pattern ('第N卷：…' without markdown heading).
        if "卷" in plain:
            starts.append((i, _volume_number_from_heading(plain)))

    if not starts:
        return [(1, text.strip())] if text.strip() else []

    volumes: list[tuple[int, str]] = []
    preface = "".join(lines[:starts[0][0]]).strip()
    used_numbers: set[int] = set()
    for idx, (start, parsed_num) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        volume_number = parsed_num or (idx + 1)
        while volume_number in used_numbers:
            volume_number += 1
        used_numbers.add(volume_number)
        body = "".join(lines[start:end]).strip()
        if idx == 0 and preface:
            body = f"{preface}\n\n{body}"
        if body:
            volumes.append((volume_number, body))
    return volumes


def ensure_volume_outlines_split(work_dir: Path) -> list[str]:
    """Migrate legacy combined volume outlines into per-volume files.

    Scans ``大纲/卷纲*.md`` for files that either:
      - are not named like ``卷纲_第X卷.md`` (old combined file, e.g. ``卷纲.md``), or
      - are named per-volume but contain more than one ``## 第X卷`` heading (an
        accidental combined dump under a per-volume name).

    Such files are passed through ``_extract_volume_outlines``; each extracted
    volume is written to its canonical ``卷纲_第N卷.md`` path. The original file
    is removed only after at least two volumes are successfully extracted, so
    that valid single-volume files are left untouched.

    Returns the list of newly written canonical file paths (relative to
    ``work_dir``), empty if no migration was needed.
    """
    outline_dir = work_dir / "大纲"
    if not outline_dir.exists():
        return []

    canonical_pattern = re.compile(r"^卷纲_第[零〇一二两三四五六七八九十\d]+卷\.md$")
    written: list[str] = []

    candidates: list[Path] = sorted(outline_dir.glob("卷纲*.md"))
    for path in candidates:
        is_canonical_name = bool(canonical_pattern.match(path.name))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        volumes = _extract_volume_outlines(text)

        # Skip files that already look correct: canonical name + at most one
        # detected volume. Files with no detectable volume heading are also
        # left alone (could be empty/manual notes).
        if is_canonical_name and len(volumes) <= 1:
            continue
        if len(volumes) <= 1:
            continue

        for volume_number, body in volumes:
            target = outline_dir / _volume_filename(volume_number)
            # Avoid clobbering an already-correct per-volume file unless this
            # path *is* that file (legacy file happens to match canonical name).
            if target.exists() and target.resolve() != path.resolve():
                continue
            _save_file(target, body)
            rel = str(target.relative_to(work_dir)).replace("\\", "/")
            if rel not in written:
                written.append(rel)

        # Only remove the legacy source if we successfully split it into
        # multiple per-volume files and the source is not itself one of those
        # canonical targets.
        if len(volumes) >= 2 and not is_canonical_name:
            try:
                path.unlink()
            except OSError:
                pass

    return written


def _collect_volume_outlines(work_dir: Path, per_file_limit: int = 4000, total_limit: int = 12000) -> tuple[str, list[dict[str, Any]]]:
    outline_dir = work_dir / "大纲"
    parts: list[str] = []
    inputs: list[dict[str, Any]] = []
    used = 0
    if not outline_dir.exists():
        return "", inputs
    ensure_volume_outlines_split(work_dir)
    for p in sorted(outline_dir.glob("卷纲_*.md")):
        remaining = total_limit - used
        if remaining <= 0:
            break
        rel = str(p.relative_to(work_dir)).replace("\\", "/")
        text = p.read_text(encoding="utf-8")
        chunk = text[:min(per_file_limit, remaining)]
        used += len(chunk)
        parts.append(f"--- {rel} ---\n{chunk}")
        inputs.append(_file_input(work_dir, rel, len(chunk.encode("utf-8")), "卷纲"))
    return "\n\n".join(parts), inputs


def _parse_volume_plan(book_outline: str, target_chapters: int) -> list[dict[str, Any]]:
    """Extract a per-volume plan from the book outline.

    Returns a list of dicts ``{vol_num, ch_start, ch_end, title}`` covering
    chapters 1..target_chapters. Recognises common formats produced by the
    outline-generation prompt, e.g.::

        ### 第一卷：「退休咸鱼的噩梦开端」
        - **章数范围**：第1-6章（6章，约1.8万字）

    Also tolerates ``## 第一卷 - 标题``, range tokens like ``1~6``/``1 到 6``,
    and bracketed titles ``「...」``/``"..."``/``《...》``. If no volumes can
    be parsed, falls back to ~6 chapters per volume (rounded to keep last
    volume non-empty) so the per-volume pipeline always has something to run.
    """
    chinese_digits = {
        "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }

    def _cn_to_int(s: str) -> int | None:
        if not s:
            return None
        if s.isdigit():
            return int(s)
        if s == "十":
            return 10
        if "十" in s:
            left, _, right = s.partition("十")
            tens = chinese_digits.get(left, 1) if left else 1
            ones = chinese_digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return chinese_digits.get(s)

    lines = book_outline.splitlines()
    heading_re = re.compile(r"^#{2,6}\s*第\s*([零〇一二两三四五六七八九十\d]+)\s*卷[:：、\s\-—]*(.*)$")
    title_clean_re = re.compile(r'^[「『"《\(\[【]+|[」』"》\)\]】]+$')
    range_re = re.compile(r"第\s*(\d+)\s*[-~～至到—]+\s*(\d+)\s*章")

    items: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        m = heading_re.match(line.strip())
        if not m:
            continue
        vol_num = _cn_to_int(m.group(1))
        if vol_num is None:
            continue
        # Extract a short title (trim trailing bullet/colon noise)
        raw_title = m.group(2).strip()
        for _ in range(3):
            raw_title = title_clean_re.sub("", raw_title).strip()
        # Look ahead a few lines for a "第A-B章" range
        ch_start: int | None = None
        ch_end: int | None = None
        for j in range(i + 1, min(i + 12, len(lines))):
            following = lines[j]
            if heading_re.match(following.strip()):
                break
            rm = range_re.search(following)
            if rm:
                ch_start, ch_end = int(rm.group(1)), int(rm.group(2))
                break
        items.append({
            "vol_num": vol_num,
            "ch_start": ch_start,
            "ch_end": ch_end,
            "title": raw_title or f"第{vol_num}卷",
        })

    # Deduplicate by vol_num keeping the first occurrence, sort by vol_num.
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda x: x["vol_num"]):
        if it["vol_num"] in seen:
            continue
        seen.add(it["vol_num"])
        deduped.append(it)
    items = deduped

    # Backfill any missing ch_start/ch_end so we always cover 1..target_chapters
    if items:
        for idx, it in enumerate(items):
            if it["ch_start"] is None:
                it["ch_start"] = (items[idx - 1]["ch_end"] + 1) if idx > 0 and items[idx - 1].get("ch_end") else 1
            if it["ch_end"] is None:
                next_start = items[idx + 1]["ch_start"] if idx + 1 < len(items) and items[idx + 1].get("ch_start") else None
                it["ch_end"] = (next_start - 1) if next_start else target_chapters
        # Clamp last to target_chapters
        items[-1]["ch_end"] = max(items[-1]["ch_end"], target_chapters)
        return items

    # Fallback: ~6 chapters per volume (3-7 volumes depending on target_chapters)
    per = 6
    n_vols = max(1, min(8, (target_chapters + per - 1) // per))
    base = target_chapters // n_vols
    rem = target_chapters - base * n_vols
    plan: list[dict[str, Any]] = []
    cursor = 1
    for k in range(n_vols):
        size = base + (1 if k < rem else 0)
        end = cursor + size - 1
        plan.append({
            "vol_num": k + 1,
            "ch_start": cursor,
            "ch_end": end,
            "title": f"第{_chinese_number(k + 1)}卷",
        })
        cursor = end + 1
    return plan


def _run_l0_single_volume(
    client: DeepSeekClient,
    work_dir: Path,
    plan_item: dict[str, Any],
    *,
    title: str,
    genre: str,
    target_chapters: int,
    words_per_chapter: int,
    all_settings: str,
    upstream_settings: list[dict[str, Any]],
    book_outline: str,
    full_plan_brief: str,
    additional_prompt: str | None = None,
) -> str:
    vol_num = plan_item["vol_num"]
    ch_start = plan_item["ch_start"]
    ch_end = plan_item["ch_end"]
    plan_title = plan_item.get("title") or ""
    chapter_count = max(1, ch_end - ch_start + 1)
    volume_words = chapter_count * words_per_chapter

    system = _load_prompt_template(
        "l0_volume_outline_system.txt",
        "你是一位网文卷纲设计师，负责把全书结构展开成单卷的详细卷纲。每次只写一卷的完整卷纲。",
    )
    user_template = _load_prompt_template("l0_volume_outline_user.txt", """请只为《{title}》（{genre}）的【第{volume_name}卷】生成完整、可执行的卷纲。

全书规模：{target_chapters}章，每章约{words_per_chapter}字。
本卷信息：第{vol_num}卷 · 章节范围 第{ch_start}-{ch_end}章 · 约{chapter_count}章 · 约{volume_words}字
本卷参考标题（可沿用或微调）：{plan_title}

已有设定（节选）：
{all_settings}

全书大纲（节选）：
{book_outline}

各卷规划（仅供参考，本次只展开本卷）：
{full_plan_brief}

一致性硬约束：
- 严格、只输出"第{vol_num}卷"的内容；不准生成其它卷，不准用"以下卷略"、"下一卷再讲"等推托语。
- 不要寒暄、不要"好的"开头、不要生成形如"# 大纲/卷纲_第N卷.md"之类的伪文件名标题。
- 不要把章节细纲写进卷纲，只写卷级推进。
- 卷标题用二级标题 `## 第{_chinese_number(vol_num)}卷：卷名` 开头；不要使用 H1 (`#`)。
- 沿用既有角色名、身份、动机、关系，不得新增主角/反派或改写关系。

本卷需包含：
- 卷名（与参考标题保持一致或微调，给出最终卷名）
- 章节范围与字数范围（必须落在第{ch_start}-{ch_end}章，约{volume_words}字）
- 本卷核心事件
- 起始状态 → 结束状态
- 本卷主爽点与情绪曲线
- 本卷人物线变化（每位核心人物如何在本卷推进）
- 本卷伏笔：埋设 / 回收 / 悬置
- 本卷与前/后卷的衔接钩子
"""
    )
    user = _render_prompt_template(user_template, {
        "title": title,
        "genre": genre,
        "volume_name": _chinese_number(vol_num),
        "target_chapters": target_chapters,
        "words_per_chapter": words_per_chapter,
        "vol_num": vol_num,
        "ch_start": ch_start,
        "ch_end": ch_end,
        "chapter_count": chapter_count,
        "volume_words": volume_words,
        "plan_title": plan_title or "（无）",
        "all_settings": all_settings[:4000],
        "book_outline": book_outline[:5000],
        "full_plan_brief": full_plan_brief,
    })
    user = _with_additional_prompt(user, additional_prompt)
    inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        _param_input("vol_num", vol_num),
        _param_input("ch_start", ch_start),
        _param_input("ch_end", ch_end),
        _param_input("words_per_chapter", words_per_chapter),
        *upstream_settings,
        _file_input(work_dir, "大纲/大纲.md", len(book_outline.encode("utf-8")), "全书大纲（节选）"),
    ]
    return _llm_traced(
        client, work_dir, f"volume_outline_v{vol_num:02d}", system, user,
        thinking=True, inputs=inputs,
        outputs=[f"大纲/{_volume_filename(vol_num)}"],
    )


def run_l0_volume_outline(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    target_chapters: int = 30,
    words_per_chapter: int = 3000,
    additional_prompt: str | None = None,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Generate volume-level outlines, one LLM call per volume.

    Volume plan is parsed from ``大纲/大纲.md``; if parsing fails the function
    falls back to ~6 chapters per volume. Each volume is generated by its own
    LLM call so AI cannot "skip" later volumes by stopping after the first.
    """

    all_settings, upstream_settings = _collect_setup_settings_traced(work_dir)
    book_outline = _read_rel(work_dir, "大纲/大纲.md", limit=8000)
    plan = _parse_volume_plan(book_outline, target_chapters)

    outline_dir = work_dir / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    for old_path in outline_dir.glob("卷纲_*.md"):
        old_path.unlink()

    brief_lines = [f"- 第{p['vol_num']}卷：第{p['ch_start']}-{p['ch_end']}章 · {p.get('title') or ''}" for p in plan]
    full_plan_brief = "\n".join(brief_lines)

    outputs: list[str] = []
    for idx, item in enumerate(plan):
        if callable(progress_cb):
            try:
                progress_cb(idx + 1, len(plan), item)
            except Exception:
                pass
        body = _run_l0_single_volume(
            client, work_dir, item,
            title=title, genre=genre,
            target_chapters=target_chapters,
            words_per_chapter=words_per_chapter,
            all_settings=all_settings,
            upstream_settings=upstream_settings,
            book_outline=book_outline,
            full_plan_brief=full_plan_brief,
            additional_prompt=additional_prompt,
        )
        filename = _volume_filename(item["vol_num"])
        # If AI ignored format and dumped multiple volumes anyway, split them
        # so we don't lose content. Otherwise write the body verbatim.
        sub_volumes = _extract_volume_outlines(body)
        if len(sub_volumes) > 1:
            for sub_num, sub_body in sub_volumes:
                # Prefer the requested vol number for the first entry to keep
                # filename ↔ plan alignment.
                target_num = item["vol_num"] if sub_num == sub_volumes[0][0] else sub_num
                sub_name = _volume_filename(target_num)
                _save_file(outline_dir / sub_name, sub_body)
                rel = f"大纲/{sub_name}"
                if rel not in outputs:
                    outputs.append(rel)
        else:
            _save_file(outline_dir / filename, body.strip())
            outputs.append(f"大纲/{filename}")

    if not outputs:
        # Defensive: produce a minimal first-volume file rather than crash.
        filename = _volume_filename(1)
        _save_file(outline_dir / filename, "（生成失败：未解析到任何卷计划）")
        outputs.append(f"大纲/{filename}")

    return {"phase": "l0_volume_outline", "outputs": outputs, "output": outputs[0], "plan": plan}


def run_l0_chapter_outlines(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    target_chapters: int = 30,
    words_per_chapter: int = 3000,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Generate chapter outlines from setup files, 大纲.md, and 卷纲_*.md."""

    all_settings, upstream_settings = _collect_setup_settings_traced(work_dir)
    book_outline = _read_rel(work_dir, "大纲/大纲.md", limit=5000)
    volume_outlines, volume_inputs = _collect_volume_outlines(work_dir)
    outline_context = f"{all_settings}\n\n--- 大纲/大纲.md ---\n{book_outline}\n\n{volume_outlines}"
    system = _load_prompt_template(
        "l0_chapter_outlines_system.txt",
        "你是一位网文细纲设计师。根据全书大纲和卷纲，为指定章节生成可直接写作的章节细纲。",
    )
    user_template = _load_prompt_template("l0_chapter_outlines_user.txt", """请为《{title}》（{genre}题材）生成第1到第{target_chapters}章的章节细纲。
每章约{words_per_chapter}字。

设定、大纲与卷纲参考：
{outline_context}

一致性硬约束：
- 章节细纲只能使用角色设计、全书大纲、卷纲中已经确立的核心人物与关系。
- 每章“出场角色”必须优先从角色设定中选择，并保持身份、动机、说话方式、关系不变。
- 不得凭空替换人物名、阵营、情感线或世界观规则；确需新增路人/工具人时标注为临时配角。

每章细纲包含：
- 核心事件
- 章首钩子
- 主要冲突
- 爽点
- 章尾钩子
- 出场角色
- 埋设/回收伏笔
- 情绪目标

用"## 第N章"分隔每章。不要重复输出全书大纲或卷纲。"""
    )
    user = _render_prompt_template(user_template, {
        "title": title,
        "genre": genre,
        "target_chapters": target_chapters,
        "words_per_chapter": words_per_chapter,
        "outline_context": outline_context[:9000],
    })
    user = _with_additional_prompt(user, additional_prompt)
    truncated_used = min(len(outline_context.encode("utf-8")), 9000 * 3)
    inputs = [
        _param_input("title", title),
        _param_input("genre", genre),
        _param_input("target_chapters", target_chapters),
        _param_input("words_per_chapter", words_per_chapter),
        *upstream_settings,
        _file_input(work_dir, "大纲/大纲.md", len(book_outline.encode("utf-8")), "全书大纲（首5000字）"),
        *volume_inputs,
        _param_input("注意：以上拼接后整体再被截断到 9000 字字符", truncated_used),
    ]
    result = _llm_traced(
        client, work_dir, "chapter_outlines", system, user,
        thinking=True, inputs=inputs,
        outputs=[f"大纲/细纲_第001章.md ... 细纲_第{target_chapters:03d}章.md"],
    )

    outline_dir = work_dir / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    chapter_outlines = _extract_chapter_outlines(result, target_chapters)

    for i, ch_text in enumerate(chapter_outlines):
        ch_num = i + 1
        _save_file(outline_dir / f"细纲_第{ch_num:03d}章.md", ch_text)

    # If LLM didn't produce enough chapters, generate remaining individually
    if len(chapter_outlines) < target_chapters:
        _fill_remaining_outlines(
            client, work_dir, title, genre, outline_context,
            len(chapter_outlines) + 1, target_chapters, words_per_chapter,
        )

    _init_tracking_files(work_dir, target_chapters)

    generated_count = sum(
        1 for ch_num in range(1, target_chapters + 1)
        if (outline_dir / f"细纲_第{ch_num:03d}章.md").exists()
    )
    return {"phase": "l0_chapter_outlines", "chapters_generated": generated_count}


def _collect_setup_settings(work_dir: Path) -> str:
    text, _ = _collect_setup_settings_traced(work_dir)
    return text


def _collect_setup_settings_traced(work_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Return (concatenated text, inputs metadata list) for prompt building."""
    all_settings = ""
    inputs: list[dict[str, Any]] = []
    file_limits = [
        ("设定/题材定位.md", 2500),
        ("设定/关系.md", 1500),
    ]
    for f, limit in file_limits:
        p = work_dir / f
        if p.exists():
            chunk = p.read_text(encoding="utf-8")[:limit]
            all_settings += f"\n--- {f} ---\n{chunk}"
            inputs.append(_file_input(work_dir, f, len(chunk.encode("utf-8"))))
    dir_limits = [
        ("设定/世界观", 2500, 12000),
        ("设定/角色", 2200, 16000),
        ("设定/势力", 1800, 10000),
    ]
    for rel_dir, per_file_limit, total_limit in dir_limits:
        dir_path = work_dir / rel_dir
        if not dir_path.exists() or not dir_path.is_dir():
            continue
        used = 0
        for p in sorted(dir_path.glob("*.md")):
            rel_path = str(p.relative_to(work_dir)).replace("\\", "/")
            text = p.read_text(encoding="utf-8")
            remaining = total_limit - used
            if remaining <= 0:
                break
            limit = min(per_file_limit, remaining)
            chunk = text[:limit]
            used += len(chunk)
            all_settings += f"\n--- {rel_path} ---\n{chunk}"
            inputs.append(_file_input(work_dir, rel_path, len(chunk.encode("utf-8"))))
    return all_settings, inputs


def _read_rel(work_dir: Path, rel_path: str, limit: int = 4000) -> str:
    path = work_dir / rel_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:limit]


def _init_tracking_files(work_dir: Path, target_chapters: int) -> None:
    from generator.long_novel.l2_chapter_write import ensure_tracking_files

    ensure_tracking_files(work_dir, target_chapters)


def _extract_chapter_outlines(text: str, max_chapters: int, start_ch: int = 1) -> list[str]:
    """Attempt to extract individual chapter outlines from the LLM output."""
    outlines = []
    for i in range(start_ch, max_chapters + 1):
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
    all_settings: str,
    start_ch: int,
    end_ch: int,
    words_per_chapter: int,
    additional_prompt: str | None = None,
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

        system = _load_prompt_template(
            "l0_chapter_outlines_fill_system.txt",
            "你是一位网文细纲设计师。为指定章节生成细纲。",
        )
        user_template = _load_prompt_template("l0_chapter_outlines_fill_user.txt", """为{title}（{genre}题材）生成第{batch_start}到第{batch_end}章的细纲。
每章约{words_per_chapter}字。
已有设定、大纲、卷纲参考：{all_settings}
上一章细纲参考：{prev_outline}

一致性硬约束：沿用既有角色设定、人物关系、全书大纲和卷纲；不得改名、换身份、重置关系或另起世界观。

每章细纲包含：核心事件、章首钩子、主要冲突、爽点、章尾钩子、出场角色、埋设/回收伏笔、情绪目标。
用"## 第N章"分隔每章。"""
        )
        user = _render_prompt_template(user_template, {
            "title": title,
            "genre": genre,
            "batch_start": batch_start,
            "batch_end": batch_end,
            "words_per_chapter": words_per_chapter,
            "all_settings": all_settings[:5000],
            "prev_outline": prev_outline,
        })
        user = _with_additional_prompt(user, additional_prompt)
        inputs = [
            _param_input("title", title),
            _param_input("genre", genre),
            _param_input("batch_start", batch_start),
            _param_input("batch_end", batch_end),
            _param_input("words_per_chapter", words_per_chapter),
            _param_input("all_settings (truncated to 5000 chars)", len(all_settings[:5000])),
            _param_input("prev_outline (上一章细纲首800字)", len(prev_outline)),
        ]
        result = _llm_traced(
            client, work_dir, "chapter_outlines", system, user,
            thinking=True, inputs=inputs,
            trace_suffix=f"_fill_{batch_start:03d}_{batch_end:03d}",
            outputs=[f"大纲/细纲_第{n:03d}章.md" for n in range(batch_start, batch_end + 1)],
        )
        ch_outlines = _extract_chapter_outlines(result, batch_end, start_ch=batch_start)
        for j, ch_text in enumerate(ch_outlines):
            ch_num = batch_start + j
            if ch_num <= end_ch:
                _save_file(outline_dir / f"细纲_第{ch_num:03d}章.md", ch_text)
        logger.info("Generated outlines for chapters %d-%d", batch_start, batch_end)


def run_l0_extend_chapter_outlines(
    client: DeepSeekClient,
    work_dir: Path,
    title: str,
    genre: str,
    old_target_chapters: int,
    new_target_chapters: int,
    words_per_chapter: int = 3000,
    additional_prompt: str | None = None,
) -> dict[str, Any]:
    """Extend an existing book plan by generating only new chapter outlines."""

    if new_target_chapters <= old_target_chapters:
        raise ValueError("new_target_chapters must be greater than old_target_chapters")

    outline_dir = work_dir / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    start_ch = old_target_chapters + 1
    end_ch = new_target_chapters

    all_settings, upstream_settings = _collect_setup_settings_traced(work_dir)
    book_outline = _read_rel(work_dir, "大纲/大纲.md", limit=7000)

    volume_parts: list[str] = []
    ensure_volume_outlines_split(work_dir)
    for p in sorted(outline_dir.glob("卷纲_*.md")):
        text = p.read_text(encoding="utf-8")[:4000]
        volume_parts.append(f"--- 大纲/{p.name} ---\n{text}")
    volume_context = "\n\n".join(volume_parts)

    recent_outline_parts: list[str] = []
    recent_start = max(1, old_target_chapters - 4)
    for ch_num in range(recent_start, old_target_chapters + 1):
        p = outline_dir / f"细纲_第{ch_num:03d}章.md"
        if p.exists():
            recent_outline_parts.append(f"--- 大纲/{p.name} ---\n{p.read_text(encoding='utf-8')[:1800]}")

    recent_draft_parts: list[str] = []
    from generator.long_novel.l2_chapter_write import find_chapter_text
    for ch_num in range(recent_start, old_target_chapters + 1):
        p = find_chapter_text(work_dir, ch_num)
        if p is not None:
            text = p.read_text(encoding="utf-8")
            recent_draft_parts.append(f"--- 正文/{p.parent.name}/{p.name}（节选） ---\n{text[-1800:]}")

    extension_context = f"""
--- 已有设定 ---
{all_settings}

--- 大纲/大纲.md ---
{book_outline}

--- 已有卷纲 ---
{volume_context}

--- 最近章节细纲 ---
{chr(10).join(recent_outline_parts)}

--- 最近已写正文结尾 ---
{chr(10).join(recent_draft_parts)}
""".strip()

    plan_path = outline_dir / f"续写规划_第{start_ch:03d}-{end_ch:03d}章.md"
    system = _load_prompt_template(
        "l0_extend_chapters_system.txt",
        "你是长篇网文的续写规划编辑，擅长在不推翻既有设定的前提下扩展后续剧情。",
    )
    user_template = _load_prompt_template("l0_extend_chapters_user.txt", """请为《{title}》（{genre}）制定第{start_ch}到第{end_ch}章的续写规划。

当前原计划到第{old_target_chapters}章，现在扩展到第{new_target_chapters}章；每章约{words_per_chapter}字。

参考材料：
{extension_context}

硬性要求：
- 不要重写第1到第{old_target_chapters}章，不要推翻已经写完的正文和细纲。
- 后续剧情必须承接最近章节的结尾、人物状态、已建立关系和世界观规则。
- 核心人物只能沿用设定/角色目录中的人物；确需新增人物时标注为临时配角。
- 输出只写续写规划，用于指导后续细纲生成，不要直接写正文。

请包含：
- 第{start_ch}到第{end_ch}章的阶段目标
- 新增冲突如何从现有冲突自然升级
- 主要人物关系变化
- 爽点和情绪曲线
- 伏笔埋设/回收建议
- 每10章左右的阶段钩子"""
    )
    user = _render_prompt_template(user_template, {
        "title": title,
        "genre": genre,
        "start_ch": start_ch,
        "end_ch": end_ch,
        "old_target_chapters": old_target_chapters,
        "new_target_chapters": new_target_chapters,
        "words_per_chapter": words_per_chapter,
        "extension_context": extension_context[:16000],
    })
    user = _with_additional_prompt(user, additional_prompt)
    plan = _llm_traced(
        client, work_dir, "extend_chapters", system, user,
        thinking=True,
        inputs=[
            _param_input("old_target_chapters", old_target_chapters),
            _param_input("new_target_chapters", new_target_chapters),
            _param_input("words_per_chapter", words_per_chapter),
            *upstream_settings,
            _file_input(work_dir, "大纲/大纲.md", len(book_outline.encode("utf-8")), "全书大纲"),
            _param_input("recent_outline_count", len(recent_outline_parts)),
            _param_input("recent_draft_excerpt_count", len(recent_draft_parts)),
        ],
        trace_suffix=f"_{start_ch:03d}_{end_ch:03d}",
        outputs=[str(plan_path.relative_to(work_dir)).replace("\\", "/")],
    )
    _save_file(plan_path, plan)

    fill_context = f"{extension_context}\n\n--- {plan_path.relative_to(work_dir)} ---\n{plan}"
    _fill_remaining_outlines(
        client,
        work_dir,
        title,
        genre,
        fill_context,
        start_ch,
        end_ch,
        words_per_chapter,
        additional_prompt,
    )

    generated_count = sum(
        1 for ch_num in range(start_ch, end_ch + 1)
        if (outline_dir / f"细纲_第{ch_num:03d}章.md").exists()
    )
    return {
        "phase": "l0_extend_chapters",
        "old_target_chapters": old_target_chapters,
        "new_target_chapters": new_target_chapters,
        "start_chapter": start_ch,
        "end_chapter": end_ch,
        "chapters_generated": generated_count,
        "plan_path": str(plan_path.relative_to(work_dir)).replace("\\", "/"),
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "run_l0_premise",
    "run_l0_world",
    "run_l0_characters",
    "run_l0_factions",
    "run_l0_relations",
    "run_l0_book_outline",
    "run_l0_volume_outline",
    "run_l0_chapter_outlines",
    "run_l0_extend_chapter_outlines",
    "run_l0_outline",
]
