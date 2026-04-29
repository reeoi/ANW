"""Publish CLI placeholder with dry-run-safe behavior."""

from __future__ import annotations

from publisher.fansq import FansqPublisher


def main() -> int:
    """Run a dry-run publish check."""
    result = FansqPublisher().publish("示例标题", "示例正文")
    print(f"{result.status}: {result.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
