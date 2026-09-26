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
                tpl: bytes, tpl_name: str, corpus_id: str = "",
                vlm: bool = True, max_slides: int | None = None,
                language: str = "ru", render_mode: str = "native"):
    job = JOBS.get(job_id)
    if job is None:
        return
    try:
        started = time.time()
        corpus = ContentCorpus.load(corpus_id, settings.data_path) if corpus_id else None
        if corpus_id and corpus is None:
            raise ValueError(f"контент-пакет {corpus_id} не найден")
        result = full_generate(brief, source, purpose, tpl, tpl_name, corpus=corpus,
                               vlm=vlm, auto_fix=settings.auto_fix_enabled,
                               max_slides=max_slides, language=language,
                               render_mode=render_mode)
        current = JOBS.get(job_id) or job
        if current.get("cancelled"):
            # пользователь нажал «Отменить»: результат не сохраняем и не показываем
            job.update({"status": "cancelled", "elapsed_s": round(time.time() - started, 1)})
        else:
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
    from ..render.pdf import find_soffice
    from ..runtime_provider import RUNTIME

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
        "pdf": {"available": bool(find_soffice(settings.libreoffice_bin))},
        "auto_fix": settings.auto_fix_enabled,
        "provider": RUNTIME.public(),
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
                   corpus_id: str = Form(""),
                   vlm: str = Form(""),
                   slides: int = Form(0),
                   language: str = Form("ru"),
                   render_mode: str = Form("native")):
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
    # форма может не передавать флаг VLM: тогда действует настройка сервиса
    vlm_enabled = (settings.vlm_audit_enabled if not vlm.strip()
                   else vlm.strip().lower() not in ("off", "false", "0", "нет"))
    mode = render_mode.strip().lower()
    mode = mode if mode in ("native", "html", "both") else "native"
    t = threading.Thread(target=_job_worker,
                         args=(job_id, brief, source, purpose, data,
                               template.filename or "template.pptx", corpus_id,
                               vlm_enabled,
                               slides or None,
                               language if language in ("ru", "en") else "ru",
                               mode),
                         daemon=True)
    t.start()
    return {"job_id": job_id, "status": "pending"}


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    """«Отменить» в интерфейсе: результат отменённого задания не показывается.

    Планирование и рендер — один вызов без внутренних точек остановки, поэтому
    отмена мгновенно убирает задание из интерфейса, а поток завершается сам и
    выбрасывает результат (в job.json он не попадает).
    """
    job = _get_job(job_id)
    if job.get("status") in ("done", "error", "cancelled"):
        return {"status": job["status"]}
    job["cancelled"] = True
    job["status"] = "cancelled"
    JOBS.put(job)
    return {"status": "cancelled"}


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


# ------------------------------------------------- внешний ключ из интерфейса
class RuntimeProviderRequest(BaseModel):
    """Внешний OpenAI-совместимый сервис, подключённый через UI (ADR-031)."""

    base_url: str = Field(min_length=4, max_length=300)
    api_key: str = Field(default="", max_length=400)
    model: str = Field(default="", max_length=120)


def _runtime_preflight(request: RuntimeProviderRequest) -> tuple[bool, str]:
    """Проверка адреса/ключа/модели без сохранения: «зелёная галочка» или причина."""
    from ..planner.llm import LlmError, OpenAICompatClient

    key = request.api_key.strip()
    if not key:
        return False, "Введите API-ключ."
    client = OpenAICompatClient(
        base_url=request.base_url.strip().rstrip("/"),
        api_key=key,
        timeout_s=min(20, settings.llm_timeout_s),
        max_retries=0,
        provider_name="внешний сервис",
        key_env="API-ключ из интерфейса",
        mask_key=False,
        extra_payload={"reasoning_effort": "none"},
    )
    try:
        models = client.list_models()
    except LlmError as exc:
        status = getattr(exc, "status", None)
        if status in (401, 403) or "401" in str(exc) or "403" in str(exc):
            return False, ("Ключ не подошёл или закончился баланс. Проверьте ключ "
                           "или вернитесь на локальный режим.")
        if status == 402 or "402" in str(exc):
            return False, ("Ключ не подошёл или закончился баланс. Проверьте ключ "
                           "или вернитесь на локальный режим.")
        if status == 404 or "404" in str(exc):
            return False, "Адрес сервиса отвечает, но незнаком: проверьте адрес API."
        return False, f"Сервис не ответил: {exc}"
    except Exception as exc:  # noqa: BLE001 — health-check не должен падать
        return False, f"Сервис не отвечает. Проверьте адрес и интернет. ({exc})"

    model = request.model.strip()
    if models and model and model not in models:
        hint = ", ".join(models[:8])
        return False, (f"Модель «{model}» недоступна у сервиса. Доступные модели: {hint}…")
    return True, ("Подключение работает" if not model
                  else f"Подключение работает, модель «{model}» доступна")


