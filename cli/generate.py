"""Generate one story and insert it into the SQLite queue."""

from __future__ import annotations

import argparse

from config_loader import load_from_environment
from generator.api_client import DeepSeekClient
from generator.prompt_builder import DEFAULT_STYLE, build_short_story_prompt
from queue.db import initialize_database, insert_story


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a short story draft and enqueue it with status='pending'."
    )
    parser.add_argument("--theme", default="雨夜归人", help="Story theme")
    parser.add_argument("--word-count", type=int, default=3000, help="Target word count")
    parser.add_argument("--style", default=DEFAULT_STYLE, help="Writing style")
    parser.add_argument(
        "--print-content",
        action="store_true",
        help="Also print the generated story content after enqueueing",
    )
    args = parser.parse_args()

    config = load_from_environment()
    for warning in config.warnings:
        print(f"[config] {warning}")

    db_path = initialize_database(config)
    prompt = build_short_story_prompt(args.theme, args.word_count, args.style)
    story = DeepSeekClient(config).generate_story(prompt)
    story_id = insert_story(db_path, story)

    print(f"Generated story ID: {story_id}")
    print(f"Title: {story.title}")
    print("Status: pending")
    print(f"Database: {db_path}")
    if args.print_content:
        print("\n--- content ---")
        print(story.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
