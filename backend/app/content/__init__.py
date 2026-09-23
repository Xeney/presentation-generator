"""Импорт и декомпозиция контент-пакетов (PPTX/DOCX/TXT → ContentCorpus)."""
from .corpus import ContentCorpus, CorpusSlide, CorpusTable, list_corpora
from .importer import ContentImportError, import_content_pack

__all__ = [
    "ContentCorpus", "CorpusSlide", "CorpusTable", "list_corpora",
    "ContentImportError", "import_content_pack",
]
