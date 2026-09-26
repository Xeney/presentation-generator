"""Парсинг PPTX-шаблона в TemplateProfile.

Данные извлекаются исключительно из файла: тема (цвета/шрифты), мастера,
макеты и плейсхолдеры с геометрией, реальные слайды (палитра, типографика,
паттерны). Никаких знаний о конкретных шаблонах VK в коде нет.
"""
from __future__ import annotations

import io
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Optional
from zipfile import ZipFile

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.util import Emu

from .profile import (
    ColorToken,
    FontToken,
    LayoutProfile,
    PlaceholderInfo,
    TemplateProfile,
)

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Имена типов плейсхолдеров берём из перечисления python-pptx: магические числа
# в прошлой версии частично не соответствовали спецификации (11 — это org_chart,
# 12 — table, 16 — date), из-за чего роли макетов определялись неверно.
PLACEHOLDER_TYPES = {
    PP_PLACEHOLDER.TITLE.value: "title",
    PP_PLACEHOLDER.BODY.value: "body",
    PP_PLACEHOLDER.CENTER_TITLE.value: "center_title",
    PP_PLACEHOLDER.SUBTITLE.value: "subtitle",
    PP_PLACEHOLDER.VERTICAL_TITLE.value: "vertical_title",
    PP_PLACEHOLDER.VERTICAL_BODY.value: "vertical_text",
    PP_PLACEHOLDER.OBJECT.value: "object",
    PP_PLACEHOLDER.VERTICAL_OBJECT.value: "vertical_object",
    PP_PLACEHOLDER.CHART.value: "chart",
    PP_PLACEHOLDER.TABLE.value: "table",
    PP_PLACEHOLDER.BITMAP.value: "bitmap",
    PP_PLACEHOLDER.MEDIA_CLIP.value: "media",
    PP_PLACEHOLDER.ORG_CHART.value: "org_chart",
    PP_PLACEHOLDER.PICTURE.value: "picture",
    PP_PLACEHOLDER.SLIDE_IMAGE.value: "slide_image",
    PP_PLACEHOLDER.SLIDE_NUMBER.value: "slide_number",
    PP_PLACEHOLDER.FOOTER.value: "footer",
    PP_PLACEHOLDER.HEADER.value: "header",
    PP_PLACEHOLDER.DATE.value: "date",
}

# типы плейсхолдеров, отвечающие за фирменные элементы (логотип, колонтитул, номер)
BRANDING_TYPES = ("slide_number", "footer", "header", "date")

ROLE_KEYWORDS_RU = {
    "title": ["титул", "обложк", "первый слайд", "first", "cover", "заставк"],
    "section": ["раздел", "переход", "section", "разделитель", "divider"],
    "agenda": ["содержани", "оглавлени", "agenda", "план", "навигаци", "contents"],
    "final": ["спасибо", "финальн", "заключительн", "контакты", "thanks", "final", "qr"],
    "content": ["содерж", "заголовок", "текст", "объект", "пункт", "цитат", "мокап",
                 "скриншот", "фото", "команд", "статистик", "стадии", "проблем", "решение",
                 "демо", "спикер", "заг", "content", "title", "photo", "quote"],
}

ROLE_KEYWORDS_EN = {
    "title": ["title", "cover", "intro"],
    "section": ["section", "divider", "transition"],
    "agenda": ["agenda", "table of content", "toc", "plan", "outline"],
    "final": ["thanks", "thank", "final", "closing", "contacts", "qr"],
    "content": ["content", "text", "object", "photo", "quote", "mockup", "screenshot",
                 "bullet", "statistic", "team", "problem", "solution", "demo", "speaker"],
}


@dataclass
class _RawSlide:
    layout: object
    text_stats: list[dict]          # per text shape
    shapes_geo: list[tuple]         # (x,y,w,h,kind,fill)
    fills: list[str]
    texts: list[dict]


def shape_code(shape) -> Optional[int]:
    """Числовой код типа фигуры.

    `str(shape.shape_type)` даёт «PICTURE (13)», поэтому проверки вида
    `str(...).endswith("PICTURE")` не срабатывали: картинки и группы макетов
    не распознавались. Сравниваем числовой код.
    """
    shape_type = getattr(shape, "shape_type", None)
    return getattr(shape_type, "value", shape_type)


def _attr(el, tag, attr):
    node = el.find(f"{{{A_NS}}}{tag}")
    return node.get(attr) if node is not None else None


def _fmt_hex(srgb: str) -> str:
    return "#" + srgb.upper() if srgb else ""


def body_like_subtitles(phs: list, slide_w: float, slide_h: float,
                        named_role: Optional[str] = None) -> list:
    """Подзаголовки, которые на деле являются телом слайда.

    Подзаголовок — конвенция обложек (строка под заголовком), но Slidesgo и
    подобные шаблоны объявляют им контентную область. Отличить их по одной
    геометрии нельзя: у стандартной обложки Office подзаголовок тоже крупный.
    Признак — соотношение с заголовком: у обложки подзаголовок сопоставим с
    заголовком, у контентного макета тело в разы больше (≥2.5×). Несколько
    подзаголовков сразу — тоже тело (обложек с двумя не бывает).

    Имя — только tie-breaker: явное «титульный слайд» оставляет подзаголовок
    подзаголовком, явное «контентный/с телом» — делает его телом.
    """
    subtitle_phs = [p for p in phs if p.type == "subtitle"]
    if not subtitle_phs:
        return []
    if named_role == "title":
        return []
    if named_role == "content":
        return list(subtitle_phs)
    slide_area = max(1e-6, slide_w * slide_h)
    title_area = max((p.w * p.h for p in phs if p.is_title), default=0.0)
    out = []
    for ph in subtitle_phs:
        area = (ph.w * ph.h) / slide_area if ph.w and ph.h else 0.0
        if len(subtitle_phs) >= 2:
            out.append(ph)
        elif title_area:
            if area * slide_area >= 2.5 * title_area:
                out.append(ph)
        elif area >= 0.10:
            out.append(ph)
    return out


