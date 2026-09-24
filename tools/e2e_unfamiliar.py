"""Сквозной прогон на НЕЗНАКОМОМ шаблоне: обезличенные макеты + контент-пакет.

Проверяет главный риск защиты: решение не заточено под известные шаблоны.
Шаблон генерируется заново (16:10, чужие палитра и шрифты, макеты `Layout N`),
затем через реальный API проходит весь путь: загрузка → планирование LLM →
три варианта вёрстки → детерминированный аудит → авто-фиксы → экспорт.

Запуск:
    python tools/e2e_unfamiliar.py                     # текст-пакет, AITUNNEL/Ollama из .env
    python tools/e2e_unfamiliar.py --corpus pack.pptx  # свой контент-пакет
    python tools/e2e_unfamiliar.py --offline           # принудительно офлайн-планировщик
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from pptx import Presentation  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: E402

from tools.make_fixtures import build_unfamiliar  # noqa: E402

BRIEF = (
    "Платформа внутренней аналитики: за квартал время подготовки отчётов "
    "сократилось на 40%, автоматизированы 12 рутинных задач, охват вырос до "
    "5 подразделений. Платформой пользуются 2000 сотрудников еженедельно. "
    "План — подключить 10 отделов к концу года и внедрить ML-предсказания выручки."
)
PACK = """# Итоги квартала
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="E2E на незнакомом шаблоне")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="файл контент-пакета (по умолчанию текстовый)")
    parser.add_argument("--offline", action="store_true",
                        help="принудительно офлайн-планировщик (DISABLE_LLM=true)")
    parser.add_argument("--no-fix", action="store_true", help="не применять авто-фиксы")
    args = parser.parse_args(argv)

    import os

    if args.offline:
        os.environ["DISABLE_LLM"] = "true"
    os.environ.setdefault("DISABLE_LLM", "false")

    from app.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from app.api.main import app

    template = build_unfamiliar()
    templates_dir = ROOT / "data" / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    template_path = templates_dir / "unfamiliar_16x10.pptx"
    template_path.write_bytes(template)

    # профиль шаблона: роли макетов должны выводиться из структуры
    from app.template.parser import TemplateParser

    profile = TemplateParser(template).parse().to_dict()
    roles = {role: len(ids) for role, ids in profile["layout_groups"].items()}
    kinds = {}
    for layout in profile["layouts"]:
        kinds[layout["kind"]] = kinds.get(layout["kind"], 0) + 1
    print(f"шаблон: {template_path.name}, слайд "
          f"{profile['slide_size']['w_in']}×{profile['slide_size']['h_in']}″, "
          f"макетов {len(profile['layouts'])}")
    print(f"  роли по структуре: {roles}")
    print(f"  композиционные типы: {kinds}")
    print(f"  шрифты: {profile.get('headline_font')} / {profile.get('body_font')}, "
          f"палитра {len(profile.get('palette', []))}")

    client = TestClient(app)
    report: dict = {"template": template_path.name, "roles": roles, "kinds": kinds,
                    "planner": {}, "variants": {}, "fixes": {}, "exports": {}}

    with client:
        # 1. контент-пакет
        if args.corpus and args.corpus.exists():
            payload = args.corpus.read_bytes()
            name = args.corpus.name
        else:
            payload, name = PACK.encode("utf-8"), "pack.md"
        imported = client.post("/api/content/import",
                               files={"file": (name, payload, "application/octet-stream")})
        assert imported.status_code == 200, imported.text
        corpus = imported.json()
        print(f"контент-пакет: {corpus['source_file']} — слайдов "
              f"{corpus['stats']['non_empty']}, цифр {corpus['stats']['numbers']}, "
              f"картинок {corpus['stats']['images']}")
        report["corpus"] = corpus["stats"]

        # 2. генерация
        started = time.perf_counter()
        response = client.post(
            "/api/generate",
            files={"template": (template_path.name, template, "application/octet-stream")},
            data={"brief": BRIEF, "source": "", "purpose": "project",
                  "corpus_id": corpus["id"]})
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]

        state = {}
        for _ in range(600):
            state = client.get(f"/api/jobs/{job_id}").json()
            if state["status"] in ("done", "error"):
                break
            time.sleep(1.0)
        elapsed = time.perf_counter() - started
        if state["status"] != "done":
            print(f"ОШИБКА генерации: {state.get('error')}")
            return 1

        summary = state["summary"]
        report["planner"] = {"label": summary.get("planner_label"),
                             "stages": summary.get("stages")}

        # структура колоды: видно, что именно сгенерировала модель
        info = client.get(f"/api/jobs/{job_id}/info").json()
        deck = info["deck"]
        report["deck"] = {
            "title": deck["title"],
            "slides": [{"type": slide["slide_type"], "heading": slide["heading"],
                        "blocks": [{"kind": block["kind"],
                                    "items": len(block.get("items") or []),
                                    "has_table": block.get("table") is not None,
                                    "has_chart": block.get("chart") is not None}
                                   for block in slide["blocks"]]}
                       for slide in deck["slides"]],
        }
        report["prompts"] = info.get("prompts")
        print(f"\nгенерация: {elapsed:.1f} c, слайдов {summary['slides']}, "
              f"планировщик {summary.get('planner_label')}")
        print(f"  стадии: {summary.get('stages')}")
        print(f"  VLM: {summary.get('vlm_label')}")

        # 3. аудит по вариантам
        all_ids: list[str] = []
        for variant in ("compact", "cards", "split"):
            audit = client.get(f"/api/jobs/{job_id}/audit",
                               params={"variant": variant}).json()
            report["variants"][variant] = {
                "errors": audit["errors"], "warnings": audit["warnings"],
                "codes": sorted({issue["code"] for issue in audit["issues"]}),
                "issues": [f"[{i['slide']}] {i['code']}: {i['message'][:90]}"
                           for i in audit["issues"]]}
            all_ids += [issue["id"] for issue in audit["issues"]]
            print(f"  {variant:8s} ошибок {audit['errors']}, замечаний {audit['warnings']}: "
                  f"{report['variants'][variant]['codes'] or 'чисто'}")
            if variant == "compact":
                for issue in audit["issues"][:12]:
                    print(f"      [{issue['slide']}] {issue['code']}: {issue['message'][:95]}")

        # 4. авто-фиксы по всем найденным проблемам
        if all_ids and not args.no_fix:
            fixed = client.post(f"/api/jobs/{job_id}/fix",
                                json={"issue_ids": all_ids[:40], "variant": "compact"})
            if fixed.status_code == 200:
                body = fixed.json()
                report["fixes"] = {"applied": len(body["applied"]),
                                   "skipped": len(body["skipped"]),
                                   "details": [f"{i['action']}: {i['detail']}"
                                               for i in body["applied"]],
                                   "skipped_details": [f"{i['code']}: {i['detail']}"
                                                       for i in body["skipped"]]}
                print(f"\nавто-фиксы: исправлено {len(body['applied'])}, "
                      f"пропущено {len(body['skipped'])}")
                for item in body["applied"]:
                    print(f"  + {item['action']}: {item['detail']}")
                for variant in ("compact", "cards", "split"):
                    audit = client.get(f"/api/jobs/{job_id}/audit",
                                       params={"variant": variant}).json()
                    print(f"  после фиксов {variant:8s}: ошибок {audit['errors']}, "
                          f"замечаний {audit['warnings']}")
                    report["variants"][variant]["after_fix"] = {
                        "errors": audit["errors"], "warnings": audit["warnings"]}
            else:
                report["fixes"] = {"error": fixed.text[:200]}

        # 5. экспорт
        out_dir = ROOT / "data" / "output" / "unfamiliar"
        out_dir.mkdir(parents=True, exist_ok=True)
        for variant in ("compact", "cards", "split"):
            pptx = client.get(f"/api/jobs/{job_id}/pptx", params={"variant": variant})
            path = out_dir / f"unfamiliar_{variant}.pptx"
            path.write_bytes(pptx.content)
            prs = Presentation(io.BytesIO(pptx.content))
            pictures_full = 0
            native = 0
            for slide in prs.slides:
                for shape in slide.shapes:
                    native += 1
                    if (shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                            and shape.width >= prs.slide_width * 0.92
                            and shape.height >= prs.slide_height * 0.92):
                        pictures_full += 1
            report["exports"][variant] = {
                "pptx_kb": len(pptx.content) // 1024,
                "slides": len(prs.slides),
                "native_shapes": native,
                "raster_slides": pictures_full,
            }
            print(f"  PPTX {variant:8s}: {len(pptx.content) // 1024} КБ, "
                  f"слайдов {len(prs.slides)}, нативных объектов {native}, "
                  f"растровых слайдов {pictures_full}")

        pdf = client.get(f"/api/jobs/{job_id}/pdf", params={"variant": "compact"})
        report["exports"]["pdf"] = {"status": pdf.status_code,
                                    "kb": len(pdf.content) // 1024 if pdf.status_code == 200 else 0}
        print(f"  PDF: {pdf.status_code}"
              + (f", {len(pdf.content) // 1024} КБ" if pdf.status_code == 200
                 else f" ({pdf.text[:80]})"))
        html = client.get(f"/api/jobs/{job_id}/html")
        report["exports"]["html"] = {"status": html.status_code, "kb": len(html.text) // 1024}
        print(f"  HTML: {html.status_code}, {len(html.text) // 1024} КБ")

    report_path = ROOT / "data" / "output" / "unfamiliar_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\nотчёт: {report_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
