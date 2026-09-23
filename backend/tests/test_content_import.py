"""Тесты импорта контент-пакетов: PPTX, DOCX, TXT и изображения."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.content.corpus import ContentCorpus, list_corpora
from app.content.importer import ContentImportError, import_content_pack

TEXT_PACK = """# Запуск платформы аналитики
- Сократили время подготовки отчётов на 40%
- Автоматизировали 12 рутинных задач
- Охватили 5 подразделений

## Эффект
Платформой пользуются 2000 сотрудников еженедельно.
План: подключить 10 отделов к концу года.

| Метрика | До | После |
| --- | --- | --- |
| Время отчёта | 8 ч | 3 ч |
| Ошибки | 15% | 4% |
"""


def test_import_text_pack_sections_and_numbers():
    corpus = import_content_pack(TEXT_PACK.encode("utf-8"), "pack.md")
    assert corpus.kind == "text"
    assert corpus.id
    headings = [s.heading for s in corpus.non_empty_slides()]
    assert headings[0].startswith("Запуск платформы")
    first = corpus.non_empty_slides()[0]
    assert len(first.bullets) == 3
    assert any("40%" in n for n in first.numbers), first.numbers
    table = next((t for s in corpus.slides for t in s.tables), None)
    assert table is not None and table.header == ["Метрика", "До", "После"]
    assert table.rows[0] == ["Время отчёта", "8 ч", "3 ч"]
    assert "40%" in corpus.text()


def test_import_pptx_pack_extracts_slides_text(synthetic_template):
    corpus = import_content_pack(synthetic_template, "synthetic.pptx")
    assert corpus.kind == "pptx"
    assert len(corpus.non_empty_slides()) >= 2
    joined = corpus.text()
    assert "Название презентации" in joined
    assert "Первый тезис" in joined
    assert "## Слайд" in joined


def test_import_pptx_extracts_images(synthetic_template, tiny_png):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation(io.BytesIO(synthetic_template))
    prs.slides[0].shapes.add_picture(io.BytesIO(tiny_png), Inches(1), Inches(1),
                                     width=Inches(2), height=Inches(1))
    buf = io.BytesIO()
    prs.save(buf)

    corpus = import_content_pack(buf.getvalue(), "with_image.pptx")
    assert corpus.images, "картинка должна быть извлечена из PPTX"
    key = next(iter(corpus.images))
    assert corpus.images[key].startswith(b"\x89PNG")
    assert key in corpus.image_labels
    assert any(key in slide.images for slide in corpus.slides)
    assert key in corpus.image_prompt_block()


def test_import_docx_pack():
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Рынок доставки</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>Рост рынка 25% в год</w:t></w:r></w:p>
    <w:p><w:r><w:t>Ключевой вывод: лидера нет.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>План</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>Выйти в 10 городов</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("[Content_Types].xml", "<Types/>")

    corpus = import_content_pack(buf.getvalue(), "brief.docx")
    assert corpus.kind == "docx"
    slides = corpus.non_empty_slides()
    assert [s.heading for s in slides] == ["Рынок доставки", "План"]
    assert slides[0].bullets == ["Рост рынка 25% в год"]
    assert any("25%" in n for n in corpus.all_numbers())


@pytest.mark.parametrize("payload,name", [
    (b"", "empty.pptx"),
    (b"not a pptx", "broken.pptx"),
    (b"hello", "unknown.xyz"),
    (b"   ", "empty.txt"),
])
def test_import_rejects_bad_input(payload, name):
    with pytest.raises(ContentImportError):
        import_content_pack(payload, name)


def test_corpus_roundtrip_on_disk(tmp_path):
    corpus = import_content_pack(TEXT_PACK.encode("utf-8"), "pack.md")
    corpus.save(tmp_path)
    loaded = ContentCorpus.load(corpus.id, tmp_path)
    assert loaded is not None
    assert loaded.text() == corpus.text()
    assert loaded.source_file == "pack.md"
    items = list_corpora(tmp_path)
    assert items and items[0]["id"] == corpus.id
    assert items[0]["stats"]["non_empty"] == len(corpus.non_empty_slides())
