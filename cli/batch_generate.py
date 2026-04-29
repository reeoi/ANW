"""Batch-generate short stories and enqueue them in SQLite."""

from __future__ import annotations

import argparse

from config_loader import LoadedConfig, load_from_environment
from generator.api_client import DeepSeekClient
from generator.prompt_builder import DEFAULT_STYLE, build_short_story_prompt
from queue.db import initialize_database, insert_story


def build_parser() -> argparse.ArgumentParser:
    """Build the batch generation argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate N short story drafts and enqueue each with status='pending'."
    )
    parser.add_argument("--count", type=int, required=True, help="Number of stories to generate")
    parser.add_argument("--theme", default="雨夜归人", help="Base story theme")
    parser.add_argument("--word-count", type=int, default=3000, help="Target word count per story")
    parser.add_argument("--style", default=DEFAULT_STYLE, help="Writing style")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force mock/dry-run generation even if live credentials are configured",
    )
    parser.add_argument(
        "--print-ids",
        action="store_true",
        help="Print each generated story id and title",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.count <= 0:
        parser.error("--count must be positive")
    if args.word_count <= 0:
        parser.error("--word-count must be positive")

    config = load_from_environment()
    if args.dry_run:
        config = _with_generation_dry_run(config)

    for warning in config.warnings:
        print(f"[config] {warning}")

    db_path = initialize_database(config)
    client = DeepSeekClient(config)
    success = 0
    failed = 0
    failure_reasons: list[str] = []
    inserted: list[tuple[int, str]] = []

    for index in range(1, args.count + 1):
        theme = _theme_for_index(args.theme, index, args.count)
        try:
            prompt = build_short_story_prompt(theme, args.word_count, args.style)
            story = client.generate_story(prompt)
            story_id = insert_story(db_path, story)
            inserted.append((story_id, story.title))
            success += 1
        except Exception as exc:  # pragma: no cover - defensive CLI boundary
            failed += 1
            failure_reasons.append(f"item {index}: {exc}")

    print(
        f"Batch generation completed: requested={args.count} success={success} "
        f"failed={failed} dry_run={client.is_mock()} database={db_path}"
    )
    if args.print_ids:
        for story_id, title in inserted:
            print(f"story_id={story_id} title={title}")
    if failure_reasons:
        print("failure_reasons=" + "; ".join(failure_reasons))
    else:
        print("failure_reasons=none")

    return 0 if failed == 0 else 1


def _theme_for_index(theme: str, index: int, count: int) -> str:
    if count == 1:
        return theme
    return f"{theme} #{index}"


def _with_generation_dry_run(config: LoadedConfig) -> LoadedConfig:
    data = dict(config.data)
    runtime = dict(data.get("runtime", {}))
    deepseek = dict(data.get("deepseek", {}))
    runtime["dry_run"] = True
    deepseek["mock"] = True
    data["runtime"] = runtime
    data["deepseek"] = deepseek
    return LoadedConfig(data=data, path=config.path, warnings=config.warnings)


if __name__ == "__main__":
    raise SystemExit(main())
