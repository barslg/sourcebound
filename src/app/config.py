"""Application settings.

This is the only module that reads the environment. Every tunable lives
here; logic modules take values as arguments instead of reading env vars.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+asyncpg://muse:muse@localhost:5434/muse"


class Settings(BaseSettings):
    """Full tunable catalogue; names map 1:1 to .env.example entries."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = DEFAULT_DATABASE_URL
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
    superadmin_emails: str = ""
    session_cookie_name: str = "muse_session"
    session_ttl_s: int = 604800
    session_secure_cookie: bool = False
    oidc_issuer: str = "https://accounts.google.com"
    oidc_redirect_url: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_allowed_domains: str = ""
    oidc_http_timeout_s: float = 10.0
    sse_ping_s: float = 20.0
    source_store_path: str = "var/sources"
    source_fetch_timeout_s: float = 15.0
    source_fetch_max_bytes: int = 2000000
    mastery_threshold: float = 0.8
    answer_max_bytes: int = 20000
    tutor_min_score: float = 0.025
    tutor_min_covered: int = 2
    tutor_max_passages: int = 4
    tutor_question_max_chars: int = 500

    @property
    def superadmin_email_set(self) -> set[str]:
        """Lower-cased superadmin addresses from SUPERADMIN_EMAILS."""
        return {item.strip().lower() for item in self.superadmin_emails.split(",") if item.strip()}

    @property
    def oidc_allowed_domain_set(self) -> tuple[str, ...]:
        """Lower-cased `hd` allow-list from config; empty means no restriction."""
        return tuple(
            item.strip().lower() for item in self.oidc_allowed_domains.split(",") if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    return Settings()


def validate_runtime_settings(settings: Settings) -> None:
    """Refuse unsafe production settings instead of booting with them.

    Outside local/test the database URL must not be the dev default, the
    session key must be set, and contributor dev mode must be off: each
    would silently weaken a deployed environment.
    """
    from app.errors import ConfigurationError

    if settings.app_env in ("local", "test"):
        return
    if settings.database_url == DEFAULT_DATABASE_URL:
        raise ConfigurationError("DATABASE_URL must not be the default in this APP_ENV")
    if settings.session_secret_key is None or not settings.session_secret_key.get_secret_value():
        raise ConfigurationError("SESSION_SECRET_KEY must be set in this APP_ENV")
    if settings.llm_dev_mode:
        raise ConfigurationError("LLM_DEV_MODE=1 is only allowed when APP_ENV is local or test")
