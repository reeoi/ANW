"""Weekly seed-evolution implementation (PLAN §4 / §3.1 + scan_seeds.yaml).

Pipeline:
    seeds = load_seeds(...)
    weekly_topics = pick_weekly_topics(seeds, today=...)
    existing_items = read theme_pool.json (if present)
    msgs = build_evolution_prompt(seeds, weekly_topics, existing_items, pool_size)
    completion = client.chat_completion(msgs, model=v4-pro, thinking_mode=False,
                                        response_format={"type":"json_object"})
    items = parse JSON (tolerant: array root OR object with "items" key)
    items = _normalize_items(...)        # canonicalize id, set defaults
    _validate_schema(items, seeds, ...)   # required fields + enum membership
    _validate_diversity(items, seeds, ...)# distribution + dedup vs existing
    on success → backup existing pool to history/{old_iso_week}.json,
                 atomically write new theme_pool.json
    on failure → if existing pool present: keep it, mark used_fallback=True
                 else: raise WeeklyScanBlockedError
"""

from __future__ import annotations

import json
import logging
import os
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from random import Random
from typing import Any

import yaml

from config_loader import LoadedConfig

logger = logging.getLogger(__name__)


_DEFAULT_SEEDS_REL = Path("data/scan_seeds.yaml")

# Required fields per scan_seeds.yaml output_schema.fields.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "theme",
    "emotion",
    "genre",
    "formula_used",
    "target_platform",
    "target_length",
    "hint_title",
    "title_pattern_used",
    "opening_mode",
    "ending_mode",
    "reversal_type",
    "expected_audience",
    "seasonal_or_topic_seed",
    "consumed_count",
    "created_at",
)

_THEME_MIN_CHARS = 10
_THEME_MAX_CHARS = 30
_KEYWORD_OVERLAP_DEFAULT = 3


class WeeklyScanBlockedError(RuntimeError):
    """Raised when scan fails and no fallback theme_pool is available."""


@dataclass(frozen=True)
class WeeklyScanResult:
    """Outcome of one run_weekly_scan call.

    `used_fallback=True` means the LLM evolution failed and we are reusing the
    existing theme_pool.json verbatim. `backed_up_to` is the path written when
    a previous pool was archived (None when no archive was written).
    """

    iso_week: str
    item_count: int
    pool_path: Path
    backed_up_to: Path | None
    used_fallback: bool
    weekly_topics: list[str]
    warnings: list[str] = field(default_factory=list)


# ============================================================ load_seeds


def load_seeds(path: str | Path | None = None) -> dict[str, Any]:
    """Load and minimally validate scan_seeds.yaml."""
    if path is None:
        path = _project_root() / _DEFAULT_SEEDS_REL
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"scan seeds file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        seeds = yaml.safe_load(f)
    if not isinstance(seeds, dict):
        raise ValueError(f"scan seeds must be a mapping: {path}")
    return seeds


# ============================================================ pick_weekly_topics


def pick_weekly_topics(
    seeds: dict[str, Any],
    *,
    count: int | None = None,
    today: date | None = None,
) -> list[str]:
    """Pick weekly trend topics from `current_topics_pool ∪ seasonal[current_season]`.

    Deterministic per ISO week so rerunning on different days of the same
    week yields the same picks. Falls back to all eligible topics when
    `count` exceeds the candidate pool size.
    """
    today = today or date.today()
    iso_week = _iso_week_string(today)

    modifiers = seeds.get("time_seed_modifiers", {})
    if count is None:
        count = int(modifiers.get("weekly_random_count", 5))

    current = list(modifiers.get("current_topics_pool", []))
    season = _current_season(today)
    seasonal = list(modifiers.get("seasonal_topics", {}).get(season, []))

    seen: set[str] = set()
    candidates: list[str] = []
    for c in current + seasonal:
        if c not in seen:
            seen.add(c)
            candidates.append(c)

    if count >= len(candidates):
        return candidates

    rng = Random(iso_week)
    pool = list(candidates)
    rng.shuffle(pool)
    return pool[:count]


# ============================================================ build_evolution_prompt