class PptxParseError(Exception):
    pass


class TemplateParser:
    """Извлекает дизайн-систему из произвольного PPTX."""

    def __init__(self, path_or_bytes):
        self._data = path_or_bytes
        self._name = getattr(path_or_bytes, "name", "template.pptx")
        self.counters = Counter()
        self.slide_count = 0
        self.layout_count = 0
        # кэш доминирующих цветов картинок: одинаковые картинки повторяются
        # в десятках макетов, без кэша разбор занимал десятки секунд
        self._picture_colors: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------ helpers
    def _load(self):
        if isinstance(self._data, (bytes, bytearray)):
            self._zip = ZipFile(io.BytesIO(self._data))
            self._prs = Presentation(io.BytesIO(bytes(self._data)))
        else:
            self._zip = ZipFile(self._data)
            self._prs = Presentation(self._data)
        self.layout_count = sum(len(m.slide_layouts) for m in self._prs.slide_masters)
        self.slide_count = len(self._prs.slides)

    def _all_layouts(self):
        for master in self._prs.slide_masters:
            for layout in master.slide_layouts:
                yield layout

    def _theme_xmls(self) -> list[tuple[str, str]]:
        out = []
        for name in self._zip.namelist():
            if re.match(r"ppt/theme/theme\d+\.xml$", name):
                out.append((name, self._zip.read(name).decode("utf-8")))
        return out

    def _parse_theme(self, xml: str) -> dict:
        """Извлекает цветовую схему и шрифты темы."""
        import lxml.etree as ET
        root = ET.fromstring(xml.encode("utf-8"))
        clr = {}
        for el in root.iter(f"{{{A_NS}}}clrScheme"):
            for child in el:
                tag = child.tag.rsplit("}", 1)[-1]
                srgb = child.find(f"{{{A_NS}}}srgbClr")
                if srgb is not None:
                    clr[tag] = _fmt_hex(srgb.get("val"))
                    continue
                sysc = child.find(f"{{{A_NS}}}sysClr")
                if sysc is not None and sysc.get("lastClr"):
                    clr[tag] = _fmt_hex(sysc.get("lastClr"))
        fonts = {}
        for el in root.iter(f"{{{A_NS}}}fontScheme"):
            major, minor = {}, {}
            for m in el:
                tag = m.tag.rsplit("}", 1)[-1]
                if tag not in ("majorFont", "minorFont"):
                    continue
                latin = m.find(f"{{{A_NS}}}latin")
                ea = m.find(f"{{{A_NS}}}ea")
                target = major if tag == "majorFont" else minor
                if latin is not None:
                    target["latin"] = latin.get("typeface")
                if ea is not None:
                    target["ea"] = ea.get("typeface")
            fonts = {"major": major, "minor": minor}
        return {"colors": clr, "fonts": fonts}

    def _shape_geo(self, sh) -> Optional[tuple]:
        if sh.left is None or sh.top is None or sh.width is None or sh.height is None:
            return None
        return (Emu(sh.left).inches, Emu(sh.top).inches,
                Emu(sh.width).inches, Emu(sh.height).inches)

    def _shape_fill_hex(self, sh) -> Optional[str]:
        """Цвет заливки фигуры из raw XML (solidFill srgbClr/schemeClr)."""
        el = getattr(sh, "_element", None)
        if el is None:
            return None
        spPr = el.find(f"{{{P_NS}}}spPr")
        if spPr is None:
            spPr = el.find(f"{{{A_NS}}}spPr")
        if spPr is None:
            return None
        solid = spPr.find(f"{{{A_NS}}}solidFill")
        if solid is None:
            return None
        srgb = solid.find(f"{{{A_NS}}}srgbClr")
        if srgb is not None:
            return _fmt_hex(srgb.get("val"))
        scheme = solid.find(f"{{{A_NS}}}schemeClr")
        if scheme is not None:
            return f"scheme:{scheme.get('val')}"
        return None

    def _run_style(self, r) -> dict:
        """Стиль одного run: шрифт, кегль, жирность, цвет."""
        name = None
        try:
            name = r.font.name
        except Exception:
            pass
        if not name:
            # типографика из ea/latin может быть задана в forEach attribute at rPr level
            rPr = r._r.rPr
            if rPr is not None:
                ea = rPr.find(f"{{{A_NS}}}ea")
                latin = rPr.find(f"{{{A_NS}}}latin")
                name = (ea.get("typeface") if ea is not None else None) or (
                    latin.get("typeface") if latin is not None else None
                )
        size = r.font.size.pt if r.font.size else None
        bold = bool(r.font.bold) if r.font.bold is not None else None
        color = None
        try:
            if r.font.color and r.font.color.type is not None:
                tc = r.font.color.theme_color
                rgb = r.font.color.rgb
                if rgb is not None:
                    color = _fmt_hex(str(rgb))
                elif tc is not None:
                    color = f"scheme:{tc}"
        except Exception:
            pass
        return {"font": name, "size": size, "bold": bold, "color": color}

    def _text_style_stats(self, shape) -> dict:
        """Сводная статистика по тексту фигуры (наиболее частый стиль + полный текст)."""
        if not shape.has_text_frame:
            return {}
        tf = shape.text_frame
        fonts = Counter()
        sizes = Counter()
        colors = Counter()
        bolds = Counter()
        total_chars = 0
        for p in tf.paragraphs:
            for r in p.runs:
                st = self._run_style(r)
                if st.get("font"):
                    fonts[st["font"]] += 1
                if st.get("size"):
                    sizes[st["size"]] += 1
                if st.get("color"):
                    colors[st["color"]] += 1
                if st.get("bold") is not None:
                    bolds[st["bold"]] += 1
                total_chars += len(r.text)
        align = None
        try:
            for p in tf.paragraphs:
                if p.alignment is not None:
                    align = str(p.alignment).split(".")[-1].lower()
                    break
        except Exception:
            pass
        return {
            "chars": total_chars,
            "font": fonts.most_common(1)[0][0] if fonts else None,
            "size": sizes.most_common(1)[0][0] if sizes else None,
            "color": colors.most_common(1)[0][0] if colors else None,
            "bold": bolds.most_common(1)[0][0] if bolds else None,
            "align": align,
            "text": tf.text[:200],
        }

    def _placeholder_info(self, sh) -> Optional[PlaceholderInfo]:
        if not sh.is_placeholder:
            return None
        geo = self._shape_geo(sh)
        if geo is None:
            return None
        try:
            ph = sh.placeholder_format
            ptype = PLACEHOLDER_TYPES.get(int(ph.type), "body")
            idx = int(ph.idx)
        except Exception:
            ptype, idx = "body", -1
        if idx > 500:
            idx = -1
        st = self._text_style_stats(sh)
        is_title = ptype in ("title", "center_title")
        return PlaceholderInfo(
            idx=idx, type=ptype, name=sh.name,
            x=geo[0], y=geo[1], w=geo[2], h=geo[3],
            font=st.get("font"), size=st.get("size"),
            color=st.get("color"), align=st.get("align") or "left",
            is_title=is_title,
        )

    # ------------------------------------------------------------- role guessing
    @staticmethod
    def _name_has(name: str, master_name: str, role: str) -> bool:
        """Tie-breaker: ключевое слово роли в имени макета или мастера."""
        haystack = f"{name or ''} {master_name or ''}".lower()
        keywords = ROLE_KEYWORDS_RU.get(role, []) + ROLE_KEYWORDS_EN.get(role, [])
        return any(kw in haystack for kw in keywords)

    @staticmethod
    def _layout_shape_kinds(layout, slide_w: float, slide_h: float) -> dict:
        """Что реально лежит на макете: таблицы, диаграммы, картинки, текстовые блоки.

        `texts` — содержательные свободные текстовые фигуры (не плейсхолдеры):
        многие шаблоны держат контентную область именно так, и без их подсчёта
        макет выглядит как «только заголовок».

        Картинка во весь слайд — фон (декор), а не контентное изображение:
        Slidesgo и подобные шаблоны кладут такую картинку на каждый макет.
        """
        kinds = {"tables": 0, "charts": 0, "pictures": 0, "texts": 0, "shapes": 0}
        slide_area = max(1e-6, slide_w * slide_h)
        try:
            shapes = list(layout.shapes)
        except Exception:  # noqa: BLE001
            return kinds
        for shape in shapes:
            try:
                if getattr(shape, "has_table", False):
                    kinds["tables"] += 1
                    continue
                if getattr(shape, "has_chart", False):
                    kinds["charts"] += 1
                    continue
                if shape_code(shape) == MSO_SHAPE_TYPE.PICTURE.value:
                    geo = self._shape_geo(shape)
                    area = (geo[2] * geo[3]) if geo else 0.0
                    if area >= 0.85 * slide_area:
                        kinds["shapes"] += 1  # фон, не контентная картинка
                    else:
                        kinds["pictures"] += 1
                    continue
                if getattr(shape, "has_text_frame", False) and not shape.is_placeholder:
                    text = shape.text_frame.text.strip()
                    area = (shape.width or 0) * (shape.height or 0)
                    area_in = area / (914400 ** 2) if area else 0.0
                    if len(text) >= 12 and area_in >= 0.02 * slide_area:
                        kinds["texts"] += 1
                        continue
                kinds["shapes"] += 1
            except Exception:  # noqa: BLE001
                continue
        return kinds

    def _named_role(self, name: str, master_name: str) -> Optional[str]:
        """Семантическая роль по имени макета — tie-breaker для структуры.

        Учитываются только однозначные слова («титул», «оглавление», «спасибо»),
        а не общие («title», «content»), иначе «Title and Content» превратился бы
        в титульный макет. Слова вроде «содержание» проверяются раньше «раздела».
        Разделители имён (TITLE_ONLY, title-slide) приводятся к пробелам, иначе
        ключ «title only» не находится.
        """
        haystack = f"{name or ''} {master_name or ''}".lower()
        haystack = re.sub(r"[_\-–—.]+", " ", haystack)
        groups = (
            ("final", ("спасибо", "thanks", "thank", "финальн", "заключительн",
                       "контакты", "closing", "qr", "谢谢", "感谢")),
            ("agenda", ("оглавлени", "содержани", "agenda", "table of content",
                        "toc", "outline", "навигаци", "目录", "议程")),
            ("section", ("раздел", "разделитель", "section", "divider",
                         "переход", "transition", "章节")),
            # «title slide»/«title only» — обложка; просто «title» намеренно не
            # берём, иначе «Title and Content» стал бы титульным макетом.
            # Для китайских шаблонов — только полная фраза «标题幻灯片» (титул),
            # иначе «标题和内容» (заголовок и содержимое) ушёл бы в обложки.
            ("title", ("титул", "обложк", "заставк", "cover", "intro",
                       "первый слайд", "title slide", "title only", "заглавный",
                       "标题幻灯片", "封面")),
            ("content", ("контент", "content", "заголовок", "текст", "пункт",
                         "bullet", "слайд с", "body", "text", "column", "list",
                         "内容", "文本", "列表")),
        )
        for role, keywords in groups:
            if any(keyword in haystack for keyword in keywords):
                return role
        return None

    def _classify_layout(self, name: str, master_name: str, phs: list,
                         shapes: dict, slide_w: float, slide_h: float) -> tuple[str, str, str]:
        """Роль и композиционный тип макета по его СТРУКТУРЕ, имя — tie-breaker.

        Порядок принятия решения:
          1) явные объекты данных (таблица/диаграмма) — они однозначны;
          2) семантическая роль из имени, но только по однозначным словам;
          3) структура: сколько контентных рамок, картинок, свободных текстов.

        Возвращает (role, kind, reason); reason сохраняется в профиле, чтобы
        решение можно было объяснить на защите.
        """
        def count(*types: str) -> int:
            return sum(1 for p in phs if p.type in types)

        # семантическая роль из имени — tie-breaker для спорных случаев
        named = self._named_role(name, master_name)

        titles = count("title", "center_title")
        bodies = count("body", "object")
        subtitle_phs = [p for p in phs if p.type == "subtitle"]
        body_subs = body_like_subtitles(phs, slide_w, slide_h, named)
        subs = len(subtitle_phs) - len(body_subs)
        pics = count("picture", "slide_image") + shapes.get("pictures", 0)
        tables = count("table") + shapes.get("tables", 0)
        charts = shapes.get("charts", 0)
        texts = shapes.get("texts", 0)
        content_boxes = bodies + texts + len(body_subs)
        body_hint = (f", тело-подзаголовков {len(body_subs)}" if body_subs else "")

        # 1. данные на макете однозначны
        if tables:
            return "content", "table", f"на макете таблица (объектов: {tables})"
        if charts:
            return "content", "chart", f"на макете диаграмма ({charts})"

        # 2. семантическая роль из имени: она сильнее структуры, когда макет
        #    пуст (в китайских шаблонах рамки живут на образце, а не на макете)
        if named == "final":
            return "final", "blank" if content_boxes == 0 else "bullets", "имя: финальный слайд"
        if named == "agenda":
            kind = "multi_column" if content_boxes >= 3 else "bullets"
            return "agenda", kind, f"имя: оглавление, блоков {content_boxes}"
        if named == "section":
            return "section", "blank" if content_boxes == 0 else "bullets", "имя: раздел"
        if named == "title":
            if content_boxes == 0 and pics == 0:
                return "title", "blank", "имя: титульный слайд, контента нет"
            if pics and content_boxes:
                return "title", "image_text", "имя: титульный слайд с картинкой и текстом"
            return "title", "bullets", f"имя: титульный слайд, блоков {content_boxes}"
        if named == "content":
            # макет назван контентным, но контентных рамок нет: вёрстка положит
            # блоки в область сетки профиля, поэтому тип — обычные буллеты
            if content_boxes == 0 and pics == 0:
                return "content", "bullets", "имя: контентный слайд, рамок нет (сетка профиля)"
            if pics and content_boxes == 0:
                return "content", "image", "имя: контентный слайд с картинкой"
            if pics:
                return ("content", "image_text",
                        f"имя: контентный слайд с картинкой и текстом{body_hint}")
            if content_boxes >= 2:
                return ("content", "multi_column",
                        f"имя: контентный, блоков {content_boxes}{body_hint}")
            return ("content", "bullets",
                    f"имя: контентный слайд, блоков {content_boxes}{body_hint}")

        # 3. структура
        if not titles and content_boxes == 0 and pics == 0:
            return "content", "blank", "пустой/декоративный макет без контентных рамок"
        if not titles:
            if content_boxes >= 2:
                return ("content", "multi_column",
                        f"нет заголовка, блоков {content_boxes}{body_hint}")
            return ("content", "image" if pics else "bullets",
                    f"нет заголовка, блоков {content_boxes}, картинок {pics}{body_hint}")

        if subs >= 1 and content_boxes == 0:
            # подзаголовок — конвенция обложек: он есть у титульного макета и
            # почти никогда у контентного (там сразу тело с тезисами)
            return ("title", "bullets" if bodies else "blank",
                    "подзаголовок на макете: титульный макет")
        if content_boxes == 0 and pics == 0:
            title_ph = next((p for p in phs if p.is_title), None)
            big = bool(title_ph and title_ph.h >= 0.10 * slide_h
                       and title_ph.w >= 0.35 * slide_w)
            if subs >= 1:
                # подзаголовок встречается только на обложках — структурный признак
                return "title", "blank", "заголовок + подзаголовок: титульный макет"
            if big:
                return "title", "blank", f"крупный заголовок ({title_ph.h:.2f}″) без контента"
            return "section", "blank", "заголовок без контента, имя неинформативно"
        if pics and content_boxes == 0:
            return "content", "image", "заголовок + картинка без текста"
        if pics and content_boxes:
            return ("content", "image_text",
                    f"заголовок + картинка + {content_boxes} блок(ов){body_hint}")
        if content_boxes >= 2:
            return ("content", "multi_column",
                    f"{content_boxes} блоков на макете{body_hint}")
        if subs and bodies:
            return "content", "bullets", "заголовок + подзаголовок + текст"
        return ("content", "bullets",
                f"заголовок + {content_boxes} текстовый блок(ов){body_hint}")

    # ---------------------------------------------------------------- main flow
    def parse(self) -> TemplateProfile:
        self._load()
        prof = TemplateProfile(
            template_id=self._slug(),
            source_file=posixpath.basename(self._name),
            slide_size={
                "w_in": Emu(self._prs.slide_width).inches,
                "h_in": Emu(self._prs.slide_height).inches,
                "w_emus": int(self._prs.slide_width),
                "h_emus": int(self._prs.slide_height),
            },
            slide_count=self.slide_count,
            layout_count=self.layout_count,
        )
        # 1. тема
        theme = {}
        for _, xml in self._theme_xmls():
            t = self._parse_theme(xml)
            if t["colors"] and not theme:
                theme = t
            elif t["colors"]:
                theme["colors"].update(t["colors"])
                if not theme.get("fonts"):
                    theme["fonts"] = t["fonts"]
        prof.theme_fonts, prof.theme_colors = theme.get("fonts", {}), theme.get("colors", {})
        # кэш цветов темы: нужен, чтобы разрешать schemeClr декора в HEX
        self._theme_colors_cache = prof.theme_colors

        # 2. соберём слайды один раз
        raw_slides = self._collect_slides()

        # 3. макеты с геометрией и ролью
        layouts = self._parse_layouts(raw_slides)
        prof.layouts = layouts

        # 4. токены дизайна из слайдов + макетов
        self._extract_tokens(prof, raw_slides, layouts)

        # 5. типографическая шкала
        prof.type_scale = self._type_scale(layouts, raw_slides)

        # 6. сетка
        prof.grid = self._grid(prof, layouts, raw_slides)

        # 7. группы макетов по ролям
        groups = defaultdict(list)
        for i, lp in enumerate(layouts):
            groups[lp.role].append(f"L{i}")
        prof.layout_groups = dict(groups)

        # 8. примеры паттернов
        prof.example_patterns = self._patterns(raw_slides)
        return prof

    def _slug(self) -> str:
        base = posixpath.basename(self._name)
        return re.sub(r"[^\w\-_.]+", "_", base)[:60]

    def _collect_slides(self) -> list[_RawSlide]:
        out = []
        for slide in self._prs.slides:
            try:
                out.append(self._raw_slide(slide))
            except Exception:
                continue
        return out

    def _raw_slide(self, slide) -> _RawSlide:
        stats, geos, fills, texts = [], [], [], []
        for sh in slide.shapes:
            geo = self._shape_geo(sh)
            kind = sh.shape_type
            fill = self._shape_fill_hex(sh)
            if fill:
                fills.append(fill)
            if sh.has_text_frame:
                st = self._text_style_stats(sh)
                if st:
                    stats.append(st)
                    texts.append({
                        "x": geo[0] if geo else None, "y": geo[1] if geo else None,
                        "w": geo[2] if geo else None, "h": geo[3] if geo else None,
                        "chars": st.get("chars", 0), "kind": str(kind),
                    })
            if geo:
                geos.append((geo[0], geo[1], geo[2], geo[3], str(kind), fill))
        return _RawSlide(layout=slide.slide_layout, text_stats=stats, shapes_geo=geos,
                         fills=fills, texts=texts)

    def _layout_ph_roles(self, layout) -> list[str]:
        roles = []
        for sh in layout.shapes:
            pi = self._placeholder_info(sh)
            if pi:
                roles.append(pi.type)
        return roles

    def _parse_layouts(self, raw_slides) -> list[LayoutProfile]:
        profs = []
        by_layout = defaultdict(list)
        for rs in raw_slides:
            by_layout[id(rs.layout)].append(rs)

        for idx, layout in enumerate(self._all_layouts()):
            try:
                phs = [self._placeholder_info(sh) for sh in layout.shapes]
                phs = [p for p in phs if p]
                title_ph = next((p.to_dict() for p in phs if p.is_title), None)
                body = None
                columns = []
                slide_w = Emu(self._prs.slide_width).inches
                slide_h = Emu(self._prs.slide_height).inches
                # тело-подзаголовки (Slidesgo и др.) — тоже реальная геометрия
                # контентной области, иначе рендер возьмёт сетку профиля
                body_candidates = [p for p in phs if p.type in ("body", "object")]
                body_candidates += body_like_subtitles(phs, slide_w, slide_h)
                if body_candidates:
                    largest = max(body_candidates, key=lambda p: p.w * p.h)
                    body = {"x": largest.x, "y": largest.y, "w": largest.w, "h": largest.h}
                    # при 2+ равношироких body-плейсхолдерах рядом — колонки
                    sorted_bodies = sorted(body_candidates, key=lambda p: p.x)
                    if len(sorted_bodies) >= 2:
                        col_boxes = []
                        prev = None
                        for p in sorted_bodies:
                            if prev is not None and abs(p.x - prev.x) > 0.05:
                                col_boxes.append({"x": p.x, "y": p.y, "w": p.w, "h": p.h})
                            prev = p
                        columns = sorted(col_boxes, key=lambda c: c["x"])
                shapes = self._layout_shape_kinds(layout, slide_w, slide_h)
                role, kind, reason = self._classify_layout(
                    layout.name, layout.slide_master.name, phs, shapes, slide_w, slide_h)
                branding = self._layout_branding(layout, slide_w, slide_h)
                decor = self._layout_decor(layout, layout.slide_master, slide_w, slide_h)
                background = self._background_color(layout, layout.slide_master)
                profs.append(LayoutProfile(
                    id=f"L{idx}",
                    master_id=f"M{self._master_index(layout.slide_master)}",
                    name=layout.name,
                    role=role,
                    kind=kind,
                    role_reason=reason,
                    score=self._layout_score(role, kind, title_ph, body, columns,
                                             slide_w * slide_h),
                    placeholders=[p.to_dict() for p in phs],
                    title_ph=title_ph,
                    body=body,
                    columns=columns,
                    branding=branding,
                    decor=decor,
                    background=background,
                    has_logo=any(item["type"] == "logo" for item in branding),
                    style_sample=self._layout_style_sample(layout, by_layout.get(id(layout), [])),
                ))
            except Exception:
                continue
        return profs

    def _layout_decor(self, layout, master, slide_w: float, slide_h: float) -> list[dict]:
        """Фоновые и декоративные фигуры макета и мастера вместе с заливками.

        Рендерер использует их, чтобы понять, на каком фоне окажется текст:
        на тёмной плашке шаблона тёмный текст не читается. Аудит — чтобы
        проверить контраст к фактическому фону, а не к белому.
        """
        out: list[dict] = []
        min_area = 0.005 * slide_w * slide_h  # отсекаем направляющие и линии
        for source, container in (("master", master), ("layout", layout)):
            for shape in self._walk_shapes(container):
                try:
                    if shape.is_placeholder:
                        continue
                    geo = self._shape_geo(shape)
                    if geo is None or geo[2] * geo[3] < min_area:
                        continue
                    is_picture = shape_code(shape) == MSO_SHAPE_TYPE.PICTURE.value
                    fill = self._shape_fill_hex(shape)
                    if fill and fill.startswith("scheme:"):
                        fill = self._resolve_scheme_name(fill)
                    if not fill and is_picture:
                        # у картинки заливки нет: берём доминирующий цвет изображения,
                        # иначе под тёмным фото текст останется нечитаемым
                        fill = self._picture_color(shape)
                    if not fill and not is_picture:
                        continue  # фигура без заливки фон не меняет
                    out.append({
                        "source": source, "name": shape.name,
                        "x": geo[0], "y": geo[1], "w": geo[2], "h": geo[3],
                        "fill": fill, "is_picture": is_picture,
                    })
                except Exception:  # noqa: BLE001
                    continue
        return out

    @staticmethod
    def _walk_shapes(container):
        """Фигуры с разворотом групп: декор VK лежит именно в группах."""
        try:
            shapes = list(container.shapes)
        except Exception:  # noqa: BLE001
            return
        for shape in shapes:
            try:
                is_group = shape_code(shape) == MSO_SHAPE_TYPE.GROUP.value
            except Exception:  # noqa: BLE001
                is_group = False
            if is_group:
                yield from TemplateParser._walk_shapes(shape)
            else:
                yield shape

    def _picture_color(self, shape) -> Optional[str]:
        """Доминирующий цвет картинки по НЕПРОЗРАЧНЫМ пикселям.

        Декор шаблонов часто экспортирован как PNG с прозрачным фоном: если
        скомпоновать его на чёрном (как делает convert("RGB")), доминирующим
        окажется чёрный, и текст на светлом слайде станет белым.

        Результат кэшируется по хэшу изображения: одни и те же картинки
        повторяются в десятках макетов, а разбор 39 макетов с картинками
        занимал почти 30 секунд.
        """
        try:
            return self._blob_color(shape.image.blob)
        except Exception:  # noqa: BLE001
            return None

    def _blob_color(self, blob: bytes) -> Optional[str]:
        """Доминирующий цвет картинки по непрозрачным пикселям (с кэшем по хэшу)."""
        try:
            from hashlib import sha1

            from ..render.images import dominant_color

            key = sha1(blob).hexdigest()
            if key in self._picture_colors:
                return self._picture_colors[key]
            color = None
            with Image.open(io.BytesIO(blob)) as img:
                rgba = img.convert("RGBA")
                # уменьшаем до 256 px: перебор пикселей полноразмерной картинки
                # (миллионы точек) занимал секунды на каждый макет
                if max(rgba.size) > 256:
                    ratio = 256 / max(rgba.size)
                    rgba = rgba.resize(
                        (max(1, round(rgba.width * ratio)),
                         max(1, round(rgba.height * ratio))), Image.NEAREST)
                pixels = [(r, g, b) for r, g, b, alpha in rgba.getdata() if alpha > 200]
                if pixels:
                    opaque = Image.new("RGB", (len(pixels), 1))
                    opaque.putdata(pixels)
                    color = dominant_color(opaque)
            self._picture_colors[key] = color
            return color
        except Exception:  # noqa: BLE001
            return None

    def _background_color(self, layout, master) -> Optional[str]:
        """Фактический фон макета: сначала p:bg макета, затем мастера.

        Google-Slides-экспорт задаёт фон не фигурой, а свойством `p:bg`
        (у VK WorkSpace это `#000000` на каждом макете). Без чтения `p:bg`
        рендер и аудит считали фоном светлую палитру темы и выбирали тёмный
        текст на чёрном слайде.
        """
        for part in (layout, master):
            try:
                color = self._bg_element_color(part._element, part.part)
            except Exception:  # noqa: BLE001
                continue
            if color:
                return color
        return None

    def _bg_element_color(self, element, part) -> Optional[str]:
        """Цвет из p:bg: solidFill (srgb/scheme) или доминирующий цвет картинки."""
        for bg in element.iter(qn("p:bg")):
            for srgb in bg.iter(qn("a:srgbClr")):
                return "#" + (srgb.get("val") or "").upper()
            for scheme in bg.iter(qn("a:schemeClr")):
                resolved = self._resolve_scheme_name("scheme:" + (scheme.get("val") or ""))
                if resolved:
                    return resolved
            for blip in bg.iter(qn("a:blip")):
                rid = blip.get(qn("r:embed"))
                if not rid:
                    continue
                try:
                    blob = part.rels[rid].target_part.blob
                except Exception:  # noqa: BLE001
                    return None
                return self._blob_color(blob)
        return None

    def _resolve_scheme_name(self, value: str) -> Optional[str]:
        """«scheme:accent1» → HEX из темы (тема читается первой)."""
        name = value.split(":", 1)[1] if ":" in value else value
        return self._theme_colors_cache.get(name) if hasattr(self, "_theme_colors_cache") else None

    def _layout_branding(self, layout, slide_w: float, slide_h: float) -> list[dict]:
        """Фирменные элементы макета и их координаты: колонтитулы, номер, логотип.

        Нужны аудиту для проверки `branding_shifted`: если элемент уехал с места,
        это заметно по расхождению с профилем.
        """
        out: list[dict] = []
        slide_area = max(1e-6, slide_w * slide_h)
        for shape in layout.shapes:
            try:
                geo = self._shape_geo(shape)
                if geo is None:
                    continue
                if shape.is_placeholder:
                    info = self._placeholder_info(shape)
                    if info and info.type in BRANDING_TYPES:
                        out.append({"name": shape.name, "type": info.type,
                                    "x": geo[0], "y": geo[1], "w": geo[2], "h": geo[3]})
                    continue
                if shape_code(shape) == MSO_SHAPE_TYPE.PICTURE.value:
                    if geo[2] * geo[3] <= 0.04 * slide_area:
                        out.append({"name": shape.name, "type": "logo",
                                    "x": geo[0], "y": geo[1], "w": geo[2], "h": geo[3]})
            except Exception:  # noqa: BLE001
                continue
        return out

    @staticmethod
    def _layout_score(role: str, kind: str, title_ph, body, columns,
                      slide_area: float = 0.0) -> float:
        """Пригодность макета под автоматическую вёрстку (0..1).

        Чем больше контентная область макета, тем выше оценка: иначе вёрстка
        выбирала бы «визиточные» макеты с крошечным телом и сжимала контент
        в узкую полосу, оставляя слайд почти пустым.
        """
        score = 0.35
        if title_ph:
            score += 0.2
        if body:
            score += 0.25
            if slide_area > 0:
                area_ratio = (body.get("w", 0) * body.get("h", 0)) / slide_area
                score += min(0.15, max(0.0, area_ratio) * 0.3)
        if columns:
            score += 0.05
        if role == "content":
            score += 0.1
        if kind in ("bullets", "multi_column"):
            score += 0.05
        if kind == "blank":
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 3)

    def _master_index(self, master) -> int:
        try:
            return list(self._prs.slide_masters).index(master)
        except Exception:
            return 0

    def _layout_style_sample(self, layout, examples: list[_RawSlide]) -> dict:
        """Усреднённые стили текста на реальных слайдах этого макета."""
        fonts, sizes, colors = Counter(), Counter(), Counter()
        for rs in examples:
            for st in rs.text_stats:
                if st.get("font"):
                    fonts[st["font"]] += 1
                if st.get("size"):
                    sizes[st["size"]] += 1
                if st.get("color"):
                    colors[st["color"]] += 1
        return {
            "font": fonts.most_common(1)[0][0] if fonts else None,
            "sizes": [s for s, _ in sizes.most_common(4)] if sizes else [],
            "colors": [c for c, _ in colors.most_common(4)] if colors else [],
        }

    def _resolve_scheme(self, prof: TemplateProfile, h: str) -> Optional[str]:
        if h and h.startswith("scheme:"):
            name = h.split(":", 1)[1]
            return prof.theme_colors.get(name)
        if h and h.startswith("#"):
            return h
        return None

    def _extract_tokens(self, prof: TemplateProfile, raw_slides, layouts=None) -> None:
        fills = Counter()
        for rs in raw_slides:
            for f in rs.fills:
                fills[f] += 1
        # заливки макетов тоже
        for layout in prof.layouts:
            pass  # заливки макетов уже включены в слайды
        # текстовые цвета из статистики
        text_colors_c = Counter()
        for rs in raw_slides:
            for st in rs.text_stats:
                if st.get("color"):
                    text_colors_c[st["color"]] += 1
        # собираем палитру (сначала из темы)
        tokens: list[ColorToken] = []
        seen = set()
        for name, hexv in prof.theme_colors.items():
            if hexv and hexv not in seen:
                seen.add(hexv)
                tokens.append(ColorToken(hex=hexv, count=50 + len(("".join(prof.theme_colors))),
                                         source="theme"))
        for h, n in fills.most_common(30):
            hexv = self._resolve_scheme(prof, h)
            if hexv and hexv not in seen:
                seen.add(hexv)
                tokens.append(ColorToken(hex=hexv, count=n, source="shape"))
        for h, n in text_colors_c.most_common(15):
            hexv = self._resolve_scheme(prof, h)
            if hexv and hexv not in seen:
                seen.add(hexv)
                tokens.append(ColorToken(hex=hexv, count=max(1, n // 3), source="text"))
        tokens.sort(key=lambda t: t.count, reverse=True)
        prof.palette = tokens[:24]
        prof.text_colors = list(dict.fromkeys(
            self._resolve_scheme(prof, h) for h, _ in text_colors_c.most_common(12)
            if self._resolve_scheme(prof, h)
        ))

        # шрифты
        major_latin = prof.theme_fonts.get("major", {}).get("latin")
        minor_latin = prof.theme_fonts.get("minor", {}).get("latin")

        def _resolve_font(fname: Optional[str], bold: bool) -> Optional[str]:
            """Плейсхолдерные имена темы заменяем на реальные гарнитуры."""
            if not fname:
                return None
            if fname.startswith("+mj"):
                return major_latin
            if fname.startswith("+mn"):
                # заголовки наследуют major, но в обычных рантаймах +mn-lt = minor
                return minor_latin
            return fname

        font_tokens: dict[tuple, int] = defaultdict(int)
        for rs in raw_slides:
            for st in rs.text_stats:
                fname, bold = st.get("font"), st.get("bold")
                real = _resolve_font(fname, bool(bold))
                if real:
                    font_tokens[(real, bool(bold))] += 1
        for layout in layouts:
            sample_font = layout.style_sample.get("font")
            real = _resolve_font(sample_font, False)
            if real:
                font_tokens[(real, False)] += 1
        prof.fonts = [FontToken(name=n, bold=b, count=c)
                      for (n, b), c in sorted(font_tokens.items(), key=lambda kv: -kv[1])[:12]]
        # заголовочный и текстовый шрифт
        theme_fonts = prof.theme_fonts
        prof.headline_font = self._pick_font(prof.fonts, bold=True, theme_major=True, theme_fonts=theme_fonts)
        prof.body_font = self._pick_font(prof.fonts, bold=False, theme_major=False, theme_fonts=theme_fonts)
        if prof.headline_font is None and prof.fonts:
            prof.headline_font = prof.fonts[0].name
        if prof.body_font is None and prof.fonts:
            prof.body_font = prof.fonts[0].name

    def _pick_font(self, fonts, bold, theme_major, theme_fonts) -> Optional[str]:
        target = theme_fonts.get("major" if theme_major else "minor", {}).get("latin")
        cands = [f for f in fonts if f.bold == bold]
        for f in cands:
            if target and f.name.find(target.split()[0]) >= 0 and len(target) > 1:
                return f.name
        if cands:
            return max(cands, key=lambda f: f.count).name
        return (target if target else None) or (cands[0].name if cands else None)

    def _type_scale(self, layouts, raw_slides) -> dict:
        title_sizes, body_sizes = Counter(), Counter()
        for layout in layouts:
            if layout.title_ph and layout.title_ph.get("size"):
                title_sizes[float(layout.title_ph["size"])] += 3
            for ph in layout.placeholders:
                if ph.get("is_title") and ph.get("size"):
                    title_sizes[float(ph["size"])] += 3
                elif ph.get("size"):
                    if float(ph["size"]) <= 60:
                        body_sizes[float(ph["size"])] += 1
        for rs in raw_slides:
            texts = sorted(rs.text_stats, key=lambda st: st.get("size") or 0, reverse=True)
            if texts and texts[0].get("size"):
                title_sizes[float(texts[0]["size"])] += 2
            for st in rs.text_stats:
                if st.get("size"):
                    sz = float(st["size"])
                    if st.get("chars", 0) > 6 and sz > 20:
                        title_sizes[sz] += 1
                    elif sz <= 60:
                        body_sizes[sz] += 1
        def _clean(counter: Counter, lo: float, hi: float) -> list[float]:
            cand = [sz for sz, _ in counter.most_common(30) if lo <= sz <= hi]
            if not cand:
                cand = [sz for sz, _ in counter.most_common(30)]
            med = median([sz for sz, c in counter.items() for _ in range(min(c, 5))]) if counter else hi
            cand = [sz for sz in cand if abs(sz - med) <= max(med * 2.2, 40)]
            return sorted(set(cand), reverse=True)[:8]
        order_t = _clean(title_sizes, 14, 80)
        order_b = _clean(body_sizes, 8, 44)
        return {"title": order_t,
                "body": order_b,
                "min_body": order_b[-1] if order_b else 12,
                "max_title": order_t[0] if order_t else 40}

    def _grid(self, prof, layouts, raw_slides=None) -> dict:
        w, h = prof.slide_size["w_in"], prof.slide_size["h_in"]
        # 1) если есть полноширинный body-плейсхолдер — используем его как канву
        big = [l.body for l in layouts
               if l.role == "content" and l.body
               and l.body.get("w", 0) >= 0.45 * w and l.body.get("h", 0) >= 0.3 * h]
        if big:
            b = max(big, key=lambda b: b["w"] * b["h"])
            return {"margin_left": round(b["x"], 3), "margin_top": round(b["y"], 3),
                    "margin_right": round(w - (b["x"] + b["w"]), 3),
                    "margin_bottom": round(h - (b["y"] + b["h"]), 3),
                    "body_x": round(b["x"], 3), "body_y": round(b["y"], 3),
                    "body_w": round(b["w"], 3), "body_h": round(b["h"], 3)}
        # 2) иначе типовые поля по границам контента реальных слайдов
        lefts, tops, rights, bottoms = [], [], [], []
        for rs in (raw_slides or []):
            for (x, y, bw, bh, kind, fill) in rs.shapes_geo:
                if kind and kind in ("13", "18"):  # picture/placeholder — может быть фон
                    if bh >= 0.8 * h and bw >= 0.8 * w:
                        continue
                if 0 <= x < w and 0 <= y < h and bw > 0.05 and bh > 0.03:
                    lefts.append(x); tops.append(y)
                    rights.append(x + bw); bottoms.append(y + bh)
        if not rights:
            return {"margin_left": 0.5, "margin_top": 1.0,
                    "margin_right": 0.5, "margin_bottom": 0.5,
                    "body_x": 0.5, "body_y": 1.0, "body_w": round(w - 1, 3), "body_h": round(h - 1.5, 3)}
        ml = round(min(1.2, max(0.2, median(lefts))), 3)
        mt = round(min(1.6, max(0.5, median([t for t in tops if t <= 0.75 * h]) if tops else 0.6)), 3)
        mr = round(min(1.2, max(0.2, w - median(rights))), 3)
        mb = round(min(1.4, max(0.3, h - median(bottoms))), 3)
        return {"margin_left": ml, "margin_top": mt,
                "margin_right": mr, "margin_bottom": mb,
                "body_x": ml, "body_y": mt,
                "body_w": round(w - ml - mr, 3), "body_h": round(h - mt - mb, 3)}

    def _patterns(self, raw_slides) -> list[dict]:
        """Детект карточных/колоночных паттернов по текстовым боксам на слайдах."""
        patterns = []
        for rs in raw_slides[:60]:
            boxes = rs.texts
            rows = defaultdict(list)
            for b in boxes:
                if b.get("y") is None or b.get("h") is None or b.get("w") is None:
                    continue
                key = round(b["y"] / 0.35)
                rows[key].append(b)
            for key, grp in rows.items():
                if len(grp) >= 3:
                    same_h = len({round(b["h"], 2) for b in grp}) == 1
                    same_w = len({round(b["w"], 2) for b in grp}) == 1
                    xs = sorted(b["x"] for b in grp)
                    if same_h and same_w and len(xs) >= 3:
                        step = (xs[-1] - xs[0]) / (len(xs) - 1)
                        if abs(step) > 0.2 and len({round(b["chars"] / 30) for b in grp if b["chars"]}) <= 3:
                            patterns.append({
                                "kind": "card_grid",
                                "cells": len(grp),
                                "cell_w": round(grp[0]["w"], 3),
                                "cell_h": round(grp[0]["h"], 3),
                                "step": round(step, 3),
                            })
                            break
            if len(patterns) > 8:
                break
        return patterns[1:8]