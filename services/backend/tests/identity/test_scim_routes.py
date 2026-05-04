"""Backend internal SCIM routes (A7).

Wire-level tests for ``/internal/v1/auth/scim/*`` — token mint, the
SCIM resource surface, and discovery endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleRecord,
)
from backend_app.identity import (
    InMemoryIdentityStore,
    InMemoryScimStore,
    InMemorySessionStore,
    ScimService,
    SessionService,
)


_AUTH_SECRET = "test-auth-secret-32characterslong-12345"
_SERVICE_TOKEN = "test-service-token"


def _service_headers(*, org_id: str = "org_acme") -> dict[str, str]:
    return {
        "x-enterprise-service-token": _SERVICE_TOKEN,
        "x-enterprise-org-id": org_id,
        "x-enterprise-user-id": "usr_admin",
        "x-enterprise-roles": "admin",
        "x-enterprise-permission-scopes": "admin:users",
        "x-enterprise-connector-scopes": "{}",
    }


def _bootstrap(monkeypatch) -> tuple[TestClient, dict[str, Any]]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _AUTH_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")

    identity_store = InMemoryIdentityStore()
    scim_store = InMemoryScimStore()
    sessions = SessionService(
        store=InMemorySessionStore(),
        auth_secret=_AUTH_SECRET,
        dev_mint_allowed=True,
    )
    org = identity_store.create_organization(
        OrganizationRecord(display_name="Acme", slug="acme")
    )
    identity_store.create_role(
        RoleRecord(
            name="employee",
            display_name="Employee",
            is_system=True,
            permission_scopes=("runtime:use",),
        )
    )
    provider = identity_store.create_auth_provider(
        AuthProviderRecord(
            org_id=org.org_id,
            kind=AuthProviderKind.SCIM,
            display_name="Okta SCIM",
            config={},
        )
    )
    service = ScimService(identity_store=identity_store, scim_store=scim_store)
    app = create_app(
        identity_store=identity_store,
        session_service=sessions,
        scim_store=scim_store,
        scim_service=service,
    )
    return TestClient(app), {
        "identity_store": identity_store,
        "scim_store": scim_store,
        "service": service,
        "org": org,
        "provider": provider,
    }


def _mint(client: TestClient, ctx: dict[str, Any]) -> str:
    response = client.post(
        f"/internal/v1/auth/scim/{ctx['provider'].provider_id}/tokens",
        headers=_service_headers(org_id=ctx["org"].org_id),
        json={"org_id": ctx["org"].org_id, "created_by_user_id": "usr_admin"},
    )
    assert response.status_code == 201, response.text
    return response.json()["plaintext"]


def _scim_headers(token: str) -> dict[str, str]:
    return {
        **_service_headers(),
        "x-scim-bearer-token": token,
    }


class TestTokenAdminRoutes:
    def test_mint_then_list_then_revoke(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        plaintext = _mint(client, ctx)
        # The token's prefix should appear in list (plaintext should not).
        listed = client.get(
            f"/internal/v1/auth/scim/{ctx['provider'].provider_id}/tokens",
            headers=_service_headers(org_id=ctx["org"].org_id),
            params={"org_id": ctx["org"].org_id},
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert len(body["tokens"]) == 1
        assert body["tokens"][0]["token_prefix"] == plaintext[:8]
        # Revoke.
        token_id = body["tokens"][0]["token_id"]
        revoke = client.delete(
            f"/internal/v1/auth/scim/{ctx['provider'].provider_id}/tokens/{token_id}",
            headers=_service_headers(org_id=ctx["org"].org_id),
            params={"org_id": ctx["org"].org_id},
        )
        assert revoke.status_code == 204


class TestUserResourceRoutes:
    def test_create_user_returns_scim_user_resource(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        token = _mint(client, ctx)
        response = client.post(
            "/internal/v1/auth/scim/resource/Users",
            headers=_scim_headers(token),
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "alice@acme.example",
                "displayName": "Alice",
                "emails": [{"value": "alice@acme.example", "primary": True}],
                "active": True,
                "externalId": "okta|0001",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["userName"] == "alice@acme.example"
        assert body["externalId"] == "okta|0001"
        assert body["meta"]["resourceType"] == "User"
        assert body["meta"]["location"].endswith(f"/Users/{body['id']}")

    def test_patch_active_false_returns_inactive_user(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        token = _mint(client, ctx)
        created = client.post(
            "/internal/v1/auth/scim/resource/Users",
            headers=_scim_headers(token),
            json={
                "userName": "bob@acme.example",
                "displayName": "Bob",
                "emails": [{"value": "bob@acme.example", "primary": True}],
                "active": True,
            },
        ).json()
        patched = client.patch(
            f"/internal/v1/auth/scim/resource/Users/{created['id']}",
            headers=_scim_headers(token),
            json={
                "Operations": [{"op": "replace", "path": "active", "value": False}],
            },
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["active"] is False

    def test_unknown_user_returns_scim_404(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        token = _mint(client, ctx)
        response = client.get(
            "/internal/v1/auth/scim/resource/Users/usr_unknown",
            headers=_scim_headers(token),
        )
        assert response.status_code == 404
        # FastAPI wraps the SCIM-shaped error body; the inner SCIM error
        # body lives in the 'detail' field.
        body = response.json()
        if "schemas" in body:  # SCIM body surfaced directly
            assert body["status"] == "404"
        else:  # FastAPI wrapping
            inner = body.get("detail")
            assert inner is not None
            assert inner.get("status") == "404"

    def test_invalid_filter_returns_scim_400(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        token = _mint(client, ctx)
        response = client.get(
            "/internal/v1/auth/scim/resource/Users",
            headers=_scim_headers(token),
            params={"filter": 'userName co "x"'},
        )
        assert response.status_code == 400
        body = response.json()
        scim_body = body.get("detail") or body
        assert scim_body.get("scimType") == "invalidFilter"


class TestDiscoveryRoutes:
    def test_service_provider_config(self, monkeypatch) -> None:
        client, _ctx = _bootstrap(monkeypatch)
        response = client.get(
            "/internal/v1/auth/scim/resource/ServiceProviderConfig",
            headers=_service_headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["filter"]["supported"] is True
        assert body["bulk"]["supported"] is False

    def test_resource_types(self, monkeypatch) -> None:
        client, _ctx = _bootstrap(monkeypatch)
        response = client.get(
            "/internal/v1/auth/scim/resource/ResourceTypes",
            headers=_service_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        ids = {entry["id"] for entry in body["Resources"]}
        assert ids == {"User", "Group"}

    def test_unauthenticated_request_rejected(self, monkeypatch) -> None:
        client, _ctx = _bootstrap(monkeypatch)
        response = client.get(
            "/internal/v1/auth/scim/resource/Users",
            headers={
                "x-enterprise-service-token": _SERVICE_TOKEN,
                "x-enterprise-org-id": "-",
                "x-enterprise-user-id": "-",
                "x-enterprise-roles": "service",
                "x-enterprise-permission-scopes": "",
                "x-enterprise-connector-scopes": "{}",
            },
        )
        assert response.status_code == 401
