"""Phase 0 — pick one theme from theme_pool.json + LLM micro-tune.

Pipeline (PLAN §3.1, §4):
    pool = read theme_pool.json
    item = pick lowest consumed_count (ties broken by id)
    apply overrides (--theme / --style / --word-count, if any)
    build prompt by looking up genre / emotion / opening / ending / reversal
        details from data/scan_seeds.yaml
    completion = client.chat_completion(messages, model=v4-pro,
                                        thinking_mode=False,
                                        response_format={"type":"json_object"})
    pitch = parse JSON tolerantly; on failure synthesize a deterministic
        fallback from the theme_pool item itself
    write 0_选题.json into work_dir
    increment consumed_count on the picked item, atomically rewrite pool

Output (0_选题.json) shape — see PHASE0_OUTPUT_FIELDS for the fixed schema.
"""

from __future__ import annotations

import json
import logging
import os
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, DeepSeekClient
from scan.seed_evolver import load_seeds

logger = logging.getLogger(__name__)


PHASE0_OUTPUT_FIELDS: tuple[str, ...] = (
    "theme",
    "tuned_pitch",
    "protagonist",
    "antagonist_or_object",
    "trigger_event",
    "tone_keywords",
    "target_length",
    "emotion_id",
    "genre_id",
    "opening_mode_id",
    "ending_mode_id",
    "reversal_type_id",
    "target_platform",
    "weekly_topic_used",
    "hint_title",
)


_PHASE0_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase0_select.txt"


class ThemePoolEmptyError(RuntimeError):
    """Raised when theme_pool.json is missing or has no items."""


@dataclass(frozen=True)
class Phase0Result:
    """Outcome of one ``select_theme`` call."""

    pitch_data: dict[str, Any]
    theme_pool_item: dict[str, Any]
    pitch_path: Path
    llm_completion: ChatCompletion | None
    used_fallback: bool
    overrides_applied: dict[str, Any] = field(default_factory=dict)


# ============================================================ public


