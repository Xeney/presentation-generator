from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

import sys  # noqa: E402

sys.path.insert(0, str(BACKEND))

TEMPLATE_FILES = {
    "lct": ROOT / "ЛЦТ2026 Шаблон презентации.pptx",
    "vktech": ROOT / "VK Tech шаблон.pptx",
    "workspace": ROOT / "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "edu": ROOT / "Шаблон презентации VK Education.pptx",
}


@pytest.fixture(scope="session")
def template_lct() -> bytes:
    return TEMPLATE_FILES["lct"].read_bytes()


@pytest.fixture(scope="session")
def template_any() -> bytes:
    for name in ("vktech", "workspace", "lct", "edu"):
        p = TEMPLATE_FILES[name]
        if p.exists():
            return p.read_bytes()
    pytest.skip("шаблоны VK не загружены в репозиторий")