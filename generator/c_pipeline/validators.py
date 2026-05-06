"""Pure validation functions for c_pipeline phases (PLAN §4, decisions #14/#16).

Five hard checks, all callable in isolation so unit tests stay fast:

1. ``count_chinese_chars(text)``        — primary length metric (CJK only)
2. ``check_section_word_count``         — per-section ≥800 / ≤1500 (#16)
3. ``check_paragraph_length``           — per-paragraph ≤60 chars (#16)
4. ``check_ai_slop``                    — 0 hits against blacklist (#16)
5. ``check_outline_section_count``      — outline 8-15 sections (#14)
6. ``check_outline_section_words``      — each outline row 800-1500 target_words (#14)
7. ``check_total_word_count``           — total ±10% of target_length (#14)
8. ``check_section_count_conservation`` — Phase 3 produced N == outline N

These functions never raise on bad input; they return a ``ValidationResult``
with ``ok``, ``message``, and ``details`` (per-failure breakdown). Callers
decide whether to retry, fail, or escalate to needs_human.

The AI-slop blacklist file lives at
``generator/c_pipeline/prompts/ai_slop_blacklist.json``. Format:

    {
      "version": "...",
      "categories": {
        "category_name": ["禁用词1", "禁用词2", ...]
      }
    }

Both flat list and category-keyed dict are accepted by ``load_ai_slop_blacklist``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# ============================================================ data classes


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of one validator call.

    ``ok=True`` means the rule passed. ``ok=False`` means it failed; ``message``
    is the human-readable summary, and ``details`` enumerates each failing
    item (e.g. paragraph index + length, or matched blacklist word + count).
    """

    ok: bool
    message: str
    details: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # convenient `if result:` usage
        return self.ok


# ============================================================ counting


def count_chinese_chars(text: str | None) -> int:
    """Count CJK Unified Ideographs in ``text``.

    Matches the convention used elsewhere in the codebase
    (see scan/seed_evolver.py::_count_chinese_chars). ASCII letters / digits /
    punctuation never contribute. ``None`` is treated as empty.
    """
    if not text:
        return 0
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def split_paragraphs(text: str | None) -> list[str]:
    """Split a section body into paragraphs.

    Paragraphs are separated by newlines (single or double). Blank lines and
    pure-whitespace lines are dropped. Each returned paragraph is stripped.
    """
    if not text:
        return []
    parts = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if stripped:
            parts.append(stripped)
    return parts


# ============================================================ section-level checks


def check_section_word_count(
    text: str,
    *,
    min_chars: int = 800,
    max_chars: int = 1500,
) -> ValidationResult:
    """Per-section word-count check (decision #16: ≥800).

    Defaults match PLAN §3.1 / §4 ranges. ``max_chars`` is enforced softly
    here so an oversized section still passes — the goal is "not too short".
    Set ``max_chars`` to ``0`` or ``None`` to disable the upper bound.
    """
    n = count_chinese_chars(text)
    if n < min_chars:
        return ValidationResult(
            ok=False,
            message=f"section too short: {n} chars < min {min_chars}",
            details=[f"chinese_chars={n}", f"min={min_chars}"],
        )
    if max_chars and n > max_chars:
        return ValidationResult(
            ok=False,
            message=f"section too long: {n} chars > max {max_chars}",
            details=[f"chinese_chars={n}", f"max={max_chars}"],
        )
    return ValidationResult(ok=True, message=f"section length ok: {n} chars")


