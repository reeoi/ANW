"""Phase 4 — integral polish (pro+thinking).

Pipeline (PLAN §3.1, §4):
    combined_md = read 3_正文_合稿.md
    completion = client.chat_completion(messages, model=v4-pro,
                                        thinking_mode=True, purpose="phase_4")
    write 4_精修稿.md

Decision §9 risk row: prompt must explicitly say "保留架构不变,只改语言"
to keep the LLM from over-rewriting. Phase 4 has no per-call retry — the
audit rewrite path (decision #31, R2) is handled at the AI-review layer
(Phase E), not here.

Mock mode returns the input verbatim (with a small note) so dry-run smoke
tests preserve total length and downstream phases still see something.
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field
from pathlib import Path

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, DeepSeekClient
from generator.c_pipeline.validators import count_chinese_chars

logger = logging.getLogger(__name__)


_PHASE4_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase4_polish.txt"


class PhasePolishError(RuntimeError):
    """Raised when Phase 4 cannot read its inputs or write its output."""


@dataclass(frozen=True)
class Phase4Result:
    """Outcome of one ``run_polish`` call."""

    polished_md: str
    polished_path: Path
    char_count: int
    llm_completion: ChatCompletion
    used_fallback: bool
    warnings: list[str] = field(default_factory=list)


def run_polish(
    config: LoadedConfig,
    *,
    work_dir: Path,
    combined_path: Path | None = None,
    client: DeepSeekClient | None = None,
) -> Phase4Result:
    """Run Phase 4 — integral polish."""
    work_dir = Path(work_dir)
    combined_path = Path(combined_path) if combined_path else work_dir / "3_正文_合稿.md"
    if not combined_path.exists():
        raise PhasePolishError(
            f"Phase 4 needs Phase 3 output but 3_正文_合稿.md missing: {combined_path}"
        )
    combined_md = combined_path.read_text(encoding="utf-8")

    project_root = _project_root(config)
    if client is None:
        client = DeepSeekClient(config)

    messages = build_phase4_prompt(combined_md=combined_md, project_root=project_root)
    completion = client.chat_completion(
        messages,
        thinking_mode=True,
        purpose="phase_4",
    )

    polished_md = (completion.text or "").strip()
    used_fallback = False
    warnings: list[str] = []

    # In mock/dry-run we get a placeholder string from the stub client; preserve
    # the underlying combined_md so downstream Phase 5 keeps real content.
    if client.is_mock() and not _looks_like_polished_output(polished_md, combined_md):
        polished_md = combined_md.strip() + "\n\n<!-- phase4 mock fallback: kept Phase 3 verbatim -->"
        used_fallback = True
        warnings.append("phase 4 mock fallback (kept Phase 3 verbatim)")

    polished_path = work_dir / "4_精修稿.md"
    polished_path.write_text(polished_md, encoding="utf-8")

    return Phase4Result(
        polished_md=polished_md,
        polished_path=polished_path,
        char_count=count_chinese_chars(polished_md),
        llm_completion=completion,
        used_fallback=used_fallback,
        warnings=warnings,
    )


def build_phase4_prompt(
    *, combined_md: str, project_root: Path
) -> list[dict[str, str]]:
    """Compose Phase 4 messages (architecture-preserving polish)."""
    template_str = _PHASE4_PROMPT_FILE.read_text(encoding="utf-8")

    template = string.Template(template_str)
    user_text = template.safe_substitute(combined_md=combined_md)
    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深主编。"
                "本次只做语言层精修,严禁改动节数、节顺序、人物身份、物件名、关键台词、"
                "主反转设计;不要写元说明或前后总结。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


def _looks_like_polished_output(text: str, original: str) -> bool:
    """Heuristic: did the LLM actually return long-form content vs a mock placeholder?"""
    if not text:
        return False
    chars = count_chinese_chars(text)
    if chars < max(500, count_chinese_chars(original) // 4):
        return False
    return True


def _project_root(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve()
    return Path(__file__).resolve().parents[2]


__all__ = [
    "Phase4Result",
    "PhasePolishError",
    "build_phase4_prompt",
    "run_polish",
]
