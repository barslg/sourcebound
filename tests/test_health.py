"""Health proof and lifespan wiring."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok() -> None:
    # Startup RBAC sync targets the process database; this test owns none.
    with TestClient(create_app(sync_on_startup=False)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_validates_llm_schemas() -> None:
    # Entering the client context runs the lifespan, which must call the
    # registry validator; a violation must prevent startup.
    import pytest

    from app.llm import prompts

    mp = pytest.MonkeyPatch()
    called = {"count": 0}

    def _fail() -> None:
        called["count"] += 1
        raise ValueError("bad schema")

    mp.setattr(prompts, "validate_registered_schemas", _fail)
    try:
        with pytest.raises(ValueError, match="bad schema"):
            with TestClient(create_app()):
                pass
    finally:
        mp.undo()
    assert called["count"] == 1
