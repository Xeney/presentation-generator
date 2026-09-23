"""HTTP-API сервиса «Цифровой дизайнер презентаций» (FastAPI).

- POST /api/generate   — создать задание (шаблон + бриф) → {job_id}
- GET  /api/jobs/{id}  — статус и результат задания
- GET  /api/jobs/{id}/pptx?variant=compact  — скачать PPTX-вариант
- GET  /api/jobs/{id}/pdf?variant=compact   — PDF (LibreOffice, контейнер)
- GET  /api/jobs/{id}/html                 — HTML-экспорт
- GET  /api/jobs/{id}/thumb?v=cards&s=3    — PNG-миниатюра слайда (with ?boxes=1)
- GET  /api/health                          — проверка состояния
"""
from __future__ import annotations

import io
import json
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..config import get_settings
from ..content.corpus import ContentCorpus, list_corpora
from ..content.importer import ContentImportError, import_content_pack
from ..pipeline import VARIANTS, full_generate
from ..render.images import draw_issue_boxes
from ..render.pdf import pdf_bytes_for, pptx_to_pngs

app = FastAPI(title="Цифровой дизайнер презентаций", version="0.1.0")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])

JOBS: dict[str, dict] = {}

MAX_TEMPLATE = settings.max_upload_mb * 1024 * 1024


