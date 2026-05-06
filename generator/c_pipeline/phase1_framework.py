"""Phase 1 — produce 1_设定.md (framework + reversal + characters + objects).

Pipeline (PLAN §3.1, §4):
    pitch = read 0_选题.json from work_dir
    seeds = load scan_seeds.yaml (for summary formulas + opening/ending refs)
    completion = client.chat_completion(messages, model=v4-pro,
                                        thinking_mode=True, purpose="phase_1")
    parse markdown → final_title, summary
    write 1_设定.md
    decision #11: Phase 1 has no retry — fail direct (mock mode synthesizes
        a fallback so dry-run smoke tests still pass).

Output (1_设定.md) contains required sections in this order:
    final_title / summary / 一句话核心 / 主角 / 核心反派 / 关键配角 /
    反转设计 / 结构物件 / 钩子设计 / 情绪曲线落点
"""

from __future__ import annotations

import json
import logging
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, DeepSeekClient
from scan.seed_evolver import load_seeds

logger = logging.getLogger(__name__)


_PHASE1_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase1_framework.txt"


# Mapping target_platform → summary_formulas key. Falls back to 番茄_主推.
_PLATFORM_TO_FORMULA: dict[str, str] = {
    "番茄短篇": "番茄_主推",
    "七猫短篇": "七猫总裁",
    "黑岩短篇": "黑岩女频",
    "知乎盐言": "知乎金句式",
    "点众短篇": "点众_浓缩",
}


class PhaseFrameworkError(RuntimeError):
    """Raised when Phase 1 cannot extract a valid framework from LLM output."""


@dataclass(frozen=True)
class Phase1Result:
    """Outcome of one ``run_framework`` call."""

    framework_md: str
    final_title: str
    summary: str
    framework_path: Path
    llm_completion: ChatCompletion
    used_fallback: bool
    summary_word_range: tuple[int, int] = (150, 300)
    warnings: list[str] = field(default_factory=list)


# ============================================================ public


