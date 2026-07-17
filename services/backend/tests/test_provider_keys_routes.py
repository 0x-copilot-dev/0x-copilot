"""Tests for the Phase 2 BYOK ``/v1/settings/provider-keys`` routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.provider_keys.store import InMemoryProviderApiKeyStore
from backend_app.token_vault import LocalTokenVault


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"

# Deliberately fake keys — long enough to pass the plausibility floor,
# obviously not real credentials.
_OPENAI_KEY = "sk-test-openai-0000000000000000001234"
_ANTHROPIC_KEY = "sk-ant-test-000000000000000000000-abcd"
_GOOGLE_KEY = "AIzaTest-00000000000000000000005678"


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            email_verified_at=datetime(2026, 1, 12, 9, 1, 24, tzinfo=timezone.utc),
        )
    )
    return store


def _client(
    *,
    identity_store: InMemoryIdentityStore | None = None,
    provider_store: InMemoryProviderApiKeyStore | None = None,
    vault: LocalTokenVault | None = None,
) -> tuple[TestClient, InMemoryIdentityStore, InMemoryProviderApiKeyStore]:
    identity = identity_store or _seeded_identity()
    provider_keys = provider_store or InMemoryProviderApiKeyStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        provider_api_keys_store=provider_keys,
        token_vault=vault or LocalTokenVault(secret=_VAULT_SECRET),
    )
    return TestClient(app), identity, provider_keys


def _params(user_id: str = "usr_sarah", org_id: str = "org_acme") -> dict[str, str]:
    return {"org_id": org_id, "user_id": user_id}


class TestListProviderKeys:
    def test_empty_listing(self) -> None:
        client, _i, _p = _client()
        response = client.get("/v1/settings/provider-keys", params=_params())
        assert response.status_code == 200, response.text
        assert response.json() == {"keys": []}

    def test_listing_is_hint_only(self) -> None:
        client, _i, _p = _client()
        put = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": _OPENAI_KEY},
        )
        assert put.status_code == 200, put.text
        response = client.get("/v1/settings/provider-keys", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["keys"]) == 1
        entry = body["keys"][0]
        assert set(entry.keys()) == {"provider", "key_hint", "updated_at"}
        assert entry["provider"] == "openai"
        assert entry["key_hint"] == "…1234"
        assert entry["updated_at"]
        # Defense-in-depth: plaintext never appears anywhere in the body.
        assert _OPENAI_KEY not in response.text

    def test_listing_is_user_scoped(self) -> None:
        client, _i, _p = _client()
        client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": _OPENAI_KEY},
        )
        other = client.get(
            "/v1/settings/provider-keys", params=_params(user_id="usr_marcus")
        )
        assert other.status_code == 200
        assert other.json() == {"keys": []}


class TestPutProviderKey:
    def test_put_round_trip_and_encryption_at_rest(self) -> None:
        vault = LocalTokenVault(secret=_VAULT_SECRET)
        client, _i, provider_store = _client(vault=vault)
        response = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": _OPENAI_KEY},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"] == "openai"
        assert body["key_hint"] == "…1234"
        assert body["updated_at"]
        # The response never carries plaintext.
        assert _OPENAI_KEY not in response.text
        # Encryption at rest: ciphertext differs from (and doesn't
        # contain) the plaintext, and round-trips through the vault.
        row = provider_store.rows[("org_acme", "usr_sarah", "openai")]
        assert row.encrypted_key != _OPENAI_KEY
        assert _OPENAI_KEY not in row.encrypted_key
        assert vault.decrypt(row.encrypted_key) == _OPENAI_KEY

    def test_put_replaces_existing_key(self) -> None:
        client, _i, _p = _client()
        client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": _OPENAI_KEY},
        )
        replaced = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": "sk-test-openai-0000000000000000009999"},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["key_hint"] == "…9999"
        listing = client.get("/v1/settings/provider-keys", params=_params()).json()
        assert len(listing["keys"]) == 1
        assert listing["keys"][0]["key_hint"] == "…9999"

    def test_unknown_provider_is_422(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/v1/settings/provider-keys/mistral",
            params=_params(),
            json={"api_key": _OPENAI_KEY},
        )
        assert response.status_code == 422

    def test_missing_api_key_field_is_422(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai", params=_params(), json={}
        )
        assert response.status_code == 422

    def test_wrong_provider_prefix_is_400(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": _ANTHROPIC_KEY},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "api_key_provider_mismatch"

    def test_too_short_key_is_400(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": "sk-short"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "api_key_too_short"

    def test_unknown_but_plausible_key_is_accepted(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": "byok-plausible-key-000000000000-zzzz"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["key_hint"] == "…zzzz"

    def test_set_writes_audit_event(self) -> None:
        client, identity, _p = _client()
        client.put(
            "/v1/settings/provider-keys/anthropic",
            params=_params(),
            json={"api_key": _ANTHROPIC_KEY},
        )
        events = [
            event
            for event in identity.list_identity_audit(org_id="org_acme")
            if event.action == "settings.provider_key.set"
        ]
        assert len(events) == 1
        assert events[0].metadata["provider"] == "anthropic"
        assert events[0].metadata["key_hint"] == "…abcd"
        assert _ANTHROPIC_KEY not in str(events[0].metadata)


class TestDeleteProviderKey:
    def test_delete_removes_key(self) -> None:
        client, identity, _p = _client()
        client.put(
            "/v1/settings/provider-keys/google",
            params=_params(),
            json={"api_key": _GOOGLE_KEY},
        )
        response = client.delete("/v1/settings/provider-keys/google", params=_params())
        assert response.status_code == 204
        listing = client.get("/v1/settings/provider-keys", params=_params()).json()
        assert listing == {"keys": []}
        events = [
            event
            for event in identity.list_identity_audit(org_id="org_acme")
            if event.action == "settings.provider_key.deleted"
        ]
        assert len(events) == 1
        assert events[0].metadata["provider"] == "google"

    def test_delete_is_idempotent_204(self) -> None:
        client, identity, _p = _client()
        response = client.delete("/v1/settings/provider-keys/openai", params=_params())
        assert response.status_code == 204
        # No audit row for a no-op delete.
        events = [
            event
            for event in identity.list_identity_audit(org_id="org_acme")
            if event.action == "settings.provider_key.deleted"
        ]
        assert events == []

    def test_delete_unknown_provider_is_422(self) -> None:
        client, _i, _p = _client()
        response = client.delete("/v1/settings/provider-keys/mistral", params=_params())
        assert response.status_code == 422


class TestRbac:
    """The routes declare ``RequireScopes(RUNTIME_USE)``; under
    ``RBAC_MODE=enforce`` a service-token caller without the scope gets
    a 403, with the scope a 200."""

    def _service_headers(self, *, scopes: str) -> dict[str, str]:
        return {
            "x-enterprise-service-token": "test-service-token",
            "x-enterprise-org-id": "org_acme",
            "x-enterprise-user-id": "usr_sarah",
            "x-enterprise-roles": "",
            "x-enterprise-permission-scopes": scopes,
        }

    def test_missing_runtime_use_scope_is_403_in_enforce(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        client, _i, _p = _client()
        response = client.get(
            "/v1/settings/provider-keys",
            params=_params(),
            headers=self._service_headers(scopes=""),
        )
        assert response.status_code == 403

    def test_runtime_use_scope_passes_in_enforce(self, monkeypatch) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        client, _i, _p = _client()
        response = client.get(
            "/v1/settings/provider-keys",
            params=_params(),
            headers=self._service_headers(scopes="runtime:use"),
        )
        assert response.status_code == 200, response.text