def _job_worker(job_id: str, brief: str, source: str, purpose: str,
                tpl: bytes, tpl_name: str, corpus_id: str = ""):
    job = JOBS[job_id]
    try:
        started = time.time()
        corpus = ContentCorpus.load(corpus_id, settings.data_path) if corpus_id else None
        if corpus_id and corpus is None:
            raise ValueError(f"контент-пакет {corpus_id} не найден")
        result = full_generate(brief, source, purpose, tpl, tpl_name, corpus=corpus,
                               vlm=settings.vlm_audit_enabled)
        job.update({"status": "done", "result": result,
                    "elapsed_s": round(time.time() - started, 1)})
    except Exception as exc:
        import traceback

        job.update({"status": "error", "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()[-2000:]})


def _get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "задание не найдено")
    return job


@app.get("/api/health")
def health():
    """Состояние сервиса: доступность Ollama, модели, включён ли VLM-аудит."""
    from ..planner.llm import OllamaClient

    client = OllamaClient()
    available = False if settings.disable_llm else client.health()
    models: list[str] = []
    if available:
        try:
            models = client.list_models()
        except Exception:  # noqa: BLE001 — health не должен падать
            models = []
    return {
        "status": "ok",
        "variants": VARIANTS,
        "llm": {
            "available": available,
            "disabled": settings.disable_llm,
            "model": settings.llm_model,
            "models": models,
        },
        "vlm": {"enabled": settings.vlm_audit_enabled, "model": settings.vlm_model},
        "content_formats": ["pptx", "docx", "txt", "md"],
    }


@app.post("/api/generate")
async def generate(template: UploadFile = File(...),
                   brief: str = Form(...),
                   source: str = Form(""),
                   purpose: str = Form("project"),
                   corpus_id: str = Form("")):
    data = await template.read()
    if len(data) > MAX_TEMPLATE:
        raise HTTPException(413, f"шаблон больше {settings.max_upload_mb} МБ")
    if not data[:8].startswith(b"PK\x03\x04") and b"ppt/" not in data[:5000]:
        raise HTTPException(415, "файл не похож на PPTX")
    if len(brief.strip()) < 20:
        raise HTTPException(422, "бриф слишком короткий (минимум 20 символов)")
    if corpus_id and ContentCorpus.load(corpus_id, settings.data_path) is None:
        raise HTTPException(404, f"контент-пакет {corpus_id} не найден")
    job_id = uuid.uuid4().hex[:12]
    # байты шаблона храним в задании: они нужны для повторного рендера после
    # авто-фиксов и для сборки вариантов на исходных макетах
    JOBS[job_id] = {"id": job_id, "status": "pending", "created": time.time(),
                    "corpus_id": corpus_id or None,
                    "template": data, "template_name": template.filename or "template.pptx"}
    t = threading.Thread(target=_job_worker,
                         args=(job_id, brief, source, purpose, data,
                               template.filename or "template.pptx", corpus_id),
                         daemon=True)
    t.start()
    return {"job_id": job_id, "status": "pending"}


# ----------------------------------------------------------- контент-пакеты
@app.post("/api/content/import")
async def content_import(file: UploadFile = File(...)):
    """Импорт контент-пакета: PPTX/DOCX/TXT → структурированный корпус."""
    data = await file.read()
    if not data:
        raise HTTPException(422, "пустой файл")
    if len(data) > MAX_TEMPLATE:
        raise HTTPException(413, f"файл больше {settings.max_upload_mb} МБ")
    try:
        corpus = import_content_pack(data, file.filename or "content")
    except ContentImportError as exc:
        raise HTTPException(415, str(exc)) from exc
    corpus.save(settings.data_path)
    payload = corpus.to_dict()
    payload["preview"] = [
        {"index": s.index, "heading": s.heading or "(без заголовка)",
         "layout": s.layout, "bullets": s.bullets[:4],
         "paragraphs": s.paragraphs[:2], "numbers": s.numbers[:4],
         "images": s.images, "tables": len(s.tables), "charts": len(s.charts)}
        for s in corpus.non_empty_slides()[:12]
    ]
    return payload


@app.get("/api/content")
def content_list():
    return {"corpora": list_corpora(settings.data_path)}


@app.get("/api/content/{corpus_id}")
def content_get(corpus_id: str):
    corpus = ContentCorpus.load(corpus_id, settings.data_path)
    if corpus is None:
        raise HTTPException(404, "контент-пакет не найден")
    return corpus.to_dict()


@app.get("/api/content/{corpus_id}/image/{key}")
def content_image(corpus_id: str, key: str):
    corpus = ContentCorpus.load(corpus_id, settings.data_path)
    if corpus is None:
        raise HTTPException(404, "контент-пакет не найден")
    blob = corpus.images.get(key)
    if blob is None:
        raise HTTPException(404, "изображение не найдено")
    media = "image/jpeg" if key.lower().endswith((".jpg", ".jpeg")) else "image/png"
    res = Response(content=blob, media_type=media)
    res.headers["Cache-Control"] = "public, max-age=3600"
    return res


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _get_job(job_id)
    # наружу не отдаём ни результат (в нём байты PPTX), ни сам шаблон
    resp = {k: v for k, v in job.items() if k not in ("result", "template")}
    if job.get("status") == "done":
        r = job["result"]
        resp["summary"] = {
            "slides": len(r["deck"]["slides"]),
            "used_llm": r["planner"]["used_llm"],
            "elapsed_s": job.get("elapsed_s"),
            "stages": r.get("stages", {}),
            "corpus_id": job.get("corpus_id"),
            "version": job.get("version", 1),
            "fixes": len(job.get("fixes", [])),
            "vlm_available": r.get("vlm", {}).get("available", False),
            "variants": [{"name": v["name"], "passed": v["audit"]["passed"],
                          "errors": v["audit"]["errors"],
                          "warnings": v["audit"]["warnings"]} for v in r["variants"]],
        }
    return resp


@app.get("/api/jobs/{job_id}/pptx")
def download_pptx(job_id: str, variant: str = "compact"):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    res = Response(content=item["pptx"], media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    res.headers["Content-Disposition"] = f'attachment; filename="{job_id}_{variant}.pptx"'
    return res


@app.get("/api/jobs/{job_id}/pdf")
def download_pdf(job_id: str, variant: str = "compact"):
    from ..render.pdf import PdfExportError

    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    try:
        pdf = pdf_bytes_for(item["pptx"])
    except PdfExportError as exc:
        raise HTTPException(503, str(exc))
    res = Response(content=pdf, media_type="application/pdf")
    res.headers["Content-Disposition"] = f'attachment; filename="{job_id}_{variant}.pdf"'
    return res


@app.get("/api/jobs/{job_id}/html")
def download_html(job_id: str):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    return Response(content=job["result"]["html"], media_type="text/html; charset=utf-8")


@app.get("/api/jobs/{job_id}/thumb")
def thumbnail(job_id: str, variant: str = "compact", s: int = 0, boxes: int = 0):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    png = _thumb(item, s, bool(boxes))
    res = Response(content=png, media_type="image/png")
    res.headers["Cache-Control"] = "public, max-age=3600"
    return res


class FixRequest(BaseModel):
    """Тело запроса авто-фиксов: какие проблемы исправляем и в каком варианте."""

    issue_ids: list[str] = Field(default_factory=list)
    variant: str = "compact"


@app.post("/api/jobs/{job_id}/fix")
def job_fix(job_id: str, request: FixRequest):
    """Применяет детерминированные авто-фиксы к выбранным проблемам.

    Колода пере-рендерится во все три варианта и пере-аудитится; в ответе —
    список того, что реально исправлено, и что пропущено (с причиной).
    """
    from ..audit.fixes import FixEngine
    from ..models.deck import Deck
    from ..pipeline import audit_variant, html_export, render_variants

    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    template_bytes = job.get("template")
    if not template_bytes:
        raise HTTPException(409, "исходный шаблон недоступен: перезапустите генерацию")
    if not request.issue_ids:
        raise HTTPException(422, "не выбрано ни одной проблемы")

    result = job["result"]
    item = _find_variant(job, request.variant)
    issues = item["audit"]["issues"]

    corpus = (ContentCorpus.load(job["corpus_id"], settings.data_path)
              if job.get("corpus_id") else None)
    images = corpus.images if corpus is not None else None

    deck = Deck.model_validate(result["deck"])
    started = time.time()
    engine = FixEngine(result["profile"])
    deck, outcomes = engine.apply(deck, issues, request.issue_ids)

    artifacts = render_variants(deck, result["profile"], template_bytes, images=images)
    for artifact in artifacts:
        artifact.audit = audit_variant(deck, artifact, result["profile"])

    result["deck"] = json.loads(deck.model_dump_json())
    result["variants"] = [{"name": a.variant, "pptx": a.pptx, "audit": a.audit}
                          for a in artifacts]
    result["html"] = html_export(deck, result["profile"])
    # VLM-аудит после фиксов не пересчитывается: это дорогая стадия, а честное
    # «не проверялось» лучше устаревшей оценки
    result["vlm"] = {"available": False, "slides": [],
                     "reason": "после авто-фиксов VLM-аудит не пересчитывался",
                     "criteria": result.get("vlm", {}).get("criteria", {})}
    result.setdefault("stages", {})["fix_s"] = round(time.time() - started, 2)
    job["fixes"] = (job.get("fixes", []) + outcomes)[-50:]
    job["version"] = job.get("version", 1) + 1

    return {
        "applied": [o for o in outcomes if o["status"] == "applied"],
        "skipped": [o for o in outcomes if o["status"] == "skipped"],
        "version": job["version"],
        "summary": {
            "slides": len(deck.slides),
            "variants": [{"name": a.variant, "passed": a.audit["passed"],
                          "errors": a.audit["errors"], "warnings": a.audit["warnings"]}
                         for a in artifacts],
        },
    }


def _find_variant(job: dict, variant: str) -> dict:
    for v in job["result"]["variants"]:
        if v["name"] == variant:
            return v
    raise HTTPException(404, f"вариант {variant} не найден")


@app.get("/api/jobs/{job_id}/audit")
def job_audit(job_id: str, variant: str = "compact"):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    return item["audit"]


@app.get("/api/jobs/{job_id}/info")
def job_info(job_id: str):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    r = job["result"]
    return {
        "profile": r["profile"],
        "deck": r["deck"],
        "planner": r["planner"],
        "corpus": r.get("corpus"),
        "vlm": r["vlm"],
        "grounding": r.get("grounding", {}),
        "prompts": r.get("prompts", {}),
        "stages": r.get("stages", {}),
        "variants": [{"name": v["name"], "audit_summary": {
            "passed": v["audit"]["passed"], "errors": v["audit"]["errors"],
            "warnings": v["audit"]["warnings"]}} for v in r["variants"]],
        "elapsed_s": job.get("elapsed_s"),
    }


def _thumb(item: dict, s: int, boxes: bool) -> bytes:
    from ..render.pdf import PdfExportError

    try:
        pngs = pptx_to_pngs(item["pptx"])
    except PdfExportError as exc:
        raise HTTPException(503, f"нужен LibreOffice для миниатюр: {exc}") from exc
    if s < len(pngs):
        png = pngs[s]
    else:
        return pngs[0]
    if boxes:
        import base64

        from PIL import Image

        issues = item["audit"]["issues"]
        slide_issues = [i for i in issues if i["slide"] == s]
        if slide_issues:
            img = Image.open(io.BytesIO(png))
            spec = [(i["bbox"][0], i["bbox"][1], i["bbox"][2], i["bbox"][3])
                    for i in slide_issues if len(i["bbox"]) == 4]
            out = draw_issue_boxes(img, spec)
            buf = io.BytesIO()
            out.save(buf, format="PNG")
            return buf.getvalue()
    return png