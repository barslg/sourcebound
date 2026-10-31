"""Retry policy by status: what retries, what backs off, what goes background."""

from app.llm.retry import backoff_ms, should_retry, use_background


def test_retryable_statuses() -> None:
    assert should_retry(429, attempt=1)
    assert should_retry(500, attempt=1)
    assert should_retry(503, attempt=1)


def test_client_errors_never_retry() -> None:
    for status in (400, 401, 402, 403, 404, 413):
        assert not should_retry(status, attempt=1), status


def test_retries_are_bounded() -> None:
    assert should_retry(503, attempt=3)
    assert not should_retry(503, attempt=4)


def test_backoff_grows_with_jitter_bounds() -> None:
    first = backoff_ms(attempt=1, retry_after_ms=None)
    second = backoff_ms(attempt=2, retry_after_ms=None)
    assert 500 <= first <= 1000
    assert 1000 <= second <= 2000


def test_retry_after_is_honoured() -> None:
    assert backoff_ms(attempt=1, retry_after_ms=30_000) >= 30_000


def test_gateway_timeout_goes_background() -> None:
    assert use_background(504)
    assert not use_background(503)
    assert not use_background(400)


def test_http_date_retry_after_parses() -> None:
    import httpx2
    from openai import APIStatusError

    from app.llm.retry import retry_after_ms

    request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
    future = httpx2.Response(
        429,
        headers={"retry-after": "Wed, 01 Jan 2031 00:00:05 GMT"},
        request=request,
    )
    error = APIStatusError("busy", response=future, body=None)
    hint = retry_after_ms(error)
    assert hint is not None and hint > 0

    numeric = httpx2.Response(429, headers={"retry-after": "2"}, request=request)
    assert retry_after_ms(APIStatusError("busy", response=numeric, body=None)) == 2000