def select_theme(
    config: LoadedConfig,
    *,
    work_dir: Path,
    theme_pool_path: Path | None = None,
    seeds_path: Path | None = None,
    client: DeepSeekClient | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Phase0Result:
    """Pick a theme, micro-tune via LLM, write 0_选题.json.

    Args:
        config: loaded ANP config (provides DeepSeek settings + project_root).
        work_dir: per-story directory ``data/works/{story_id}/``.
        theme_pool_path: defaults to ``data/theme_pool.json`` under project root.
        seeds_path: defaults to ``data/scan_seeds.yaml``.
        client: optional pre-built DeepSeekClient (tests inject mocks).
        overrides: optional ``{theme, target_length, target_platform}`` from CLI.
    """
    project_root = _project_root(config)
    pool_path = Path(theme_pool_path) if theme_pool_path else project_root / "data" / "theme_pool.json"
    seeds_p = Path(seeds_path) if seeds_path else project_root / "data" / "scan_seeds.yaml"
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    selected_item = (overrides or {}).get("theme_pool_item")
    if isinstance(selected_item, Mapping):
        pool_data: dict[str, Any] | None = None
        items: list[dict[str, Any]] = []
        item_index = -1
        item = dict(selected_item)
    else:
        pool_data = _read_pool(pool_path)
        items = list(pool_data.get("items") or [])
        if not items:
            raise ThemePoolEmptyError(
                f"theme_pool.json has no items (path={pool_path}). "
                "Run scan/seed_evolver first."
            )
        item, item_index = _pick_least_consumed(items)
    overrides_applied = _apply_overrides(item, overrides or {})

    seeds = load_seeds(seeds_p)

    if client is None:
        client = DeepSeekClient(config)

    messages = build_phase0_prompt(item, seeds=seeds, project_root=project_root)
    completion = client.chat_completion(
        messages,
        thinking_mode=False,
        response_format={"type": "json_object"},
        purpose="phase_0",
    )

    pitch_data, used_fallback = _parse_pitch(completion.text, item)

    pitch_path = work_dir / "0_选题.json"
    _atomic_write_json(pitch_path, pitch_data)

    if pool_data is not None:
        items[item_index] = {**item, "consumed_count": int(item.get("consumed_count", 0)) + 1}
        _atomic_write_json(pool_path, {**pool_data, "items": items})
        result_item = items[item_index]
    else:
        result_item = item

    return Phase0Result(
        pitch_data=pitch_data,
        theme_pool_item=result_item,
        pitch_path=pitch_path,
        llm_completion=completion,
        used_fallback=used_fallback,
        overrides_applied=overrides_applied,
    )


def build_phase0_prompt(
    item: dict[str, Any],
    *,
    seeds: dict[str, Any],
    project_root: Path,
) -> list[dict[str, str]]:
    """Compose the OpenAI-style messages for Phase 0 micro-tune.

    Pulls per-id detail (formula / signature_scenes / opening template / ...)
    out of seeds.yaml so the LLM has all the references it needs without us
    re-loading the YAML downstream.
    """
    template_str = _PHASE0_PROMPT_FILE.read_text(encoding="utf-8")

    genre_id = str(item.get("genre", ""))
    emotion_id = str(item.get("emotion", ""))
    opening_id = str(item.get("opening_mode", ""))
    ending_id = str(item.get("ending_mode", ""))
    reversal_id = str(item.get("reversal_type", ""))

    genre = _find_by_id(seeds.get("genres", []), genre_id)
    emotion = _find_by_id(seeds.get("emotion_types", []), emotion_id)
    opening = _find_by_id(seeds.get("opening_modes", []), opening_id)
    ending = _find_by_id(seeds.get("ending_modes", []), ending_id)
    reversal = _find_by_id(seeds.get("reversal_types", []), reversal_id)

    target_length = item.get("target_length") or [8000, 12000]
    target_min, target_max = int(target_length[0]), int(target_length[1])

    optional_values = (
        ("公式情绪曲线", genre.get("emotion_arc", "")),
        ("标志性场景", "; ".join(genre.get("signature_scenes", []) or [])),
        ("创作提醒", genre.get("notes", "")),
        ("开头模式参考", " / ".join(filter(None, (opening_id, opening.get("template", ""), opening.get("example", ""))))),
        ("结尾模式参考", " / ".join(filter(None, (ending_id, ending.get("技巧", ""), ending.get("name", ""))))),
        ("反转类型参考", " / ".join(filter(None, (reversal_id, reversal.get("pattern", ""))))),
        ("风向词", item.get("seasonal_or_topic_seed", "")),
    )
    optional_references = "\n".join(
        f"- {label}：{value}" for label, value in optional_values if value
    )
    if optional_references:
        optional_references = "【可选参考】\n" + optional_references

    template = string.Template(template_str)
    user_text = template.safe_substitute(
        theme=item.get("theme", ""),
        emotion_id=emotion_id,
        emotion_arc=emotion.get("target_arc", ""),
        genre_id=genre_id,
        genre_formula=genre.get("formula", ""),
        target_platform=item.get("target_platform", ""),
        target_length_min=target_min,
        target_length_max=target_max,
        hint_title=item.get("hint_title", ""),
        opening_mode_id=opening_id,
        ending_mode_id=ending_id,
        reversal_type_id=reversal_id,
        seasonal_or_topic_seed=item.get("seasonal_or_topic_seed", ""),
        expected_audience=item.get("expected_audience", ""),
        optional_references=optional_references,
    )

    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深编剧。"
                "严格按 JSON 对象输出选题卡片,不要 Markdown 代码块,不要解释文字。输出必须是合法 json。"
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


def _read_pool(pool_path: Path) -> dict[str, Any]:
    if not pool_path.exists():
        raise ThemePoolEmptyError(f"theme_pool.json missing: {pool_path}")
    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThemePoolEmptyError(f"theme_pool.json invalid: {exc}") from exc
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"items": data}
    raise ThemePoolEmptyError(f"theme_pool.json wrong type: {type(data).__name__}")


