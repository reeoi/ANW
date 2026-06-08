"""Phase 3 — per-section body generation with full prior context (C3).

Pipeline (PLAN §3.1, §4, decision #16):
    framework = read 1_设定.md
    outline = parse 2_小节大纲.md
    blacklist = load ai_slop_blacklist.json
    for section in outline:
        prior_sections_md = concat of all previously written sections (FULL)
        completion = client.chat_completion(messages, model=v4-pro,
                                            thinking_mode=False,
                                            purpose="phase_3_section_NN")
        validate text (decision #16):
            - chinese chars ≥ 800
            - paragraph length ≤ 60 chars
            - AI slop blacklist 0 hits
        retry up to max_section_retries (default 2). After retries:
            - mock mode → synthesize a valid fallback section
            - live mode → keep best attempt, mark needs_human=True
        write 3_正文_第 NN 节.md
    write 3_正文_合稿.md (all sections concat)

C3 strategy (full prior context) keeps every section coherent with prior
detail at the cost of large prompt size; DeepSeek prompt cache amortizes
the cost across sections (PLAN §6 cost estimate).
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.c_pipeline.cost_tracker import CostTracker
from generator.c_pipeline.phase2_outline import (
    OutlineSection,
    parse_outline_md,
)
from generator.c_pipeline.validators import (
    ValidationResult,
    check_ai_slop,
    check_paragraph_length,
    check_section_word_count,
    count_chinese_chars,
    load_ai_slop_blacklist,
    summarize_section_validations,
)

logger = logging.getLogger(__name__)


_PHASE3_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase3_section.txt"
_DEFAULT_BLACKLIST_FILE = Path(__file__).parent / "prompts" / "ai_slop_blacklist.json"

SECTION_MIN_CHARS = 800
PARAGRAPH_MAX_CHARS = 60


class PhaseSectionsError(RuntimeError):
    """Raised when Phase 3 cannot start (missing inputs, parse fail)."""


@dataclass(frozen=True)
class SectionResult:
    """Result of generating one section."""

    index: int
    text: str
    path: Path
    attempts: int
    char_count: int
    validations: Mapping[str, ValidationResult]
    used_fallback: bool
    needs_human: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Phase3Result:
    """Aggregate result of all sections + the combined draft."""

    sections: list[SectionResult]
    combined_path: Path
    combined_md: str
    total_chars: int
    needs_human: bool
    used_fallback: bool
    warnings: list[str] = field(default_factory=list)


# ============================================================ public


def run_sections(
    config: LoadedConfig,
    *,
    work_dir: Path,
    framework_path: Path | None = None,
    outline_path: Path | None = None,
    blacklist_path: Path | None = None,
    max_section_retries: int = 2,
    client: DeepSeekClient | None = None,
    cost_tracker: CostTracker | None = None,
) -> Phase3Result:
    """Generate every section listed in the outline, write per-section files,
    then write the combined draft to ``3_正文_合稿.md``."""
    work_dir = Path(work_dir)
    framework_path = Path(framework_path) if framework_path else work_dir / "1_设定.md"
    outline_path = Path(outline_path) if outline_path else work_dir / "2_小节大纲.md"

    if not framework_path.exists():
        raise PhaseSectionsError(
            f"Phase 3 needs Phase 1 output but 1_设定.md missing: {framework_path}"
        )
    if not outline_path.exists():
        raise PhaseSectionsError(
            f"Phase 3 needs Phase 2 output but 2_小节大纲.md missing: {outline_path}"
        )

    framework_md = framework_path.read_text(encoding="utf-8")
    outline_md = outline_path.read_text(encoding="utf-8")
    sections, parse_warnings = parse_outline_md(outline_md)
    if not sections:
        raise PhaseSectionsError(
            f"Phase 3 could not parse outline: {parse_warnings}"
        )

    project_root = _project_root(config)
    blacklist_p = Path(blacklist_path) if blacklist_path else _DEFAULT_BLACKLIST_FILE
    blacklist = load_ai_slop_blacklist(blacklist_p)

    if client is None:
        client = DeepSeekClient(config)

    written_sections: list[SectionResult] = []
    prior_blocks: list[str] = []
    aggregate_warnings: list[str] = list(parse_warnings)
    any_needs_human = False
    any_used_fallback = False

    for outline_section in sections:
        prior_md = "\n\n".join(prior_blocks) if prior_blocks else "(本节为第一节,尚无前文)"
        section_result = _generate_one_section(
            config=config,
            client=client,
            project_root=project_root,
            framework_md=framework_md,
            outline_md=outline_md,
            prior_sections_md=prior_md,
            outline_section=outline_section,
            section_total=len(sections),
            blacklist=blacklist,
            max_retries=max_section_retries,
            work_dir=work_dir,
            cost_tracker=cost_tracker,
        )
        written_sections.append(section_result)
        prior_blocks.append(section_result.text)
        if section_result.needs_human:
            any_needs_human = True
        if section_result.used_fallback:
            any_used_fallback = True
        aggregate_warnings.extend(section_result.warnings)

    combined_md = _assemble_combined_md(written_sections)
    combined_path = work_dir / "3_正文_合稿.md"
    combined_path.write_text(combined_md, encoding="utf-8")

    total = sum(s.char_count for s in written_sections)

    return Phase3Result(
        sections=written_sections,
        combined_path=combined_path,
        combined_md=combined_md,
        total_chars=total,
        needs_human=any_needs_human,
        used_fallback=any_used_fallback,
        warnings=aggregate_warnings,
    )


def build_phase3_prompt(
    *,
    framework_md: str,
    outline_md: str,
    prior_sections_md: str,
    outline_section: OutlineSection,
    section_total: int,
    blacklist: Sequence[str],
    project_root: Path,
) -> list[dict[str, str]]:
    """Compose Phase 3 messages for one section."""
    template_str = _PHASE3_PROMPT_FILE.read_text(encoding="utf-8")

    # Show only a short excerpt of the blacklist in the prompt to keep token
    # cost predictable; the full blacklist is enforced post-generation by the
    # validator (decision #16: code-side check is authoritative).
    excerpt = "、".join(list(blacklist)[:30]) if blacklist else "(blacklist 暂空)"

    template = string.Template(template_str)
    user_text = template.safe_substitute(
        section_index=outline_section.index,
        section_total=section_total,
        section_target_words=outline_section.target_words,
        section_emotion=outline_section.emotion,
        section_hook=outline_section.hook,
        section_objects=outline_section.foreshadowing or "(本节无新物件)",
        section_dialogue_ratio=outline_section.dialogue_ratio,
        framework_md=framework_md,
        outline_md=outline_md,
        prior_sections_md=prior_sections_md,
        ai_slop_excerpt=excerpt,
    )

    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深作者。"
                "只输出本节正文,严格遵守用户给出的硬约束(字数/段长/AI 腔黑名单/钩子)。"
                "不要输出节号、标题、Markdown 标记或任何元说明。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


def validate_section_text(
    text: str, *, blacklist: Sequence[str], section_min_chars: int = SECTION_MIN_CHARS
) -> dict[str, ValidationResult]:
    """Run the three section-level hard checks (decision #16)."""
    return {
        "length": check_section_word_count(text, min_chars=section_min_chars, max_chars=0),
        "paragraph": check_paragraph_length(text, max_chars=PARAGRAPH_MAX_CHARS),
        "slop": check_ai_slop(text, blacklist),
    }


# ============================================================ helpers


def _generate_one_section(
    *,
    config: LoadedConfig,
    client: DeepSeekClient,
    project_root: Path,
    framework_md: str,
    outline_md: str,
    prior_sections_md: str,
    outline_section: OutlineSection,
    section_total: int,
    blacklist: Sequence[str],
    max_retries: int,
    work_dir: Path,
    cost_tracker: CostTracker | None = None,
) -> SectionResult:
    """Generate, validate, and write one section. Retries up to max_retries."""
    messages = build_phase3_prompt(
        framework_md=framework_md,
        outline_md=outline_md,
        prior_sections_md=prior_sections_md,
        outline_section=outline_section,
        section_total=section_total,
        blacklist=blacklist,
        project_root=project_root,
    )

    last_text: str = ""
    last_validations: dict[str, ValidationResult] = {}
    warnings: list[str] = []
    attempt: int = 0

    # Decision #22/#24: ask the cost tracker which model to use right
    # before issuing the call so a mid-pipeline budget hit downgrades the
    # remaining sections without restarting Phase 3.
    chosen_model = _resolve_phase3_model(config, client, cost_tracker)

    for attempt in range(max_retries + 1):
        round_messages = list(messages)
        if attempt > 0:
            failure_summary = summarize_section_validations(last_validations)
            round_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"上一次第 {outline_section.index} 节正文未通过硬校验:"
                        f"{failure_summary.message}。具体:"
                        + "; ".join(failure_summary.details[:6])
                        + "。请严格按硬约束重新写本节正文,不要解释,直接出正文。"
                    ),
                }
            )
        completion = client.chat_completion(
            round_messages,
            thinking_mode=False,
            model=chosen_model,
            purpose=f"phase_3_section_{outline_section.index:02d}{'_retry_' + str(attempt) if attempt else ''}",
        )
        text = (completion.text or "").strip()
        validations = validate_section_text(text, blacklist=blacklist)
        last_text = text
        last_validations = validations
        if all(v.ok for v in validations.values()):
            return _persist_section(
                work_dir=work_dir,
                outline_section=outline_section,
                text=text,
                attempts=attempt + 1,
                validations=validations,
                used_fallback=False,
                needs_human=False,
                warnings=warnings,
            )
        failure = summarize_section_validations(validations)
        warnings.append(
            f"section#{outline_section.index} attempt {attempt + 1}: {failure.message}"
        )

    # Retries exhausted.
    if client.is_mock():
        text = _fallback_section_text(outline_section)
        validations = validate_section_text(text, blacklist=blacklist)
        warnings.append(
            f"section#{outline_section.index}: fallback synthesized (mock mode)"
        )
        return _persist_section(
            work_dir=work_dir,
            outline_section=outline_section,
            text=text,
            attempts=max_retries + 1,
            validations=validations,
            used_fallback=True,
            needs_human=False,
            warnings=warnings,
        )

    # Live mode: keep the last (best-effort) text but flag needs_human.
    warnings.append(
        f"section#{outline_section.index}: needs_human after {max_retries + 1} attempts"
    )
    return _persist_section(
        work_dir=work_dir,
        outline_section=outline_section,
        text=last_text,
        attempts=max_retries + 1,
        validations=last_validations,
        used_fallback=False,
        needs_human=True,
        warnings=warnings,
    )


