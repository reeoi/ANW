"""Generate a story from the command line using dry-run-safe config."""

from __future__ import annotations

import argparse

from config_loader import load_from_environment
from generator.api_client import DeepSeekClient
from generator.prompt_builder import build_short_story_prompt


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a short story draft.")
    parser.add_argument("--theme", default="雨夜归人", help="Story theme")
    parser.add_argument("--word-count", type=int, default=3000, help="Target word count")
    args = parser.parse_args()

    config = load_from_environment()
    for warning in config.warnings:
        print(f"[config] {warning}")

    prompt = build_short_story_prompt(args.theme, args.word_count)
    story = DeepSeekClient(config).generate_story(prompt)
    print(story)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
