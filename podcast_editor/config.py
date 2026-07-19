from functools import lru_cache
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("data")
    state_backend: str = "filesystem"
    run_inline_pipeline: bool | None = None
    worker_poll_seconds: float = 10.0

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    assemblyai_api_key: str | None = None

    hf_token: str | None = None
    whisper_model: str = "large-v3"
    whisper_device: str = "cpu"
    reason_language: str = "he"

    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_bucket: str = "podcast-artifacts"
    better_auth_url: str | None = None
    better_auth_secret: str | None = None
    resend_api_key: str | None = None
    feed_email_from: str = "Get To The Point <feeds@example.com>"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if os.getenv("VERCEL") and settings.data_dir == Path("data"):
        settings.data_dir = Path("/tmp/podcast-editor-data")
    if settings.run_inline_pipeline is None:
        settings.run_inline_pipeline = not bool(os.getenv("VERCEL"))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
