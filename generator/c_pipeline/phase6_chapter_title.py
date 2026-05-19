"""Phase 6 — chapter titling (pro/flash, no thinking).

Pipeline (decision: 番茄短故事单篇内部加「第X章 短标题」):
    final_md = read 5_最终稿.md
    paragraphs = split_paragraphs(final_md)
    completion = client.chat_completion(messages, model=v4-pro,
                                        thinking_mode=False, purpose="phase_6")
    plan = parse_chapter_plan(completion.text)  # JSON with start_para_index + title
    chaptered_md = render_chapters(paragraphs, plan)
    write 6_最终稿_带章节.md

This phase only **inserts** chapter headers into existing manuscript text — it
never rewrites the body. The LLM call returns a strict JSON plan (split points
+ titles); on parse failure or constraint violation we fall back to evenly-
spaced splits with placeholder titles so production never blocks here.
"""

from __future__ import annotations

import json
import logging
import re
import string
from dataclasses import dataclass, field
from pathlib import Path

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, DeepSeekClient
from generator.c_pipeline.cost_tracker import CostTracker
from generator.c_pipeline.validators import count_chinese_chars

logger = logging.getLogger(__name__)


_PHASE6_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase6_chapter.txt"
_DEFAULT_MIN_CHAPTERS = 5
_DEFAULT_MAX_CHAPTERS = 10
_DEFAULT_TITLE_MIN = 3
_DEFAULT_TITLE_MAX = 8

_CHINESE_NUM = [
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
]


class PhaseChapterError(RuntimeError):
    """Raised when Phase 6 cannot read its inputs."""


@dataclass(frozen=True)
class Chapter:
    start_para_index: int
    title: str


@dataclass(frozen=True)
class Phase6Result:
    """Outcome of one ``run_chapter_titling`` call."""

    chaptered_md: str
    chaptered_path: Path
    chapter_count: int
    titles: list[str]
    char_count: int
    llm_completion: ChatCompletion | None
    used_fallback: bool
    warnings: list[str] = field(default_factory=list)


def run_chapter_titling(
    config: LoadedConfig,
    *,
    work_dir: Path,
    final_path: Path | None = None,
    client: DeepSeekClient | None = None,
    cost_tracker: CostTracker | None = None,
) -> Phase6Result:
    """Run Phase 6 — split manuscript into chapters and title each one."""
    work_dir = Path(work_dir)
    final_path = Path(final_path) if final_path else work_dir / "5_最终稿.md"
    if not final_path.exists():
        raise PhaseChapterError(
            f"Phase 6 needs Phase 5 output but 5_最终稿.md missing: {final_path}"
        )
    final_md = final_path.read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(final_md)
    if not paragraphs:
        raise PhaseChapterError("Phase 6: 5_最终稿.md has no paragraphs to chapter")

    settings = _resolve_settings(config)

    completion: ChatCompletion | None = None
    used_fallback = False
    warnings: list[str] = []

    if client is None:
        client = DeepSeekClient(config)

    chapters: list[Chapter] = []
    if settings["enabled"]:
        try:
            messages = build_phase6_prompt(
                paragraphs=paragraphs,
                min_chapters=settings["min_chapters"],
                max_chapters=settings["max_chapters"],
                title_min_chars=settings["title_min_chars"],
                title_max_chars=settings["title_max_chars"],
            )
            model_override = _resolve_phase6_model(config, client, cost_tracker)
            completion = client.chat_completion(
                messages,
                thinking_mode=False,
                model=model_override,
                purpose="phase_6",
            )
            chapters = _parse_chapter_plan(
                completion.text or "",
                paragraph_count=len(paragraphs),
                min_chapters=settings["min_chapters"],
                max_chapters=settings["max_chapters"],
                title_min_chars=settings["title_min_chars"],
                title_max_chars=settings["title_max_chars"],
            )
        except (ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"phase 6 LLM plan invalid, falling back: {exc}")
            chapters = []
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(
                f"phase 6 LLM call failed ({exc.__class__.__name__}: {exc}), falling back"
            )
            chapters = []

    if not chapters:
        chapters = _fallback_evenly_split(
            paragraph_count=len(paragraphs),
            min_chapters=settings["min_chapters"],
            max_chapters=settings["max_chapters"],
        )
        used_fallback = True

    chaptered_md = render_chapters(paragraphs, chapters)
    chaptered_path = work_dir / "6_最终稿_带章节.md"
    chaptered_path.write_text(chaptered_md, encoding="utf-8")

    titles = [c.title for c in chapters]
    return Phase6Result(
        chaptered_md=chaptered_md,
        chaptered_path=chaptered_path,
        chapter_count=len(chapters),
        titles=titles,
        char_count=count_chinese_chars(chaptered_md),
        llm_completion=completion,
        used_fallback=used_fallback,
        warnings=warnings,
    )


