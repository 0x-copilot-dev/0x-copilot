from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_runtime.harness_quality.diagnostics import EvaluationDiagnosticsService
from agent_runtime.harness_quality.evaluation_contracts import EvaluationScope
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from runtime_api.http.evaluation_diagnostics import (
    LocalEvaluationDiagnosticsRouter,
)


_TOKEN = "local-diagnostics-token"


def _app() -> FastAPI:
    app = FastAPI()
    app.state.local_evaluation_diagnostics_service = EvaluationDiagnosticsService(
        repository=InMemoryEvaluationRepository(),
        scope=EvaluationScope(profile_id="configured-local-profile"),
    )
    app.include_router(LocalEvaluationDiagnosticsRouter.create_router())
    return app


def test_loopback_service_token_reads_configured_scope_only(monkeypatch) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    response = TestClient(
        _app(),
        client=("127.0.0.1", 51_000),
    ).get(
        "/internal/dev/evaluation/diagnostics/snapshot",
        headers={"x-enterprise-service-token": _TOKEN},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["scope_digest"]) == 64
    assert "profile_id" not in payload
    assert "project_id" not in payload


def test_non_loopback_diagnostics_peer_is_forbidden(monkeypatch) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    response = TestClient(
        _app(),
        client=("203.0.113.9", 51_000),
    ).get(
        "/internal/dev/evaluation/diagnostics/snapshot",
        headers={"x-enterprise-service-token": _TOKEN},
    )

    assert response.status_code == 403


def test_diagnostics_require_service_token(monkeypatch) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    response = TestClient(
        _app(),
        client=("127.0.0.1", 51_000),
    ).get("/internal/dev/evaluation/diagnostics/snapshot")

    assert response.status_code == 401
