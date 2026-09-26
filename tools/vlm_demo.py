"""Пример «VLM ловит то, что детерминированный аудит не может» (для защиты).

Колода собрана с дефектами уровня смысла: служебная реплика спикера, опечатка,
англоязычный заголовок-тема. Детерминированный аудит молчит (координаты и токены
в порядке), VLM-аудит отмечает нарушенные вопросы Приложения 1.

Запуск (нужен доступный VLM-провайдер, например AITUNNEL):
    python tools/vlm_demo.py
Артефакты: docs/evidence/audit/vlm_only_*.png|json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.audit.checks import Audit  # noqa: E402
from app.audit.vlm import CRITERIA, VlmAudit, violations_to_issues  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.layout.engine import DesignContext  # noqa: E402
from app.models.deck import Block, Deck, Slide, SlideType  # noqa: E402
from app.render.pdf import pptx_to_pngs  # noqa: E402
from app.render.pptx_renderer import Renderer  # noqa: E402
from app.template.parser import TemplateParser  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

settings = get_settings()
print("VLM-провайдер:", settings.vlm_label, "| доступен:", settings.vlm_audit_enabled)

template = ROOT / "data" / "external_templates" / "PowerTemplates_Anatomy-Lesson.pptx"
template_bytes = template.read_bytes()
profile = TemplateParser(template_bytes).parse().to_dict()

# заголовки-ТЕМЫ вместо выводов: содержание при этом корректное
deck = Deck(title="Пример для VLM", slides=[
    Slide(slide_type=SlideType.TITLE, heading="Обзор продукта",
          subheading="Платформа аналитики: итоги квартала"),
    Slide(slide_type=SlideType.CONTENT, heading="Рынок EdTech",
          blocks=[Block(kind="bullets", items=[
              "Платформа сократила время подготовки отчётов на 40%",
              "Платфомра автоматизировала 12 рутинных задач",
              "Здесь рассказать про пилот с заказчиком и показать отзывы"])]),
    Slide(slide_type=SlideType.CONTENT, heading="Key metrics",
          blocks=[Block(kind="factoids", factoids=[
              {"value": "5", "label": "подразделений"},
              {"value": "2000", "label": "сотрудников"}]),
              Block(kind="bullets", items=["Охват вырос за квартал"])]),
    Slide(slide_type=SlideType.FINAL, heading="Спасибо за внимание"),
])

renderer = Renderer(profile, variant="compact", template_bytes=template_bytes)
pptx = renderer.render_deck(deck, DesignContext.from_profile(profile))

det = Audit(profile).audit(deck, pptx)
print(f"\nдетерминированный аудит: ошибок {det['errors']}, замечаний {det['warnings']}")
for issue in det["issues"]:
    print(f"  {issue['code']}: {issue['message'][:80]}")

vlm = VlmAudit(profile=profile).audit(pptx, deck=deck,
                                      source_digest="платформа аналитики: 40%, 12 задач, "
                                                    "5 подразделений, 2000 сотрудников")
print(f"\nVLM-аудит: available={vlm.get('available')}, ошибок {vlm.get('errors')}, "
      f"пропущено {vlm.get('skipped')}")
for slide in vlm.get("slides", []):
    if slide.get("violations") or slide.get("summary"):
        print(f"  слайд {slide['slide'] + 1}: нарушения {slide['violations']} "
              f"({', '.join(slide['violations_text'])[:120]})")
        if slide.get("summary"):
            print(f"     VLM: {slide['summary'][:160]}")

issues = violations_to_issues(vlm)
print(f"\nзамечаний от VLM: {len(issues)}")
for issue in issues:
    print(f"  слайд {issue['slide'] + 1}: {issue['code']} — {issue['message'][:140]}")

pngs = pptx_to_pngs(pptx, dpi=100)
for index in range(min(3, len(pngs))):
    (OUT / f"vlm_only_s{index + 1}.png").write_bytes(pngs[index])
(OUT / "vlm_only_result.json").write_text(json.dumps(
    {"deterministic": det, "vlm": vlm, "vlm_issues": issues,
     "criteria": CRITERIA}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nартефакты: {OUT}")
