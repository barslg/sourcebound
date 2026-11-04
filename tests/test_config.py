"""config.py is the only env reader; .env.example names every setting."""

from pathlib import Path

import pytest

from app import config


def _setting_names() -> set[str]:
    return set(config.Settings.model_fields)


# Names .env carries for docker-compose.yml (host ports), which config.py never reads.
COMPOSE_ONLY_NAMES = {"WEB_PORT", "API_HOST_PORT"}


def test_env_example_matches_settings() -> None:
    names = {
        line.split("=", 1)[0].strip()
        for line in Path(".env.example").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    } - COMPOSE_ONLY_NAMES
    assert names == {name.upper() for name in _setting_names()}, (
        f"extra in .env.example: {names - {n.upper() for n in _setting_names()}}; "
        f"missing: { {n.upper() for n in _setting_names()} - names }"
    )


def test_compose_only_names_are_used_by_compose_and_unknown_to_settings() -> None:
    compose = Path("docker-compose.yml").read_text()
    for name in COMPOSE_ONLY_NAMES:
        assert f"${{{name}" in compose, f"{name} is not read by docker-compose.yml"
        assert name.lower() not in _setting_names(), f"{name} is a setting; drop it from the list"


def test_env_overrides_defaults(monkeypatch: object) -> None:
    import pytest

    mp = pytest.MonkeyPatch()
    mp.setenv("LOG_LEVEL", "DEBUG")
    try:
        assert config.Settings().log_level == "DEBUG"
    finally:
        mp.undo()
    assert config.Settings().log_level == "INFO"


def test_test_database_url_derivation() -> None:
    from conftest import derive_test_url

    assert (
        derive_test_url("postgresql+asyncpg://muse:muse@localhost:5434/muse")
        == "postgresql+asyncpg://muse:muse@localhost:5434/muse_test"
    )


def test_api_key_is_a_secret() -> None:
    from pydantic import SecretStr

    keyed = config.Settings(_env_file=None, model_api_key="synthetic-key-value")
    dumped = keyed.model_dump()
    assert isinstance(dumped.get("model_api_key"), SecretStr)
    assert "synthetic-key-value" not in str(dumped)
    assert keyed.model_api_key is not None
    assert "synthetic-key-value" not in repr(keyed.model_api_key)
    assert keyed.model_api_key.get_secret_value() == "synthetic-key-value"


def test_only_meta_runs_outside_local_and_test() -> None:
    """Submission rule: the runtime models are Meta's; another provider is for local tests only."""
    from app.config import Settings, validate_runtime_settings
    from app.errors import ConfigurationError

    common = {
        "app_env": "prod",
        "database_url": "postgresql+asyncpg://u:p@db/x",
        "session_secret_key": "s" * 32,
        "model_key_secret": "k" * 32,
    }
    validate_runtime_settings(Settings(model_provider="meta", **common))
    with pytest.raises(ConfigurationError):
        validate_runtime_settings(Settings(model_provider="anthropic", **common))
