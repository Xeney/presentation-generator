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


def pptx_to_pdf(pptx_bytes: bytes, soffice: str | None = None, timeout_s: int = 300) -> bytes:
    """Конвертирует PPTX в PDF байтовым потоком (работает и в контейнере)."""
    bin = soffice or os.environ.get("SOFFICE", "")
    if not bin:
        import shutil

        bin = shutil.which("soffice") or shutil.which("libreoffice") or ""
    if not bin:
        raise PdfExportError("LibreOffice не найден (переменная SOFFICE или soffice в PATH)")

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


def pptx_to_pngs(pptx_bytes: bytes, dpi: int = 90, **kw) -> list[bytes]:
    """Миниатюры слайдов PNG. Требует LibreOffice + PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # noqa: BLE001 — сообщаем как ошибку экспорта, а не 500
        raise PdfExportError(
            "PyMuPDF не установлен: миниатюры недоступны (pip install PyMuPDF)") from exc

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