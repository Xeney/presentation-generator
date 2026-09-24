"""Репетиция живого демо: весь сценарий через реальный HTTP с хронометражем.

Проходит путь из docs/DEMO.md без монтажа: здоровье → контент-пакет → генерация
→ три варианта с миниатюрами → аудит → авто-фиксы → экспорт PPTX/PDF/HTML.
Сервис должен быть запущен (`./start.sh --local` или docker compose up).

Запуск:
    python tools/demo_rehearsal.py --template "data/templates/ЛЦТ2026.pptx"
    python tools/demo_rehearsal.py --template tpl.pptx --corpus pack.pptx --budget 420
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
VARIANTS = ("compact", "cards", "split")
BRIEF = (
    "Платформа внутренней аналитики: за квартал время подготовки отчётов "
    "сократилось на 40%, автоматизированы 12 рутинных задач, охват вырос до "
    "5 подразделений. Платформой пользуются 2000 сотрудников еженедельно. "
    "План — подключить 10 отделов к концу года и внедрить ML-предсказания выручки."
)


def discover_template(explicit: Path | None) -> Path | None:
    if explicit and explicit.exists():
        return explicit
    for directory in (ROOT / "data" / "templates", ROOT):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.pptx")):
            if not path.name.lower().startswith(("vk tech", "unfamiliar", "synthetic")):
                return path
    return None


def discover_corpus(explicit: Path | None) -> Path | None:
    if explicit and explicit.exists():
        return explicit
    for directory in (ROOT / "data" / "templates", ROOT):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.pptx")):
            if path.name.lower().startswith("vk tech"):
                return path
    return None


def step(label: str, started: float) -> float:
    elapsed = time.perf_counter() - started
    print(f"  {label}: {elapsed:.1f} c")
    return elapsed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Репетиция живого демо")
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--api", default=API)
    parser.add_argument("--budget", type=float, default=420.0,
                        help="бюджет всего демо, секунды (ТЗ: питч 7 минут)")
    parser.add_argument("--skip-fixes", action="store_true")
    args = parser.parse_args(argv)

    api = args.api.rstrip("/")
    template = discover_template(args.template)
    if template is None:
        print("нет шаблона: положите PPTX в data/templates/ или передайте --template")
        return 1
    corpus_path = discover_corpus(args.corpus)

    timings: dict[str, float] = {}
    started_all = time.perf_counter()
    print(f"шаблон: {template.name}")
    print(f"контент-пакет: {corpus_path.name if corpus_path else '(не найден, только бриф)'}")

    with httpx.Client(base_url=api, timeout=httpx.Timeout(300)) as client:
        # 1. здоровье
        began = time.perf_counter()
        try:
            health = client.get("/api/health").json()
        except Exception as exc:  # noqa: BLE001
            print(f"сервис недоступен: {exc}\nзапустите ./start.sh --local")
            return 1
        timings["health"] = step(f"health: LLM {health['llm']['available']}", began)
        if not health["llm"]["available"]:
            print("  ! провайдер недоступен — колода соберётся офлайн-планировщиком")

        # 2. контент-пакет
        corpus_id = ""
        if corpus_path:
            began = time.perf_counter()
            response = client.post("/api/content/import",
                                   files={"file": (corpus_path.name,
                                                   corpus_path.read_bytes(),
                                                   "application/octet-stream")})
            if response.status_code == 200:
                payload = response.json()
                corpus_id = payload["id"]
                timings["corpus"] = step(
                    f"контент-пакет разобран: слайдов {payload['stats']['non_empty']}, "
                    f"цифр {payload['stats']['numbers']}", began)
            else:
                print(f"  ! контент-пакет не принят: {response.status_code}")

        # 3. генерация
        began = time.perf_counter()
        response = client.post(
            "/api/generate",
            files={"template": (template.name, template.read_bytes(),
                                "application/octet-stream")},
            data={"brief": BRIEF, "source": "", "purpose": "project",
                  "corpus_id": corpus_id})
        if response.status_code != 200:
            print(f"генерация не запустилась: {response.status_code} {response.text[:200]}")
            return 1
        job_id = response.json()["job_id"]

        state = {}
        for _ in range(900):
            state = client.get(f"/api/jobs/{job_id}").json()
            if state["status"] in ("done", "error"):
                break
            time.sleep(1.0)
        if state["status"] != "done":
            print(f"задание упало: {state.get('error')}")
            return 1
        summary = state["summary"]
        timings["generation"] = step(
            f"генерация: слайдов {summary['slides']}, планировщик "
            f"{summary.get('planner_label')}, VLM {summary.get('vlm_label')}", began)
        print(f"    стадии: {summary.get('stages')}")

        # 4. миниатюры и аудит по вариантам
        began = time.perf_counter()
        issues_total: list[str] = []
        for variant in VARIANTS:
            thumb = client.get(f"/api/jobs/{job_id}/thumb",
                               params={"variant": variant, "s": 0, "boxes": 1})
            audit = client.get(f"/api/jobs/{job_id}/audit",
                               params={"variant": variant}).json()
            issues_total += [i["id"] for i in audit["issues"]]
            mark = "OK" if thumb.status_code == 200 else f"{thumb.status_code}"
            print(f"    {variant:8s} миниатюра {mark}, ошибок {audit['errors']}, "
                  f"замечаний {audit['warnings']}")
        timings["thumbs_audit"] = step("миниатюры и аудит ×3", began)

        # 5. авто-фиксы
        if issues_total and not args.skip_fixes:
            began = time.perf_counter()
            fixed = client.post(f"/api/jobs/{job_id}/fix",
                                json={"issue_ids": issues_total[:40], "variant": "compact"})
            if fixed.status_code == 200:
                body = fixed.json()
                timings["fixes"] = step(
                    f"авто-фиксы: исправлено {len(body['applied'])}, "
                    f"пропущено {len(body['skipped'])}", began)
            else:
                print(f"    ! фиксы не применились: {fixed.status_code}")

        # 6. экспорт
        began = time.perf_counter()
        for variant in VARIANTS:
            pptx = client.get(f"/api/jobs/{job_id}/pptx", params={"variant": variant})
            print(f"    PPTX {variant:8s} {pptx.status_code}, {len(pptx.content) // 1024} КБ")
        pdf = client.get(f"/api/jobs/{job_id}/pdf", params={"variant": "compact"})
        print(f"    PDF {pdf.status_code}, "
              + (f"{len(pdf.content) // 1024} КБ" if pdf.status_code == 200
                 else pdf.text[:80]))
        html = client.get(f"/api/jobs/{job_id}/html")
        print(f"    HTML {html.status_code}, {len(html.text) // 1024} КБ")
        timings["export"] = step("экспорт PPTX ×3 + PDF + HTML", began)

    total = time.perf_counter() - started_all
    print("\n" + "=" * 62)
    print(f"ИТОГО демо-прогон: {total:.1f} c (бюджет {args.budget:.0f} c)")
    for name, value in timings.items():
        print(f"  {name:14s} {value:6.1f} c")
    print("=" * 62)
    if total > args.budget:
        print("ВНИМАНИЕ: не укладываемся в бюджет — сокращайте рассказ или VLM")
        return 1
    print("В бюджет укладываемся")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
