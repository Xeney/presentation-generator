"""Сквозные тесты пайплайна на синтетическом шаблоне (работают без файлов VK)."""
from app.pipeline import build_profile, full_generate

BRIEF = ("Хакатон VK Tech: нужна презентация проекта «Цифровой дизайнер презентаций». "
         "Решение генерирует колоду по брифу на произвольном шаблоне, верстает 3 варианта "
         "нативными объектами, прогоняет детерминированный и VLM-аудит и возвращает "
         "pptx/pdf/html. Конкурсное требование — открытые модели, без платных API.")


def test_full_generate_three_variants_without_llm(synthetic_template):
    res = full_generate(BRIEF, "", "project", synthetic_template, "synthetic.pptx")
    assert res["planner"]["used_llm"] is False
    assert len(res["variants"]) == 3
    for variant in res["variants"]:
        assert variant["name"] in ("compact", "cards", "split")
        assert variant["pptx"].startswith(b"PK")
        assert variant["audit"]["passed"], variant["audit"]["issues"][:2]
    assert "<!DOCTYPE html>" in res["html"]
    assert res["deck"]["slides"]
    # VLM недоступен в тестовом окружении — стадия обязана деградировать, а не падать
    assert res["vlm"]["available"] is False


def test_profile_is_purpose_agnostic(synthetic_template):
    profile = build_profile(synthetic_template, "x.pptx")
    assert profile["template_id"]
    assert profile["slide_size"]["w_in"] > 0
    assert profile["layouts"]
    assert profile["palette"]
    assert profile["headline_font"]


def test_generate_on_unfamiliar_template(unfamiliar_template):
    """Мутированный шаблон (имена макетов Slide N, чужие токены) обязан работать."""
    res = full_generate(BRIEF, "", "project", unfamiliar_template, "unfamiliar.pptx")
    profile = res["profile"]
    assert profile["layouts"]
    roles = profile["layout_groups"]
    assert "content" in roles, f"роли макетов не определены: {roles}"
    for variant in res["variants"]:
        assert variant["audit"]["errors"] == 0, variant["audit"]["issues"][:3]