def run_framework(
    config: LoadedConfig,
    *,
    work_dir: Path,
    pitch_path: Path | None = None,
    seeds_path: Path | None = None,
    client: DeepSeekClient | None = None,
) -> Phase1Result:
    """Run Phase 1 — framework / reversal / characters / objects."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    pitch_path = Path(pitch_path) if pitch_path else work_dir / "0_选题.json"
    if not pitch_path.exists():
        raise PhaseFrameworkError(
            f"Phase 1 needs Phase 0 output but 0_选题.json missing: {pitch_path}"
        )

    pitch = json.loads(pitch_path.read_text(encoding="utf-8"))
    project_root = _project_root(config)
    seeds_p = Path(seeds_path) if seeds_path else project_root / "data" / "scan_seeds.yaml"
    seeds = load_seeds(seeds_p)

    if client is None:
        client = DeepSeekClient(config)

    summary_formula = _resolve_summary_formula(seeds, pitch.get("target_platform"))
    summary_min = int(summary_formula.get("word_range", [150, 300])[0])
    summary_max = int(summary_formula.get("word_range", [150, 300])[1])

    messages = build_phase1_prompt(
        pitch,
        seeds=seeds,
        project_root=project_root,
        summary_min=summary_min,
        summary_max=summary_max,
    )
    completion = client.chat_completion(
        messages,
        thinking_mode=True,
        purpose="phase_1",
    )

    framework_md = completion.text
    final_title, summary, warnings = _extract_title_and_summary(framework_md)
    used_fallback = False

    if not final_title or not summary:
        if client.is_mock():
            framework_md, final_title, summary = _fallback_framework(
                pitch,
                completion_text=completion.text,
                summary_min=summary_min,
                summary_max=summary_max,
            )
            used_fallback = True
            warnings.append("Phase 1 fallback synthesized (mock/dry-run mode)")
        else:
            raise PhaseFrameworkError(
                "Phase 1 LLM output missing final_title or summary section. "
                f"warnings={warnings}"
            )

    framework_path = work_dir / "1_设定.md"
    framework_path.write_text(framework_md, encoding="utf-8")

    return Phase1Result(
        framework_md=framework_md,
        final_title=final_title,
        summary=summary,
        framework_path=framework_path,
        llm_completion=completion,
        used_fallback=used_fallback,
        summary_word_range=(summary_min, summary_max),
        warnings=warnings,
    )


def build_phase1_prompt(
    pitch: dict[str, Any],
    *,
    seeds: dict[str, Any],
    project_root: Path,
    summary_min: int | None = None,
    summary_max: int | None = None,
) -> list[dict[str, str]]:
    """Compose Phase 1 messages from pitch + seeds references.

    When ``summary_min``/``summary_max`` are not given, they are auto-resolved
    from ``seeds.summary_formulas`` using the pitch's ``target_platform`` —
    so callers that just want to inspect the prompt content (e.g. tests) do
    not need to duplicate the platform → formula mapping.
    """
    template_str = _PHASE1_PROMPT_FILE.read_text(encoding="utf-8")

    summary_formula = _resolve_summary_formula(seeds, pitch.get("target_platform"))
    if summary_min is None:
        summary_min = int(summary_formula.get("word_range", [150, 300])[0])
    if summary_max is None:
        summary_max = int(summary_formula.get("word_range", [150, 300])[1])

    genre_id = str(pitch.get("genre_id", ""))
    opening_id = str(pitch.get("opening_mode_id", ""))
    ending_id = str(pitch.get("ending_mode_id", ""))

    genre = _find_by_id(seeds.get("genres", []), genre_id)
    opening = _find_by_id(seeds.get("opening_modes", []), opening_id)
    ending = _find_by_id(seeds.get("ending_modes", []), ending_id)
    title_pattern_used = _guess_title_pattern_for_platform(pitch.get("target_platform", ""))

    # The pitch is dumped verbatim so the LLM can read every override the
    # CLI applied (theme / target_length / weekly_topic_used).
    pitch_json = json.dumps(pitch, ensure_ascii=False, indent=2)

    template = string.Template(template_str)
    user_text = template.safe_substitute(
        pitch_json=pitch_json,
        genre_formula=genre.get("formula", ""),
        genre_emotion_arc=genre.get("emotion_arc", ""),
        genre_signature_scenes="; ".join(genre.get("signature_scenes", []) or []),
        genre_notes=genre.get("notes", ""),
        opening_mode_id=opening_id,
        opening_mode_template=opening.get("template", ""),
        opening_mode_example=opening.get("example", ""),
        ending_mode_id=ending_id,
        ending_mode_skill=ending.get("技法", ending.get("name", "")),
        summary_platform_key=_platform_key_for_pitch(pitch),
        summary_structure=summary_formula.get("structure", ""),
        summary_word_min=summary_min,
        summary_word_max=summary_max,
        summary_must_include="; ".join(summary_formula.get("must_include", []) or []),
        title_pattern_used=title_pattern_used,
    )

    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深架构师。"
                "严格按用户给定的 Markdown 章节顺序产出 1_设定.md,"
                "不要添加额外章节,不要省略任何章节,不要解释或总结。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


# ============================================================ helpers


def _project_root(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve()
    return Path(__file__).resolve().parents[2]


def _resolve_summary_formula(
    seeds: Mapping[str, Any], target_platform: Any
) -> dict[str, Any]:
    formulas = seeds.get("summary_formulas", {}) or {}
    key = _PLATFORM_TO_FORMULA.get(str(target_platform), "番茄_主推")
    if key in formulas and isinstance(formulas[key], dict):
        return formulas[key]
    # Last-resort default tuned to 番茄.
    return {
        "structure": "情境设定 → 冲突引爆 → 对话金句 → 主角转折 → 悬念钩子",
        "word_range": [150, 300],
        "must_include": ["第一人称", "至少 1 句对话", "结尾留钩"],
    }


def _platform_key_for_pitch(pitch: dict[str, Any]) -> str:
    return _PLATFORM_TO_FORMULA.get(str(pitch.get("target_platform", "")), "番茄_主推")


def _guess_title_pattern_for_platform(platform: str) -> str:
    return {
        "番茄短篇": "番茄主流",
        "七猫短篇": "七猫式",
        "黑岩短篇": "黑岩式",
        "点众短篇": "点众式",
        "知乎盐言": "知乎式",
    }.get(str(platform), "番茄主流")


def _find_by_id(items: list[Any], target_id: str) -> dict[str, Any]:
    for it in items or []:
        if isinstance(it, dict) and it.get("id") == target_id:
            return it
    return {}


_TITLE_HEAD = re.compile(r"^##\s*final_title\s*$", re.MULTILINE)
_SUMMARY_HEAD = re.compile(r"^##\s*summary\s*$", re.MULTILINE)


def _extract_title_and_summary(md: str) -> tuple[str, str, list[str]]:
    """Pull the ``## final_title`` and ``## summary`` blocks out of markdown."""
    warnings: list[str] = []
    title = _extract_section(md, _TITLE_HEAD).strip()
    summary = _extract_section(md, _SUMMARY_HEAD).strip()

    if not title:
        warnings.append("missing '## final_title' section")
    if not summary:
        warnings.append("missing '## summary' section")

    # Strip leading/trailing fences or quote markers if any.
    title = title.lstrip("`").rstrip("`").strip()
    return title, summary, warnings


