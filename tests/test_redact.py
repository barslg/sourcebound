"""Secrets never appear in logs or errors; comparisons use digests."""

from app.redact import REDACTED, constant_time_equal, redact


def test_known_secrets_are_masked() -> None:
    secret = "s3cr3t-value-xyz"
    assert secret not in redact(f"key={secret} done", secrets=[secret])
    assert REDACTED in redact(f"key={secret} done", secrets=[secret])


def test_key_like_patterns_are_masked() -> None:
    text = "Authorization: Bearer abcdefghijklmnop"
    assert "abcdefghijklmnop" not in redact(text)
    assert "LLM_SYNTHETICKEY0123456789abcdefgHIJK" not in redact(text)


def test_digest_comparison() -> None:
    assert constant_time_equal("abc", "abc")
    assert not constant_time_equal("abc", "abd")


def test_log_pipeline_redacts_nested_strings() -> None:
    from app.logging import _redact_processor

    event = _redact_processor(
        None,  # type: ignore[arg-type]
        "",
        {
            "event": "call",
            "auth": "Bearer abcdefghijklmnop",
            "nested": {"key": "sk-1234567890abcdef"},
            "items": ["LLM_SYNTHETICKEY0123456789", 42, None],
        },
    )
    assert event["auth"] == f"Bearer {REDACTED}"
    assert event["nested"] == {"key": REDACTED}
    assert event["items"][0] == REDACTED
    dumped = str(event)
    assert "abcdefghijklmnop" not in dumped
    assert "sk-1234567890abcdef" not in dumped
    assert "LLM_SYNTHETICKEY0123456789" not in dumped
    assert event["items"][1:] == [42, None]
    assert event["event"] == "call"
