"""Экспорт PPTX → PDF через LibreOffice (soffice).

LibreOffice контейнеризирован (docker-compose), локальный прогон опционален.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class PdfExportError(RuntimeError):
    pass


# Типичные места установки: в Windows и macOS soffice не всегда попадает в PATH
COMMON_SOFFICE_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
)


def find_soffice(explicit: str | None = None) -> str:
    """Ищет LibreOffice: аргумент → SOFFICE → PATH → типовые каталоги установки."""
    import shutil

    candidates = [explicit, os.environ.get("SOFFICE"), shutil.which("soffice"),
                  shutil.which("libreoffice"), *COMMON_SOFFICE_PATHS]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def pptx_to_pdf(pptx_bytes: bytes, soffice: str | None = None, timeout_s: int = 300) -> bytes:
    """Конвертирует PPTX в PDF байтовым потоком (работает и в контейнере)."""
    bin = find_soffice(soffice)
    if not bin:
        raise PdfExportError(
            "LibreOffice не найден: задайте SOFFICE, добавьте soffice в PATH или "
            "установите LibreOffice (Windows: winget install "
            "TheDocumentFoundation.LibreOffice; macOS: brew install --cask libreoffice)")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        src = tmp_p / "deck.pptx"
        src.write_bytes(pptx_bytes)
        out_dir = tmp_p / "out"
        out_dir.mkdir()
        cmd = [bin, "--headless", "--norestore", "--convert-to", "pdf",
               "--outdir", str(out_dir), str(src)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        except FileNotFoundError as exc:
            raise PdfExportError(f"LibreOffice не найден: {bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PdfExportError("превышено время конвертации в PDF") from exc
        pdfs = list(out_dir.glob("*.pdf"))
        if proc.returncode != 0 or not pdfs:
            raise PdfExportError(f"LibreOffice вернул {proc.returncode}: {proc.stderr[:300]}")
        return pdfs[0].read_bytes()


def _load_pymupdf():
    """Импорт PyMuPDF: новое имя `pymupdf`, старое `fitz` — запасной вариант."""
    try:
        import pymupdf  # noqa: PLC0415 — импорт по требованию
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # noqa: PLC0415 — устаревшее имя, но работает
        return fitz
    except ImportError as exc:  # noqa: BLE001
        raise PdfExportError(
            "PyMuPDF не установлен: миниатюры недоступны (pip install PyMuPDF)") from exc


def pptx_to_pngs(pptx_bytes: bytes, dpi: int = 90, **kw) -> list[bytes]:
    """Миниатюры слайдов PNG. Требует LibreOffice + PyMuPDF."""
    fitz = _load_pymupdf()
    pdf = pptx_to_pdf(pptx_bytes, **kw)
    out = []
    doc = fitz.open(stream=pdf, filetype="pdf")
    for page in doc:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out.append(pix.tobytes("png"))
    doc.close()
    return out


pdf_bytes_for = pptx_to_pdf  # обратносовместимый алиас для API