"""9 вариантов презентаций: 3 шаблона × 3 варианта вёрстки на одном контент-пакете.

Требование ТЗ: результат демонстрации — три варианта презентации на одном
контенте, сгенерированные на трёх разных шаблонах (всего 9 вариантов).
Скрипт проходит весь путь через реальный API: контент-пакет → для каждого
шаблона генерация → аудит → скачивание трёх вариантов → отчёт.

Запуск:
    python tools/e2e_9variants.py                          # 3 шаблона из data/templates
    python tools/e2e_9variants.py --templates a.pptx b.pptx c.pptx --corpus pack.pptx
    python tools/e2e_9variants.py --brief "текст брифа"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from pptx import Presentation  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: E402

BRIEF = (
    "Платформа внутренней аналитики: за квартал время подготовки отчётов "
    "сократилось на 40%, автоматизированы 12 рутинных задач, охват вырос до "
    "5 подразделений. Платформой пользуются 2000 сотрудников еженедельно. "
    "План — подключить 10 отделов к концу года и внедрить ML-предсказания выручки."
)
FALLBACK_PACK = """# Итоги квартала
- Время подготовки отчётов сократилось на 40%
- Автоматизированы 12 рутинных задач
- Охват вырос до 5 подразделений
Платформой пользуются 2000 сотрудников еженедельно.

## План на год
- Подключить 10 отделов к концу года
- Внедрить ML-предсказания выручки

