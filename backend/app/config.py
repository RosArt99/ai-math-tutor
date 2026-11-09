"""Application settings, read from the environment or a local ``.env`` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: str = "openrouter"  # "openrouter" or "stub" (offline, for tests and UI work)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Tried in order: when one model is unavailable the next one answers the same request.
    llm_models: str = (
        "nvidia/nemotron-3-ultra-550b-a55b:free,"
        "nvidia/nemotron-3.5-super-vl:free,"
        "google/gemma-4-31b-it:free"
    )
    llm_timeout_seconds: float = 40.0
    llm_retry_window_seconds: float = 25.0
    llm_retry_pause_seconds: float = 3.0
    llm_max_tokens: int = 1500
    public_url: str = "http://localhost:8000"

    database_url: str = f"sqlite:///{ROOT / 'data' / 'tutor.db'}"
    max_message_length: int = 1000
    max_turns_per_minute: int = 12
    history_messages: int = 12

    @property
    def model_list(self) -> list[str]:
        return [model.strip() for model in self.llm_models.split(",") if model.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
