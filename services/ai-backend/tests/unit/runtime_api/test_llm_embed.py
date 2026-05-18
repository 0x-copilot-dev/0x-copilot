"""Tests for the P7.5 ``POST /internal/v1/llm/embed`` endpoint.

Verifies:

- Happy path: vectors returned in caller-supplied order, ``dimensions``
  matches the model output, exactly one ``runtime_model_call_usage``
  row is recorded with ``purpose='library_retrieval'`` or
  ``'library_indexing'`` (TU-1 single-tracker invariant).
- Invalid ``purpose`` is rejected (anything outside the two
  Library values) — Pydantic Literal narrows the wire schema.
- Oversized payload (texts count and byte size) is rejected before the
  embed call fires.
- Missing service-token / identity headers return 401 when the
  service token is configured (production-style auth).

Every test patches :func:`build_embeddings_model` in the route
module's namespace so no real provider SDK is touched — the CI guard
(``tools/check_llm_provider_imports.py``) still verifies the import
path lives in ``deep_agent_builder.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http import llm_embed_routes as llm_embed_module


# -- fixtures / fakes --------------------------------------------------------


class _FakeEmbeddings:
    """LangChain-Embeddings stub with a controllable response.

    Mirrors the surface used by the route handler:
    ``aembed_documents(list[str]) -> list[list[float]]``.
    """

    def __init__(self, response: list[list[float]]) -> None:
        self._response = response
        self.calls: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return self._response


class _LlmEmbedClientMixin:
    """Build a TestClient with an injected in-memory usage recorder."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_PARALLEL_TASKS": "1",
            }
        )

    @classmethod
    def _build_client(
        cls,
        *,
        recorder: InMemoryUsageRecorder | None = None,
    ) -> tuple[TestClient, InMemoryUsageRecorder]:
        store = InMemoryRuntimeApiStore()
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=cls._settings())
        used_recorder = recorder or InMemoryUsageRecorder()
        # ``LlmEmbedRoutes._resolve_usage_recorder`` checks this slot first
        # so tests can assert against recorded rows without standing up
        # the Postgres recorder.
        app.state.llm_embed_usage_recorder = used_recorder
        return TestClient(app, raise_server_exceptions=False), used_recorder

    @staticmethod
    def _patch_embeddings(
        monkeypatch: pytest.MonkeyPatch, embeddings: _FakeEmbeddings
    ) -> None:
        # Patching the route module's reference keeps the canonical
        # entry point in ``deep_agent_builder.py`` honoured — the CI
        # guard ensures the route module imports the helper from there.
        monkeypatch.setattr(
            llm_embed_module,
            "build_embeddings_model",
            lambda **_kwargs: embeddings,
        )

    @staticmethod
    def _trusted_headers() -> dict[str, str]:
        # Service token is NOT configured in these tests, so the auth
        # path is lenient on the token itself but still requires the
        # org / user identity headers (``require_identity`` raises 401
        # without them).
        return {
            "x-enterprise-org-id": "org_lib",
            "x-enterprise-user-id": "user_lib",
        }


# -- tests -------------------------------------------------------------------


class TestLlmEmbedHappyPath(_LlmEmbedClientMixin):
    """Vectors round-trip and one usage row is recorded per call."""

    @pytest.mark.parametrize(
        ("wire_purpose", "expected_column"),
        [
            ("library_indexing", "library_indexing"),
            ("library_retrieval", "library_retrieval"),
        ],
    )
    def test_returns_vectors_and_records_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wire_purpose: str,
        expected_column: str,
    ) -> None:
        client, recorder = self._build_client()
        embeddings = _FakeEmbeddings(
            response=[
                [0.1, 0.2, 0.3, 0.4],
                [0.5, 0.6, 0.7, 0.8],
            ]
        )
        self._patch_embeddings(monkeypatch, embeddings)

        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": ["hello world", "library indexing chunk"],
                "model": "openai:text-embedding-3-small",
                "purpose": wire_purpose,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model"] == "openai:text-embedding-3-small"
        assert body["dimensions"] == 4
        assert body["vectors"] == [
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
        ]
        # Exactly one row per call; ``purpose`` lands on the canonical column.
        assert len(recorder.calls) == 1
        row = recorder.calls[0]
        assert row.purpose == expected_column
        assert row.org_id == "org_lib"
        assert row.model_provider == "openai"
        assert row.model_name == "text-embedding-3-small"
        assert row.input_tokens == 2  # one per text item
        assert row.total_tokens == 2

    def test_bare_model_name_defaults_to_openai_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, recorder = self._build_client()
        embeddings = _FakeEmbeddings(response=[[0.1]])
        self._patch_embeddings(monkeypatch, embeddings)

        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": ["one"],
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 200
        assert recorder.calls[0].model_provider == "openai"
        assert recorder.calls[0].model_name == "text-embedding-3-small"


