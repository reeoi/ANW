"""ANW 托盘图标生成器 (Phase 2)。"""

from __future__ import annotations

from io import BytesIO
from typing import Literal

from PIL import Image, ImageDraw

ColorName = Literal["green", "yellow", "red", "gray"]

PALETTE: dict[str, str] = {
    "green": "#16a34a",
    "yellow": "#d97706",
    "red": "#dc2626",
    "gray": "#64748b",
}


def make_icon(color: ColorName | str = "gray", badge: int = 0, size: int = 64) -> Image.Image:
    """生成 ``size x size`` 的圆形纯色 ANW 图标。

    参数:
        color: ``green`` / ``yellow`` / ``red`` / ``gray``。其他值回退到灰色。
        badge: 右下角红色数字角标 (1-9),0 表示不显示。
        size: 图标边长 (像素),默认 64。

    返回:
        ``PIL.Image.Image`` (RGBA)。
    """
    fill = PALETTE.get(color, PALETTE["gray"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 16)
    draw.ellipse((pad, pad, size - pad, size - pad), fill=fill)
    # 中央写 "A" (字体加载失败时退到默认像素字体)
    try:
        from PIL import ImageFont

        font_size = max(int(size * 0.55), 12)
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "A", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2 - bbox[0], (size - th) // 2 - bbox[1] - size // 32), "A", fill="white", font=font)
    except Exception:
        # 极简退化：用一条粗白线代表 A
        draw.line((size // 3, size // 4, size * 2 // 3, size // 4), fill="white", width=max(2, size // 16))
    if badge > 0:
        b = max(badge, 1)
        digit = str(b if b <= 9 else 9)
        bx0 = int(size * 0.62)
        bx1 = size - 1
        by0 = int(size * 0.62)
        by1 = size - 1
        draw.ellipse((bx0, by0, bx1, by1), fill="#dc2626", outline="white", width=max(1, size // 64))
        try:
            from PIL import ImageFont

            try:
                font = ImageFont.truetype("arial.ttf", max(10, (bx1 - bx0) - 4))
            except OSError:
                font = ImageFont.load_default()
            draw.text(
                (bx0 + (bx1 - bx0) // 2 - 4, by0 + (by1 - by0) // 2 - 8),
                digit,
                fill="white",
                font=font,
            )
        except Exception:
            pass
    return img


def make_icon_bytes(color: ColorName | str = "gray", badge: int = 0, size: int = 64) -> bytes:
    """便捷函数：返回 PNG 字节流,方便直接喂给 pystray / 写到磁盘。"""
    img = make_icon(color, badge=badge, size=size)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


__all__ = ["PALETTE", "make_icon", "make_icon_bytes"]
