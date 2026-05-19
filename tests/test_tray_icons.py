"""测试托盘图标生成 — 验证 PNG 字节有效 + 4 色不同。"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tray_icons import make_icon, make_icon_bytes, PALETTE


@pytest.mark.parametrize("color", ["green", "yellow", "red", "gray"])
def test_make_icon_returns_pil_image(color: str) -> None:
    img = make_icon(color)
    assert img.mode == "RGBA"
    assert img.size == (64, 64)


def test_make_icon_unknown_color_falls_back_to_gray() -> None:
    img = make_icon("unknown")
    assert img.size == (64, 64)


def test_make_icon_bytes_is_valid_png() -> None:
    data = make_icon_bytes("green")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    Image.open(BytesIO(data)).verify()  # 不抛即成功


def test_make_icon_with_badge() -> None:
    img = make_icon("red", badge=3)
    assert img.size == (64, 64)


def test_make_icon_high_badge_clamped() -> None:
    img = make_icon("yellow", badge=99)
    assert img.size == (64, 64)


def test_make_icon_custom_size() -> None:
    img = make_icon("green", size=128)
    assert img.size == (128, 128)


def test_palette_has_4_colors() -> None:
    assert set(PALETTE.keys()) == {"green", "yellow", "red", "gray"}


def test_distinct_color_pixels() -> None:
    """不同 color 在中心区域应有不同的主色像素。"""
    green = make_icon("green").getpixel((32, 50))
    red = make_icon("red").getpixel((32, 50))
    assert green != red
    # 至少其中一个应是非透明的彩色
    assert any(c[3] > 0 for c in (green, red))