@app.post("/api/provider/test")
def provider_test(request: RuntimeProviderRequest):
    """Проверить подключение, ничего не сохраняя."""
    ok, message = _runtime_preflight(request)
    return {"ok": ok, "message": message}


@app.post("/api/provider/set")
def provider_set(request: RuntimeProviderRequest):
    """Сохранить внешний ключ в памяти процесса: следующее задание идёт через него."""
    from ..runtime_provider import RUNTIME

    ok, message = _runtime_preflight(request)
    if not ok:
        RUNTIME.mark("fail", message)
        return {"ok": False, "message": message, "provider": RUNTIME.public()}
    RUNTIME.set(request.base_url, request.api_key, request.model or "gpt-4o-mini",
                status="ok", message=message)
    return {"ok": True, "message": message,
            "provider": RUNTIME.public(),
            "label": settings.planner_label}


@app.post("/api/provider/reset")
def provider_reset():
    """Вернуться к локальной Ollama: ключ стирается из памяти."""
    from ..runtime_provider import RUNTIME

    RUNTIME.reset()
    return {"ok": True, "provider": RUNTIME.public(),
            "label": settings.planner_label}


@app.get("/api/provider/status")
def provider_status():
    """Текущий провайдер: источник, адрес, модель, ключ — только в маске."""
    from ..runtime_provider import RUNTIME

    return {"provider": RUNTIME.public(), "label": settings.planner_label,
            "local_label": f"ollama/{settings.llm_model}"}


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
        auto = r.get("auto_fixes") or {}
        resp["summary"] = {
            "slides": len(r["deck"]["slides"]),
            "used_llm": r["planner"]["used_llm"],
            "planner_label": r["planner"].get("label", "offline-fallback"),
            "vlm_label": vlm_label,
            "elapsed_s": job.get("elapsed_s"),
            "stages": r.get("stages", {}),
            "corpus_id": job.get("corpus_id"),
            "version": job.get("version", 1),
            "render_mode": r.get("render_mode", "native"),
            "fixes": len(job.get("fixes", [])),
            "auto_fix_applied": len(auto.get("applied", [])),
            "auto_fix_skipped": len(auto.get("skipped", [])),
            "vlm_available": r.get("vlm", {}).get("available", False),
            "variants": [{"name": v["name"], "passed": v["audit"]["passed"],
                          "errors": v["audit"]["errors"],
                          "warnings": v["audit"]["warnings"],
                          "html": bool(v.get("html")),
                          "html_errors": (v.get("html_audit") or {}).get("errors")}
                         for v in r["variants"]],
        }
    return resp


