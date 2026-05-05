"""A10 — RBAC dependency tests for ai-backend.

Mirrors ``services/backend/tests/identity/test_rbac.py`` for the
ai-backend service. The two implementations share semantics but live
in separate modules (service boundary).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from runtime_api.rbac import (
    RbacMode,
    RequireAnyScope,
    RequireRoles,
    RequireScopes,
    public_route,
)


_SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv("RBAC_MODE", raising=False)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    yield


def _service_headers(
    *,
    org_id: str = "org_a",
    user_id: str = "usr_a",
    permission_scopes: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
) -> dict[str, str]:
    return {
        "x-enterprise-service-token": _SERVICE_TOKEN,
        "x-enterprise-org-id": org_id,
        "x-enterprise-user-id": user_id,
        "x-enterprise-roles": ",".join(roles),
        "x-enterprise-permission-scopes": ",".join(permission_scopes),
        "x-enterprise-connector-scopes": "{}",
    }


def _app(*, scopes_required: tuple[str, ...]) -> FastAPI:
    app = FastAPI()

    @app.get(
        "/probe",
        dependencies=[Depends(RequireScopes(*scopes_required))],
    )
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    return app


class TestEnforceMode:
    def test_missing_scope_403(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client = TestClient(_app(scopes_required=("admin:users",)))
        r = client.get("/probe", headers=_service_headers(permission_scopes=()))
        assert r.status_code == 403

    def test_present_scope_200(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client = TestClient(_app(scopes_required=("runtime:use",)))
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("runtime:use",)),
        )
        assert r.status_code == 200


class TestAuditMode:
    def test_default_passes_through(self) -> None:
        client = TestClient(_app(scopes_required=("admin:users",)))
        r = client.get("/probe", headers=_service_headers(permission_scopes=()))
        assert r.status_code == 200
        assert RbacMode.is_enforce() is False


class TestRequireAnyScope:
    def test_any_one_admits(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get(
            "/probe",
            dependencies=[Depends(RequireAnyScope("audit:read", "admin:users"))],
        )
        def probe() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("admin:users",)),
        )
        assert r.status_code == 200

    def test_none_match_denies(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get(
            "/probe",
            dependencies=[Depends(RequireAnyScope("audit:read", "admin:users"))],
        )
        def probe() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("runtime:use",)),
        )
        assert r.status_code == 403


class TestMfaPending:
    def test_blocks_in_enforce(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client = TestClient(_app(scopes_required=("runtime:use",)))
        r = client.get(
            "/probe",
            headers=_service_headers(
                permission_scopes=("runtime:use", "mfa:pending"),
            ),
        )
        assert r.status_code == 401


class TestRequireRoles:
    def test_any_role_admits(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get(
            "/probe",
            dependencies=[Depends(RequireRoles("admin", "auditor"))],
        )
        def probe() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        r = client.get("/probe", headers=_service_headers(roles=("auditor",)))
        assert r.status_code == 200


class TestPublicRoute:
    def test_public_route_skips_rbac(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get("/health", dependencies=[Depends(public_route())])
        def health() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
