"""Phase 2 — produce 2_小节大纲.md (section breakdown).

Pipeline (PLAN §3.1, §4):
    framework_md = read 1_设定.md from work_dir
    target_length = pull from 0_选题.json or argument
    completion = client.chat_completion(messages, model=v4-pro,
                                        thinking_mode=True, purpose="phase_2")
    parse markdown table → OutlineSection list
    code validators (decisions #14):
        - section count in [8, 15]
        - each target_words in [800, 1500]
        - total target_words within ±10% of target_length
    on validation failure, append a "fix this" follow-up message and retry,
        max ``max_retries`` times (default 2 per PLAN §3.1)
    write 2_小节大纲.md

Decision #11 says Phase 0-2 fail-direct; here that means "fail after retries
exhausted". Mock mode synthesizes a deterministic, valid outline so dry-run
smoke tests still pass without a real model.
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
from generator.c_pipeline.validators import (
    check_outline_section_count,
    check_outline_section_words,
    check_total_word_count,
)

logger = logging.getLogger(__name__)


_PHASE2_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase2_outline.txt"


SECTION_COUNT_MIN = 8
SECTION_COUNT_MAX = 15
SECTION_CHARS_MIN = 800
SECTION_CHARS_MAX = 1500
TOTAL_TOLERANCE = 0.10


class PhaseOutlineError(RuntimeError):
    """Raised when Phase 2 cannot produce a validating outline after retries."""


@dataclass(frozen=True)
class OutlineSection:
    """One row of the outline table."""

    index: int
    main_event: str
    sub_events: list[str]
    emotion: str
    new_info: str
    hook: str
    foreshadowing: str
    static_dynamic: str
    dialogue_ratio: str
    target_words: int


@dataclass(frozen=True)
class Phase2Result:
    """Outcome of one ``run_outline`` call."""

    outline_md: str
    outline_path: Path
    sections: list[OutlineSection]
    target_length: int
    total_target_words: int
    llm_completion: ChatCompletion
    used_fallback: bool
    attempts: int
    warnings: list[str] = field(default_factory=list)


# ============================================================ public


def run_outline(
    config: LoadedConfig,
    *,
    work_dir: Path,
    framework_path: Path | None = None,
    pitch_path: Path | None = None,
    target_length: int | None = None,
    max_retries: int = 2,
    client: DeepSeekClient | None = None,
) -> Phase2Result:
    """Run Phase 2 — outline with hard validators + retries.

    Args:
        config: loaded ANW config.
        work_dir: ``data/works/{story_id}/``.
        framework_path: defaults to ``work_dir/1_设定.md``.
        pitch_path: defaults to ``work_dir/0_选题.json``; used to read
            ``target_length`` when the caller does not pass it.
        target_length: target manuscript word count (mid-point of pitch's
            target_length range when not supplied).
        max_retries: how many times to re-prompt on validator failure (default 2,
            per PLAN §3.1).
    """
    work_dir = Path(work_dir)
    framework_path = Path(framework_path) if framework_path else work_dir / "1_设定.md"
    pitch_path = Path(pitch_path) if pitch_path else work_dir / "0_选题.json"

    if not framework_path.exists():
        raise PhaseOutlineError(
            f"Phase 2 needs Phase 1 output but 1_设定.md missing: {framework_path}"
        )
    framework_md = framework_path.read_text(encoding="utf-8")

    if target_length is None:
        target_length = _resolve_target_length(pitch_path)

    project_root = _project_root(config)
    if client is None:
        client = DeepSeekClient(config)

    base_messages = build_phase2_prompt(
        framework_md=framework_md,
        target_length=target_length,
        project_root=project_root,
    )

    last_error: str = ""
    last_completion: ChatCompletion | None = None
    warnings: list[str] = []

    for attempt in range(max_retries + 1):
        messages = list(base_messages)
        if last_error and attempt > 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"上一次输出未通过硬校验:{last_error}\n"
                        "请严格按要求重新产出 2_小节大纲.md 全文,"
                        "不要解释,不要保留之前的错误版本。"
                    ),
                }
            )
        completion = client.chat_completion(
            messages,
            thinking_mode=True,
            purpose=f"phase_2{'_retry_' + str(attempt) if attempt else ''}",
        )
        last_completion = completion

        sections, parse_warnings = parse_outline_md(completion.text)
        warnings.extend(parse_warnings)
        if not sections:
            last_error = "未能解析出大纲表格(0 行)"
            warnings.append(f"attempt {attempt + 1}: parse failed")
            continue

        validation_error = _run_outline_validators(sections, target_length=target_length)
        if validation_error is None:
            outline_path = work_dir / "2_小节大纲.md"
            outline_path.write_text(completion.text, encoding="utf-8")
            return Phase2Result(
                outline_md=completion.text,
                outline_path=outline_path,
                sections=sections,
                target_length=target_length,
                total_target_words=sum(s.target_words for s in sections),
                llm_completion=completion,
                used_fallback=False,
                attempts=attempt + 1,
                warnings=warnings,
            )
        last_error = validation_error
        warnings.append(f"attempt {attempt + 1}: {validation_error}")

    if client.is_mock():
        sections = _fallback_outline(target_length)
        md = render_outline_md(
            sections,
            target_length=target_length,
            note="mock fallback — real model would have produced this",
        )
        outline_path = work_dir / "2_小节大纲.md"
        outline_path.write_text(md, encoding="utf-8")
        return Phase2Result(
            outline_md=md,
            outline_path=outline_path,
            sections=sections,
            target_length=target_length,
            total_target_words=sum(s.target_words for s in sections),
            llm_completion=last_completion or _empty_completion(),
            used_fallback=True,
            attempts=max_retries + 1,
            warnings=warnings + ["fallback synthesized (mock/dry-run)"],
        )

    raise PhaseOutlineError(
        f"Phase 2 failed after {max_retries + 1} attempts. last_error={last_error}"
    )


def build_phase2_prompt(
    *,
    framework_md: str,
    target_length: int,
    project_root: Path,
) -> list[dict[str, str]]:
    """Compose Phase 2 messages."""
    template_str = _PHASE2_PROMPT_FILE.read_text(encoding="utf-8")

    template = string.Template(template_str)
    user_text = template.safe_substitute(
        framework_md=framework_md,
        target_length=target_length,
        section_count_min=SECTION_COUNT_MIN,
        section_count_max=SECTION_COUNT_MAX,
        section_min_chars=SECTION_CHARS_MIN,
        section_max_chars=SECTION_CHARS_MAX,
    )
    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深主编。"
                "严格按 Markdown 表格输出 2_小节大纲.md,字段顺序固定,"
                "不要省略表头,不要附加任何解释文字。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


# ============================================================ markdown parser


_OUTLINE_TABLE_HEADER = re.compile(
    r"\|\s*节号\s*\|\s*主事件\s*\|.+\|\s*target_words\s*\|", re.IGNORECASE
)


def parse_outline_md(md: str) -> tuple[list[OutlineSection], list[str]]:
    """Parse the LLM's markdown output into ``OutlineSection`` records.

    Robustness:
    - finds the table by locating the row that contains the literal headers
      ``节号`` and ``target_words`` (Markdown table format)
    - skips the separator row (``|---|---|...|``)
    - drops any trailing rows that are not pipe-delimited
    - target_words pulled out by the first integer in the column
    """
    warnings: list[str] = []
    if not md:
        return [], ["empty markdown"]

    lines = md.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if _OUTLINE_TABLE_HEADER.search(line)),
        -1,
    )
    if header_idx < 0:
        return [], ["could not find outline table header"]

    table_rows: list[str] = []
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            break
        if not stripped.startswith("|"):
            break
        # separator row like |---|---|
        if re.match(r"^\|[\s\-:|]+\|\s*$", stripped):
            continue
        table_rows.append(stripped)

    sections: list[OutlineSection] = []
    for raw in table_rows:
        cells = [c.strip() for c in raw.strip("|").split("|")]
        if len(cells) < 10:
            warnings.append(f"row has {len(cells)} cells, need ≥10: '{raw[:60]}'")
            continue
        try:
            index = _parse_int(cells[0]) or len(sections) + 1
            target_words = _parse_int(cells[9]) or 0
        except ValueError:
            warnings.append(f"bad row: '{raw[:60]}'")
            continue
        sub_events = [s.strip() for s in re.split(r"[/／、]", cells[2]) if s.strip()]
        sections.append(
            OutlineSection(
                index=index,
                main_event=cells[1],
                sub_events=sub_events,
                emotion=cells[3],
                new_info=cells[4],
                hook=cells[5],
                foreshadowing=cells[6],
                static_dynamic=cells[7],
                dialogue_ratio=cells[8],
                target_words=target_words,
            )
        )
    return sections, warnings


def render_outline_md(
    sections: list[OutlineSection], *, target_length: int, note: str = ""
) -> str:
    """Render an OutlineSection list back to the markdown format."""
    lines = ["# 小节大纲", ""]
    if note:
        lines.append(f"> note: {note}")
        lines.append("")
    lines.extend(
        [
            "## 元信息",
            f"- target_length: {target_length}",
            f"- section_count: {len(sections)}",
            "- emotion_arc_used: (fallback)按公式逐节落点",
            "",
            "## 大纲表",
            "",
            "| 节号 | 主事件 | 子事件×3-5 | 情绪 | 读者新获知 | 章末钩子 | 伏笔/物件 | 动静 | 对话密度 | target_words |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for s in sections:
        lines.append(
            "| {idx:02d} | {main} | {sub} | {emo} | {info} | {hook} | {fore} | {sd} | {dr} | {tw} |".format(
                idx=s.index,
                main=s.main_event,
                sub=" / ".join(s.sub_events),
                emo=s.emotion,
                info=s.new_info,
                hook=s.hook,
                fore=s.foreshadowing,
                sd=s.static_dynamic,
                dr=s.dialogue_ratio,
                tw=s.target_words,
            )
        )
    total = sum(s.target_words for s in sections)
    deviation = (total - target_length) / target_length * 100 if target_length else 0
    lines.extend(
        [
            "",
            "## 总字数核对",
            f"- 各节 target_words 之和 = {total}",
            f"- 与目标 {target_length} 的偏差 = {deviation:+.1f}%",
        ]
    )
    return "\n".join(lines) + "\n"


# ============================================================ validators


def _run_outline_validators(
    sections: list[OutlineSection], *, target_length: int
) -> str | None:
    """Run all three outline validators; return None or the first error message."""
    r1 = check_outline_section_count(
        len(sections), min_count=SECTION_COUNT_MIN, max_count=SECTION_COUNT_MAX
    )
    if not r1.ok:
        return r1.message

    r2 = check_outline_section_words(
        [s.target_words for s in sections],
        min_chars=SECTION_CHARS_MIN,
        max_chars=SECTION_CHARS_MAX,
    )
    if not r2.ok:
        return r2.message

    total = sum(s.target_words for s in sections)
    r3 = check_total_word_count(total, target=target_length, tolerance=TOTAL_TOLERANCE)
    if not r3.ok:
        return r3.message
    return None


# ============================================================ helpers


def _project_root(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve()
    return Path(__file__).resolve().parents[2]


def _resolve_target_length(pitch_path: Path) -> int:
    """Pull a target word count out of 0_选题.json (midpoint of the range)."""
    if not pitch_path.exists():
        return 10000
    try:
        pitch = json.loads(pitch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 10000
    target = pitch.get("target_length")
    if isinstance(target, list) and len(target) == 2:
        try:
            return int((int(target[0]) + int(target[1])) / 2)
        except (TypeError, ValueError):
            pass
    return 10000


def _parse_int(s: str) -> int:
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else 0


def _empty_completion() -> ChatCompletion:
    from generator.api_client import ChatUsage

    return ChatCompletion(
        text="",
        reasoning=None,
        model="(no-call)",
        usage=ChatUsage(),
        finish_reason="(no-call)",
        cached=False,
    )


def _fallback_outline(target_length: int) -> list[OutlineSection]:
    """Build a deterministic 8-section outline that passes all validators.

    Distributes ``target_length`` evenly across 8 sections (clamped to
    [800, 1500]) and gives each a placeholder description so Phase 3 still
    has something concrete to work with in mock/dry-run mode.
    """
    section_count = 8
    per_section = max(SECTION_CHARS_MIN, min(SECTION_CHARS_MAX, target_length // section_count))
    # Spread the rounding error across the last section.
    distributed = [per_section] * section_count
    distributed[-1] = max(SECTION_CHARS_MIN, target_length - per_section * (section_count - 1))
    if distributed[-1] > SECTION_CHARS_MAX:
        distributed[-1] = SECTION_CHARS_MAX
    sections = []
    arc = ["铺垫", "压抑", "推进", "转折", "积累", "爆发", "碾压", "余韵"]
    for i, words in enumerate(distributed, start=1):
        sections.append(
            OutlineSection(
                index=i,
                main_event=f"(fallback)节 {i} 主事件",
                sub_events=[f"子事件 {i}.{j}" for j in range(1, 4)],
                emotion=arc[i - 1] if i <= len(arc) else "余韵",
                new_info=f"(fallback)节 {i} 读者新获知",
                hook=f"(fallback)节 {i} 章末钩子",
                foreshadowing=f"(fallback)节 {i} 物件推进" if i in (1, 4, 8) else "",
                static_dynamic="动" if i % 2 == 0 else "静",
                dialogue_ratio="20%-40%",
                target_words=words,
            )
        )
    return sections


__all__ = [
    "OutlineSection",
    "Phase2Result",
    "PhaseOutlineError",
    "SECTION_CHARS_MAX",
    "SECTION_CHARS_MIN",
    "SECTION_COUNT_MAX",
    "SECTION_COUNT_MIN",
    "TOTAL_TOLERANCE",
    "build_phase2_prompt",
    "parse_outline_md",
    "render_outline_md",
    "run_outline",
]
