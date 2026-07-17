"""Store round-trip tests for the Phase 2 BYOK provider-key adapters."""

from __future__ import annotations

from backend_app.provider_keys.store import (
    InMemoryProviderApiKeyStore,
    ProviderApiKeyRecord,
    ProviderName,
)


_ORG = "org_acme"
_USER = "usr_sarah"


def _record(
    *,
    provider: ProviderName = ProviderName.OPENAI,
    encrypted_key: str = "vault-envelope-ciphertext",
    key_hint: str = "…1234",
    org_id: str = _ORG,
    user_id: str = _USER,
) -> ProviderApiKeyRecord:
    return ProviderApiKeyRecord(
        org_id=org_id,
        user_id=user_id,
        provider=provider,
        encrypted_key=encrypted_key,
        key_hint=key_hint,
    )


class TestInMemoryStoreRoundTrip:
    def test_upsert_then_get(self) -> None:
        store = InMemoryProviderApiKeyStore()
        saved = store.upsert(_record())
        fetched = store.get(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
        assert fetched is not None
        assert fetched.encrypted_key == "vault-envelope-ciphertext"
        assert fetched.key_hint == "…1234"
        assert fetched.updated_at == saved.updated_at

    def test_get_missing_returns_none(self) -> None:
        store = InMemoryProviderApiKeyStore()
        assert (
            store.get(org_id=_ORG, user_id=_USER, provider=ProviderName.GOOGLE) is None
        )

    def test_upsert_replaces_and_preserves_created_at(self) -> None:
        store = InMemoryProviderApiKeyStore()
        first = store.upsert(_record(encrypted_key="cipher-one", key_hint="…aaaa"))
        second = store.upsert(_record(encrypted_key="cipher-two", key_hint="…bbbb"))
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at
        fetched = store.get(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
        assert fetched is not None
        assert fetched.encrypted_key == "cipher-two"
        assert fetched.key_hint == "…bbbb"
        # Still exactly one row for the (org, user, provider) key.
        assert len(store.list_for_user(org_id=_ORG, user_id=_USER)) == 1

    def test_list_for_user_is_provider_ordered_and_scoped(self) -> None:
        store = InMemoryProviderApiKeyStore()
        store.upsert(_record(provider=ProviderName.OPENAI))
        store.upsert(_record(provider=ProviderName.GOOGLE))
        store.upsert(_record(provider=ProviderName.ANTHROPIC))
        # Another user + another org must not bleed into the listing.
        store.upsert(_record(user_id="usr_marcus"))
        store.upsert(_record(org_id="org_other"))
        listed = store.list_for_user(org_id=_ORG, user_id=_USER)
        assert [record.provider.value for record in listed] == [
            "anthropic",
            "google",
            "openai",
        ]

    def test_delete_true_then_idempotent_false(self) -> None:
        store = InMemoryProviderApiKeyStore()
        store.upsert(_record())
        assert (
            store.delete(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
            is True
        )
        assert (
            store.delete(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
            is False
        )
        assert store.list_for_user(org_id=_ORG, user_id=_USER) == ()

    def test_delete_is_provider_scoped(self) -> None:
        store = InMemoryProviderApiKeyStore()
        store.upsert(_record(provider=ProviderName.OPENAI))
        store.upsert(_record(provider=ProviderName.ANTHROPIC))
        assert (
            store.delete(org_id=_ORG, user_id=_USER, provider=ProviderName.OPENAI)
            is True
        )
        remaining = store.list_for_user(org_id=_ORG, user_id=_USER)
        assert [record.provider.value for record in remaining] == ["anthropic"]
