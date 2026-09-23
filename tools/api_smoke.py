"""Smoke-тест API без запуска сервера (FastAPI TestClient).

Запуск:
    python tools/api_smoke.py                       # первый PPTX из data/templates/
    python tools/api_smoke.py path/to/template.pptx

Проверяет полный цикл: generate → poll → pptx (3 варианта) → html → audit → info.
LLM можно выключить переменной окружения DISABLE_LLM=true.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402

BRIEF = ("VK Tech запускает внутреннюю платформу аналитики. За квартал платформа "
         "сократила время подготовки отчётов на 40%, автоматизировала 12 рутинных "
         "задач, охватила 5 подразделений. 2000 сотрудников используют её еженедельно. "
         "План: подключить 10 отделов к концу года и внедрить ML-предсказания выручки.")


def find_template(argv: list[str]) -> Path | None:
    if argv:
        path = Path(argv[0])
        return path if path.exists() else None
    for directory in (ROOT / "data" / "templates", ROOT):
        for path in sorted(directory.glob("*.pptx")):
            return path
    return None


def main(argv: list[str]) -> int:
    tpl_path = find_template(argv)
    if tpl_path is None:
        print("PPTX-шаблон не найден: положите файл в data/templates/ или передайте путь аргументом.")
        return 0

    print(f"шаблон: {tpl_path.name}")
    client = TestClient(app)
    with client:
        resp = client.post(
            "/api/generate",
            files={"template": (tpl_path.name, tpl_path.read_bytes(), "application/octet-stream")},
            data={"brief": BRIEF, "source": "", "purpose": "project"})
        print("generate:", resp.status_code, resp.json())
        if resp.status_code != 200:
            return 1
        job = resp.json()["job_id"]

        state: dict = {}
        for _ in range(600):
            state = client.get(f"/api/jobs/{job}").json()
            if state["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        print("status:", state.get("status"), state.get("summary") or state.get("error"))
        if state["status"] != "done":
            print("ERROR:", state.get("error"))
            return 1

        for variant in ("compact", "cards", "split"):
            pptx = client.get(f"/api/jobs/{job}/pptx", params={"variant": variant})
            print(f"pptx[{variant}]:", pptx.status_code, len(pptx.content) // 1024, "KB")
            if pptx.status_code != 200 or len(pptx.content) < 1000:
                return 1

        html = client.get(f"/api/jobs/{job}/html")
        print("html:", html.status_code, len(html.text) // 1024, "KB")
        if "DOCTYPE" not in html.text:
            return 1

        audit = client.get(f"/api/jobs/{job}/audit", params={"variant": "cards"}).json()
        print("audit:", audit["errors"], "err /", audit["warnings"], "warn /",
              len(audit["issues"]), "issues")

        info = client.get(f"/api/jobs/{job}/info").json()
        print("info: слайдов", len(info["deck"]["slides"]),
              "| LLM:", info["planner"]["used_llm"],
              "| VLM доступен:", info["vlm"].get("available"))
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
