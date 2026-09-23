"""Прогон детерминированного аудита по готовым PPTX (CLI).

Запуск:
    python tools/audit_check.py data/e2e_out/*.pptx
    python tools/audit_check.py deck.pptx --profile template_profiles/tpl.json
    python tools/audit_check.py deck.pptx --brief "текст брифа"

Профиль нужен аудиту, чтобы знать разрешённые шрифты, цвета и типографическую
шкалу. Если профиль не указан, берётся единственный JSON из template_profiles/
или строится из PPTX рядом с колодой.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))


def load_profile(path: Path | None) -> dict:
    import json

    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    candidates = sorted((ROOT / "template_profiles").glob("*.json"))
    if len(candidates) == 1:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    if candidates:
        print(f"Внимание: профилей несколько, беру {candidates[0].name} "
              f"(уточните через --profile)")
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    raise SystemExit("Нет JSON-профилей в template_profiles/. Сначала: python tools/make_profiles.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="Детерминированный аудит PPTX-колоды")
    parser.add_argument("decks", nargs="+", help="файлы .pptx")
    parser.add_argument("--profile", type=Path, default=None, help="JSON-профиль шаблона")
    parser.add_argument("--brief", default="", help="бриф для проверки покрытия темами")
    args = parser.parse_args()

    from app.audit.checks import Audit
    from app.models.deck import Deck
    from app.planner.fallback import FallbackPlanner

    profile = load_profile(args.profile)
    deck = FallbackPlanner().plan(args.brief or "проверка аудита готового файла", "", "project")
    audit = Audit(profile)

    failed = 0
    for name in args.decks:
        path = Path(name)
        if not path.exists():
            print(f"нет файла: {path}")
            failed += 1
            continue
        try:
            deck_for_audit: Deck = deck
            result = audit.audit(deck_for_audit, path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            print(f"{path.name}: НЕ ОТКРЫВАЕТСЯ ({type(exc).__name__}: {exc})")
            failed += 1
            continue
        status = "PASS" if result["passed"] else "FAIL"
        if not result["passed"]:
            failed += 1
        print(f"{path.name:50s} -> {status}  err={result['errors']} warn={result['warnings']}")
        for issue in result["issues"][:40]:
            slide = issue["slide"] if issue["slide"] >= 0 else "—"
            print(f"    [слайд {slide}] {issue['severity']:7s} {issue['code']}: {issue['message'][:110]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