class TestLlmEmbedRejectsInvalidPurpose(_LlmEmbedClientMixin):
    """Anything outside the two Library values is rejected at the wire."""

    @pytest.mark.parametrize(
        "bad_purpose",
        [
            "main",
            "todo_extraction",
            "context_compression",
            "library_other",
            "",
        ],
    )
    def test_returns_400_for_non_library_purpose(
        self, monkeypatch: pytest.MonkeyPatch, bad_purpose: str
    ) -> None:
        client, recorder = self._build_client()
        embeddings = _FakeEmbeddings(response=[[0.1]])
        self._patch_embeddings(monkeypatch, embeddings)

        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": ["one"],
                "model": "text-embedding-3-small",
                "purpose": bad_purpose,
            },
        )
        # ``RuntimeApiErrorMapper`` projects RequestValidationError to 400.
        assert response.status_code == 400
        # No embedding call, no usage row.
        assert embeddings.calls == []
        assert recorder.calls == []


class TestLlmEmbedRejectsOversizedPayload(_LlmEmbedClientMixin):
    """Both ``len(texts)`` and total UTF-8 byte size are capped."""

    def test_rejects_too_many_texts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _recorder = self._build_client()
        embeddings = _FakeEmbeddings(response=[[0.1]])
        self._patch_embeddings(monkeypatch, embeddings)

        too_many = ["chunk"] * 1025  # MAX_TEXTS = 1024
        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": too_many,
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 400
        assert embeddings.calls == []

    def test_rejects_oversized_total_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _recorder = self._build_client()
        embeddings = _FakeEmbeddings(response=[[0.1]])
        self._patch_embeddings(monkeypatch, embeddings)

        # One text just past the 8 MB cap. ASCII so 1 byte per char.
        big_text = "x" * (8 * 1024 * 1024 + 1)
        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": [big_text],
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 400
        assert embeddings.calls == []

    def test_rejects_empty_texts_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _recorder = self._build_client()
        embeddings = _FakeEmbeddings(response=[])
        self._patch_embeddings(monkeypatch, embeddings)

        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": [],
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 400
        assert embeddings.calls == []


class TestLlmEmbedAuth(_LlmEmbedClientMixin):
    """Without identity headers (or with a bad service token) the route is 401."""

    def test_missing_identity_headers_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _recorder = self._build_client()
        # Patch even though we shouldn't reach the embedding call —
        # this guards the test against a regression that bypasses auth.
        embeddings = _FakeEmbeddings(response=[[0.0]])
        self._patch_embeddings(monkeypatch, embeddings)

        response = client.post(
            "/internal/v1/llm/embed",
            json={
                "texts": ["hello"],
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 401
        assert embeddings.calls == []

    def test_invalid_service_token_returns_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Configure the service token so the authenticator's strict
        # branch (token-present-and-must-match) fires.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "expected-token")
        client, _recorder = self._build_client()
        embeddings = _FakeEmbeddings(response=[[0.0]])
        self._patch_embeddings(monkeypatch, embeddings)

        response = client.post(
            "/internal/v1/llm/embed",
            headers={
                "x-enterprise-service-token": "wrong-token",
                **self._trusted_headers(),
            },
            json={
                "texts": ["hello"],
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 401
        assert embeddings.calls == []


class TestLlmEmbedProviderFailure(_LlmEmbedClientMixin):
    """Embedding-call exceptions surface as a 502 with no usage row written."""

    def test_provider_raises_yields_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, recorder = self._build_client()

        class _BoomEmbeddings:
            async def aembed_documents(self, texts: list[str]) -> Any:
                raise RuntimeError("provider exploded")

        monkeypatch.setattr(
            llm_embed_module,
            "build_embeddings_model",
            lambda **_kwargs: _BoomEmbeddings(),
        )
        response = client.post(
            "/internal/v1/llm/embed",
            headers=self._trusted_headers(),
            json={
                "texts": ["hello"],
                "model": "text-embedding-3-small",
                "purpose": "library_indexing",
            },
        )
        assert response.status_code == 502
        assert recorder.calls == []