# ============================================================ prompt building


def build_phase6_prompt(
    *,
    paragraphs: list[str],
    min_chapters: int,
    max_chapters: int,
    title_min_chars: int,
    title_max_chars: int,
) -> list[dict[str, str]]:
    """Compose Phase 6 messages."""
    template_str = _PHASE6_PROMPT_FILE.read_text(encoding="utf-8")
    numbered = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))
    template = string.Template(template_str)
    user_text = template.safe_substitute(
        numbered_paragraphs=numbered,
        min_chapters=min_chapters,
        max_chapters=max_chapters,
        title_min_chars=title_min_chars,
        title_max_chars=title_max_chars,
    )
    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文章节编辑。你只输出 JSON,不写任何解释、"
                "前后缀或代码围栏。每个 chapter 的 title 不带'第几章',"
                "代码会自动加。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


# ============================================================ rendering


def render_chapters(paragraphs: list[str], chapters: list[Chapter]) -> str:
    """Insert `第X章 标题` headers between paragraph slices."""
    if not chapters:
        return "\n\n".join(paragraphs)
    parts: list[str] = []
    sorted_chs = sorted(chapters, key=lambda c: c.start_para_index)
    for i, ch in enumerate(sorted_chs):
        end = (
            sorted_chs[i + 1].start_para_index
            if i + 1 < len(sorted_chs)
            else len(paragraphs)
        )
        body = "\n\n".join(paragraphs[ch.start_para_index:end]).strip()
        header = f"第{_chinese_numeral(i + 1)}章 {ch.title}"
        parts.append(f"{header}\n\n{body}" if body else header)
    return "\n\n".join(parts)


def _chinese_numeral(n: int) -> str:
    """Convert 1..20 to 一..二十. Beyond 20 falls back to digits."""
    if 1 <= n <= len(_CHINESE_NUM):
        return _CHINESE_NUM[n - 1]
    return str(n)


# ============================================================ parsing