def build_evolution_prompt(
    seeds: dict[str, Any],
    *,
    weekly_topics: list[str],
    existing_pool: list[dict[str, Any]] | None,
    pool_size: int,
) -> list[dict[str, str]]:
    """Compose the OpenAI-style messages list per `llm_evolution_prompt_template`."""
    template_str = seeds.get("llm_evolution_prompt_template", "")
    if not template_str:
        raise ValueError("seeds missing 'llm_evolution_prompt_template'")

    target = seeds.get("target_platform", {})
    primary_platform = target.get("primary", "")
    primary_traits = target.get("primary_traits", {})
    primary_word_range = primary_traits.get("word_count_range", [8000, 12000])
    audience = primary_traits.get("audience", "")

    comparators = target.get("comparator_platforms", {})
    comparator_summary = "; ".join(
        f"{name}({info.get('core', '')}, {info.get('word_count', '')}字)"
        for name, info in comparators.items()
    )

    emotion_options = ", ".join(
        f"{e['id']}({e.get('name','')}: {e.get('target_arc','')})"
        for e in seeds.get("emotion_types", [])
    )
    genre_options = ", ".join(
        f"{g['id']}({g.get('name','')})" for g in seeds.get("genres", [])
    )
    title_pattern_options = ", ".join(seeds.get("title_patterns", {}).keys())
    opening_mode_options = ", ".join(
        f"{o['id']}({o.get('name','')})" for o in seeds.get("opening_modes", [])
    )
    ending_mode_options = ", ".join(
        f"{e['id']}({e.get('name','')})" for e in seeds.get("ending_modes", [])
    )
    reversal_type_options = ", ".join(
        f"{r['id']}({r.get('name','')})" for r in seeds.get("reversal_types", [])
    )

    constraints = seeds.get("diversity_constraints", {})
    diversity_summary = json.dumps(constraints, ensure_ascii=False)

    if existing_pool:
        existing_themes_short = " | ".join(
            str(it.get("theme", ""))[:30] for it in existing_pool[:50]
        )
    else:
        existing_themes_short = "(无前次池,本次无需去重)"

    weekly_topics_str = ", ".join(weekly_topics) if weekly_topics else "(本周无注入)"

    pool_size_int = max(1, int(pool_size))
    genre_max_share = float(constraints.get("genre_max_share", 0.15))
    emotion_count = max(1, len(seeds.get("emotion_types", [])))
    genre_max_count_per_emotion = max(
        1, int(pool_size_int * genre_max_share / emotion_count)
    )

    template = string.Template(template_str)
    user_text = template.safe_substitute(
        pool_size=pool_size,
        primary_platform=primary_platform,
        primary_word_range=primary_word_range,
        audience=audience,
        comparator_summary=comparator_summary,
        emotion_options=emotion_options,
        genre_options=genre_options,
        title_pattern_options=title_pattern_options,
        opening_mode_options=opening_mode_options,
        ending_mode_options=ending_mode_options,
        reversal_type_options=reversal_type_options,
        weekly_topics=weekly_topics_str,
        existing_pool_themes_short_list=existing_themes_short,
        diversity_constraints_summary=diversity_summary,
        genre_max_count_per_emotion=genre_max_count_per_emotion,
        reversal_type_min_distinct=constraints.get("reversal_type_min_distinct", 4),
        opening_mode_min_distinct=constraints.get("opening_mode_min_distinct", 8),
        ending_mode_min_distinct=constraints.get("ending_mode_min_distinct", 6),
    )

    system = (
        "你是中文短篇网文市场分析师 + 题材策划。"
        "严格按 JSON 数组输出 100 条 theme_pool item,不要 Markdown 代码块,不要解释文字。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]


# ============================================================ run_weekly_scan


def run_weekly_scan(
    config: LoadedConfig,
    *,
    today: date | None = None,
    force: bool = False,
    client: Any = None,
) -> WeeklyScanResult:
    """Run weekly theme-pool evolution; see module docstring for full flow."""
    today = today or date.today()
    iso_week = _iso_week_string(today)

    project_root = _project_root_from_config(config)
    pool_path = project_root / "data" / "theme_pool.json"
    history_dir = project_root / "data" / "theme_pool.history"
    seed_path = project_root / config.data.get("scan", {}).get(
        "seed_file", "data/scan_seeds.yaml"
    )

    seeds = load_seeds(seed_path)
    pool_size = int(config.data.get("scan", {}).get("pool_size", 100))
    weekly_topics = pick_weekly_topics(seeds, today=today)

    existing_pool_data = _read_existing_pool(pool_path)
    existing_items = (
        existing_pool_data.get("items", [])
        if isinstance(existing_pool_data, dict)
        else []
    )

    # Skip when this ISO week already has a pool, unless caller forces a rerun.
    if (
        not force
        and isinstance(existing_pool_data, dict)
        and existing_pool_data.get("iso_week") == iso_week
        and existing_items
    ):
        return WeeklyScanResult(
            iso_week=iso_week,
            item_count=len(existing_items),
            pool_path=pool_path,
            backed_up_to=None,
            used_fallback=False,
            weekly_topics=existing_pool_data.get("weekly_topics", []),
            warnings=[
                f"theme_pool for {iso_week} already exists; skipped "
                "(call with force=True to rerun)"
            ],
        )

    warnings: list[str] = []

    try:
        if client is None:
            from generator.api_client import DeepSeekClient

            client = DeepSeekClient(config)

        messages = build_evolution_prompt(
            seeds,
            weekly_topics=weekly_topics,
            existing_pool=existing_items,
            pool_size=pool_size,
        )
        deepseek_cfg = config.data.get("deepseek", {})
        completion = client.chat_completion(
            messages,
            thinking_mode=False,
            model=deepseek_cfg.get("model", "deepseek-v4-pro"),
            response_format={"type": "json_object"},
            purpose="weekly_scan",
        )
        raw_items = _parse_llm_items(completion.text)
        normalized = _normalize_items(raw_items, iso_week=iso_week)
        _validate_schema(normalized, seeds=seeds, pool_size=pool_size)
        _validate_diversity(normalized, seeds=seeds, existing_items=existing_items)

        backed_up_to = (
            _backup_existing_pool(existing_pool_data, history_dir)
            if existing_pool_data
            else None
        )
        new_pool = {
            "version": 1,
            "iso_week": iso_week,
            "generated_at": _utc_now_iso(),
            "weekly_topics": weekly_topics,
            "used_fallback": False,
            "items": normalized,
        }
        _atomic_write_json(pool_path, new_pool)
        return WeeklyScanResult(
            iso_week=iso_week,
            item_count=len(normalized),
            pool_path=pool_path,
            backed_up_to=backed_up_to,
            used_fallback=False,
            weekly_topics=weekly_topics,
            warnings=warnings,
        )

    except WeeklyScanBlockedError:
        raise
    except Exception as exc:
        warnings.append(f"weekly_scan failure: {exc}")
        if existing_pool_data is not None and existing_items:
            warnings.append("falling back to existing theme_pool.json")
            logger.warning(
                "weekly_scan failed, using existing theme_pool: %s", exc
            )
            return WeeklyScanResult(
                iso_week=iso_week,
                item_count=len(existing_items),
                pool_path=pool_path,
                backed_up_to=None,
                used_fallback=True,
                weekly_topics=weekly_topics,
                warnings=warnings,
            )
        raise WeeklyScanBlockedError(
            "weekly_scan failed and no theme_pool.json fallback exists: " f"{exc}"
        ) from exc


# ============================================================ helpers


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _project_root_from_config(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt:
        return Path(rt).resolve()
    return _project_root()


def _iso_week_string(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}W{iso.week:02d}"


def _current_season(d: date) -> str:
    m = d.month
    if 3 <= m <= 5:
        return "spring"
    if 6 <= m <= 8:
        return "summer"
    if 9 <= m <= 11:
        return "autumn"
    return "winter"


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0, tzinfo=None)
        .isoformat()
        + "Z"
    )