def _persist_section(
    *,
    work_dir: Path,
    outline_section: OutlineSection,
    text: str,
    attempts: int,
    validations: Mapping[str, ValidationResult],
    used_fallback: bool,
    needs_human: bool,
    warnings: list[str],
) -> SectionResult:
    path = work_dir / f"3_正文_第 {outline_section.index:02d} 节.md"
    path.write_text(text, encoding="utf-8")
    return SectionResult(
        index=outline_section.index,
        text=text,
        path=path,
        attempts=attempts,
        char_count=count_chinese_chars(text),
        validations=dict(validations),
        used_fallback=used_fallback,
        needs_human=needs_human,
        warnings=warnings,
    )


def _assemble_combined_md(sections: Iterable[SectionResult]) -> str:
    """Concat all section texts with one blank line between them."""
    parts: list[str] = []
    for s in sections:
        parts.append(s.text.strip())
    return "\n\n".join(parts) + "\n"


def _project_root(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve()
    return Path(__file__).resolve().parents[2]


def _resolve_phase3_model(
    config: LoadedConfig,
    client: DeepSeekClient,
    cost_tracker: CostTracker | None,
) -> str | None:
    """Pick model for Phase 3 calls, honouring B2 / daily-token degrade.

    Returns None when no override is needed (so ``client.chat_completion``
    falls back to its configured default). When the cost tracker says
    ``phase_3`` should degrade, returns ``deepseek.flash_model``.
    """
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
        "phase_3",
        default_model=default_model,
        flash_model=flash_model,
    )
    return chosen if chosen != default_model else None


