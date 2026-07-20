"""Settings from environment variables (compose injects them; no .env discovery)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram_bot_token: SecretStr
    telegram_api_id: int
    telegram_api_hash: SecretStr
    default_chat_id: str | None = None
    bootstrap_drives: str = ""  # "alias:chat_id,alias2:chat_id2" registered on first boot

    database_url: str = "postgresql+asyncpg://tup:change-me@localhost:5432/tup"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr
    allow_registration: bool = True  # new Telegram identities may request codes (pending approval)
    allowed_telegram_ids: str = ""  # comma-separated ids/usernames auto-approved on first login
    login_code_ttl_seconds: int = Field(300, ge=60)
    access_token_minutes: int = Field(15, ge=1)
    refresh_token_days: int = Field(14, ge=1)

    observer_enabled: bool = True
    max_retries: int = Field(3, ge=1, le=10)

    # Collabora Online (CODE): container-internal URLs. wopi_base is how the
    # CODE container reaches this backend for WOPI calls. SSL is disabled via
    # the mounted coolwsd.xml (CODE 26's --use-env-vars ignores extra_params).
    collabora_url: str = "http://collabora:9980"
    wopi_base: str = "http://backend:8000"
    wopi_token_hours: int = Field(10, ge=1)
    office_max_bytes: int = Field(200 * 1024 * 1024, ge=1)

    session_dir: Path = Path("/data/session")
    cache_dir: Path = Path("/data/cache")
    spool_dir: Path = Path("/data/spool")
    cache_ttl_minutes: int = Field(60, ge=1)
    cache_sweep_seconds: int = Field(300, ge=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
