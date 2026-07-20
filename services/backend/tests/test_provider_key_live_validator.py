"""Unit tests for the BYOK live key probe (``live_validator.py``).

Every test drives the REAL ``ProviderKeyLiveValidator`` through an
injected ``httpx.MockTransport`` — no network, no new dependencies —
and asserts the two invariants that matter: correct tri-state verdicts
per provider quirk, and key material never escaping (never in a URL,
never in a log record).

The suite is synchronous (the backend test suite carries no
pytest-asyncio): each test drives the async ``validate`` through
``asyncio.run`` via ``_probe``.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from backend_app.provider_keys.live_validator import (
    LiveCheckStatus,
    ProviderKeyLiveValidator,
)
from backend_app.provider_keys.store import ProviderName

_KEY = "sk-test-live-probe-key-0000000000009999"


def _probe(handler, provider: ProviderName):
    validator = ProviderKeyLiveValidator(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return asyncio.run(validator.validate(provider=provider, api_key=_KEY))


class TestVerdicts:
    def test_openai_200_is_valid_with_model_ids(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == f"Bearer {_KEY}"
            return httpx.Response(
                200, json={"data": [{"id": "gpt-4o"}, {"id": "o3"}, {"id": ""}]}
            )

        result = _probe(handler, ProviderName.OPENAI)
        assert result.status is LiveCheckStatus.VALID
        assert result.model_ids == ("gpt-4o", "o3")

    def test_anthropic_uses_x_api_key_and_version(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == _KEY
            assert request.headers["anthropic-version"]
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"data": [{"id": "claude-opus-4"}]})

        result = _probe(handler, ProviderName.ANTHROPIC)
        assert result.status is LiveCheckStatus.VALID
        assert result.model_ids == ("claude-opus-4",)

    def test_google_key_rides_header_never_url(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            assert request.headers["x-goog-api-key"] == _KEY
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "models/gemini-2.5-pro"},
                        {"name": "models/gemini-2.5-flash"},
                        {"name": 42},
                    ]
                },
            )

        result = _probe(handler, ProviderName.GOOGLE)
        assert result.status is LiveCheckStatus.VALID
        assert result.model_ids == ("gemini-2.5-pro", "gemini-2.5-flash")
        # The key must never appear in the URL (query-param auth is banned).
        assert _KEY not in seen["url"]

    def test_google_400_means_invalid_key(self) -> None:
        result = _probe(
            lambda request: httpx.Response(
                400, json={"error": {"status": "INVALID_ARGUMENT"}}
            ),
            ProviderName.GOOGLE,
        )
        assert result.status is LiveCheckStatus.INVALID_KEY

    def test_openrouter_valid_has_no_model_ids(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/key"
            return httpx.Response(200, json={"data": {"usage": 0}})

        result = _probe(handler, ProviderName.OPENROUTER)
        assert result.status is LiveCheckStatus.VALID
        assert result.model_ids == ()

    @pytest.mark.parametrize("code", [401, 403])
    def test_auth_rejections_are_invalid_key(self, code: int) -> None:
        result = _probe(
            lambda request: httpx.Response(code, json={}), ProviderName.OPENAI
        )
        assert result.status is LiveCheckStatus.INVALID_KEY

    @pytest.mark.parametrize("code", [429, 500, 503])
    def test_non_verdictive_statuses_are_unreachable(self, code: int) -> None:
        result = _probe(
            lambda request: httpx.Response(code, json={}), ProviderName.OPENAI
        )
        assert result.status is LiveCheckStatus.PROVIDER_UNREACHABLE

    def test_transport_failure_is_unreachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        result = _probe(handler, ProviderName.OPENAI)
        assert result.status is LiveCheckStatus.PROVIDER_UNREACHABLE

    def test_unparseable_200_body_is_still_valid(self) -> None:
        result = _probe(
            lambda request: httpx.Response(200, content=b"not-json"),
            ProviderName.OPENAI,
        )
        assert result.status is LiveCheckStatus.VALID
        assert result.model_ids == ()


class TestKeyNeverEscapes:
    def test_key_never_reaches_logs_even_on_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"connect failed for {request.url}")

        with caplog.at_level(logging.DEBUG):
            _probe(handler, ProviderName.GOOGLE)
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert _KEY not in joined
