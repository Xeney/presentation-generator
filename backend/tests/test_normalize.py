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


def test_garbage_input_is_returned_unchanged():
    for payload in (None, "строка", 42, {}, {"slides": []}, {"slides": "нет"}):
        data, fixes = normalize_deck(payload)
        assert fixes == []
