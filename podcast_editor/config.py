from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("data")
    state_backend: str = "filesystem"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    hf_token: str | None = None
    whisper_model: str = "large-v3"
    whisper_device: str = "cpu"
    reason_language: str = "he"

    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_bucket: str = "podcast-artifacts"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
