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
        # snapshot factory falls through to deployment defaults. PRD-C1 adds the
        # per-connector override lane — empty when no override is stored.
        assert body["tool_use"] == {
            "workspace": {},
            "user": {},
            "connector_write_policy": {},
        }
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


class TestConnectorWritePolicySection:
    """PRD-C1 — the aggregate carries the per-connector write-policy overrides,
    keyed by connector slug, only for connectors with a non-NULL override."""

    def _seed(self, store, *, slug: str, write_policy):  # type: ignore[no-untyped-def]
        from backend_app.connectors.store import (
            ConnectorScopeEntry,
            McpUpsertInput,
        )

        record = store.upsert_from_mcp_registration(
            McpUpsertInput(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                slug=slug,
                display_name=slug.title(),
                description=f"{slug} connector",
                status="connected",
                status_reason=None,
                scopes=(ConnectorScopeEntry(scope=f"{slug}.read", granted=True),),
                last_sync_at=None,
                last_error_at=None,
                vault_ref="vault:abc",
            )
        )
        if write_policy is not None:
            record = record.model_copy(update={"write_policy": write_policy})
            store.update_connector(record)
        return record

    def _client_with_connectors(self, store) -> TestClient:  # type: ignore[no-untyped-def]
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
            connectors_store=store,
        )
        return TestClient(app)

    def test_empty_map_when_no_overrides(self) -> None:
        from backend_app.connectors.store import InMemoryConnectorsStore

        store = InMemoryConnectorsStore()
        self._seed(store, slug="linear", write_policy=None)
        client = self._client_with_connectors(store)
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        assert response.json()["tool_use"]["connector_write_policy"] == {}

    def test_overridden_connectors_only_appear(self) -> None:
        from backend_app.connectors.store import (
            ConnectorWritePolicy,
            InMemoryConnectorsStore,
        )

        store = InMemoryConnectorsStore()
        self._seed(store, slug="linear", write_policy=ConnectorWritePolicy.ALLOW_ALWAYS)
        self._seed(store, slug="github", write_policy=ConnectorWritePolicy.ASK_FIRST)
        self._seed(store, slug="notion", write_policy=None)  # no override -> absent
        client = self._client_with_connectors(store)
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        assert response.json()["tool_use"]["connector_write_policy"] == {
            "linear": "allow_always",
            "github": "ask_first",
        }

    def test_empty_map_when_store_not_wired(self) -> None:
        # Backward compat: the default in-memory store has no rows -> {}.
        client = _client()
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200
        assert response.json()["tool_use"]["connector_write_policy"] == {}


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


class TestProviderEndpointsSection:
    """Decision D-2 — the aggregate carries the NON-secret ``base_url`` of a
    stored custom OpenAI-compatible endpoint (never the key) for the runtime."""

    _CUSTOM_KEY = "sk-custom-gateway-000000000000000000abcd"
    _BASE_URL = "https://vllm.public.example/v1"

    def _client(self) -> TestClient:
        from backend_app.provider_keys.ssrf_guard import SsrfGuard
        from backend_app.provider_keys.store import InMemoryProviderApiKeyStore
        from backend_app.token_vault import LocalTokenVault

        def resolver(host: str) -> tuple[str, ...]:
            return {"vllm.public.example": ("93.184.216.34",)}[host]

        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
            provider_api_keys_store=InMemoryProviderApiKeyStore(),
            token_vault=LocalTokenVault(
                secret="test-vault-secret-32-chars-min-length-yes"
            ),
            provider_key_ssrf_guard=SsrfGuard(
                allow_private_networks=False, resolver=resolver
            ),
        )
        return TestClient(app)

    def test_absent_when_no_custom_endpoint(self) -> None:
        client = self._client()
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        assert response.json()["provider_endpoints"] is None

    def test_custom_endpoint_projects_base_url_not_key(self) -> None:
        client = self._client()
        put = client.put(
            "/v1/settings/provider-keys/openai_compatible",
            params=_params(),
            json={
                "api_key": self._CUSTOM_KEY,
                "base_url": self._BASE_URL,
                "label": "My vLLM",
            },
        )
        assert put.status_code == 200, put.text
        response = client.get("/internal/v1/policies/runtime", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider_endpoints"] == {"openai_compatible": self._BASE_URL}
        # The decrypted key rides the separate secret lane; the base_url lane
        # never carries it.
        assert self._CUSTOM_KEY not in str(body["provider_endpoints"])
        assert body["provider_keys"] == {"openai_compatible": self._CUSTOM_KEY}
