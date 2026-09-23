from app.export.html import deck_to_html
from app.planner.fallback import FallbackPlanner
from app.template.parser import TemplateParser

BRIEF = ("Обновление фирменного стиля VK: новый логотип, усиление контраста, "
         "рефакторинг типографики. Пилот на 3 продуктах прошёл успешно, вовлечённость "
         "выросла на 12%. Релиз во всех продуктах — в следующем квартале.")


def test_html_export_contains_content(template_any):
    profile = TemplateParser(template_any).parse().to_dict()
    deck = FallbackPlanner().plan(BRIEF, "", "product")
    html = deck_to_html(deck, profile)
    assert "<!DOCTYPE html>" in html
    assert deck.title[:20] in html
    assert "compact" in html or "cards" in html or "slide" in html.lower()


def test_html_export_no_secrets():
    html = deck_to_html(FallbackPlanner().plan(BRIEF, "", "product"),
                        {"palette": [{"hex": "#FF0053", "role": "primary"}]})
    assert "api_key" not in html.lower()
    assert "secret" not in html.lower()