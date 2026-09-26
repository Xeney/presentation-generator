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
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import __version__ as app_version
from ..config import get_settings
from ..content.corpus import ContentCorpus, list_corpora
from ..content.importer import ContentImportError, import_content_pack
from ..pipeline import VARIANTS, full_generate
from ..render.images import draw_issue_boxes, png_bytes
from ..render.pdf import PdfExportError, pdf_bytes_for, pptx_to_pngs
from ..storage import JobStore, ThumbCache

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """При старте подчищаем задания и кэш миниатюр старше TTL (ADR-016)."""
    JOBS.cleanup(force=True)
    THUMBS.prune(force=True)
    yield


app = FastAPI(title="Цифровой дизайнер презентаций", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])

# задания и миниатюры переживают перезапуск: метаданные и артефакты на диске
JOBS = JobStore(settings.data_path, settings.job_cleanup_hours)
THUMBS = ThumbCache(settings.data_path, settings.job_cleanup_hours)

MAX_TEMPLATE = settings.max_upload_mb * 1024 * 1024


def _job_worker(job_id: str, brief: str, source: str, purpose: str,
                tpl: bytes, tpl_name: str, corpus_id: str = ""):
    job = JOBS.get(job_id)
    if job is None:
        return
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
    JOBS.put(job)


def _get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "задание не найдено")
    return job


def _housekeeping() -> None:
    """Ленивая очистка по TTL: запускается не чаще раза в 10 минут."""
    JOBS.cleanup()
    THUMBS.prune()


@app.get("/api/health")
def health():
    """Состояние сервиса: активный провайдер LLM/VLM, модели, флаги аудита."""
    from ..planner.llm import get_llm_client, get_vlm_client

    client = get_llm_client()
    models: list[str] = []
    if hasattr(client, "list_models"):
        try:
            models = client.list_models()
        except Exception:  # noqa: BLE001 — health не должен падать
            models = []
    available = False if settings.disable_llm else _model_ready(
        client, settings.active_llm_model, models)
    vlm_client = get_vlm_client()
    vlm_models: list[str] = []
    if vlm_client is not None and hasattr(vlm_client, "list_models"):
        try:
            vlm_models = vlm_client.list_models()
        except Exception:  # noqa: BLE001
            vlm_models = []
    vlm_available = bool(vlm_client) and not settings.disable_llm and _model_ready(
        vlm_client, settings.active_vlm_model, vlm_models)
    return {
        "status": "ok",
        "version": app_version,
        "variants": VARIANTS,
        "llm": {
            "available": available,
            "disabled": settings.disable_llm,
            "provider": settings.active_llm_provider,
            "model": settings.active_llm_model,
            "label": settings.planner_label,
            "models": models,
        },
        "vlm": {
            "enabled": settings.vlm_audit_enabled,
            "provider": settings.active_vlm_provider,
            "model": settings.active_vlm_model,
            "available": vlm_available,
            "label": settings.vlm_label,
            "models": vlm_models,
        },
        "content_formats": ["pptx", "docx", "txt", "md"],
    }


def _model_ready(client, model: str, models: list[str]) -> bool:
    """Провайдер жив и нужная модель есть в наличии.

    Сервер Ollama может отвечать, но модели в нём ещё нет (идёт скачивание или
    опечатка в теге): тогда провайдер формально доступен, а каждый запрос падает.
    Наличие модели проверяем по списку, если провайдер его отдаёт.
    """
    if client is None or not client.health():
        return False
    if not models:
        return True
    short = {name.split(":")[0] for name in models}
    return model in models or model.split(":")[0] in short


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
    _housekeeping()
    job_id = uuid.uuid4().hex[:12]
    # байты шаблона храним в задании: они нужны для повторного рендера после
    # авто-фиксов и для сборки вариантов на исходных макетах
    JOBS.put({"id": job_id, "status": "pending", "created": time.time(),
              "corpus_id": corpus_id or None,
              "template": data, "template_name": template.filename or "template.pptx"})
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


