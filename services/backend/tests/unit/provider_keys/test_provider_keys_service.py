"""Service-layer tests — encryption at rest, hints, validation, audit."""

from __future__ import annotations

import pytest

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.provider_keys.service import (
    ProviderKeyFormatError,
    ProviderKeysService,
    key_hint_for,
    validate_api_key_format,
)
from backend_app.provider_keys.store import (
    InMemoryProviderApiKeyStore,
    ProviderName,
)
from backend_app.token_vault import LocalTokenVault


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"
_ORG = "org_acme"
_USER = "usr_sarah"

# Deliberately fake keys — long enough to pass the plausibility floor,
# obviously not real credentials.
_OPENAI_KEY = "sk-test-openai-0000000000000000001234"
_ANTHROPIC_KEY = "sk-ant-test-000000000000000000000-abcd"
_GOOGLE_KEY = "AIzaTest-00000000000000000000005678"
_UNKNOWN_PLAUSIBLE_KEY = "byok-plausible-key-000000000000-zzzz"


def _service(
    *,
    store: InMemoryProviderApiKeyStore | None = None,
    identity_store: InMemoryIdentityStore | None = None,
    vault: LocalTokenVault | None = None,
) -> tuple[ProviderKeysService, InMemoryProviderApiKeyStore, InMemoryIdentityStore]:
    resolved_store = store or InMemoryProviderApiKeyStore()
    resolved_identity = identity_store or InMemoryIdentityStore()
    service = ProviderKeysService(
        store=resolved_store,
        identity_store=resolved_identity,
        token_vault=vault or LocalTokenVault(secret=_VAULT_SECRET),
    )
    return service, resolved_store, resolved_identity


class TestEncryptionAtRest:
    def test_stored_ciphertext_differs_from_plaintext(self) -> None:
        service, store, _identity = _service()
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI,
            api_key=_OPENAI_KEY,
        )
        row = store.get(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
        assert row is not None
        assert row.encrypted_key != _OPENAI_KEY
        assert _OPENAI_KEY not in row.encrypted_key

    def test_vault_round_trips_back_to_plaintext(self) -> None:
        vault = LocalTokenVault(secret=_VAULT_SECRET)
        service, store, _identity = _service(vault=vault)
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.ANTHROPIC,
            api_key=_ANTHROPIC_KEY,
        )
        row = store.get(org_id=_ORG, user_id=_USER, provider=ProviderName.ANTHROPIC)
        assert row is not None
        assert vault.decrypt(row.encrypted_key) == _ANTHROPIC_KEY

    def test_key_hint_is_ellipsis_plus_last_four(self) -> None:
        service, _store, _identity = _service()
        saved = service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI,
            api_key=_OPENAI_KEY,
        )
        assert saved.key_hint == "…1234"
        assert key_hint_for(_GOOGLE_KEY) == "…5678"


class TestDecryptedKeys:
    def test_returns_only_stored_providers(self) -> None:
        service, _store, _identity = _service()
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI,
            api_key=_OPENAI_KEY,
        )
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.GOOGLE,
            api_key=_GOOGLE_KEY,
        )
        keys = service.decrypted_keys(org_id=_ORG, user_id=_USER)
        assert keys == {"openai": _OPENAI_KEY, "google": _GOOGLE_KEY}

    def test_empty_when_nothing_stored(self) -> None:
        service, _store, _identity = _service()
        assert service.decrypted_keys(org_id=_ORG, user_id=_USER) == {}


class TestValidation:
    def test_rejects_too_short(self) -> None:
        with pytest.raises(ProviderKeyFormatError, match="api_key_too_short"):
            validate_api_key_format(provider=ProviderName.OPENAI, api_key="sk-short")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ProviderKeyFormatError, match="api_key_too_long"):
            validate_api_key_format(
                provider=ProviderName.OPENAI, api_key="sk-" + "a" * 600
            )

    def test_rejects_embedded_whitespace(self) -> None:
        with pytest.raises(ProviderKeyFormatError, match="api_key_contains_whitespace"):
            validate_api_key_format(
                provider=ProviderName.OPENAI,
                api_key="sk-test 0000000000000000001234",
            )

    def test_rejects_anthropic_prefix_for_openai(self) -> None:
        with pytest.raises(ProviderKeyFormatError, match="api_key_provider_mismatch"):
            validate_api_key_format(
                provider=ProviderName.OPENAI, api_key=_ANTHROPIC_KEY
            )

    def test_rejects_openai_prefix_for_anthropic(self) -> None:
        # ``sk-`` without ``sk-ant-`` is the OpenAI family.
        with pytest.raises(ProviderKeyFormatError, match="api_key_provider_mismatch"):
            validate_api_key_format(
                provider=ProviderName.ANTHROPIC, api_key=_OPENAI_KEY
            )

    def test_rejects_openai_prefix_for_google(self) -> None:
        with pytest.raises(ProviderKeyFormatError, match="api_key_provider_mismatch"):
            validate_api_key_format(provider=ProviderName.GOOGLE, api_key=_OPENAI_KEY)

    def test_accepts_matching_prefixes(self) -> None:
        assert (
            validate_api_key_format(provider=ProviderName.OPENAI, api_key=_OPENAI_KEY)
            == _OPENAI_KEY
        )
        assert (
            validate_api_key_format(
                provider=ProviderName.ANTHROPIC, api_key=_ANTHROPIC_KEY
            )
            == _ANTHROPIC_KEY
        )
        assert (
            validate_api_key_format(provider=ProviderName.GOOGLE, api_key=_GOOGLE_KEY)
            == _GOOGLE_KEY
        )

    def test_accepts_unknown_but_plausible_prefix(self) -> None:
        for provider in ProviderName:
            assert (
                validate_api_key_format(
                    provider=provider, api_key=_UNKNOWN_PLAUSIBLE_KEY
                )
                == _UNKNOWN_PLAUSIBLE_KEY
            )

    def test_strips_surrounding_whitespace(self) -> None:
        assert (
            validate_api_key_format(
                provider=ProviderName.OPENAI, api_key=f"  {_OPENAI_KEY}\n"
            )
            == _OPENAI_KEY
        )


class TestAudit:
    def test_set_key_writes_audit_without_plaintext(self) -> None:
        service, _store, identity = _service()
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.OPENAI,
            api_key=_OPENAI_KEY,
        )
        events = [
            event
            for event in identity.list_identity_audit(org_id=_ORG)
            if event.action == "settings.provider_key.set"
        ]
        assert len(events) == 1
        event = events[0]
        assert event.actor_user_id == _USER
        assert event.metadata["provider"] == "openai"
        assert event.metadata["key_hint"] == "…1234"
        # The plaintext key never lands in audit metadata.
        assert _OPENAI_KEY not in str(event.metadata)

    def test_delete_key_audits_once_and_is_idempotent(self) -> None:
        service, _store, identity = _service()
        service.set_key(
            org_id=_ORG,
            user_id=_USER,
            provider=ProviderName.GOOGLE,
            api_key=_GOOGLE_KEY,
        )
        assert (
            service.delete_key(org_id=_ORG, user_id=_USER, provider=ProviderName.GOOGLE)
            is True
        )
        # Second delete: no row, no extra audit event.
        assert (
            service.delete_key(org_id=_ORG, user_id=_USER, provider=ProviderName.GOOGLE)
            is False
        )
        events = [
            event
            for event in identity.list_identity_audit(org_id=_ORG)
            if event.action == "settings.provider_key.deleted"
        ]
        assert len(events) == 1
        assert events[0].metadata["provider"] == "google"
        assert events[0].metadata["key_hint"] == "…5678"
