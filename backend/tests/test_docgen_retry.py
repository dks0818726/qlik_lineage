from types import SimpleNamespace

import httpx
import pytest
from litellm import RateLimitError

from app.docs import generator


def _rate_limit(headers: dict[str, str] | None = None) -> RateLimitError:
    response = httpx.Response(
        429,
        headers=headers,
        request=httpx.Request("POST", "https://example.test/completions"),
    )
    return RateLimitError(
        "too many requests",
        llm_provider="azure",
        model="test-deployment",
        response=response,
    )


def _response(content: str = "generated") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_docgen_retries_429_using_provider_delay(monkeypatch):
    calls = iter([_rate_limit({"x-ms-retry-after-ms": "1500"}), _response()])
    sleeps: list[float] = []

    def fake_completion(**kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        assert kwargs["num_retries"] == 0
        return result

    monkeypatch.setattr(generator, "completion", fake_completion)
    monkeypatch.setattr(generator.time, "sleep", sleeps.append)
    monkeypatch.setattr(generator.settings, "docgen_max_retries", 2)

    result = generator.DocumentationGenerator(None, None)._call_model(
        SimpleNamespace(text="evidence"), "azure/test-deployment"
    )

    assert result == "generated"
    assert sleeps == [1.5]


def test_docgen_uses_capped_exponential_backoff_with_jitter(monkeypatch):
    monkeypatch.setattr(generator.settings, "docgen_retry_base_seconds", 2.0)
    monkeypatch.setattr(generator.settings, "docgen_retry_max_seconds", 5.0)
    monkeypatch.setattr(generator.random, "uniform", lambda low, high: high)
    service = generator.DocumentationGenerator(None, None)
    exc = _rate_limit()

    assert service._retry_delay(exc, 1) == 2.0
    assert service._retry_delay(exc, 2) == 4.0
    assert service._retry_delay(exc, 3) == 5.0


def test_docgen_stops_after_configured_retries(monkeypatch):
    attempts = 0
    sleeps: list[float] = []

    def always_rate_limited(**kwargs):
        nonlocal attempts
        attempts += 1
        raise _rate_limit({"retry-after": "0"})

    monkeypatch.setattr(generator, "completion", always_rate_limited)
    monkeypatch.setattr(generator.time, "sleep", sleeps.append)
    monkeypatch.setattr(generator.settings, "docgen_max_retries", 2)

    with pytest.raises(RateLimitError):
        generator.DocumentationGenerator(None, None)._call_model(
            SimpleNamespace(text="evidence"), "azure/test-deployment"
        )

    assert attempts == 3
    assert sleeps == [0.0, 0.0]