def _read_existing_pool(pool_path: Path) -> dict[str, Any] | None:
    if not pool_path.exists():
        return None
    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        # Backward-compat: array-only format becomes a wrapper.
        return {"items": data, "iso_week": "", "weekly_topics": [], "used_fallback": False}
    return None


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
                logger.debug("could not remove tmp pool file %s", tmp)


def _backup_existing_pool(
    existing_pool_data: dict[str, Any], history_dir: Path
) -> Path | None:
    iso_week = existing_pool_data.get("iso_week")
    if not iso_week:
        return None
    history_dir.mkdir(parents=True, exist_ok=True)
    backup_path = history_dir / f"{iso_week}.json"
    backup_path.write_text(
        json.dumps(existing_pool_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_path


def _parse_llm_items(text: str) -> list[dict[str, Any]]:
    """Parse LLM response into list of item dicts.

    Tolerant of:
    - ``` or ```json fenced blocks
    - JSON array at root
    - JSON object with "items" key (set when response_format=json_object)
    """
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        # drop trailing ```
        while lines and lines[-1].strip() == "":
            lines.pop()
        if lines and lines[-1].strip().startswith("```"):
            lines.pop()
        text = "\n".join(lines)

    parsed = json.loads(text)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("items", "theme_pool", "data", "themes"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        raise ValueError(
            "expected JSON array or object with 'items' field, "
            f"got object with keys: {sorted(parsed.keys())}"
        )
    raise ValueError(f"expected JSON array or object, got {type(parsed).__name__}")


def _normalize_items(
    raw_items: list[Any], *, iso_week: str
) -> list[dict[str, Any]]:
    """Canonicalize ids and ensure consumed_count / created_at defaults."""
    iso_week_lower = iso_week.lower()
    now_iso = _utc_now_iso()
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError(f"item #{i} is not a JSON object")
        new_item = dict(item)
        new_item["id"] = f"tp_{iso_week_lower}_{i + 1:03d}"
        new_item.setdefault("consumed_count", 0)
        new_item.setdefault("created_at", now_iso)
        out.append(new_item)
    return out


def _validate_schema(
    items: list[dict[str, Any]], *, seeds: dict[str, Any], pool_size: int
) -> None:
    """Enforce required-field presence and enum membership per scan_seeds.yaml."""
    if len(items) != pool_size:
        raise ValueError(
            f"pool size mismatch: got {len(items)}, expected {pool_size}"
        )

    valid_emotions = {e["id"] for e in seeds.get("emotion_types", [])}
    valid_genres = {g["id"] for g in seeds.get("genres", [])}
    valid_openings = {o["id"] for o in seeds.get("opening_modes", [])}
    valid_endings = {e["id"] for e in seeds.get("ending_modes", [])}
    valid_reversals = {r["id"] for r in seeds.get("reversal_types", [])}
    valid_title_patterns = set(seeds.get("title_patterns", {}).keys())

    target = seeds.get("target_platform", {})
    valid_platforms = {target.get("primary", "")}
    valid_platforms.update(target.get("comparator_platforms", {}).keys())
    valid_platforms.discard("")

    for i, item in enumerate(items):
        for f in _REQUIRED_FIELDS:
            if f not in item:
                raise ValueError(f"item #{i} missing required field: {f}")

        theme = item["theme"]
        if not isinstance(theme, str):
            raise ValueError(f"item #{i} theme must be string")
        if not (_THEME_MIN_CHARS <= len(theme) <= _THEME_MAX_CHARS):
            raise ValueError(
                f"item #{i} theme length {len(theme)} not in "
                f"[{_THEME_MIN_CHARS},{_THEME_MAX_CHARS}]"
            )

        if item["emotion"] not in valid_emotions:
            raise ValueError(
                f"item #{i} emotion '{item['emotion']}' not in valid set"
            )
        if item["genre"] not in valid_genres:
            raise ValueError(
                f"item #{i} genre '{item['genre']}' not in valid set"
            )
        if item["opening_mode"] not in valid_openings:
            raise ValueError(
                f"item #{i} opening_mode '{item['opening_mode']}' not in valid set"
            )
        if item["ending_mode"] not in valid_endings:
            raise ValueError(
                f"item #{i} ending_mode '{item['ending_mode']}' not in valid set"
            )
        if item["reversal_type"] not in valid_reversals:
            raise ValueError(
                f"item #{i} reversal_type '{item['reversal_type']}' not in valid set"
            )
        if item["target_platform"] not in valid_platforms:
            raise ValueError(
                f"item #{i} target_platform '{item['target_platform']}' not in valid set"
            )
        if item["title_pattern_used"] not in valid_title_patterns:
            raise ValueError(
                f"item #{i} title_pattern_used '{item['title_pattern_used']}' "
                "not in valid set"
            )

        target_length = item["target_length"]
        if not isinstance(target_length, (list, tuple)) or len(target_length) != 2:
            raise ValueError(f"item #{i} target_length must be [min, max]")


def _validate_diversity(
    items: list[dict[str, Any]],
    *,
    seeds: dict[str, Any],
    existing_items: list[dict[str, Any]],
) -> None:
    """Enforce distribution + dedup constraints from `diversity_constraints`."""
    constraints = seeds.get("diversity_constraints", {})

    genre_max_share = float(constraints.get("genre_max_share", 0.15))
    max_genre_count = max(1, int(len(items) * genre_max_share))
    genre_counts = Counter(it["genre"] for it in items)
    for g, c in genre_counts.items():
        if c > max_genre_count:
            raise ValueError(
                f"genre '{g}' has {c} items, exceeds max {max_genre_count}"
            )

    reversal_min = int(constraints.get("reversal_type_min_distinct", 4))
    distinct_reversals = len({it["reversal_type"] for it in items})
    if distinct_reversals < reversal_min:
        raise ValueError(
            f"reversal_type distinct count {distinct_reversals} < min {reversal_min}"
        )

    opening_min = int(constraints.get("opening_mode_min_distinct", 8))
    distinct_openings = len({it["opening_mode"] for it in items})
    if distinct_openings < opening_min:
        raise ValueError(
            f"opening_mode distinct count {distinct_openings} < min {opening_min}"
        )

    ending_min = int(constraints.get("ending_mode_min_distinct", 6))
    distinct_endings = len({it["ending_mode"] for it in items})
    if distinct_endings < ending_min:
        raise ValueError(
            f"ending_mode distinct count {distinct_endings} < min {ending_min}"
        )

    duplicate_prevention = constraints.get("duplicate_prevention", {}) or {}
    overlap_max = int(
        duplicate_prevention.get(
            "no_two_themes_share_more_than_n_keywords", _KEYWORD_OVERLAP_DEFAULT
        )
    )

    keyword_sets = [_theme_keywords(it["theme"]) for it in items]
    for i in range(len(keyword_sets)):
        for j in range(i + 1, len(keyword_sets)):
            shared = len(keyword_sets[i] & keyword_sets[j])
            if shared > overlap_max:
                raise ValueError(
                    f"theme #{i} and #{j} share {shared} keywords (> {overlap_max})"
                )

    if duplicate_prevention.get("avoid_recent_pool_themes", True) and existing_items:
        existing_keywords = [
            _theme_keywords(it.get("theme", "")) for it in existing_items
        ]
        for i, ks in enumerate(keyword_sets):
            for j, eks in enumerate(existing_keywords):
                shared = len(ks & eks)
                if shared > overlap_max:
                    raise ValueError(
                        f"new theme #{i} overlaps existing #{j}: {shared} keywords"
                    )


def _theme_keywords(theme: str) -> set[str]:
    """Extract Chinese 2-char bigrams from a theme.

    Strips non-CJK chars first so digits/ASCII punctuation never contribute
    to overlap counts. Empty / single-char themes yield an empty set.
    """
    cleaned = re.sub(r"[^一-鿿]+", "", str(theme))
    if len(cleaned) < 2:
        return set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


__all__ = [
    "WeeklyScanBlockedError",
    "WeeklyScanResult",
    "build_evolution_prompt",
    "load_seeds",
    "pick_weekly_topics",
    "run_weekly_scan",
]
