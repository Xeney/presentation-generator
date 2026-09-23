"""Пиксельные утилиты: формирование изображений, доминирующий цвет, наложение рамок."""
from __future__ import annotations

from io import BytesIO
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

RGB_HEX = ("#",)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = (h or "#000000").lstrip("#")
    if len(h) != 6:
        return (0, 0, 0)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgba(h: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(h)
    return (r, g, b, alpha)


def fit_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Вписывает изображение в w x h с кропом (соотношение сторон сохраняется)."""
    if img.width == w and img.height == h:
        return img
    return ImageOps.fit(img, (w, h), method=Image.LANCZOS)


def dominant_color(img: Image.Image, n: int = 8) -> Optional[str]:
    """Самый частый цвет изображения (квантование), иначе None."""
    try:
        small = img.convert("RGB").resize((64, 64))
        q = small.quantize(colors=n, method=Image.MEDIANCUT)
        counts = sorted(enumerate(q.getcolors()), key=lambda x: -x[1][0])
        if not counts:
            return None
        idx = counts[0][1][1]
        return "#%02X%02X%02X" % q.getpalette()[idx * 3:idx * 3 + 3]
    except Exception:
        return None


def draw_issue_boxes(img: Image.Image, boxes: Iterable[tuple[float, float, float, float]],
                     color: str = "#E62117", width: int = 3) -> Image.Image:
    """Рисует красные рамки проблем на миниатюре (доли 0..1)."""
    out = img.convert("RGB")
    dr = ImageDraw.Draw(out)
    W, H = out.size
    for (x, y, w, h) in boxes:
        dr.rectangle([x * W, y * H, min(W, (x + w) * W), min(H, (y + h) * H)],
                     outline=color, width=width)
    return out


def png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def brightness(hex_color: str) -> float:
    r, g, b = hex_to_rgb(hex_color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0