# ============================================================ fallback synthesis


_FALLBACK_PARAGRAPHS: tuple[str, ...] = (
    "我把钥匙放在桌上,门外的脚步声停了一下。",
    "她抬头看了我一眼,没有说话,只是把手里的纸袋又攥紧了。",
    "茶水在杯沿晃出一圈水纹,我盯着那道纹路出神。",
    "电话在桌上震动,屏幕亮起又熄灭。",
    "我数到第三声铃响才接起来,母亲的声音很哑。",
    "外面下起了雨,窗台上的盆栽歪了一下。",
    "他靠在门框上,手指在裤袋外面来回摩挲。",
    "时间仿佛被拉长,我听见自己心跳的回声。",
    "客厅的吊灯坏了一只灯泡,只有半边是亮的。",
    "我把外套挂回衣架,袖口还在滴水。",
    "她把照片推到我面前,没有解释。",
    "我笑了一下,把茶杯端到嘴边,茶已经凉了。",
    "走廊尽头有人在打电话,声音压得很低。",
    "我站起来,把窗帘拉到一半,光线斜进来。",
    "他终于开口,只说了一句:对不起。",
    "我没有回答,把账本翻到了下一页。",
)


def _fallback_section_text(section: OutlineSection) -> str:
    """Build a deterministic, validator-passing section in mock/dry-run mode.

    Each paragraph is short (≤60 chars), avoids every blacklist word, and
    the total length is padded to at least 850 chars (a safe margin above
    the 800 floor). The text is intentionally generic and tagged so a human
    reviewer can find these in the work_dir later.
    """
    target = max(section.target_words, SECTION_MIN_CHARS + 50)
    chunks: list[str] = [
        f"(fallback 第 {section.index} 节)",
    ]
    accumulated = 0
    idx = 0
    n_pool = len(_FALLBACK_PARAGRAPHS)
    while accumulated < target:
        para = _FALLBACK_PARAGRAPHS[idx % n_pool]
        chunks.append(para)
        accumulated += count_chinese_chars(para)
        idx += 1
    return "\n".join(chunks)


__all__ = [
    "PARAGRAPH_MAX_CHARS",
    "Phase3Result",
    "PhaseSectionsError",
    "SECTION_MIN_CHARS",
    "SectionResult",
    "build_phase3_prompt",
    "run_sections",
    "validate_section_text",
]
