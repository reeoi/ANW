"""Phase 5 — de-slop (pro, no thinking).

Pipeline (PLAN §3.1, §4):
    polished_md = read 4_精修稿.md
    blacklist = load ai_slop_blacklist.json
    completion = client.chat_completion(messages, model=v4-pro,
                                        thinking_mode=False, purpose="phase_5")
    write 5_最终稿.md

The AI-slop blacklist is dumped wholesale into the prompt (Phase 5 is the
last chance to remove residue). After the LLM call, we run the same slop
validator one more time; remaining hits are reported as warnings on the
result so the AI-review (Phase E) layer can decide whether to rerun
Phase 4-5 (decision #31 R2).
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, DeepSeekClient
from generator.c_pipeline.validators import (
    ValidationResult,
    check_ai_slop,
    count_chinese_chars,
    load_ai_slop_blacklist,
)

logger = logging.getLogger(__name__)


_PHASE5_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase5_deslop.txt"
_DEFAULT_BLACKLIST_FILE = Path(__file__).parent / "prompts" / "ai_slop_blacklist.json"


class PhaseDeSlopError(RuntimeError):
    """Raised when Phase 5 cannot read its inputs."""


@dataclass(frozen=True)
class Phase5Result:
    """Outcome of one ``run_deslop`` call."""

    final_md: str
    final_path: Path
    char_count: int
    slop_check: ValidationResult
    llm_completion: ChatCompletion
    used_fallback: bool
    warnings: list[str] = field(default_factory=list)


def run_deslop(
    config: LoadedConfig,
    *,
    work_dir: Path,
    polished_path: Path | None = None,
    blacklist_path: Path | None = None,
    client: DeepSeekClient | None = None,
) -> Phase5Result:
    """Run Phase 5 — de-AI-slop pass."""
    work_dir = Path(work_dir)
    polished_path = Path(polished_path) if polished_path else work_dir / "4_精修稿.md"
    if not polished_path.exists():
        raise PhaseDeSlopError(
            f"Phase 5 needs Phase 4 output but 4_精修稿.md missing: {polished_path}"
        )
    polished_md = polished_path.read_text(encoding="utf-8")

    project_root = _project_root(config)
    blacklist_p = (
        Path(blacklist_path) if blacklist_path else _DEFAULT_BLACKLIST_FILE
    )
    blacklist = load_ai_slop_blacklist(blacklist_p)

    if client is None:
        client = DeepSeekClient(config)

    messages = build_phase5_prompt(
        polished_md=polished_md, blacklist=blacklist, project_root=project_root
    )
    completion = client.chat_completion(
        messages,
        thinking_mode=False,
        purpose="phase_5",
    )
    final_md = (completion.text or "").strip()

    used_fallback = False
    warnings: list[str] = []

    if client.is_mock() and not _looks_like_deslopped_output(final_md, polished_md):
        # Mock fallback: strip blacklist words from polished_md so the final
        # validator passes without a real LLM call.
        final_md = _local_strip_blacklist(polished_md, blacklist)
        used_fallback = True
        warnings.append("phase 5 mock fallback (locally stripped blacklist words)")

    slop_check = check_ai_slop(final_md, blacklist)
    if not slop_check.ok:
        warnings.append(
            f"phase 5 slop residue: {slop_check.message} "
            f"(details: {'; '.join(slop_check.details[:5])})"
        )

    final_path = work_dir / "5_最终稿.md"
    final_path.write_text(final_md, encoding="utf-8")

    return Phase5Result(
        final_md=final_md,
        final_path=final_path,
        char_count=count_chinese_chars(final_md),
        slop_check=slop_check,
        llm_completion=completion,
        used_fallback=used_fallback,
        warnings=warnings,
    )


def build_phase5_prompt(
    *,
    polished_md: str,
    blacklist: Sequence[str],
    project_root: Path,
) -> list[dict[str, str]]:
    """Compose Phase 5 messages."""
    template_str = _PHASE5_PROMPT_FILE.read_text(encoding="utf-8")

    full_list = "、".join(blacklist) if blacklist else "(blacklist 暂空)"
    template = string.Template(template_str)
    user_text = template.safe_substitute(
        polished_md=polished_md,
        ai_slop_full_list=full_list,
    )
    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深'去 AI 味'编辑。"
                "只做词级 / 句级替换,严禁增删段落、改顺序、改人物名或物件名、改数字。"
                "不要输出任何元说明,直接产出最终稿全文。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


def _looks_like_deslopped_output(text: str, original: str) -> bool:
    if not text:
        return False
    chars = count_chinese_chars(text)
    if chars < max(500, count_chinese_chars(original) // 4):
        return False
    return True


def _local_strip_blacklist(text: str, blacklist: Sequence[str]) -> str:
    """Best-effort local de-slop for mock mode.

    Substring-replaces every blacklist word with an empty string. Order
    matters for overlapping entries so longer phrases are stripped first.
    """
    out = text
    for word in sorted(blacklist, key=len, reverse=True):
        word = (word or "").strip()
        if word:
            out = out.replace(word, "")
    return out


def _project_root(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve()
    return Path(__file__).resolve().parents[2]


__all__ = [
    "Phase5Result",
    "PhaseDeSlopError",
    "build_phase5_prompt",
    "run_deslop",
]
