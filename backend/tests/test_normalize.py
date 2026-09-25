"""Тесты нормализации колоды: структуру чинит код, а не повторный запрос к LLM.

Реальные сбои Qwen3.5 на большом промпте: первый слайд не титульный, 16 слайдов
вместо 15, семь буллетов, неизвестный тип блока.
"""
from __future__ import annotations

from app.models.deck import Deck
from app.planner.normalize import normalize_deck


def _deck(**overrides) -> dict:
    data = {
        "title": "Платформа аналитики",
        "language": "ru",
        "slides": [
            {"slide_type": "title", "heading": "Платформа ускорила отчёты"},
            {"slide_type": "content", "heading": "Эффект квартала",
             "blocks": [{"kind": "bullets", "items": ["Минус 40% времени"]}]},
            {"slide_type": "final", "heading": "Следующие шаги"},
        ],
    }
    data.update(overrides)
    return data


def test_valid_deck_is_untouched():
    data, fixes = normalize_deck(_deck())
    assert fixes == []
    assert Deck.model_validate(data)


def test_content_first_becomes_title():
    data = _deck(slides=[
        {"slide_type": "content", "heading": "Сразу к делу",
         "blocks": [{"kind": "bullets", "items": ["Тезис"]}]},
        {"slide_type": "final", "heading": "Финал презентации"},
        {"slide_type": "content", "heading": "Ещё слайд",
         "blocks": [{"kind": "bullets", "items": ["Тезис"]}]},
    ])
    data, fixes = normalize_deck(data)
    assert data["slides"][0]["slide_type"] == "title"
    assert any("титульный" in fix for fix in fixes)
    assert Deck.model_validate(data)


def test_existing_title_is_moved_to_front():
    data = _deck(slides=[
        {"slide_type": "content", "heading": "Контент",
         "blocks": [{"kind": "bullets", "items": ["Тезис"]}]},
        {"slide_type": "title", "heading": "Настоящий титул"},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    assert data["slides"][0]["heading"] == "Настоящий титул"
    assert any("перенесён" in fix for fix in fixes)


def test_too_many_slides_are_trimmed():
    slides = [{"slide_type": "title", "heading": "Титул презентации"}]
    slides += [{"slide_type": "content", "heading": f"Слайд {i}",
                "blocks": [{"kind": "bullets", "items": ["Тезис"]}]}
               for i in range(20)]
    data, fixes = normalize_deck(_deck(slides=slides))
    assert len(data["slides"]) == 15
    assert any("обрезана" in fix for fix in fixes)
    assert Deck.model_validate(data)


def test_too_many_bullets_are_capped():
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "Много тезисов",
         "blocks": [{"kind": "bullets", "items": [f"Тезис {i}" for i in range(9)]}]},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    assert len(data["slides"][1]["blocks"][0]["items"]) == 6
    assert any("буллетов" in fix for fix in fixes)
    assert Deck.model_validate(data)


def test_unknown_block_kind_is_relabelled():
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "Странный блок",
         "blocks": [{"kind": "kpi_cards", "items": ["Рост 25%"]},
                    {"kind": "неизвестно"}]},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    blocks = data["slides"][1]["blocks"]
    assert blocks[0]["kind"] == "bullets"
    assert len(blocks) == 1, "блок без данных удаляется"
    assert Deck.model_validate(data)


def test_missing_title_and_language_are_filled():
    data = _deck(title="", language="kz")
    data, fixes = normalize_deck(data)
    assert data["title"] == "Платформа ускорила отчёты"
    assert data["language"] == "ru"
    assert len(fixes) == 2


def test_short_heading_is_replaced():
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "x"},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    assert data["slides"][1]["heading"] == "Слайд 2"
    assert any("подставлен заголовок" in fix for fix in fixes)


def test_empty_block_is_dropped():
    """Модель назвала тип, но данных не дала — иначе получится пустой слайд."""
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "Пустой блок",
         "blocks": [{"kind": "quote"}, {"kind": "bullets", "items": ["Тезис"]}]},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    assert [block["kind"] for block in data["slides"][1]["blocks"]] == ["bullets"]
    assert any("пустой блок" in fix for fix in fixes)


def test_duplicate_items_are_collapsed():
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "Синергия метрик",
         "blocks": [{"kind": "bullets", "items": [
             "Рост выручки на 25%", "рост выручки на 25%!",
             "Охват — 5 отделов", "Рост выручки на 25%"][:4]}]},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    items = data["slides"][1]["blocks"][0]["items"]
    assert items == ["Рост выручки на 25%", "Охват — 5 отделов"]
    assert any("дубликаты пунктов" in fix for fix in fixes)


def test_long_text_becomes_bullets():
    long_text = ("Платформа сократила время отчётов на 40%. "
                 "Автоматизированы 12 рутинных задач. Охват вырос до 5 подразделений. "
                 "План — подключить 10 отделов к концу года и внедрить предсказания "
                 "выручки, чтобы снизить стоимость отчётности ещё на четверть "
                 "и высвободить время аналитиков для задач развития.")
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "Длинный абзац",
         "blocks": [{"kind": "text", "text": long_text}]},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    block = data["slides"][1]["blocks"][0]
    assert block["kind"] == "bullets"
    assert block["text"] is None
    assert len(block["items"]) >= 2
    assert any("длинный абзац разбит" in fix for fix in fixes)


def test_subheading_on_content_slide_becomes_block():
    """Реальный сбой: Qwen3.5 положила текст слайда в subheading, blocks пусты.

    subheading рисуется только на титуле и разделе, поэтому контент терялся —
    слайд выходил с одним заголовком. Нормализация переносит текст в блок.
    """
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации",
         "subheading": "Короткий подзаголовок обложки"},
        {"slide_type": "content", "heading": "Эффект квартала",
         "subheading": "Платформа сократила время подготовки отчётов на 40%",
         "blocks": []},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    slide = data["slides"][1]
    assert slide["blocks"], "текст из subheading обязан стать блоком"
    assert slide["subheading"] is None
    assert "40%" in (slide["blocks"][0].get("text") or "")
    assert data["slides"][0]["subheading"] == "Короткий подзаголовок обложки", \
        "подзаголовок титула не трогаем"
    assert any("вне схемы" in fix for fix in fixes)
    assert Deck.model_validate(data)


def test_stray_fields_are_moved_to_blocks():
    """Поля вне схемы (body/content/items на уровне слайда) не теряются."""
    data = _deck(slides=[
        {"slide_type": "title", "heading": "Титул презентации"},
        {"slide_type": "content", "heading": "Метрики", "body": "Охват вырос до 5 отделов",
         "content": ["Отчёты быстрее на 40%", "12 задач автоматизированы"]},
        {"slide_type": "final", "heading": "Финал презентации"},
    ])
    data, fixes = normalize_deck(data)
    slide = data["slides"][1]
    assert "body" not in slide and "content" not in slide
    items = slide["blocks"][0].get("items") or [slide["blocks"][0].get("text")]
    assert any("5 отделов" in str(i) for i in items)
    assert any("вне схемы" in fix for fix in fixes)
    assert Deck.model_validate(data)


def test_garbage_input_is_returned_unchanged():
    for payload in (None, "строка", 42, {}, {"slides": []}, {"slides": "нет"}):
        data, fixes = normalize_deck(payload)
        assert fixes == []