def check_paragraph_length(
    text: str,
    *,
    max_chars: int = 60,
) -> ValidationResult:
    """Per-paragraph length check (decision #16: each paragraph ≤60 chars).

    Counts CJK chars only (per project convention — the work is pure Chinese
    短篇). Returns ok=False with one detail per offending paragraph; the
    detail records the 1-based index, the over-limit length, and a truncated
    preview so the orchestrator log can show *which* paragraph failed.
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return ValidationResult(
            ok=False,
            message="text has no non-empty paragraphs",
            details=[],
        )
    failures: list[str] = []
    for idx, para in enumerate(paragraphs, start=1):
        n = count_chinese_chars(para)
        if n > max_chars:
            preview = para[:30] + ("…" if len(para) > 30 else "")
            failures.append(f"para#{idx} chars={n} preview='{preview}'")
    if failures:
        return ValidationResult(
            ok=False,
            message=(
                f"{len(failures)} paragraph(s) exceed {max_chars} chars "
                f"out of {len(paragraphs)} total"
            ),
            details=failures,
        )
    return ValidationResult(
        ok=True,
        message=f"all {len(paragraphs)} paragraphs ≤{max_chars} chars",
    )


def check_ai_slop(
    text: str,
    blacklist: Iterable[str],
) -> ValidationResult:
    """AI slop blacklist check (decision #16: 0 hits required).

    ``blacklist`` is an iterable of forbidden substrings (loaded from
    ``ai_slop_blacklist.json``). Matches are substring-based, case-sensitive,
    and counted across the full text. Empty/whitespace blacklist entries are
    skipped silently.
    """
    if not text:
        return ValidationResult(ok=True, message="empty text — nothing to check")

    failures: list[str] = []
    for raw in blacklist:
        word = (raw or "").strip()
        if not word:
            continue
        count = text.count(word)
        if count > 0:
            failures.append(f"'{word}' x{count}")

    if failures:
        return ValidationResult(
            ok=False,
            message=f"AI slop blacklist hits: {len(failures)} term(s)",
            details=failures,
        )
    return ValidationResult(ok=True, message="no AI slop hits")


# ============================================================ outline-level checks


def check_outline_section_count(
    section_count: int,
    *,
    min_count: int = 8,
    max_count: int = 15,
) -> ValidationResult:
    """Outline section-count check (decision #14: 8-15 sections)."""
    if section_count < min_count:
        return ValidationResult(
            ok=False,
            message=f"outline has {section_count} sections, min {min_count}",
            details=[f"actual={section_count}", f"min={min_count}"],
        )
    if section_count > max_count:
        return ValidationResult(
            ok=False,
            message=f"outline has {section_count} sections, max {max_count}",
            details=[f"actual={section_count}", f"max={max_count}"],
        )
    return ValidationResult(
        ok=True, message=f"outline section count ok: {section_count}"
    )


def check_outline_section_words(
    target_words_per_section: Sequence[int],
    *,
    min_chars: int = 800,
    max_chars: int = 1500,
) -> ValidationResult:
    """Per-row outline target_words check (decision #14: 800-1500).

    ``target_words_per_section`` is the parsed ``target_words`` column from
    each outline row. Rows whose target is outside [min_chars, max_chars] are
    listed in ``details``.
    """
    failures: list[str] = []
    for idx, words in enumerate(target_words_per_section, start=1):
        if words < min_chars or words > max_chars:
            failures.append(
                f"section#{idx} target={words} not in [{min_chars},{max_chars}]"
            )
    if failures:
        return ValidationResult(
            ok=False,
            message=(
                f"{len(failures)} outline row(s) have target_words out of range"
            ),
            details=failures,
        )
    return ValidationResult(
        ok=True,
        message=f"all {len(target_words_per_section)} outline targets in range",
    )


def check_total_word_count(
    actual_total: int,
    *,
    target: int,
    tolerance: float = 0.10,
) -> ValidationResult:
    """Total word-count check (decision #14: ±10% of target).

    ``target`` is the per-story target_length pulled from the theme_pool item
    (e.g. 10000). ``tolerance=0.10`` enforces ±10%. Either bound can be
    relaxed by raising ``tolerance``.
    """
    if target <= 0:
        return ValidationResult(
            ok=False,
            message=f"invalid target word count: {target}",
            details=[],
        )
    lower = int(target * (1 - tolerance))
    upper = int(target * (1 + tolerance))
    if actual_total < lower:
        return ValidationResult(
            ok=False,
            message=f"total {actual_total} below target {target} -{int(tolerance*100)}%",
            details=[f"actual={actual_total}", f"min={lower}", f"target={target}"],
        )
    if actual_total > upper:
        return ValidationResult(
            ok=False,
            message=f"total {actual_total} above target {target} +{int(tolerance*100)}%",
            details=[f"actual={actual_total}", f"max={upper}", f"target={target}"],
        )
    return ValidationResult(
        ok=True,
        message=f"total {actual_total} within ±{int(tolerance*100)}% of {target}",
    )


def check_section_count_conservation(
    expected: int, actual: int
) -> ValidationResult:
    """Phase 3 conservation: produced section count must match outline count."""
    if expected != actual:
        return ValidationResult(
            ok=False,
            message=f"section count mismatch: expected {expected}, got {actual}",
            details=[f"expected={expected}", f"actual={actual}"],
        )
    return ValidationResult(
        ok=True, message=f"section count conserved: {actual}"
    )


# ============================================================ blacklist loader


def load_ai_slop_blacklist(path: str | Path) -> list[str]:
    """Load the AI-slop blacklist file into a flat list.

    Accepts two formats:
    - flat array: ``["禁用词1", "禁用词2", ...]``
    - category-keyed: ``{"version": ..., "categories": {"name": [...], ...}}``
      (the value can also be an array of strings instead of a dict-of-lists)

    Returns an empty list if the file does not exist (the caller decides
    whether that is a hard failure).
    """
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(data, list):
        return [str(x) for x in data if x]

    if isinstance(data, Mapping):
        cats = data.get("categories")
        if isinstance(cats, Mapping):
            words: list[str] = []
            for entries in cats.values():
                if isinstance(entries, list):
                    words.extend(str(w) for w in entries if w)
            return words
        words_field = data.get("words") or data.get("blacklist")
        if isinstance(words_field, list):
            return [str(w) for w in words_field if w]

    return []


# ============================================================ summary aggregator


def summarize_section_validations(
    results: Mapping[str, ValidationResult],
) -> ValidationResult:
    """Aggregate three section-level checks (length / paragraph / slop).

    The orchestrator calls each individual validator and feeds the named map
    here, e.g. ``{"length": r1, "paragraph": r2, "slop": r3}``. The returned
    ``ValidationResult.ok`` is the AND of all inputs; ``message`` lists the
    names that failed; ``details`` concatenates each failed validator's own
    details with a name prefix.
    """
    failed_names = [name for name, r in results.items() if not r.ok]
    if not failed_names:
        return ValidationResult(
            ok=True,
            message="all section checks passed",
            details=[],
        )
    details: list[str] = []
    for name in failed_names:
        r = results[name]
        details.append(f"[{name}] {r.message}")
        for d in r.details:
            details.append(f"  - {d}")
    return ValidationResult(
        ok=False,
        message=f"section checks failed: {', '.join(failed_names)}",
        details=details,
    )


__all__ = [
    "ValidationResult",
    "check_ai_slop",
    "check_outline_section_count",
    "check_outline_section_words",
    "check_paragraph_length",
    "check_section_count_conservation",
    "check_section_word_count",
    "check_total_word_count",
    "count_chinese_chars",
    "load_ai_slop_blacklist",
    "split_paragraphs",
    "summarize_section_validations",
]