# GET и HEAD: интерфейс перед скачиванием проверяет, что файл собирается
@app.api_route("/api/jobs/{job_id}/pptx", methods=["GET", "HEAD"])
def download_pptx(job_id: str, variant: str = "compact", render: str = "native"):
    """PPTX задания: классический (python-pptx) или собранный из HTML (ADR-035)."""
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    if render == "html":
        blob = item.get("html_pptx") or b""
        if not blob:
            raise HTTPException(404, "HTML-версия не собиралась: выберите режим "
                                     "«Через HTML» или «Оба»")
        res = Response(content=blob, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        res.headers["Content-Disposition"] = \
            f'attachment; filename="presentation_{variant}_html.pptx"'
        return res
    res = Response(content=item["pptx"], media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    # имя файла — как на карточке скачивания: браузер сохраняет его как есть
    res.headers["Content-Disposition"] = f'attachment; filename="presentation_{variant}.pptx"'
    return res


def _pdf_error_text(exc: Exception) -> str:
    """Понятная человеку причина, почему PDF не собрался."""
    return ("PDF сейчас недоступен: на сервере не установлен LibreOffice. "
            "Скачайте PPTX — он собирается всегда.")


@app.api_route("/api/jobs/{job_id}/pdf", methods=["GET", "HEAD"])
def download_pdf(job_id: str, variant: str = "compact"):
    from ..render.pdf import PdfExportError

    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    try:
        pdf = pdf_bytes_for(item["pptx"])
    except PdfExportError as exc:
        raise HTTPException(503, _pdf_error_text(exc))
    res = Response(content=pdf, media_type="application/pdf")
    res.headers["Content-Disposition"] = f'attachment; filename="presentation_{variant}.pdf"'
    return res


@app.get("/api/jobs/{job_id}/download")
def download_all(job_id: str, formats: str = "pptx,pdf",
                 variants: str = "compact,cards,split"):
    """ZIP со всеми выбранными форматами всех вариантов одним файлом.

    Если PDF не собрался (нет LibreOffice), он просто не кладётся в архив, а
    PPTX остаётся: кнопка «Скачать всё» не должна падать из-за опционального
    формата.
    """
    import zipfile

    from ..render.pdf import PdfExportError

    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    wanted = {part.strip().lower() for part in formats.split(",")} & {"pptx", "pdf", "html"}
    if not wanted:
        raise HTTPException(422, "не выбран ни один формат")
    names = [part.strip() for part in variants.split(",") if part.strip()] or list(VARIANTS)
    buf = io.BytesIO()
    added = 0
    pdf_failed = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            item = _find_variant(job, name)
            if "pptx" in wanted:
                archive.writestr(f"presentation_{name}.pptx", item["pptx"])
                added += 1
            if "html" in wanted and item.get("html"):
                archive.writestr(f"presentation_{name}.html", item["html"])
                added += 1
            if "pdf" in wanted:
                try:
                    archive.writestr(f"presentation_{name}.pdf", pdf_bytes_for(item["pptx"]))
                    added += 1
                except PdfExportError:
                    pdf_failed = True
    if not added:
        raise HTTPException(503, _pdf_error_text(PdfExportError("нет LibreOffice")))
    res = Response(content=buf.getvalue(), media_type="application/zip")
    res.headers["Content-Disposition"] = \
        f'attachment; filename="presentations_{job_id}.zip"'
    if pdf_failed:
        res.headers["X-Pdf-Skipped"] = "1"
    return res


@app.get("/api/jobs/{job_id}/html")
def download_html(job_id: str, variant: str = ""):
    """HTML-колода: без `variant` — легаси-экспорт, с `variant` — рендер ADR-035.

    Новый HTML-рендер (Block B) открывается в браузере (`inline`), потому что
    скачивание HTML по ТЗ v2.0 — это «посмотреть в браузере».
    """
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    if variant:
        item = _find_variant(job, variant)
        page = item.get("html") or ""
        if not page:
            raise HTTPException(404, "HTML-версия не собиралась: выберите режим "
                                     "«Через HTML» или «Оба»")
        res = Response(content=page, media_type="text/html; charset=utf-8")
        res.headers["Content-Disposition"] = \
            f'inline; filename="presentation_{variant}.html"'
        return res
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


def _file_info(blob: bytes | None) -> dict:
    """Размер и доступность артефакта для карточек скачивания (bytes из памяти)."""
    size = len(blob or b"")
    return {"available": size > 0, "bytes": size if size else None}


@app.get("/api/jobs/{job_id}/audit")
def job_audit(job_id: str, variant: str = "compact", render: str = "native"):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    item = _find_variant(job, variant)
    if render == "html":
        return item.get("html_audit") or item["audit"]
    return item["audit"]


@app.get("/api/jobs/{job_id}/info")
def job_info(job_id: str):
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(409, "задание ещё выполняется")
    from ..render.pdf import find_soffice

    pdf_ok = bool(find_soffice(settings.libreoffice_bin))
    r = job["result"]
    return {
        "profile": r["profile"],
        "deck": r["deck"],
        "render_mode": r.get("render_mode", "native"),
        "planner": r["planner"],
        "corpus": r.get("corpus"),
        "vlm": r["vlm"],
        "grounding": r.get("grounding", {}),
        "auto_fixes": r.get("auto_fixes", {}),
        "prompts": r.get("prompts", {}),
        "stages": r.get("stages", {}),
        "variants": [{"name": v["name"], "audit_summary": {
            "passed": v["audit"]["passed"], "errors": v["audit"]["errors"],
            "warnings": v["audit"]["warnings"]},
            # размеры файлов для карточек результата. PDF собирается только при
            # скачивании, поэтому для него отдаём доступность без размера
            "files": {
                "pptx": _file_info(v.get("pptx")),
                "pdf": {"available": pdf_ok, "bytes": None},
                "html_pptx": _file_info(v.get("html_pptx")),
                "html": _file_info((v.get("html") or "").encode("utf-8")
                                   if v.get("html") else None),
            }} for v in r["variants"]],
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