"""HTTP route tests for /v1/local-models (Round 2 + PRD-P8).

Covers gating (disabled → 404 on management routes, /status always answers),
the JSON routes against an injected fake service, the SSE pull stream framing,
upstream-error mapping to 502 with a kind-derived ``retryable``, and the new
``POST /runtime/start`` route with its second (manage-runtime) gate.
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
    LocalModelErrorKind,
    LocalModelPullEvent,
    LocalModelSize,
    LocalModelSummary,
    LocalModelsList,
    LocalModelsStatus,
    LocalRuntimeState,
)

_ORG = "org_a"
_USER = "user_1"
_PARAMS = {"org_id": _ORG, "user_id": _USER}
_DAEMON_SECRET = "internal ollama detail: /Users/p/.ollama/models"


def _raise_audit(**_kwargs: object) -> None:
    """Audit sink that always fails — the route must swallow it."""

    raise RuntimeError("audit store down")


class _FakeService:
    """Mimics LocalModelService's async surface for the routes."""

    def __init__(
        self,
        *,
        pull_error: str | None = None,
        pull_error_kind: LocalModelErrorKind = LocalModelErrorKind.TERMINAL,
        start_error: LocalModelError | None = None,
    ) -> None:
        self._pull_error = pull_error
        self._pull_error_kind = pull_error_kind
        self._start_error = start_error
        self.deleted: list[str] = []
        self.start_calls = 0

    async def status(self, *, enabled: bool) -> LocalModelsStatus:
        return LocalModelsStatus(
            enabled=enabled,
            ollama_running=enabled,
            ollama_version="0.5.1" if enabled else None,
            runtime_state=LocalRuntimeState.RUNNING
            if enabled
            else LocalRuntimeState.UNKNOWN,
            runtime_managed=enabled,
        )

    async def start_runtime(self) -> LocalModelsStatus:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error
        return LocalModelsStatus(
            enabled=True,
            ollama_running=True,
            ollama_version="0.5.1",
            runtime_state=LocalRuntimeState.RUNNING,
            runtime_managed=True,
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
            raise LocalModelError(self._pull_error, kind=self._pull_error_kind)
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


def _client(
    *,
    enabled: bool,
    service: _FakeService | None = None,
    manage_runtime: bool = False,
    audit_rows: list[dict] | None = None,
) -> TestClient:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_ENABLE_LOCAL_MODELS": "true" if enabled else "false",
            "RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME": "true"
            if manage_runtime
            else "false",
        }
    )
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
    if service is not None:
        app.state.local_model_service = service
    if audit_rows is not None:

        def append(*, org_id: str, event_type: str, data: dict) -> None:
            audit_rows.append({"org_id": org_id, "event_type": event_type, **data})

        app.state.runtime_audit_appender = append
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
            "runtime_state": "unknown",
            "runtime_managed": False,
        }

    def test_status_keeps_round_two_fields_alongside_new_ones(self) -> None:
        client = _client(enabled=True, service=_FakeService(), manage_runtime=True)
        body = client.get("/v1/local-models/status", params=_PARAMS).json()
        # Five client call sites still read these two — they must never go away.
        assert body["ollama_running"] is True
        assert body["ollama_version"] == "0.5.1"
        assert body["runtime_state"] == "running"
        assert body["runtime_managed"] is True

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

    def test_error_frame_carries_error_kind(self) -> None:
        client = _client(
            enabled=True,
            service=_FakeService(
                pull_error="Ollama pull stream failed",
                pull_error_kind=LocalModelErrorKind.RUNTIME_UNREACHABLE,
            ),
        )
        response = client.get(
            "/v1/local-models/pull",
            params={**_PARAMS, "repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
        )
        assert '"error_kind":"runtime_unreachable"' in response.text

    def test_transient_error_frame_carries_transient_kind(self) -> None:
        client = _client(
            enabled=True,
            service=_FakeService(
                pull_error="Ollama pull stream failed",
                pull_error_kind=LocalModelErrorKind.TRANSIENT,
            ),
        )
        response = client.get(
            "/v1/local-models/pull",
            params={**_PARAMS, "repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
        )
        assert '"error_kind":"transient"' in response.text

    def test_progress_frames_carry_null_error_kind(self) -> None:
        client = _client(enabled=True, service=_FakeService())
        response = client.get(
            "/v1/local-models/pull",
            params={**_PARAMS, "repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
        )
        assert '"error_kind":null' in response.text


class TestRuntimeStartRoute:
    """PRD-P8 §4.3."""

    _PATH = "/v1/local-models/runtime/start"

    def test_404_when_feature_disabled(self) -> None:
        service = _FakeService()
        client = _client(enabled=False, service=service, manage_runtime=True)
        response = client.post(self._PATH, params=_PARAMS)
        assert response.status_code == 404
        assert response.json()["code"] == "configuration_error"
        assert service.start_calls == 0

    def test_404_when_runtime_management_disabled(self) -> None:
        service = _FakeService()
        client = _client(enabled=True, service=service, manage_runtime=False)
        response = client.post(self._PATH, params=_PARAMS)
        assert response.status_code == 404
        assert response.json()["code"] == "configuration_error"
        assert response.json()["retryable"] is False
        # Gated before the service is ever asked to touch the host.
        assert service.start_calls == 0

    def test_starts_and_returns_status(self) -> None:
        service = _FakeService()
        client = _client(enabled=True, service=service, manage_runtime=True)
        response = client.post(self._PATH, params=_PARAMS)
        assert response.status_code == 200
        body = response.json()
        assert body["runtime_state"] == "running"
        assert body["runtime_managed"] is True
        assert body["ollama_running"] is True
        assert service.start_calls == 1

    def test_repeat_calls_are_idempotent_at_the_route(self) -> None:
        service = _FakeService()
        client = _client(enabled=True, service=service, manage_runtime=True)
        first = client.post(self._PATH, params=_PARAMS)
        second = client.post(self._PATH, params=_PARAMS)
        assert first.json() == second.json()
        assert service.start_calls == 2

    def test_spawn_failure_is_502_not_500(self) -> None:
        client = _client(
            enabled=True,
            service=_FakeService(
                start_error=LocalModelError(
                    "Could not start the local model runtime.",
                    kind=LocalModelErrorKind.TERMINAL,
                )
            ),
            manage_runtime=True,
        )
        response = client.post(self._PATH, params=_PARAMS)
        assert response.status_code == 502
        body = response.json()
        assert body["code"] == "external_service_error"
        assert body["safe_message"] == "Could not start the local model runtime."
        # Terminal failures are not advertised as retryable.
        assert body["retryable"] is False

    def test_unreachable_failure_is_retryable(self) -> None:
        client = _client(
            enabled=True,
            service=_FakeService(
                start_error=LocalModelError(
                    "Ollama is not installed on this machine.",
                    kind=LocalModelErrorKind.RUNTIME_UNREACHABLE,
                )
            ),
            manage_runtime=True,
        )
        response = client.post(self._PATH, params=_PARAMS)
        assert response.status_code == 502
        assert response.json()["retryable"] is True

    def test_emits_an_audit_event(self) -> None:
        rows: list[dict] = []
        client = _client(
            enabled=True,
            service=_FakeService(),
            manage_runtime=True,
            audit_rows=rows,
        )
        assert client.post(self._PATH, params=_PARAMS).status_code == 200
        # The same appender also receives RBAC rows; select ours.
        started = [
            row for row in rows if row["event_type"] == "local_models.runtime_start"
        ]
        assert len(started) == 1
        assert started[0]["resource_type"] == "local_model_runtime"
        assert started[0]["outcome"] == "started"
        assert started[0]["metadata"]["runtime_state"] == "running"

    def test_audit_failure_never_fails_the_request(self) -> None:
        client = _client(enabled=True, service=_FakeService(), manage_runtime=True)
        client.app.state.runtime_audit_appender = _raise_audit  # type: ignore[attr-defined]
        assert client.post(self._PATH, params=_PARAMS).status_code == 200

    def test_no_daemon_text_leaks_into_the_public_message(self) -> None:
        # The typed error's *cause* carries the host detail, exactly as a real
        # spawn/transport failure does. The response must render only
        # ``public_message`` — never ``str(exc)`` and never the cause chain.
        cause = PermissionError(_DAEMON_SECRET)
        start_error = LocalModelError("Could not start the local model runtime.")
        start_error.__cause__ = cause
        client = _client(
            enabled=True,
            service=_FakeService(start_error=start_error),
            manage_runtime=True,
        )
        response = client.post(self._PATH, params=_PARAMS)
        assert response.status_code == 502
        assert _DAEMON_SECRET not in response.text
        assert (
            response.json()["safe_message"]
            == "Could not start the local model runtime."
        )
