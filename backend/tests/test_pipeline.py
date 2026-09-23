from app.pipeline import full_generate

BRIEF = ("Хакатон VK Tech: нужна презентация проекта «Цифровой дизайнер презентаций». "
         "Решение генерирует колоду по брифу на произвольном шаблоне, верстает 3 варианта "
         "нативными объектами, прогоняет детерминированный и VLM-аудит и возвращает "
         "pptx/pdf/html. Конкурсное требование — открытые модели, без платных API.")


def test_full_generate_three_variants_without_llm(template_lct):
    res = full_generate(BRIEF, "", "project", template_lct, "lct.pptx")
    assert res["planner"]["used_llm"] is False
    assert len(res["variants"]) == 3
    for v in res["variants"]:
        assert v["name"] in ("compact", "cards", "split")
        assert v["pptx"].startswith(b"PK")
        assert v["audit"]["passed"], v["audit"]["issues"][:2]
    assert "<!DOCTYPE html>" in res["html"]
    assert res["deck"]["slides"]


def test_profile_is_purpose_agnostic(template_lct):
    from app.pipeline import build_profile

    assert build_profile(template_lct, "x.pptx")["template_id"]