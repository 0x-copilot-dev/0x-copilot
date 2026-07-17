"""Tests for the PR 8.0.5 ``/internal/v1/policies/runtime`` aggregate."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.policies.store import (
    InMemoryToolUsePolicyStore,
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicyRow,
)
from backend_app.privacy.store import (
    DataResidencyRegion,
    InMemoryPrivacySettingsStore,
    PrivacySettingsRow,
)


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
    tool_use_store: InMemoryToolUsePolicyStore | None = None,
    privacy_store: InMemoryPrivacySettingsStore | None = None,
) -> TestClient:
    tool_use = tool_use_store or InMemoryToolUsePolicyStore()
    privacy = privacy_store or InMemoryPrivacySettingsStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        tool_use_policy_store=tool_use,
        privacy_settings_store=privacy,
    )
    return TestClient(app)


def _params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestAggregateShape:
    def test_empty_stores_return_default_shape(self) -> None:
        client = _client()
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()
        # tool_use exposes both scopes as empty dicts; the AI backend's
        # snapshot factory falls through to deployment defaults.
        assert body["tool_use"] == {"workspace": {}, "user": {}}
        # privacy hydrates deployment defaults.
        assert body["privacy"]["training_opt_out"] is True
        assert body["privacy"]["share_metadata"] is True
        assert body["privacy"]["memory_enabled"] is True
        assert body["privacy"]["region"] is None
        assert body["privacy"]["retention_days"] is None

    def test_workspace_and_user_rows_compose(self) -> None:
        tool_use = InMemoryToolUsePolicyStore()
        privacy = InMemoryPrivacySettingsStore()
        # Workspace destructive=block; user override flips destructive=auto.
        tool_use.upsert(
            ToolUsePolicyRow(
                org_id="org_acme",
                user_id=None,
                kind=ToolUsePolicyKind.DESTRUCTIVE,
                mode=ToolUsePolicyMode.BLOCK,
                updated_by_user_id="usr_admin",
            )
        )
        tool_use.upsert(
            ToolUsePolicyRow(
                org_id="org_acme",
                user_id="usr_sarah",
                kind=ToolUsePolicyKind.DESTRUCTIVE,
                mode=ToolUsePolicyMode.AUTO,
                updated_by_user_id="usr_sarah",
            )
        )
        privacy.upsert(
            PrivacySettingsRow(
                org_id="org_acme",
                user_id="usr_sarah",
                training_opt_out=False,
                region=DataResidencyRegion.EU_WEST_1,
                retention_days=30,
                share_metadata=True,
                memory_enabled=False,
                updated_by_user_id="usr_sarah",
            )
        )
        client = _client(tool_use_store=tool_use, privacy_store=privacy)
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()
        # Both scopes surface so the AI backend's
        # ToolUsePolicySnapshot.from_response can pick the override.
        assert body["tool_use"]["workspace"]["destructive"] == "block"
        assert body["tool_use"]["user"]["destructive"] == "auto"
        # Privacy is pre-composed (user override wins).
        assert body["privacy"]["training_opt_out"] is False
        assert body["privacy"]["region"] == "eu-west-1"
        assert body["privacy"]["retention_days"] == 30
        assert body["privacy"]["memory_enabled"] is False
        # Unset on user, unset on workspace => deployment default kicks in.
        assert body["privacy"]["share_metadata"] is True

    def test_workspace_privacy_falls_through_when_no_user_row(self) -> None:
        privacy = InMemoryPrivacySettingsStore()
        privacy.upsert(
            PrivacySettingsRow(
                org_id="org_acme",
                user_id=None,
                training_opt_out=False,
                region=DataResidencyRegion.US_EAST_1,
                retention_days=180,
                share_metadata=True,
                memory_enabled=True,
                updated_by_user_id="usr_admin",
            )
        )
        client = _client(privacy_store=privacy)
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200
        body = response.json()
        assert body["privacy"]["training_opt_out"] is False
        assert body["privacy"]["region"] == "us-east-1"
        assert body["privacy"]["retention_days"] == 180


class TestProviderKeysSection:
    """Phase 2 BYOK — the aggregate carries decrypted per-user provider
    keys for the run, only when the user actually stored some."""

    # Deliberately fake keys — long enough to pass the plausibility
    # floor, obviously not real credentials.
    _OPENAI_KEY = "sk-test-openai-0000000000000000001234"
    _GOOGLE_KEY = "AIzaTest-00000000000000000000005678"

    def _byok_client(self) -> TestClient:
        from backend_app.provider_keys.store import InMemoryProviderApiKeyStore
        from backend_app.token_vault import LocalTokenVault

        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
            provider_api_keys_store=InMemoryProviderApiKeyStore(),
            token_vault=LocalTokenVault(
                secret="test-vault-secret-32-chars-min-length-yes"
            ),
        )
        return TestClient(app)

    def test_absent_when_no_keys_stored(self) -> None:
        client = self._byok_client()
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        assert response.json()["provider_keys"] is None

    def test_decrypted_keys_present_only_for_stored_providers(self) -> None:
        client = self._byok_client()
        # Store through the public surface so the test covers the full
        # encrypt-on-write → decrypt-on-internal-read path.
        for provider, key in (
            ("openai", self._OPENAI_KEY),
            ("google", self._GOOGLE_KEY),
        ):
            put = client.put(
                f"/v1/settings/provider-keys/{provider}",
                params=_params(),
                json={"api_key": key},
            )
            assert put.status_code == 200, put.text
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        assert response.json()["provider_keys"] == {
            "openai": self._OPENAI_KEY,
            "google": self._GOOGLE_KEY,
        }

    def test_keys_are_scoped_to_the_requesting_user(self) -> None:
        client = self._byok_client()
        put = client.put(
            "/v1/settings/provider-keys/openai",
            params=_params(),
            json={"api_key": self._OPENAI_KEY},
        )
        assert put.status_code == 200, put.text
        response = client.get(
            "/internal/v1/policies/runtime",
            params={"org_id": "org_acme", "user_id": "usr_marcus"},
        )
        assert response.status_code == 200
        assert response.json()["provider_keys"] is None
