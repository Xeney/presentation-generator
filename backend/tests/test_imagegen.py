"""Тесты генерации иллюстраций: слот, fallback, бюджет, лицензии.

Провайдер не вызывается: подменяется генератор. Проверяется логика пайплайна —
какие блоки получают картинку, что происходит при недоступности стадии и как
работает бюджет.
"""
from __future__ import annotations

import pytest

from app.imagegen import ImageGenError, generate_images_for_deck
from app.models.deck import Block, Deck, Slide, SlideType

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


class _FakeGenerator:
    def __init__(self, *, available: bool = True, payload: bytes | None = PNG,
                 fail: bool = False):
        self._available = available
        self._payload = payload
        self._fail = fail
        self.calls: list[str] = []

    def available(self) -> bool:
        return self._available

    def unavailable_reason(self) -> str:
        return "выключено в тесте"

    def generate(self, prompt: str, *, size: str = "") -> bytes | None:
        self.calls.append(prompt)
        if self._fail:
            raise ImageGenError("провайдер недоступен")
        return self._payload


@pytest.fixture
def deck() -> Deck:
    return Deck(title="Иллюстрации", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        Slide(slide_type=SlideType.CONTENT, heading="Схема роста показателей",
              blocks=[Block(kind="image", image_prompt="схема роста, плоский стиль"),
                      Block(kind="bullets", items=["Тезис"])]),
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации"),
    ])


def test_image_prompt_becomes_generated_image(deck):
    generator = _FakeGenerator()
    registry, report = generate_images_for_deck(deck, {}, generator=generator)
    block = deck.slides[1].blocks[0]
    assert block.image_ref and block.image_ref in registry
    assert registry[block.image_ref] == PNG
    assert block.image_prompt is None, "промпт снимается после генерации"
    assert report and report[0]["status"] == "generated"
    assert generator.calls == ["схема роста, плоский стиль"]


def test_disabled_stage_leaves_slot(deck):
    """Стадия выключена — колода не меняется, слот остаётся плейсхолдером."""
    generator = _FakeGenerator(available=False)
    registry, report = generate_images_for_deck(deck, {}, generator=generator)
    assert registry == {}
    assert report == []
    assert deck.slides[1].blocks[0].image_ref is None
    assert deck.slides[1].blocks[0].image_prompt, "промпт остаётся для отчёта"


def test_provider_failure_does_not_break_deck(deck):
    """Провайдер упал — задание не падает, причина попадает в отчёт."""
    generator = _FakeGenerator(fail=True)
    registry, report = generate_images_for_deck(deck, {}, generator=generator)
    assert registry == {}
    assert report[0]["status"] == "failed"
    assert "недоступен" in report[0]["reason"]


def test_corpus_image_has_priority(deck):
    """Картинка из контент-пакета важнее генерации: промпт не тратится."""
    generator = _FakeGenerator()
    deck.slides[1].blocks[0].image_ref = "s7_img1.png"
    registry, report = generate_images_for_deck(
        deck, {"s7_img1.png": PNG}, generator=generator)
    assert generator.calls == []
    assert report == []


def test_budget_limits_generation(monkeypatch, deck):
    from app import imagegen as module

    class _Settings:
        image_max_per_deck = 1
        image_size = "1280x720"

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    extra = Slide(slide_type=SlideType.CONTENT, heading="Вторая схема",
                  blocks=[Block(kind="image", image_prompt="вторая схема")])
    deck.slides.insert(2, extra)
    generator = _FakeGenerator()
    registry, report = generate_images_for_deck(deck, {}, generator=generator)
    assert len(generator.calls) == 1
    assert any(item["status"] == "skipped" for item in report)


def test_no_prompt_leaves_slot(deck):
    """Блок image без промпта и без ключа — просто слот, генерации нет."""
    deck.slides[1].blocks[0] = Block(kind="image")
    generator = _FakeGenerator()
    registry, report = generate_images_for_deck(deck, {}, generator=generator)
    assert generator.calls == []
    assert registry == {}
