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
    """Самый частый цвет изображения (квантование), иначе None.

    `getcolors()` без явного лимита возвращает None, если цветов больше
    ожидаемого — на вытянутых в полосу картинках это ломало определение цвета.
    """
    try:
        small = img.convert("RGB").resize((64, 64))
        quantized = small.quantize(colors=n, method=Image.MEDIANCUT)
        colors = quantized.getcolors(maxcolors=1 << 16)
        if not colors:
            return None
        _, index = max(colors, key=lambda item: item[0])
        palette = quantized.getpalette()
        red, green, blue = palette[index * 3:index * 3 + 3]
        return f"#{red:02X}{green:02X}{blue:02X}"
    except Exception:  # noqa: BLE001
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


def contrast_ratio(first: str, second: str) -> float:
    """Контраст двух цветов по WCAG (1..21). Используется вёрсткой и аудитом."""
    def luminance(hex_color: str) -> float:
        def channel(value: int) -> float:
            normalized = value / 255.0
            return normalized / 12.92 if normalized <= 0.03928 \
                else ((normalized + 0.055) / 1.055) ** 2.4

        r, g, b = hex_to_rgb(hex_color)
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)