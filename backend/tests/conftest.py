"""Общие фикстуры тестов backend.

Шаблоны VK и контент-пакет в git не хранятся (docs/DECISIONS.md ADR-002), поэтому
базовые фикстуры — синтетические PPTX, генерируемые `tools/make_fixtures.py`.
Реальные шаблоны подхватываются автоматически, если лежат в корне или в
`data/templates/`; при их отсутствии такие тесты скипаются.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

# Тесты не должны ждать Ollama: недоступный адрес даёт мгновенный отказ,
# а сервис обязан деградировать к офлайн-планировщику (ADR-005).
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:1")
# Тесты герметичны: внешний шлюз (если он включён в локальном .env) не должен
# получать запросы с реальным ключом. Тесты провайдера задают окружение сами
# и подменяют HTTP-вызовы.
os.environ.setdefault("LLM_PROVIDER", "ollama")
# VLM-провайдер тоже не берём из локального .env: тесты должны быть одинаковыми
# на машине разработчика и в CI
os.environ.setdefault("VLM_PROVIDER", "ollama")
# и общий выключатель моделей: в CI он может стоять, тестам провайдеров нужен false
os.environ.setdefault("DISABLE_LLM", "false")

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for path in (str(BACKEND), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

TEMPLATE_FILES = {
    "lct": "ЛЦТ2026 Шаблон презентации.pptx",
    "vktech": "VK Tech шаблон.pptx",
    "workspace": "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "edu": "Шаблон презентации VK Education.pptx",
}


def _find(name: str) -> Path | None:
    for directory in (ROOT, ROOT / "data" / "templates"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


# --------------------------------------------------------------- шаблоны
@pytest.fixture(scope="session")
def synthetic_template() -> bytes:
    """Синтетический шаблон 16:9 со своими палитрой и шрифтами (в git)."""
    from tools.make_fixtures import FIXTURES, build_template

    return build_template(**FIXTURES["synthetic_16x9"])


@pytest.fixture(scope="session")
def synthetic_template_4x3() -> bytes:
    from tools.make_fixtures import FIXTURES, build_template

    return build_template(**FIXTURES["synthetic_4x3"])


@pytest.fixture(scope="session")
def unfamiliar_template(synthetic_template: bytes) -> bytes:
    """«Незнакомый шаблон»: имена макетов Slide N, чужие цвета и шрифты."""
    from tools.make_fixtures import mutate_template

    return mutate_template(synthetic_template)


@pytest.fixture(scope="session")
def template_any() -> bytes:
    for key in ("lct", "vktech", "workspace", "edu"):
        path = _find(TEMPLATE_FILES[key])
        if path is not None:
            return path.read_bytes()
    pytest.skip("шаблоны VK не загружены (см. docs/README.md §3)")


@pytest.fixture(scope="session")
def template_lct() -> bytes:
    path = _find(TEMPLATE_FILES["lct"])
    if path is None:
        pytest.skip("шаблон ЛЦТ2026 не загружен (см. docs/README.md §3)")
    return path.read_bytes()


@pytest.fixture(scope="session")
def all_templates() -> list[tuple[str, bytes]]:
    """Все доступные шаблоны: синтетические + реальные, если загружены."""
    from tools.make_fixtures import FIXTURES, build_template

    items = [(name, build_template(**params)) for name, params in FIXTURES.items()]
    for key, filename in TEMPLATE_FILES.items():
        path = _find(filename)
        if path is not None:
            items.append((key, path.read_bytes()))
    return items


# --------------------------------------------------------------- помощники
@pytest.fixture(scope="session")
def profile_of():
    """bytes шаблона → dict-профиль дизайн-системы (с кэшем на сессию).

    Ключ — sha256 содержимого, а не id(bytes): идентификаторы объектов Python
    переиспользуются после сборки мусора, и тест мог получить профиль чужого
    шаблона (плавающий сбой).
    """
    import hashlib

    from app.template.parser import TemplateParser

    cache: dict[str, dict] = {}

    def build(template_bytes: bytes) -> dict:
        key = hashlib.sha256(template_bytes).hexdigest()
        if key not in cache:
            cache[key] = TemplateParser(template_bytes).parse().to_dict()
        return cache[key]

    return build


@pytest.fixture(scope="session")
def render_variants():
    """(profile, deck, template_bytes) → {вариант: bytes PPTX} для трёх вариантов."""
    import io

    from app.layout.engine import DesignContext
    from app.render.pptx_renderer import Renderer

    def run(profile: dict, deck, template_bytes: bytes,
            images: dict[str, bytes] | None = None) -> dict[str, bytes]:
        dc = DesignContext.from_profile(profile)
        out: dict[str, bytes] = {}
        for variant in ("compact", "cards", "split"):
            renderer = Renderer(profile, variant=variant, template_bytes=template_bytes,
                                images=images)
            out[variant] = renderer.render_deck(deck, dc)
        return out

    return run


@pytest.fixture(scope="session")
def tiny_png() -> bytes:
    """PNG 40×20 (2:1) — для проверки сохранения пропорций картинки."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 90)).save(buf, format="PNG")
    return buf.getvalue()
