"""A10 — RBAC dependency tests for backend.

Pin the contract:

  - audit-mode passes through; enforce-mode 403s on missing scopes;
  - mfa:pending sessions never pass an RBAC check (except via
    ``public_route`` which doesn't run RBAC at all);
  - ``RequireRoles`` is ANY-of, ``RequireScopes`` is ALL-of.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend_app.auth import ScopedIdentity
from backend_app.identity.rbac import (
    RbacMode,
    RequireRoles,
    RequireScopes,
    public_route,
)


_SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv("RBAC_MODE", raising=False)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
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


class TestAuditMode:
    def test_missing_scope_passes_in_audit_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "audit")
        client = TestClient(_app(scopes_required=("admin:users",)))
        r = client.get("/probe", headers=_service_headers(permission_scopes=()))
        assert r.status_code == 200, r.text

    def test_default_mode_is_audit(self) -> None:
        # No RBAC_MODE env set.
        assert RbacMode.current() == "audit"
        assert RbacMode.is_enforce() is False


class TestEnforceMode:
    def test_missing_scope_returns_403(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client = TestClient(_app(scopes_required=("admin:users",)))
        r = client.get("/probe", headers=_service_headers(permission_scopes=()))
        assert r.status_code == 403, r.text

    def test_present_scope_returns_200(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client = TestClient(_app(scopes_required=("admin:users",)))
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("admin:users",)),
        )
        assert r.status_code == 200, r.text

    def test_partial_scope_match_returns_403(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        # Route requires both scopes; caller has only one.
        client = TestClient(_app(scopes_required=("admin:users", "admin:idp")))
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("admin:users",)),
        )
        assert r.status_code == 403


class TestMfaPending:
    def test_mfa_pending_blocks_in_enforce_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client = TestClient(_app(scopes_required=("runtime:use",)))
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("runtime:use", "mfa:pending")),
        )
        # mfa:pending takes precedence over presence of the required scope.
        assert r.status_code == 401
        assert "MFA" in r.text

    def test_mfa_pending_passes_in_audit_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "audit")
        client = TestClient(_app(scopes_required=("runtime:use",)))
        r = client.get(
            "/probe",
            headers=_service_headers(permission_scopes=("runtime:use", "mfa:pending")),
        )
        # Audit mode: deny is logged, pass through.
        assert r.status_code == 200


class TestRequireRoles:
    def test_any_of_roles_admits(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get("/probe", dependencies=[Depends(RequireRoles("admin", "auditor"))])
        def probe() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        r = client.get("/probe", headers=_service_headers(roles=("auditor",)))
        assert r.status_code == 200

    def test_no_roles_match_denies(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get("/probe", dependencies=[Depends(RequireRoles("admin"))])
        def probe() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        r = client.get("/probe", headers=_service_headers(roles=("employee",)))
        assert r.status_code == 403


class TestPublicRoute:
    def test_public_route_skips_rbac(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        app = FastAPI()

        @app.get("/health", dependencies=[Depends(public_route())])
        def health() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)
        # No service token, no scopes — public_route admits.
        r = client.get("/health")
        assert r.status_code == 200


class TestRbacModeMisconfig:
    def test_unknown_mode_falls_back_to_audit(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "junk")
        # Misconfig must NOT silently switch to enforce (would lock
        # everyone out). Falls back to audit so the deploy stays usable.
        assert RbacMode.current() == "audit"
        assert RbacMode.is_enforce() is False


class TestScopedIdentityHeaders:
    def test_csv_headers_parsed_into_tuple(self) -> None:
        from backend_app.auth import BackendServiceAuthenticator
        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [
                (b"x-enterprise-service-token", _SERVICE_TOKEN.encode()),
                (b"x-enterprise-org-id", b"org_a"),
                (b"x-enterprise-user-id", b"usr_a"),
                (b"x-enterprise-roles", b"admin,auditor"),
                (b"x-enterprise-permission-scopes", b"admin:users, admin:idp ,"),
            ],
            "query_string": b"",
        }
        request = Request(scope)
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id="-", user_id="-"
        )
        assert isinstance(identity, ScopedIdentity)
        assert identity.roles == ("admin", "auditor")
        # Whitespace stripped, empty entries dropped.
        assert identity.permission_scopes == ("admin:users", "admin:idp")
