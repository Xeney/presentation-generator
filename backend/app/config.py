"""Настройки сервиса. Все секреты и параметры окружения читаются из .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# .env ищем и в текущем каталоге, и в корне репозитория: сервис одинаково
# подхватывает настройки при запуске из корня, из backend/ и из Docker.
ENV_FILES = (Path(".env"), Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, env_file_encoding="utf-8",
                                      extra="ignore")

    # --- LLM: провайдер ---
    # ollama — по умолчанию: открытые веса, локальный инференс (требование ТЗ);
    # openai_compat — OpenAI-совместимый шлюз, только для разработки (ADR-019),
    # ключ читается из .env и никогда не попадает в репозиторий и логи.
    llm_provider: str = "ollama"
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""
    openai_compat_vlm_model: str = ""
    openai_compat_embedding_model: str = ""

    # --- LLM (Ollama) ---
    ollama_base_url: str = "http://ollama:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_model_14b: str = "qwen2.5:14b-instruct"
    vlm_model: str = "qwen2.5-vl:7b-instruct"
    embedding_model: str = "bge-m3"
    llm_timeout_s: int = 300
    disable_llm: bool = False
    planner_llm_max_retries: int = 3

    # --- Pipeline ---
    max_slides: int = 15
    min_slides: int = 4
    target_duration_min: int = 5
    vlm_audit_enabled: bool = True
    vlm_audit_all_variants: bool = False
    grounding_enabled: bool = True
    grounding_off_source_threshold: float = 0.55
    grounding_duplicate_threshold: float = 0.92

    # --- Хранилище ---
    data_dir: str = "./data"
    max_upload_mb: int = 60

    # --- Экспорт ---
    libreoffice_bin: str = "soffice"

    # --- API ---
    cors_origins: str = "http://localhost:3000"

    job_cleanup_hours: float = 12.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uses_external_provider(self) -> bool:
        """True, если включён внешний шлюз (для сдачи требуется ollama)."""
        return self.llm_provider == "openai_compat" and bool(self.openai_compat_base_url)

    @property
    def active_llm_model(self) -> str:
        if self.uses_external_provider and self.openai_compat_model:
            return self.openai_compat_model
        return self.llm_model

    @property
    def active_vlm_model(self) -> str:
        if self.uses_external_provider and self.openai_compat_vlm_model:
            return self.openai_compat_vlm_model
        return self.vlm_model

    @property
    def active_embedding_model(self) -> str:
        if self.uses_external_provider and self.openai_compat_embedding_model:
            return self.openai_compat_embedding_model
        return self.embedding_model

    @property
    def data_path(self):
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()