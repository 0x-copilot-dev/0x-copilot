"""Backend internal SAML routes (A5).

Wire-level tests for ``/internal/v1/auth/saml/*``. The route layer just
maps service exceptions to HTTP status codes — these tests pin those
mappings so a refactor to the service-layer error hierarchy can't
silently change the public surface.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleRecord,
)
from backend_app.identity import (
    FakeSamlVerifier,
    InMemoryIdentityStore,
    InMemorySamlStore,
    InMemorySessionStore,
    ParsedSamlAssertion,
    SamlService,
    SamlSignatureError,
    SessionService,
)


_AUTH_SECRET = "test-auth-secret-32characterslong-12345"
_SERVICE_TOKEN = "test-service-token"


def _service_headers(*, org_id: str = "org_acme") -> dict[str, str]:
    return {
        "x-enterprise-service-token": _SERVICE_TOKEN,
        "x-enterprise-org-id": org_id,
        "x-enterprise-user-id": "-",
        "x-enterprise-roles": "admin",
        "x-enterprise-permission-scopes": "admin:users",
        "x-enterprise-connector-scopes": "{}",
    }


def _bootstrap(monkeypatch) -> tuple[TestClient, dict[str, object]]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _AUTH_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")

    identity_store = InMemoryIdentityStore()
    saml_store = InMemorySamlStore()
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
            kind=AuthProviderKind.SAML,
            display_name="Acme SAML",
            config={
                "idp_entity_id": "https://idp.example/entity",
                "idp_sso_url": "https://idp.example/sso",
                "idp_x509_cert": "MIIDfake==",
                "sp_entity_id": "https://sp.example/sp",
                "sp_acs_url": "https://sp.example/acs",
                "attribute_map": {"email": "email"},
                "auto_provision_user": True,
            },
        )
    )
    verifier = FakeSamlVerifier()
    service = SamlService(
        identity_store=identity_store,
        saml_store=saml_store,
        sessions=sessions,
        verifier=verifier,
    )
    app = create_app(
        identity_store=identity_store,
        session_service=sessions,
        saml_store=saml_store,
        saml_service=service,
        saml_verifier=verifier,
    )
    return TestClient(app), {
        "identity_store": identity_store,
        "saml_store": saml_store,
        "org": org,
        "provider": provider,
        "verifier": verifier,
    }


class TestAuthorizeRoute:
    def test_authorize_returns_sso_url(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        response = client.post(
            f"/internal/v1/auth/saml/{ctx['provider'].provider_id}/authorize",
            headers=_service_headers(org_id=ctx["org"].org_id),
            json={
                "org_id": ctx["org"].org_id,
                "provider_id": ctx["provider"].provider_id,
                "relay_state": "/dashboard",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["request_id"] == "fake-req-1"
        assert "idp.example/sso" in body["sso_url"]


class TestConsumeRoute:
    def test_consume_happy_path_returns_bearer(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        # Pre-authorize to land a pending row.
        authorize = client.post(
            f"/internal/v1/auth/saml/{ctx['provider'].provider_id}/authorize",
            headers=_service_headers(org_id=ctx["org"].org_id),
            json={
                "org_id": ctx["org"].org_id,
                "provider_id": ctx["provider"].provider_id,
            },
        )
        request_id = authorize.json()["request_id"]
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="email",
            assertion_id="route-assertion-1",
            in_response_to=request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"]},
        )
        response = client.post(
            "/internal/v1/auth/saml/consume",
            headers=_service_headers(),
            json={
                "provider_id": ctx["provider"].provider_id,
                "saml_response": "<base64>",
                "relay_state": "/return",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["bearer_token"]
        assert body["relay_state"] == "/return"

    def test_signature_failure_returns_401(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        ctx["verifier"].next_error = SamlSignatureError("bad sig")
        response = client.post(
            "/internal/v1/auth/saml/consume",
            headers=_service_headers(),
            json={
                "provider_id": ctx["provider"].provider_id,
                "saml_response": "<bad>",
            },
        )
        assert response.status_code == 401
        assert "bad sig" in response.text


class TestMetadataRoute:
    def test_metadata_returns_xml(self, monkeypatch) -> None:
        client, ctx = _bootstrap(monkeypatch)
        response = client.get(
            f"/internal/v1/auth/saml/{ctx['provider'].provider_id}/metadata",
            headers=_service_headers(org_id=ctx["org"].org_id),
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/xml")
        assert "https://sp.example/sp" in response.text

    def test_unknown_provider_returns_404(self, monkeypatch) -> None:
        client, _ctx = _bootstrap(monkeypatch)
        response = client.get(
            "/internal/v1/auth/saml/prv_unknown/metadata",
            headers=_service_headers(),
        )
        assert response.status_code == 404
