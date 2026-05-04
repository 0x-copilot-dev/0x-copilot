"""Tests for the health/readiness route registration helper."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.routes.health import CheckResult, register_health_routes


class TestHealthEndpoint:
    def test_liveness_is_unconditional_200(self) -> None:
        app = FastAPI()
        register_health_routes(app)
        client = TestClient(app)
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    def test_liveness_does_not_invoke_checkers(self) -> None:
        invoked: list[str] = []

        def checker() -> CheckResult:
            invoked.append("called")
            return CheckResult(name="db", ok=True)

        app = FastAPI()
        register_health_routes(app, readiness_checkers=[checker])
        client = TestClient(app)
        client.get("/healthz")
        assert invoked == []


class TestReadinessEndpoint:
    def test_no_checkers_reports_ready(self) -> None:
        app = FastAPI()
        register_health_routes(app)
        client = TestClient(app)
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "checks": []}

    def test_passing_checkers(self) -> None:
        app = FastAPI()
        register_health_routes(
            app,
            readiness_checkers=[
                lambda: CheckResult(name="db", ok=True),
                lambda: CheckResult(name="otel", ok=True),
            ],
        )
        client = TestClient(app)
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert {check["name"] for check in body["checks"]} == {"db", "otel"}

    def test_failing_checker_returns_503(self) -> None:
        app = FastAPI()
        register_health_routes(
            app,
            readiness_checkers=[
                lambda: CheckResult(name="db", ok=True),
                lambda: CheckResult(
                    name="otel", ok=False, detail="collector unreachable"
                ),
            ],
        )
        client = TestClient(app)
        response = client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        otel_check = next(c for c in body["checks"] if c["name"] == "otel")
        assert otel_check["ok"] is False
        assert otel_check["detail"] == "collector unreachable"
