"""Store + service tests for the custom OpenAI-compatible endpoint (D-2).

Covers the additive ``base_url`` / ``label`` columns, rotation-preserve
semantics, the relaxed format check for the ``openai_compatible`` slug, the
non-secret ``endpoint_base_urls`` projection, and audit-metadata honesty.
"""

from __future__ import annotations

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.provider_keys.service import (
    ProviderKeysService,
    validate_api_key_format,
)
from backend_app.provider_keys.store import (
    InMemoryProviderApiKeyStore,
    ProviderApiKeyRecord,
    ProviderName,
)
from backend_app.token_vault import LocalTokenVault


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"
_ORG = "org_acme"
_USER = "usr_sarah"
# An sk-… key that would trip the OpenAI prefix-mismatch gate for a native
# provider, but is legitimate for a custom self-hosted gateway.
_CUSTOM_KEY = "sk-custom-gateway-000000000000000000abcd"
_BASE_URL = "https://vllm.internal.example/v1"


def _service() -> tuple[
    ProviderKeysService, InMemoryProviderApiKeyStore, InMemoryIdentityStore
]:
    store = InMemoryProviderApiKeyStore()
    identity = InMemoryIdentityStore()
    service = ProviderKeysService(
        store=store,
        identity_store=identity,
        token_vault=LocalTokenVault(secret=_VAULT_SECRET),
    )
    return service, store, identity


class TestStoreCustomColumns:
    def test_round_trip_base_url_and_label(self) -> None:
        store = InMemoryProviderApiKeyStore()
        store.upsert(
            ProviderApiKeyRecord(
                org_id=_ORG,
                user_id=_USER,
                provider=ProviderName.OPENAI_COMPATIBLE,
                encrypted_key="ciphertext",
                key_hint="…abcd",
                base_url=_BASE_URL,
                label="My vLLM",
            )
        )
        fetched = store.get(
            org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI_COMPATIBLE
        )
        assert fetched is not None
        assert fetched.base_url == _BASE_URL
        assert fetched.label == "My vLLM"

    def test_rotation_preserves_base_url_and_label(self) -> None:
        store = InMemoryProviderApiKeyStore()
        store.upsert(
            ProviderApiKeyRecord(
                org_id=_ORG,
                user_id=_USER,
                provider=ProviderName.OPENAI_COMPATIBLE,
                encrypted_key="c1",
                key_hint="…0001",
                base_url=_BASE_URL,
                label="My vLLM",
                default_model="llama-3.1-70b",
            )
        )
        # A rotation that re-sends only the key must preserve endpoint + label
        # + default_model (COALESCE), mirroring the native-provider behavior.
        store.upsert(
            ProviderApiKeyRecord(
                org_id=_ORG,
                user_id=_USER,
                provider=ProviderName.OPENAI_COMPATIBLE,
                encrypted_key="c2",
                key_hint="…0002",
            )
        )
        fetched = store.get(
            org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI_COMPATIBLE
        )
        assert fetched is not None
        assert fetched.encrypted_key == "c2"
        assert fetched.base_url == _BASE_URL
        assert fetched.label == "My vLLM"
        assert fetched.default_model == "llama-3.1-70b"

    def test_native_rows_leave_columns_null(self) -> None:
        store = InMemoryProviderApiKeyStore()
        store.upsert(
            ProviderApiKeyRecord(
                org_id=_ORG,
                user_id=_USER,
                provider=ProviderName.OPENAI,
                encrypted_key="c",
                key_hint="…1234",
            )
        )
        fetched = store.get(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
        assert fetched is not None
        assert fetched.base_url is None
        assert fetched.label is None


class TestFormatRelaxation:
    def test_custom_slug_accepts_sk_prefixed_key(self) -> None:
        # For a NATIVE provider this key would be rejected as a provider
        # mismatch (sk- is OpenAI's); the custom slug must accept it.
        cleaned = validate_api_key_format(
            provider=ProviderName.OPENAI_COMPATIBLE, api_key=_CUSTOM_KEY
        )
        assert cleaned == _CUSTOM_KEY

    def test_native_slug_still_rejects_foreign_prefix(self) -> None:
        import pytest

        from backend_app.provider_keys.service import ProviderKeyFormatError

        with pytest.raises(ProviderKeyFormatError):
            validate_api_key_format(
                provider=ProviderName.ANTHROPIC, api_key=_CUSTOM_KEY
            )


class TestServiceCustomEndpoint:
    def test_set_key_persists_endpoint_and_audits_host_not_key(self) -> None:
        service, _store, identity = _service()
        saved = service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI_COMPATIBLE,
            api_key=_CUSTOM_KEY,
            base_url=_BASE_URL,
            label="My vLLM",
        )
        assert saved.base_url == _BASE_URL
        assert saved.label == "My vLLM"
        # Audit row records the NON-secret host + label, never the key.
        sets = [
            event
            for event in identity.list_identity_audit(org_id=_ORG)
            if event.action == "settings.provider_key.set"
        ]
        assert sets, "expected an audit row on set"
        meta = sets[-1].metadata
        assert meta["label"] == "My vLLM"
        assert meta["base_url_host"] == "vllm.internal.example"
        assert _CUSTOM_KEY not in str(meta)

    def test_endpoint_base_urls_projects_only_custom_rows(self) -> None:
        service, _store, _identity = _service()
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI,
            api_key="sk-native-00000000000000000000abcd",
        )
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI_COMPATIBLE,
            api_key=_CUSTOM_KEY,
            base_url=_BASE_URL,
            label="My vLLM",
        )
        endpoints = service.endpoint_base_urls(org_id=_ORG, user_id=_USER)
        assert endpoints == {"openai_compatible": _BASE_URL}

    def test_decrypted_keys_includes_custom_slug(self) -> None:
        service, _store, _identity = _service()
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI_COMPATIBLE,
            api_key=_CUSTOM_KEY,
            base_url=_BASE_URL,
            label="My vLLM",
        )
        keys = service.decrypted_keys(org_id=_ORG, user_id=_USER)
        assert keys["openai_compatible"] == _CUSTOM_KEY
