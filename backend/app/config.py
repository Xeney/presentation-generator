"""Настройки сервиса. Все секреты и параметры окружения читаются из .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    def data_path(self):
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()