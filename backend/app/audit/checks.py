"""Детерминированный аудит сгенерированной PPTX по гайдлайнам шаблона.

Работает на python-pptx напрямую: границы, наложения, обрезка текста,
разрешённые токены (шрифты/цвета), контраст WCAG (>=4.5:1), плотность контента,
пустые слайды и заглушки, дубликаты, запрет «слайда-картинки».
Возвращает список проблем с bbox (в долях слайда) для красных рамок.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, asdict

from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE

from ..models.deck import Deck
from ..render.images import hex_to_rgb

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

PLACEHOLDER_MARKERS = ("Заголовок", "Дважды щёлкните", "Дважды щелкните", "Текст",
                       "Lorem", "Слайд", "Введите", "здесь текст", "подзаголовок")


@dataclass
class Issue:
    code: str
    severity: str            # error | warning
    slide: int               # 0-based
    message: str
    bbox: list = field(default_factory=list)   # [x, y, w, h] в долях

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def at(cls, code, severity, slide, message, bbox=None):
        return cls(code=code, severity=severity, slide=slide, message=message,
                   bbox=list(bbox or []))


class Audit:
    """Выполняет набор проверок над PDF/PPTX-артефактом."""

    def __init__(self, profile: dict):
        self.profile = profile
        allowed_hex = [p.get("hex") for p in profile.get("palette", []) if p.get("hex")]
        self.allowed_colors = set(c.upper() for c in allowed_hex)
        self.allowed_colors |= {c.upper() for c in profile.get("text_colors", [])}
        theme_colors = (profile.get("theme_colors") or {}).values()
        self.allowed_colors |= {c.upper() for c in theme_colors if c}
        self.fonts_allowed = set()
        for f in (profile.get("headline_font"), profile.get("body_font")):
            if f:
                self.fonts_allowed.add(f.lower())
        for fb in profile.get("fonts", []):
            self.fonts_allowed.add((fb.get("name") or "").lower())
        self.bg_color = self._default_bg()

    def _default_bg(self) -> str:
        hs = [p.get("hex") for p in self.profile.get("palette", []) if p.get("hex")]
        for h in hs:
            if h and _lum(h) >= 0.6:
                return h.upper()
        return "#FFFFFF"

    # ------------------------------------------------------------------- main
    def audit(self, deck: Deck, pptx_bytes: bytes) -> dict:
        prs = Presentation(io.BytesIO(pptx_bytes))
        self.W, self.H = prs.slide_width, prs.slide_height
        issues: list[Issue] = []

        slide_objs = list(prs.slides)
        headings: dict[str, int] = {}

        for si, slide in enumerate(slide_objs):
            geo = self._shape_geoms(slide)
            # 1. границы
            self._check_bounds(slide, geo, si, issues)
            # 2. наложения
            self._check_overlaps(slide, geo, si, issues)
            # 3. обрезка текста
            self._check_text_overflow(slide, si, issues)
            # 4. токены
            self._check_tokens(slide, si, issues)
            # 5. контраст
            self._check_contrast(slide, si, issues)
            # 6. плотность контента
            self._check_density(slide, si, issues)
            # 7. пустой слайд
            self._check_empty(slide, si, issues, geo)
            # 8. заполнители-заглушки
            self._check_placeholders(slide, si, issues, geo)
            # 9. слайд-картинка
            self._check_raster_slide(slide, si, issues, geo)
            # заголовки для дубликатов
            h = self._heading(slide)
            if h:
                headings[h.lower()] = headings.get(h.lower(), 0) + 1

        for h, n in headings.items():
            if n > 1:
                issues.append(Issue.at("duplicate_heading", "warning", -1,
                                       f"повторяющийся заголовок «{h[:60]}» встречается {n} раза"))

        n_err = sum(1 for i in issues if i.severity == "error")
        return {
            "passed": n_err == 0,
            "errors": n_err,
            "warnings": sum(1 for i in issues if i.severity == "warning"),
            "total": len(issues),
            "issues": [i.to_dict() for i in issues],
        }

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _fr(x, total):
        return 0.0 if total == 0 else max(0.0, min(1.0, x / total))

    def _shape_geoms(self, slide):
        out = []
        for sh in slide.shapes:
            if sh.left is None or sh.width is None:
                continue
            out.append({
                "shape": sh, "type": sh.shape_type,
                "x": sh.left, "y": sh.top, "w": sh.width, "h": sh.height,
                "filled": self._is_visible(sh),
            })
        return out

    @staticmethod
    def _is_visible(sh) -> bool:
        try:
            if sh.shape_type == 17 and not sh.has_text_frame.text.strip():
                return False
            if sh.shape_type in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):  # placeholders
                return False
        except Exception:
            pass
        return True

    def _heading(self, slide) -> str:
        try:
            if slide.shapes.title is not None:
                return slide.shapes.title.text_frame.text.strip()
        except Exception:
            pass
        return ""

    def _scan_runs(self, slide):
        """Все текстовые runs слайда с геометрией."""
        out = []
        for sh in slide.shapes:
            if not sh.has_text_frame:
                continue
            tf = sh.text_frame
            for p in tf.paragraphs:
                ptext = "".join(r.text for r in p.runs).strip()
                for r in p.runs:
                    if not r.text.strip():
                        continue
                    out.append({
                        "shape": sh, "run": r, "para": p, "ptext": ptext,
                        "font": self._run_font(r), "size": self._run_size(r),
                        "color": self._run_color(r), "bold": bool(r.font.bold) if r.font.bold is not None else False,
                    })
        return out

    @staticmethod
    def _run_font(r) -> str:
        try:
            n = r.font.name
            if n:
                return n
            rPr = r._r.rPr
            if rPr is not None:
                ea = rPr.find(f"{A_NS}ea")
                if ea is not None and ea.get("typeface"):
                    return ea.get("typeface")
                latin = rPr.find(f"{A_NS}latin")
                if latin is not None and latin.get("typeface"):
                    return latin.get("typeface")
        except Exception:
            pass
        return ""

    @staticmethod
    def _run_size(r) -> float:
        try:
            if r.font.size:
                return r.font.size.pt
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _run_color(r) -> str:
        try:
            if r.font.color.type is not None:
                rgb = r.font.color.rgb
                if rgb is not None:
                    return str(rgb)
        except Exception:
            pass
        return ""

    # ---------------------------------------------------------------- checks
    def _check_bounds(self, slide, geo, si, issues):
        W, H = self.W, self.H
        for g in geo:
            x, y, w, h = g["x"], g["y"], g["w"], g["h"]
            if g["type"] == 19 or g.get("shape").shape_type in (3, 17):
                pass
            if x < -10000 or y < -10000 or x + w > W + 10000 or y + h > H + 10000:
                issues.append(Issue.at(
                    "out_of_bounds", "error", si,
                    f"элемент «{g['shape'].name}» выходит за границы слайда",
                    [self._fr(x, W), self._fr(y, H), self._fr(w, W), self._fr(h, H)]))

    def _check_overlaps(self, slide, geo, si, issues):
        cur = []
        for g in geo:
            if not g["filled"] or g["w"] <= 0.02 * self.W:
                continue
            # если g лежит целиком внутри более крупной фигуры — это композиция карточки
            if self._contained_in(g, cur):
                continue
            for old in cur:
                if self._contained_in(g, [old]):
                    continue
                ox = max(g["x"], old["x"]); oy = max(g["y"], old["y"])
                ox2 = min(g["x"] + g["w"], old["x"] + old["w"])
                oy2 = min(g["y"] + g["h"], old["y"] + old["h"])
                iw, ih = ox2 - ox, oy2 - oy
                if iw <= 0 or ih <= 0:
                    continue
                if iw * ih > 0.18 * min(g["w"] * g["h"], old["w"] * old["h"]):
                    issues.append(Issue.at(
                        "overlap", "warning", si,
                        f"элементы «{old['shape'].name}» и «{g['shape'].name}» перекрываются",
                        [self._fr(ox, self.W), self._fr(oy, self.H),
                         self._fr(iw, self.W), self._fr(ih, self.H)]))
            cur.append(g)

    @staticmethod
    def _contained_in(g, boxes, tol=0.92):
        """Истина, если площадь g почти целиком внутри одной из заполненных рамок."""
        for b in boxes:
            if not b.get("filled"):
                continue
            ix = max(g["x"], b["x"]); iy = max(g["y"], b["y"])
            ix2 = min(g["x"] + g["w"], b["x"] + b["w"])
            iy2 = min(g["y"] + g["h"], b["y"] + b["h"])
            inter = max(0, ix2 - ix) * max(0, iy2 - iy)
            own = g["w"] * g["h"]
            if own and inter >= tol * own and b["w"] * b["h"] >= own:
                return True
        return False

    def _check_text_overflow(self, slide, si, issues):
        for sh in slide.shapes:
            if not sh.has_text_frame or sh.width is None:
                continue
            tf = sh.text_frame
            total = tf.text.strip()
            if not total:
                continue
            # суммарный кегль: максимум по runs
            estim = self._estimate_text_h(tf, sh.width)
            # запас 1.25 — честная защита от реальной вместимости (word_wrap)
            if estim > 0 and estim > sh.height * 1.75 and sh.height > 0:
                issues.append(Issue.at(
                    "text_overflow", "warning", si,
                    f"текст «{total[:40]}…» может не поместиться в «{sh.name}»",
                    [self._fr(sh.left, self.W), self._fr(sh.top, self.H),
                     self._fr(sh.width, self.W), self._fr(sh.height, self.H)]))

    def _estimate_text_h(self, tf, w_emu) -> float:
        from ..layout.geometry import text_height_in
        total_h = 0.0
        for p in tf.paragraphs:
            psize = 0.0
            for r in p.runs:
                sz = self._run_size(r)
                if sz:
                    psize = max(psize, sz)
            if psize == 0:
                psize = 14.0
            txt = "".join(r.text for r in p.runs)
            w_in = max(0.1, float(w_emu) / 914400.0)
            total_h += text_height_in(txt, w_in, psize)
        return total_h  # inches

    def _check_tokens(self, slide, si, issues):
        for it in self._scan_runs(slide):
            fname = (it["font"] or "").lower()
            if fname and fname not in self.fonts_allowed:
                issues.append(Issue.at(
                    "font_not_allowed", "error", si,
                    f"шрифт «{it['font']}» не входит в дизайн-систему шаблона"))
            col = it["color"]
            if not col:
                continue
            colkey = col if col.startswith("#") else "#" + col
            colkey = colkey.upper()
            if colkey not in self.allowed_colors and colkey not in (self.allowed_extra()):
                issues.append(Issue.at(
                    "color_not_allowed", "error", si,
                    f"цвет «{colkey}» не входит в палитру шаблона",
                    self._shape_bbox(it["shape"], slide)))

    def allowed_extra(self):
        return {"#FFFFFF", "#000000"}

    def _shape_bbox(self, sh, slide):
        if sh.left is None:
            return []
        return [self._fr(sh.left, self.W), self._fr(sh.top, self.H),
                self._fr(sh.width, self.W), self._fr(sh.height, self.H)]

    def _check_contrast(self, slide, si, issues):
        slide_bg = self._shape_bg(slide)
        for it in self._scan_runs(slide):
            col = it["color"]
            size = it["size"]
            if not col or not size:
                continue
            bg = self._shape_fill(it["shape"]) or slide_bg
            large = size >= 18 or (size >= 14 and it["bold"])
            threshold = 3.0 if large else 4.5
            r, g, b = hex_to_rgb(col)
            if _contrast(r, g, b, *_hex(bg)) < threshold:
                req = f"{threshold:.1f}:1 (WCAG)"
                issues.append(Issue.at(
                    "contrast_too_low", "error", si,
                    f"контраст текста «#{col}» ниже {req}",
                    self._shape_bbox(it["shape"], slide)))

    @staticmethod
    def _shape_fill(sh):
        """Заливка фигуры (solidFill srgbClr), иначе None."""
        try:
            if sh.fill.type == 1 and sh.fill.fore_color is not None:
                return str(sh.fill.fore_color.rgb)
        except Exception:
            pass
        return None

    def _shape_bg(self, slide) -> str:
        fill_colors = []
        for sh in slide.shapes:
            try:
                if sh.fill.type == 1:
                    rgb = sh.fill.fore_color.rgb
                    if rgb is not None:
                        fill_colors.append((sh.width or 0) * (sh.height or 0), str(rgb))
            except Exception:
                continue
        if fill_colors:
            fill_colors.sort(reverse=True)
            return fill_colors[0][1]
        return self.bg_color

    def _check_density(self, slide, si, issues):
        runs = self._scan_runs(slide)
        # буллеты
        bullets = [it for it in runs if self._is_bullet(it["para"], it["run"])]
        unique_bullets = {it["ptext"] for it in bullets if it["ptext"]}
        if len(unique_bullets) > 6:
            issues.append(Issue.at("too_many_bullets", "error", si,
                                   f"на слайде {len(unique_bullets)} буллетов (максимум 6)"))
        for it in bullets:
            if len(it["ptext"].split()) > 15:
                issues.append(Issue.at("bullet_too_long", "error", si,
                                       f"буллет длиннее 15 слов: «{it['ptext'][:50]}…»",
                                       self._shape_bbox(it["shape"], slide)))
        # диаграммы
        for sh in slide.shapes:
            gf = getattr(sh, "has_chart", False)
            if not gf:
                continue
            try:
                nseries = 0
                for plot in sh.chart.plots:
                    nseries += len(plot.series)
                if nseries > 5:
                    issues.append(Issue.at("too_many_series", "error", si,
                                           f"в диаграмме {nseries} серий (максимум 5)",
                                           self._shape_bbox(sh, slide)))
            except Exception:
                continue
            # таблицы
            if sh.shape_type == 19:
                tbl = sh.table
                if len(tbl.columns) > 5 or len(tbl.rows) > 7:
                    issues.append(Issue.at("table_too_big", "error", si,
                                           f"таблица {len(tbl.rows)}x{len(tbl.columns)} (макс. 7x5)",
                                           self._shape_bbox(sh, slide)))

    @staticmethod
    def _is_bullet(p, run) -> bool:
        try:
            pPr = p._pPr
            if pPr is None:
                return False
            if pPr.find(f"{A_NS}buChar") is not None or pPr.find(f"{A_NS}buAutoNum") is not None:
                return True
        except Exception:
            pass
        return False

    def _check_empty(self, slide, si, issues, geo):
        has_text = any(it["ptext"] for it in self._scan_runs(slide) if it["ptext"] and it["run"].text.strip())
        has_obj = any(g["type"] in (3, 19) or g.get("shape").shape_type in (3, 19) for g in geo)
        toolbar = any(True for g in geo)  # placeholder shapes не считаются пустыми
        if not has_text and not has_obj and len(geo) > 0:
            issues.append(Issue.at("empty_slide", "warning", si, "слайд не содержит контента"))

    def _check_placeholders(self, slide, si, issues, geo):
        full = " ".join(it["ptext"] for it in self._scan_runs(slide))
        for marker in PLACEHOLDER_MARKERS:
            if re.search(rf"(?i){re.escape(marker)}", full):
                issues.append(Issue.at("placeholder_text", "warning", si,
                                       f"найден текст-заглушка «{marker}»"))
                break

    def _check_raster_slide(self, slide, si, issues, geo):
        W, H = self.W, self.H
        for g in geo:
            if g["type"] == 18 and g["w"] >= 0.92 * W and g["h"] >= 0.92 * H:
                issues.append(Issue.at("raster_slide", "error", si,
                                       "слайд целиком является растровой картинкой",
                                       [0, 0, 1, 1]))


def _lum(h: str) -> float:
    r, g, b = hex_to_rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _hex(h: str) -> tuple:
    try:
        if len(h) == 8:
            h = h[2:]
        return hex_to_rgb(h)
    except Exception:
        return (0, 0, 0)


def _contrast(r1, g1, b1, r2, g2, b2) -> float:
    def L(r, g, b):
        rs = r / 255.0; gs = g / 255.0; bs = b / 255.0
        def f(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(rs) + 0.7152 * f(gs) + 0.0722 * f(bs)
    l1, l2 = L(r1, g1, b1), L(r2, g2, b2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)