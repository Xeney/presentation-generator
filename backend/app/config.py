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
    # ollama        — по умолчанию в коде: открытые веса, локальный инференс (ТЗ);
    # openai_compat — произвольный OpenAI-совместимый шлюз (ADR-019);
    # aitunnel      — OpenAI-совместимый шлюз с моделями Qwen (ADR-020).
    # Ключи читаются только из .env и никогда не попадают в репозиторий и логи.
    llm_provider: str = "ollama"
    vlm_provider: str = ""            # пусто = как у LLM; "off" — VLM-аудит выключен
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""
    openai_compat_vlm_model: str = ""
    openai_compat_embedding_model: str = ""

    # --- AITUNNEL (OpenAI-совместимый шлюз, модели Qwen с открытыми весами) ---
    aitunnel_base_url: str = "https://api.aitunnel.ru/v1"
    aitunnel_api_key: str = ""
    aitunnel_llm_model: str = "qwen3.5-9b"
    aitunnel_vlm_model: str = "qwen3.5-9b"
    aitunnel_timeout_sec: int = 120
    aitunnel_max_retries: int = 1
    # Qwen3.5 по умолчанию «размышляет» и может долго генерировать преамбулу;
    # для структурированных ответов thinking выключаем (быстрее и стабильнее JSON)
    aitunnel_disable_thinking: bool = True

    # DEMO_MODE=true — без тихого отката: если модель недоступна, задание
    # завершается понятной ошибкой, а не офлайн-колодой (для живого демо).
    demo_mode: bool = False

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
    def uses_aitunnel(self) -> bool:
        return self.llm_provider == "aitunnel" and bool(self.aitunnel_api_key)

    @property
    def uses_external_provider(self) -> bool:
        """True, если включён внешний шлюз (для сдачи требуется ollama)."""
        if self.llm_provider == "aitunnel":
            return bool(self.aitunnel_api_key)
        return self.llm_provider == "openai_compat" and bool(self.openai_compat_base_url)

    @property
    def active_llm_provider(self) -> str:
        """Фактический провайдер планировщика: ollama | openai_compat | aitunnel."""
        if self.llm_provider == "aitunnel":
            return "aitunnel" if self.aitunnel_api_key else "ollama"
        if self.llm_provider == "openai_compat" and self.openai_compat_base_url:
            return "openai_compat"
        return "ollama"

    @property
    def active_vlm_provider(self) -> str:
        """Провайдер VLM-аудита: пусто — как у LLM, `off` — стадия выключена."""
        requested = (self.vlm_provider or self.llm_provider).strip().lower()
        if requested == "off":
            return "off"
        if requested == "aitunnel":
            return "aitunnel" if self.aitunnel_api_key else "ollama"
        if requested == "openai_compat" and self.openai_compat_base_url:
            return "openai_compat"
        return "ollama"

    @property
    def active_llm_model(self) -> str:
        provider = self.active_llm_provider
        if provider == "aitunnel":
            return self.aitunnel_llm_model
        if provider == "openai_compat":
            return self.openai_compat_model or self.llm_model
        return self.llm_model

    @property
    def active_vlm_model(self) -> str:
        provider = self.active_vlm_provider
        if provider == "aitunnel":
            return self.aitunnel_vlm_model
        if provider == "openai_compat":
            return self.openai_compat_vlm_model or self.vlm_model
        return self.vlm_model

    @property
    def active_embedding_model(self) -> str:
        if self.llm_provider == "openai_compat" and self.openai_compat_embedding_model:
            return self.openai_compat_embedding_model
        # у AITUNNEL эмбеддинги не заявлены: смысловая часть grounding работает
        # на локальной BGE-M3, числа проверяются всегда
        return self.embedding_model

    @property
    def planner_label(self) -> str:
        """Строка для отчёта задания: «провайдер/модель»."""
        return f"{self.active_llm_provider}/{self.active_llm_model}"

    @property
    def vlm_label(self) -> str:
        if self.active_vlm_provider == "off":
            return "off"
        return f"{self.active_vlm_provider}/{self.active_vlm_model}"

    @property
    def data_path(self):
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()