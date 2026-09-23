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
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from ..config import get_settings
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
                tpl: bytes, tpl_name: str):
    job = JOBS[job_id]
    try:
        started = time.time()
        result = full_generate(brief, source, purpose, tpl, tpl_name)
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
    return {"status": "ok", "variants": VARIANTS}


@app.post("/api/generate")
async def generate(template: UploadFile = File(...),
                   brief: str = Form(...),
                   source: str = Form(""),
                   purpose: str = Form("project")):
    data = await template.read()
    if len(data) > MAX_TEMPLATE:
        raise HTTPException(413, f"шаблон больше {settings.max_upload_mb} МБ")
    if not data[:8].startswith(b"PK\x03\x04") and b"ppt/" not in data[:5000]:
        raise HTTPException(415, "файл не похож на PPTX")
    if len(brief.strip()) < 20:
        raise HTTPException(422, "бриф слишком короткий (минимум 20 символов)")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "status": "pending", "created": time.time()}
    t = threading.Thread(target=_job_worker, args=(job_id, brief, source, purpose,
                                                   data, template.filename or "template.pptx"),
                         daemon=True)
    t.start()
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _get_job(job_id)
    resp = {k: v for k, v in job.items() if k != "result"}
    if job.get("status") == "done":
        r = job["result"]
        resp["summary"] = {
            "slides": len(r["deck"]["slides"]),
            "used_llm": r["planner"]["used_llm"],
            "elapsed_s": job.get("elapsed_s"),
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
        "vlm": r["vlm"],
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