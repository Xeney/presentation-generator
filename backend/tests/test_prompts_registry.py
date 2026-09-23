"""Тесты версионирования промптов: манифест, хэши, попадание версии в задание."""
from __future__ import annotations

from app import prompts_meta


def test_registry_lists_all_agents_and_versions():
    registry = prompts_meta.load_registry()
    artifacts = {item["id"]: item for item in registry["artifacts"]}
    assert {"planner/system", "planner/user", "planner/purpose",
            "vlm/audit", "vlm/user"} <= set(artifacts)
    for artifact in artifacts.values():
        assert artifact["version"], f"{artifact['id']}: нет версии"
        assert artifact["kind"] in ("prompt", "config")
        assert artifact["purpose"]


def test_registry_is_in_sync_with_files():
    """После правки промпта нужно запустить scripts/sync_prompts.py."""
    problems = prompts_meta.verify()
    assert not problems, problems


def test_versions_include_hash_and_model():
    versions = prompts_meta.versions(["planner/system", "vlm/audit"])
    assert set(versions) == {"planner/system", "vlm/audit"}
    for item in versions.values():
        assert len(item["hash"]) >= 8
        assert item["model"]


def test_generate_records_prompt_versions(synthetic_template):
    from app.pipeline import full_generate

    result = full_generate("Короткий бриф для проверки версий промптов в задании.",
                           "", "project", synthetic_template, "synthetic.pptx")
    assert "prompts" in result
    assert "planner/system" in result["prompts"]
    assert result["prompts"]["planner/system"]["version"]