class ProviderRequest(BaseModel):
    """Переключение провайдера на лету: план Б на живом демо.

    `llm_provider`: aitunnel | openai_compat | ollama | offline (последний —
    принудительный офлайн-планировщик без обращения к моделям).
    `vlm_provider`: aitunnel | openai_compat | ollama | off.
    """

    llm_provider: Optional[str] = None
    vlm_provider: Optional[str] = None


LLM_PROVIDERS = {"aitunnel", "openai_compat", "ollama", "offline"}
VLM_PROVIDERS = {"aitunnel", "openai_compat", "ollama", "off"}


@app.post("/api/provider")
def switch_provider(request: ProviderRequest):
    """Меняет провайдера без перезапуска сервиса.

    Нужно на защите: если внешний шлюз отвалился, демо переключается на
    офлайн-планировщик одной кнопкой, и генерация продолжает работать.
    """
    from ..planner.llm import get_llm_client, get_vlm_client

    if request.llm_provider is None and request.vlm_provider is None:
        raise HTTPException(422, "укажите llm_provider и/или vlm_provider")
    if request.llm_provider and request.llm_provider not in LLM_PROVIDERS:
        raise HTTPException(422, f"llm_provider: ожидается одно из {sorted(LLM_PROVIDERS)}")
    if request.vlm_provider and request.vlm_provider not in VLM_PROVIDERS:
        raise HTTPException(422, f"vlm_provider: ожидается одно из {sorted(VLM_PROVIDERS)}")

    applied: dict = {}
    if request.llm_provider:
        if request.llm_provider == "offline":
            settings.disable_llm = True
        else:
            settings.llm_provider = request.llm_provider
            settings.disable_llm = False
        applied["llm_provider"] = settings.active_llm_provider
        applied["llm_model"] = settings.active_llm_model
    if request.vlm_provider:
        settings.vlm_provider = request.vlm_provider
        applied["vlm_provider"] = settings.active_vlm_provider
        applied["vlm_model"] = settings.active_vlm_model

    client = get_llm_client()
    vlm_client = get_vlm_client()
    return {
        "applied": applied,
        "llm": {
            "provider": settings.active_llm_provider,
            "model": settings.active_llm_model,
            "label": settings.planner_label,
            "available": False if settings.disable_llm else client.health(),
            "disabled": settings.disable_llm,
        },
        "vlm": {
            "provider": settings.active_vlm_provider,
            "model": settings.active_vlm_model,
            "label": settings.vlm_label,
            "available": bool(vlm_client) and not settings.disable_llm
            and vlm_client.health(),
        },
    }


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
        vlm = r.get("vlm", {})
        vlm_label = ("off" if not vlm.get("available")
                     else f"{vlm.get('provider', '?')}/{vlm.get('model', '?')}")
        resp["summary"] = {
            "slides": len(r["deck"]["slides"]),
            "used_llm": r["planner"]["used_llm"],
            "planner_label": r["planner"].get("label", "offline-fallback"),
            "vlm_label": vlm_label,
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
    JOBS.put(job)

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
    """Миниатюра слайда из кэша; при промахе рендерит всю колоду за один прогон.

    Ключ кэша — хэш PPTX, поэтому после авто-фиксов (файл меняется) миниатюры
    пересобираются сами, а рамки проблем берутся из актуального аудита.
    """
    digest = THUMBS.digest(item["pptx"])
    cached = THUMBS.get(digest, s, boxes)
    if cached is not None:
        return cached
    try:
        pngs = pptx_to_pngs(item["pptx"])
    except PdfExportError as exc:
        raise HTTPException(503, f"нужен LibreOffice для миниатюр: {exc}") from exc
    issues = item["audit"]["issues"]
    for index, raw in enumerate(pngs):
        THUMBS.put(digest, index, False, raw)
        spec = [issue["bbox"] for issue in issues
                if issue["slide"] == index and len(issue["bbox"]) == 4]
        if spec:
            from PIL import Image

            marked = draw_issue_boxes(Image.open(io.BytesIO(raw)), spec)
            THUMBS.put(digest, index, True, png_bytes(marked))
    return THUMBS.get(digest, s, boxes) or (pngs[s] if s < len(pngs) else pngs[0])