"""CLI entry point: manually trigger the weekly seed-evolution scan.

Usage:
    python -m cli.scan_now              # live: hits DeepSeek if api_key configured
    python -m cli.scan_now --dry-run    # offline: synthesizes a valid pool locally
    python -m cli.scan_now --force      # rerun even if this week's pool exists
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import load_from_environment
from generator.api_client import ChatCompletion, ChatUsage, DeepSeekClient
from scan import WeeklyScanBlockedError, load_seeds, run_weekly_scan


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Manually trigger the weekly seed-evolution scan. "
            "Refreshes data/theme_pool.json from data/scan_seeds.yaml via DeepSeek-V4-Pro."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rerun even if a pool exists for the current ISO week.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the live DeepSeek call; synthesize a valid pool locally.",
    )
    args = parser.parse_args()

    config = load_from_environment()
    for warning in config.warnings:
        print(f"[config] {warning}")

    client: Any
    if args.dry_run:
        print("[scan_now] dry-run mode: synthesizing pool locally, no DeepSeek call")
        seeds = load_seeds()
        client = _DryRunScanClient(seeds=seeds)
    else:
        client = DeepSeekClient(config)

    try:
        result = run_weekly_scan(config, force=args.force, client=client)
    except WeeklyScanBlockedError as exc:
        print(f"BLOCKED: {exc}")
        return 1

    print(f"iso_week={result.iso_week}")
    print(f"item_count={result.item_count}")
    print(f"used_fallback={result.used_fallback}")
    print(f"pool_path={result.pool_path}")
    if result.backed_up_to:
        print(f"backed_up_to={result.backed_up_to}")
    print("weekly_topics=" + (", ".join(result.weekly_topics) or "(none)"))
    for w in result.warnings:
        print(f"warning: {w}")
    return 0


class _DryRunScanClient:
    """Offline stand-in for DeepSeekClient that emits a valid synthetic pool.

    Used only by ``--dry-run``: builds 100 schema-compliant items by drawing
    from the loaded seeds, so the full validation + write pipeline exercises
    end-to-end without a network call. Themes use disjoint CJK code-point
    slots so the in-pool keyword-overlap check passes.
    """

    def __init__(self, *, seeds: dict[str, Any]) -> None:
        self._seeds = seeds

    def chat_completion(
        self,
        messages,  # noqa: ARG002 — unused: synthetic mode ignores prompt
        *,
        thinking_mode=None,  # noqa: ARG002
        model: str | None = None,
        temperature: float = 0.8,  # noqa: ARG002
        response_format=None,  # noqa: ARG002
        purpose: str = "chat",  # noqa: ARG002
    ) -> ChatCompletion:
        items = _build_synthetic_pool(self._seeds, count=100)
        text = json.dumps(items, ensure_ascii=False)
        return ChatCompletion(
            text=text,
            reasoning=None,
            model=(model or "deepseek-v4-pro") + "-dryrun",
            usage=ChatUsage(
                input_tokens=200, cached_tokens=0, output_tokens=400, raw={}
            ),
            finish_reason="stop",
            cached=False,
        )

    def is_mock(self) -> bool:
        return True


def _build_synthetic_pool(seeds: dict[str, Any], *, count: int = 100) -> list[dict[str, Any]]:
    """Emit a count=100 schema-compliant pool for offline dry-run."""
    if count != 100:
        raise ValueError("synthetic pool helper supports count=100 only")

    emotion_ids = [e["id"] for e in seeds.get("emotion_types", [])]
    genre_ids = [g["id"] for g in seeds.get("genres", [])]
    reversal_ids = [r["id"] for r in seeds.get("reversal_types", [])]
    opening_ids = [o["id"] for o in seeds.get("opening_modes", [])]
    ending_ids = [e["id"] for e in seeds.get("ending_modes", [])]
    pattern_keys = list(seeds.get("title_patterns", {}).keys())
    target_platform = seeds.get("target_platform", {})
    primary_platform = target_platform.get("primary", "番茄短篇")
    comparator_keys = list(target_platform.get("comparator_platforms", {}).keys())

    constraints = seeds.get("diversity_constraints", {})
    emotion_target = constraints.get("emotion_distribution_target", {})
    platform_target = constraints.get("platform_distribution_target", {})

    emotions = _expand_distribution(emotion_target, emotion_ids, count)
    platform_pool = [primary_platform] + comparator_keys
    platforms = _expand_distribution(platform_target, platform_pool, count)

    items: list[dict[str, Any]] = []
    base = 0x5400
    for i in range(count):
        theme = "".join(chr(base + slot * 200 + i) for slot in range(10))
        items.append(
            {
                "id": f"tp_dryrun_{i + 1:03d}",
                "theme": theme,
                "emotion": emotions[i],
                "genre": genre_ids[i % max(1, len(genre_ids))],
                "formula_used": "dry-run-formula",
                "target_platform": platforms[i],
                "target_length": [8000, 12000],
                "hint_title": f"样例标题第{i + 1}号",
                "title_pattern_used": pattern_keys[0] if pattern_keys else "番茄主流",
                "opening_mode": opening_ids[i % max(1, len(opening_ids))],
                "ending_mode": ending_ids[i % max(1, len(ending_ids))],
                "reversal_type": reversal_ids[i % max(1, len(reversal_ids))],
                "expected_audience": "女频/25-35 都市",
                "seasonal_or_topic_seed": "(dry-run)",
                "consumed_count": 0,
                "created_at": "2026-05-06T03:00:00Z",
            }
        )
    return items


def _expand_distribution(
    ratios: dict[str, float], pool: list[str], total: int
) -> list[str]:
    """Expand a ratio dict into a `total`-length list, padded by pool fallback."""
    out: list[str] = []
    for key, ratio in ratios.items():
        if key in pool or True:  # keys not in pool still legal target_platform names
            out.extend([key] * round(total * float(ratio)))
    fallback = pool[0] if pool else ""
    while len(out) < total:
        out.append(fallback)
    return out[:total]


if __name__ == "__main__":
    raise SystemExit(main())
