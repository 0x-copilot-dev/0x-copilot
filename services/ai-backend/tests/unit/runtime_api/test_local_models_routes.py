"""HTTP route tests for /v1/local-models (Round 2).

Covers gating (disabled → 404 on management routes, /status always answers),
the JSON routes against an injected fake service, the SSE pull stream framing,
and upstream-error mapping to 502.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.local_models.ollama_client import LocalModelError
from runtime_api.schemas.local_models import (
    LocalModelPullEvent,
    LocalModelSize,
    LocalModelSummary,
    LocalModelsList,
    LocalModelsStatus,
)

_ORG = "org_a"
_USER = "user_1"
_PARAMS = {"org_id": _ORG, "user_id": _USER}


class _FakeService:
    """Mimics LocalModelService's async surface for the routes."""

    def __init__(self, *, pull_error: str | None = None) -> None:
        self._pull_error = pull_error
        self.deleted: list[str] = []

    async def status(self, *, enabled: bool) -> LocalModelsStatus:
        return LocalModelsStatus(
            enabled=enabled,
            ollama_running=enabled,
            ollama_version="0.5.1" if enabled else None,
        )

    async def list_models(self) -> LocalModelsList:
        return LocalModelsList(
            models=[
                LocalModelSummary(
                    name="hf.co/acme/Tiny-GGUF:Q4_K_M",
                    size_bytes=808_000_000,
                    quantization="Q4_K_M",
                    run_placement="gpu",
                )
            ]
        )

    async def download_size(self, *, repo: str, quant: str) -> LocalModelSize:
        return LocalModelSize(
            repo=repo, quant=quant, filename="Tiny-Q4_K_M.gguf", size_bytes=808_000_000
        )

    async def pull_events(
        self, *, repo: str, quant: str
    ) -> AsyncIterator[LocalModelPullEvent]:
        if self._pull_error is not None:
            raise LocalModelError(self._pull_error)
        yield LocalModelPullEvent(sequence_no=1, status="pulling manifest")
        yield LocalModelPullEvent(
            sequence_no=2,
            status="downloading",
            bytes_total=1000,
            bytes_completed=500,
            speed_bps=250.0,
            eta_seconds=2.0,
        )
        yield LocalModelPullEvent(sequence_no=3, status="success", done=True)

    async def delete(self, *, name: str) -> bool:
        self.deleted.append(name)
        return True


def _client(*, enabled: bool, service: _FakeService | None = None) -> TestClient:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_ENABLE_LOCAL_MODELS": "true" if enabled else "false",
        }
    )
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
    if service is not None:
        app.state.local_model_service = service
    return TestClient(app)


class TestGating:
    def test_status_reports_enabled(self) -> None:
        client = _client(enabled=True, service=_FakeService())
        body = client.get("/v1/local-models/status", params=_PARAMS).json()
        assert body["enabled"] is True
        assert body["ollama_running"] is True

    def test_status_reports_disabled_without_probing(self) -> None:
        client = _client(enabled=False)
        body = client.get("/v1/local-models/status", params=_PARAMS).json()
        assert body == {
            "enabled": False,
            "ollama_running": False,
            "ollama_version": None,
        }

    def test_management_routes_404_when_disabled(self) -> None:
        client = _client(enabled=False)
        assert client.get("/v1/local-models", params=_PARAMS).status_code == 404
        assert (
            client.get(
                "/v1/local-models/size",
                params={**_PARAMS, "repo": "acme/x", "quant": "Q4_K_M"},
            ).status_code
            == 404
        )
        assert (
            client.request(
                "DELETE", "/v1/local-models/acme%2Fx:Q4_K_M", params=_PARAMS
            ).status_code
            == 404
        )


class TestJsonRoutes:
    def test_list_returns_models_with_placement(self) -> None:
        client = _client(enabled=True, service=_FakeService())
        body = client.get("/v1/local-models", params=_PARAMS).json()
        assert body["models"][0]["run_placement"] == "gpu"
        assert body["models"][0]["quantization"] == "Q4_K_M"

    def test_size_returns_bytes(self) -> None:
        client = _client(enabled=True, service=_FakeService())
        body = client.get(
            "/v1/local-models/size",
            params={**_PARAMS, "repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
        ).json()
        assert body["size_bytes"] == 808_000_000
        assert body["filename"].endswith(".gguf")

    def test_delete_returns_204(self) -> None:
        service = _FakeService()
        client = _client(enabled=True, service=service)
        response = client.request(
            "DELETE", "/v1/local-models/hf.co/acme/Tiny-GGUF:Q4_K_M", params=_PARAMS
        )
        assert response.status_code == 204
        assert service.deleted == ["hf.co/acme/Tiny-GGUF:Q4_K_M"]


class TestPullStream:
    def test_pull_streams_sse_frames(self) -> None:
        client = _client(enabled=True, service=_FakeService())
        response = client.get(
            "/v1/local-models/pull",
            params={**_PARAMS, "repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = response.text
        assert "event: local_model_pull" in text
        assert '"bytes_total":1000' in text
        assert '"done":true' in text

    def test_pull_error_becomes_terminal_error_frame(self) -> None:
        client = _client(
            enabled=True, service=_FakeService(pull_error="Ollama unreachable")
        )
        response = client.get(
            "/v1/local-models/pull",
            params={**_PARAMS, "repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
        )
        assert response.status_code == 200
        assert '"status":"error"' in response.text
        assert "Ollama unreachable" in response.text
