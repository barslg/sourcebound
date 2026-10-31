"""Application settings.

This is the only module that reads the environment. Every tunable lives
here; logic modules take values as arguments instead of reading env vars.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Full tunable catalogue; names map 1:1 to .env.example entries."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://muse:muse@localhost:5434/muse"
    test_database_url: str = ""
    model_api_key: SecretStr | None = None
    model_api_base_url: str = "https://api.meta.ai/v1"
    llm_default_model: str = "muse-spark-1.3"
    llm_dev_mode: bool = False
    llm_spend_ceiling_usd: float = 5.0
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    llm_connect_timeout_s: float = 10.0
    llm_read_timeout_s: float = 120.0
    llm_poll_timeout_s: float = 600.0
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr | None = None
    session_secret_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    return Settings()