def _pick_least_consumed(
    items: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    """Return (item, index) of the lowest-consumed entry, ties broken by id."""
    best_idx = 0
    best_key: tuple[int, str] = (
        int(items[0].get("consumed_count", 0)),
        str(items[0].get("id", "")),
    )
    for idx in range(1, len(items)):
        key = (
            int(items[idx].get("consumed_count", 0)),
            str(items[idx].get("id", "")),
        )
        if key < best_key:
            best_key = key
            best_idx = idx
    return items[best_idx], best_idx


def _apply_overrides(
    item: dict[str, Any], overrides: Mapping[str, Any]
) -> dict[str, Any]:
    """Mutate ``item`` in place with CLI overrides; return what changed."""
    applied: dict[str, Any] = {}
    if "theme" in overrides and overrides["theme"]:
        item["theme"] = str(overrides["theme"])[:30] or item["theme"]
        applied["theme"] = item["theme"]
    if "target_length" in overrides and overrides["target_length"]:
        wc = int(overrides["target_length"])
        # Build a tight ±5% range so Phase 1/2 still see [min, max].
        item["target_length"] = [int(wc * 0.95), int(wc * 1.05)]
        applied["target_length"] = item["target_length"]
    if "target_platform" in overrides and overrides["target_platform"]:
        item["target_platform"] = str(overrides["target_platform"])
        applied["target_platform"] = item["target_platform"]
    if "hint_title" in overrides and overrides["hint_title"]:
        item["hint_title"] = str(overrides["hint_title"])
        applied["hint_title"] = item["hint_title"]
    return applied


def _parse_pitch(
    text: str, item: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Parse LLM JSON output; on failure synthesize a fallback from item."""
    pitch = _try_parse_json(text)
    if pitch is None:
        return _fallback_pitch(item), True

    out: dict[str, Any] = {}
    for f in PHASE0_OUTPUT_FIELDS:
        out[f] = pitch.get(f, _default_for(f, item))
    if not isinstance(out.get("target_length"), (list, tuple)) or len(out["target_length"]) != 2:
        out["target_length"] = list(item.get("target_length") or [8000, 12000])
    if not isinstance(out.get("tone_keywords"), list):
        out["tone_keywords"] = []
    if not isinstance(out.get("protagonist"), dict):
        out["protagonist"] = {
            "identity": str(out.get("protagonist") or ""),
            "narrative_voice": "第一人称",
        }
    return out, False


def _try_parse_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        while lines and lines[-1].strip() == "":
            lines.pop()
        if lines and lines[-1].strip().startswith("```"):
            lines.pop()
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # tolerate text-then-json wrapping (mock returns Chinese text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None
    return parsed if isinstance(parsed, dict) else None


def _fallback_pitch(item: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Phase 0 output when LLM JSON parse fails or mock mode runs.

    The pitch is built directly from the theme_pool item so Phase 1 still has
    every field it expects. Used in dry-run smoke tests and as an LLM
    safety net.
    """
    return {
        "theme": str(item.get("theme", "")),
        "tuned_pitch": (
            f"基于种子题材『{item.get('theme','')}』,"
            f"主角(第一人称)在『{item.get('seasonal_or_topic_seed','')}』背景下,"
            f"按 {item.get('genre','')} 公式展开。(fallback — LLM 输出未通过 JSON 解析)"
        ),
        "protagonist": {
            "identity": "(fallback)主角身份待 Phase 1 推演",
            "narrative_voice": "第一人称",
        },
        "antagonist_or_object": "(fallback)核心反对力量待 Phase 1 推演",
        "trigger_event": f"(fallback){item.get('hint_title','开篇冲突')}",
        "tone_keywords": ["(fallback)冷静", "(fallback)落地", "(fallback)细节"],
        "target_length": list(item.get("target_length") or [8000, 12000]),
        "emotion_id": str(item.get("emotion", "")),
        "genre_id": str(item.get("genre", "")),
        "opening_mode_id": str(item.get("opening_mode", "")),
        "ending_mode_id": str(item.get("ending_mode", "")),
        "reversal_type_id": str(item.get("reversal_type", "")),
        "target_platform": str(item.get("target_platform", "")),
        "weekly_topic_used": str(item.get("seasonal_or_topic_seed", "")),
        "hint_title": str(item.get("hint_title", "")),
    }


def _default_for(field_name: str, item: dict[str, Any]) -> Any:
    map_ = {
        "theme": item.get("theme", ""),
        "target_length": list(item.get("target_length") or [8000, 12000]),
        "emotion_id": item.get("emotion", ""),
        "genre_id": item.get("genre", ""),
        "opening_mode_id": item.get("opening_mode", ""),
        "ending_mode_id": item.get("ending_mode", ""),
        "reversal_type_id": item.get("reversal_type", ""),
        "target_platform": item.get("target_platform", ""),
        "weekly_topic_used": item.get("seasonal_or_topic_seed", ""),
        "hint_title": item.get("hint_title", ""),
        "tone_keywords": [],
    }
    return map_.get(field_name, "")


def _find_by_id(items: list[Any], target_id: str) -> dict[str, Any]:
    for it in items or []:
        if isinstance(it, dict) and it.get("id") == target_id:
            return it
    return {}


def _atomic_write_json(target: Path, data: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"{target.name}.tmp"
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                logger.debug("could not remove tmp file %s", tmp)


__all__ = [
    "PHASE0_OUTPUT_FIELDS",
    "Phase0Result",
    "ThemePoolEmptyError",
    "build_phase0_prompt",
    "select_theme",
]