def _parse_chapter_plan(
    raw: str,
    *,
    paragraph_count: int,
    min_chapters: int,
    max_chapters: int,
    title_min_chars: int,
    title_max_chars: int,
) -> list[Chapter]:
    """Parse LLM JSON output into Chapter list. Raise ValueError on bad shape."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty LLM response")

    # Strip optional code fence
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    # Locate the JSON object
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        raise ValueError("no JSON object found in response")
    payload = json.loads(text[first:last + 1])

    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ValueError("'chapters' missing or empty")

    if not (min_chapters <= len(raw_chapters) <= max_chapters):
        raise ValueError(
            f"chapter count {len(raw_chapters)} outside "
            f"[{min_chapters}, {max_chapters}]"
        )

    parsed: list[Chapter] = []
    seen_indices: set[int] = set()
    for entry in raw_chapters:
        if not isinstance(entry, dict):
            raise ValueError(f"chapter entry not a dict: {entry!r}")
        idx = entry.get("start_para_index")
        title = entry.get("title")
        if not isinstance(idx, int) or idx < 0 or idx >= paragraph_count:
            raise ValueError(f"start_para_index out of range: {idx!r}")
        if idx in seen_indices:
            raise ValueError(f"duplicate start_para_index: {idx}")
        seen_indices.add(idx)
        if not isinstance(title, str):
            raise ValueError(f"title not a string: {title!r}")
        title = title.strip()
        title = re.sub(r"^第[一二三四五六七八九十百千零0-9]+章\s*", "", title)
        if not title:
            raise ValueError("empty title after stripping prefix")
        title_chars = count_chinese_chars(title) or len(title)
        if title_chars > title_max_chars + 4:
            raise ValueError(f"title too long: {title!r}")
        parsed.append(Chapter(start_para_index=idx, title=title))

    parsed.sort(key=lambda c: c.start_para_index)
    if parsed[0].start_para_index != 0:
        raise ValueError(
            f"first chapter must start at paragraph 0, got {parsed[0].start_para_index}"
        )
    return parsed


# ============================================================ fallback


def _fallback_evenly_split(
    *,
    paragraph_count: int,
    min_chapters: int,
    max_chapters: int,
) -> list[Chapter]:
    """Produce evenly-spaced chapters with placeholder titles."""
    target = min(max(min_chapters, paragraph_count // 4 or min_chapters), max_chapters)
    target = max(1, min(target, paragraph_count))
    step = max(1, paragraph_count // target)
    chapters: list[Chapter] = []
    for i in range(target):
        idx = min(i * step, paragraph_count - 1)
        if any(c.start_para_index == idx for c in chapters):
            continue
        chapters.append(Chapter(start_para_index=idx, title=f"未命名{i + 1}"))
    if chapters[0].start_para_index != 0:
        chapters[0] = Chapter(start_para_index=0, title=chapters[0].title)
    return chapters


# ============================================================ helpers


def _split_paragraphs(text: str) -> list[str]:
    """Split a manuscript by blank-line boundaries; trims whitespace per paragraph."""
    parts = re.split(r"\n\s*\n", (text or "").strip())
    out: list[str] = []
    for p in parts:
        cleaned = p.strip()
        if cleaned:
            out.append(cleaned)
    return out


def _resolve_settings(config: LoadedConfig) -> dict[str, int | bool]:
    cfg = (config.data.get("c_pipeline", {}) or {}).get("chapter_titling", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "min_chapters": int(cfg.get("min_chapters") or _DEFAULT_MIN_CHAPTERS),
        "max_chapters": int(cfg.get("max_chapters") or _DEFAULT_MAX_CHAPTERS),
        "title_min_chars": int(cfg.get("title_min_chars") or _DEFAULT_TITLE_MIN),
        "title_max_chars": int(cfg.get("title_max_chars") or _DEFAULT_TITLE_MAX),
    }


def _resolve_phase6_model(
    config: LoadedConfig,
    client: DeepSeekClient,
    cost_tracker: CostTracker | None,
) -> str | None:
    """Pick model for Phase 6; honour B2 / daily-token degrade."""
    if cost_tracker is None:
        return None
    deepseek = config.data.get("deepseek", {}) or {}
    default_model = (
        getattr(client.settings, "model", None)
        or str(deepseek.get("model") or "deepseek-v4-pro")
    )
    flash_model = (
        getattr(client.settings, "flash_model", None)
        or str(deepseek.get("flash_model") or "deepseek-v4-flash")
    )
    chosen = cost_tracker.select_model_for_phase(
        "phase_6",
        default_model=default_model,
        flash_model=flash_model,
    )
    return chosen if chosen != default_model else None


__all__ = [
    "Chapter",
    "Phase6Result",
    "PhaseChapterError",
    "build_phase6_prompt",
    "render_chapters",
    "run_chapter_titling",
]