def _extract_section(md: str, head_regex: re.Pattern[str]) -> str:
    """Return text between ``head_regex`` and the next ``## `` header (or EOF)."""
    if not md:
        return ""
    m = head_regex.search(md)
    if m is None:
        return ""
    start = m.end()
    next_head = re.search(r"^##\s+", md[start:], re.MULTILINE)
    if next_head is None:
        return md[start:].strip()
    return md[start : start + next_head.start()].strip()


def _fallback_framework(
    pitch: dict[str, Any],
    *,
    completion_text: str,
    summary_min: int,
    summary_max: int,
) -> tuple[str, str, str]:
    """Synthesize a usable 1_设定.md for mock/dry-run mode.

    Pads the summary block to at least ``summary_min`` chars so downstream
    Phase 2/3 prompts have realistic input sizes during smoke tests. Marks
    every fallback section with the literal "(fallback)" tag for grep-ability.
    """
    title = (pitch.get("hint_title") or "fallback 标题待人工修订")[:25]
    seed_summary = (
        f"(fallback)主角(第一人称)在『{pitch.get('weekly_topic_used','')}』背景下,"
        f"按照 {pitch.get('genre_id','')} 公式展开:"
        f"{pitch.get('tuned_pitch','')}"
    )
    while len(seed_summary) < summary_min:
        seed_summary += "(fallback 占位)继续展开冲突与反转,直至本节字数补足。"
    summary_text = seed_summary[:summary_max]

    md = "\n".join(
        [
            "# 故事设定",
            "",
            "## final_title",
            title,
            "",
            "## summary",
            summary_text,
            "",
            "## 一句话核心",
            "(fallback) " + str(pitch.get("tuned_pitch", "")),
            "",
            "## 主角",
            "- 身份:" + str(pitch.get("protagonist", {}).get("identity", "")),
            "- 标志性动作:(fallback)端起水杯",
            "- 内心驱动力:(fallback)守住自己的边界",
            "- 弧光:从被动 → 到主动",
            "",
            "## 核心反派",
            "- 身份:" + str(pitch.get("antagonist_or_object", "")),
            "- 标志性动作:(fallback)惯性辩解",
            "- 动机:(fallback)利益与认知偏差驱动",
            "",
            "## 关键配角",
            "- 配角A:作用 / 标志性动作 (fallback)",
            "",
            "## 反转设计",
            "- 主反转(第 6-7 节):(fallback)真相揭露",
            "- 小反转 1:(fallback)开篇钩子兑现",
            "- 小反转 2:(fallback)中段身份/动机翻转",
            "",
            "## 结构物件(物件三现)",
            "- 物件 1:(fallback)信件",
            "  - 一现:第 1 节",
            "  - 二现:中段推进",
            "  - 三现:收尾呼应",
            "",
            "## 钩子设计",
            "- 开篇钩子:(fallback)" + str(pitch.get("trigger_event", "")),
            "- 章末钩子原则:(fallback)每节末埋一个具体物件或台词",
            "- 收尾设计:(fallback)电影感场景收场",
            "",
            "## 情绪曲线落点",
            "(fallback)按公式 8 节情绪逐节标注",
            "",
            f"<!-- mock-llm-raw: {completion_text[:120].replace('\n', ' ')} -->",
        ]
    )
    return md, title, summary_text


__all__ = [
    "Phase1Result",
    "PhaseFrameworkError",
    "build_phase1_prompt",
    "run_framework",
]
