"""One exception hierarchy, one exception-to-HTTP table."""

from fastapi.testclient import TestClient

from app.errors import (
    AppError,
    AuthError,
    ConfigurationError,
    ConflictError,
    ForbiddenError,
    LLMError,
    NotFoundError,
    ValidationError,
    http_status_for,
)
from app.main import create_app


def test_every_error_maps_to_its_status() -> None:
    assert http_status_for(ValidationError("bad")) == 400
    assert http_status_for(AuthError("who")) == 401
    assert http_status_for(ForbiddenError("no")) == 403
    assert http_status_for(NotFoundError("gone")) == 404
    assert http_status_for(ConflictError("clash")) == 409
    assert http_status_for(LLMError("down")) == 502
    assert http_status_for(ConfigurationError("no key")) == 500
    assert http_status_for(AppError("plain")) == 500
    assert http_status_for(ValueError("foreign")) == 500
    assert ConfigurationError("no key").error_code == "misconfigured"


def test_handler_renders_json_without_leaking() -> None:
    # Startup RBAC sync targets the process database; this test owns none.
    app = create_app(sync_on_startup=False)

    @app.get("/boom")
    def boom() -> None:
        raise ForbiddenError("you lack `section.plan`")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "forbidden"
    assert "section.plan" in body["message"]


def test_a_plain_sentence_survives_a_request_language() -> None:
    """A browser always sends Accept-Language; the guard sentences written for people stay."""
    import asyncio
    import json

    from app.errors import UnprocessableError, ValidationError, _render

    class Req:
        headers = {"accept-language": "en-US,en;q=0.9"}
        query_params: dict[str, str] = {}

    def body(exc: Exception) -> dict[str, str]:
        resp = asyncio.new_event_loop().run_until_complete(_render(Req(), exc))  # type: ignore[arg-type]
        return json.loads(bytes(resp.body))  # type: ignore[attr-defined,no-any-return]

    assert body(UnprocessableError("The plan is empty. Build the plan first"))["message"] == (
        "The plan is empty. Build the plan first"
    )
    assert body(ValidationError("db column x.y missing"))["message"] == "The request is invalid."