| Метрика | До | После |
| --- | --- | --- |
| Время отчёта | 8 ч | 3 ч |
| Ошибки | 15% | 4% |
"""
VARIANTS = ("compact", "cards", "split")


def slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", Path(name).stem)[:40].strip("_")


def discover_templates(explicit: list[Path]) -> list[Path]:
    if explicit:
        return explicit
    found: dict[str, Path] = {}
    for directory in (ROOT / "data" / "templates", ROOT):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.pptx")):
            # контент-пакет и синтетические фикстуры в роли шаблонов не нужны:
            # берём «настоящие» шаблоны (на финале их будет три незнакомых)
            name = path.name.lower()
            if name.startswith(("vk tech", "unfamiliar", "synthetic")):
                continue
            found.setdefault(path.name, path)
    return list(found.values())[:3]


def discover_corpus(explicit: Path | None) -> tuple[bytes, str]:
    if explicit and explicit.exists():
        return explicit.read_bytes(), explicit.name
    for directory in (ROOT / "data" / "templates", ROOT):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.pptx")):
            if path.name.startswith("VK Tech"):
                return path.read_bytes(), path.name
    return FALLBACK_PACK.encode("utf-8"), "pack.md"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="9 вариантов: 3 шаблона × 3 вёрстки")
    parser.add_argument("--templates", nargs="*", type=Path, default=[])
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--brief", default=BRIEF)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "output" / "9variants")
    parser.add_argument("--purpose", default="project")
    args = parser.parse_args(argv)

    templates = discover_templates(args.templates)
    if not templates:
        print("нет шаблонов: положите PPTX в data/templates/ или передайте --templates")
        return 1
    corpus_bytes, corpus_name = discover_corpus(args.corpus)
    # абсолютный путь: иначе relative_to в отчёте падает на относительном --out
    args.out = args.out.resolve()

    from app.config import get_settings

    settings = get_settings()
    print(f"планировщик: {settings.planner_label} · VLM: {settings.vlm_label}")
    print(f"шаблонов: {len(templates)} · контент-пакет: {corpus_name}")

    from fastapi.testclient import TestClient

    from app.api.main import app

    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    total_started = time.perf_counter()

    client = TestClient(app)
    with client:
        imported = client.post("/api/content/import",
                               files={"file": (corpus_name, corpus_bytes,
                                               "application/octet-stream")})
        if imported.status_code != 200:
            print(f"контент-пакет не принят: {imported.status_code} {imported.text[:200]}")
            return 1
        corpus = imported.json()
        print(f"контент-пакет разобран: слайдов {corpus['stats']['non_empty']}, "
              f"цифр {corpus['stats']['numbers']}, картинок {corpus['stats']['images']}\n")

        for template_path in templates:
            if not template_path.exists():
                print(f"нет файла: {template_path}")
                continue
            template_bytes = template_path.read_bytes()
            started = time.perf_counter()
            response = client.post(
                "/api/generate",
                files={"template": (template_path.name, template_bytes,
                                    "application/octet-stream")},
                data={"brief": args.brief, "source": "", "purpose": args.purpose,
                      "corpus_id": corpus["id"]})
            if response.status_code != 200:
                print(f"{template_path.name}: генерация не запустилась "
                      f"({response.status_code}) {response.text[:150]}")
                continue
            job_id = response.json()["job_id"]

            state = {}
            for _ in range(900):
                state = client.get(f"/api/jobs/{job_id}").json()
                if state["status"] in ("done", "error"):
                    break
                time.sleep(1.0)
            elapsed = time.perf_counter() - started
            if state["status"] != "done":
                print(f"{template_path.name}: ОШИБКА {state.get('error')}")
                continue

            summary = state["summary"]
            stages = summary.get("stages", {})
            print(f"=== {template_path.name} ===")
            print(f"  планировщик {summary.get('planner_label')} · слайдов "
                  f"{summary['slides']} · VLM {summary.get('vlm_label')}")
            print(f"  стадии: парсинг {stages.get('parse_s')} c · планирование "
                  f"{stages.get('plan_s')} c · рендер ×3 {stages.get('render_s')} c · "
                  f"аудит ×3 {stages.get('audit_s')} c · grounding "
                  f"{stages.get('grounding_s')} c · VLM {stages.get('vlm_s')} c")
            print(f"  ИТОГО {elapsed:.1f} c")

            for variant in VARIANTS:
                audit = client.get(f"/api/jobs/{job_id}/audit",
                                   params={"variant": variant}).json()
                pptx = client.get(f"/api/jobs/{job_id}/pptx", params={"variant": variant})
                target = args.out / f"{slug(template_path.name)}_{variant}.pptx"
                target.write_bytes(pptx.content)
                prs = Presentation(__import__("io").BytesIO(pptx.content))
                raster = sum(
                    1 for slide in prs.slides for shape in slide.shapes
                    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                    and shape.width >= prs.slide_width * 0.92
                    and shape.height >= prs.slide_height * 0.92)
                row = {
                    "template": template_path.name, "variant": variant,
                    "job_id": job_id, "slides": len(prs.slides),
                    "job_s": round(elapsed, 1),
                    "errors": audit["errors"], "warnings": audit["warnings"],
                    "codes": sorted({i["code"] for i in audit["issues"]}),
                    "issues": [f"[слайд {i['slide']}] {i['code']}: {i['message'][:110]}"
                               for i in audit["issues"]],
                    "raster_slides": raster,
                    "kb": len(pptx.content) // 1024,
                    "file": str(target.relative_to(ROOT)),
                    "planner": summary.get("planner_label"),
                    "vlm": summary.get("vlm_label"),
                    "stages": stages,
                }
                rows.append(row)
                print(f"    {variant:8s} слайдов {len(prs.slides):2d} · ошибок "
                      f"{audit['errors']} · замечаний {audit['warnings']:2d} · "
                      f"{len(pptx.content) // 1024} КБ → {target.name}")
            print()

    total = time.perf_counter() - total_started
    print("=" * 78)
    print(f"{'шаблон':34s} {'вариант':8s} {'слайдов':>7s} {'ошибок':>7s} "
          f"{'замеч.':>7s} {'время':>7s}")
    print("-" * 78)
    for row in rows:
        print(f"{row['template'][:33]:34s} {row['variant']:8s} {row['slides']:7d} "
              f"{row['errors']:7d} {row['warnings']:7d} {row['job_s']:6.1f} c")
    print("-" * 78)
    print(f"всего вариантов: {len(rows)} · суммарное время: {total:.1f} c")

    report = {
        "planner": settings.planner_label, "vlm": settings.vlm_label,
        "corpus": corpus["source_file"], "corpus_stats": corpus["stats"],
        "templates": [str(p.name) for p in templates],
        "rows": rows, "total_s": round(total, 1),
        "all_passed": all(row["errors"] == 0 for row in rows),
    }
    report_path = args.out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"отчёт: {report_path.relative_to(ROOT)}")
    return 0 if report["all_passed"] and len(rows) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
