"""Prompt construction helpers for short-story generation.

The prompt builder is intentionally dependency-free so it can be reused by CLI,
scheduler, tests, and future review/rewrite flows.
"""

from __future__ import annotations

import argparse


DEFAULT_THEME = "雨夜归人"
DEFAULT_WORD_COUNT = 3000
DEFAULT_STYLE = "现实温情"


def build_short_story_prompt(
    theme: str = DEFAULT_THEME,
    word_count: int = DEFAULT_WORD_COUNT,
    style: str = DEFAULT_STYLE,
) -> str:
    """Build a DeepSeek-ready prompt for a Chinese short story.

    Args:
        theme: Story theme or core image, for example ``雨夜归人``.
        word_count: Target Chinese character/word count. Must be positive.
        style: Desired writing style, for example ``现实温情`` or ``悬疑``.

    Returns:
        A complete instruction prompt that asks for a title and full story body.

    Raises:
        ValueError: If any input is empty or ``word_count`` is not positive.
    """
    normalized_theme = theme.strip()
    normalized_style = style.strip()
    if not normalized_theme:
        raise ValueError("theme must not be empty")
    if not normalized_style:
        raise ValueError("style must not be empty")
    if word_count <= 0:
        raise ValueError("word_count must be greater than 0")

    return "\n".join(
        [
            "你是一名擅长中文网文节奏的短篇小说作者。",
            f"请围绕主题《{normalized_theme}》创作一篇约 {word_count} 字的短篇小说。",
            f"整体风格：{normalized_style}。",
            "硬性要求：",
            "1. 输出包含明确标题，标题单独放在第一行，格式为《标题》；",
            "2. 正文结构完整，有开端、转折、高潮和留有余韵的结尾；",
            "3. 人物动机清晰，情绪推进自然，避免流水账；",
            "4. 内容适合大众阅读，不包含违法、露骨色情或仇恨内容；",
            "5. 除标题和正文外，不要输出解释、提纲或创作说明。",
        ]
    )


def main() -> int:
    """Print a prompt example; CLI flags can override all defaults."""
    parser = argparse.ArgumentParser(description="Build a short-story generation prompt.")
    parser.add_argument("--theme", default=DEFAULT_THEME, help="Story theme")
    parser.add_argument(
        "--word-count",
        type=int,
        default=DEFAULT_WORD_COUNT,
        help="Target story length",
    )
    parser.add_argument("--style", default=DEFAULT_STYLE, help="Writing style")
    args = parser.parse_args()

    print(build_short_story_prompt(args.theme, args.word_count, args.style))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
