"""Prompt construction helpers for short-story generation."""

from __future__ import annotations


def build_short_story_prompt(theme: str, word_count: int = 3000, style: str = "现实温情") -> str:
    """Build a concise prompt for a Chinese short story."""
    return (
        f"请围绕主题《{theme}》创作一篇约 {word_count} 字的短篇小说，"
        f"风格为{style}，要求结构完整、人物动机清晰、结尾有余韵。"
    )
