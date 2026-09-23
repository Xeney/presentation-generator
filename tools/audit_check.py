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


def sibling_profile(deck_path: Path) -> dict | None:
    """Профиль рядом с колодой: e2e-прогон кладёт «{шаблон}.profile.json»."""
    import json

    stem = deck_path.stem
    for variant in ("compact", "cards", "split"):
        if stem.endswith(f"_{variant}"):
            stem = stem[: -len(variant) - 1]
            break
    candidate = deck_path.with_name(f"{stem}.profile.json")
    if candidate.exists():
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — битый файл не должен ломать проверку
            return None
    return None


def load_profile(path: Path | None, deck_name: str = "") -> dict:
    """Профиль шаблона: явный, либо подобранный по имени колоды.

    Колоды из e2e-прогона называются «{stem профиля}_{вариант}.pptx», поэтому
    при нескольких профилях берём тот, чей шаблон совпадает с именем файла —
    иначе аудит сравнивал бы колоду с чужим дизайн-системным профилем и выдавал
    ложные font_size_not_in_scale и layout_not_from_template.
    """
    import json

    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    candidates = sorted((ROOT / "template_profiles").glob("*.json"))
    if not candidates:
        raise SystemExit(
            "Нет JSON-профилей в template_profiles/. Сначала: python tools/make_profiles.py")

    profiles = []
    for candidate in candidates:
        try:
            profiles.append((candidate, json.loads(candidate.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001 — битый профиль пропускаем
            continue
    if deck_name:
        for candidate, profile in profiles:
            stem = Path(str(profile.get("source_file", ""))).stem
            if stem and deck_name.startswith(stem):
                return profile
        if len(profiles) > 1:
            print(f"Внимание: профиль не определён по имени «{deck_name}», "
                  f"беру {profiles[0][0].name} (уточните через --profile)")
    return profiles[0][1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Детерминированный аудит PPTX-колоды")
    parser.add_argument("decks", nargs="+", help="файлы .pptx")
    parser.add_argument("--profile", type=Path, default=None, help="JSON-профиль шаблона")
    parser.add_argument("--brief", default="", help="бриф для проверки покрытия темами")
    args = parser.parse_args()

    from app.audit.checks import Audit
    from app.models.deck import Deck
    from app.planner.fallback import FallbackPlanner

    deck = FallbackPlanner().plan(args.brief or "проверка аудита готового файла", "", "project")
    fallback_profile = load_profile(args.profile)

    failed = 0
    for name in args.decks:
        path = Path(name)
        if not path.exists():
            print(f"нет файла: {path}")
            failed += 1
            continue
        profile = (fallback_profile if args.profile
                   else sibling_profile(path) or load_profile(None, path.name))
        audit = Audit(profile)
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
