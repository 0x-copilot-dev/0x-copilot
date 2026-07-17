"""Provider API key service (Phase 2 BYOK).

Single seat for the cryptographic + validation detail — mirrors the
``MfaService`` composition: routes call high-level methods and never
touch the ``TokenVault`` directly.

Invariants:

* Plaintext keys exist in memory only inside ``set_key`` (encrypt on
  write) and ``decrypted_keys`` (internal runtime lane). They are never
  stored, logged, or embedded in audit metadata / exception messages.
* ``key_hint`` is the ONLY display artifact: ``"…" + last 4 chars``.
* Every set/delete writes an identity audit row inside the same store
  transaction as the primary write (C3 atomicity discipline).

Format validation is deliberately permissive (frozen wire contract):
reject obviously-wrong prefixes — a key carrying ANOTHER provider's
well-known prefix — and implausibly short values, but accept
unknown-but-plausible keys (length >= 20) so new key formats don't
require a backend release.
"""

from __future__ import annotations

from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.store import IdentityStore
from backend_app.provider_keys.store import (
    ProviderApiKeyRecord,
    ProviderApiKeyStore,
    ProviderName,
)
from backend_app.token_vault import TokenVault


_MIN_PLAUSIBLE_LENGTH = 20
_MAX_KEY_LENGTH = 512
_HINT_CHARS = 4

# Ordered longest-prefix-first so ``sk-ant-`` wins over ``sk-`` when
# detecting which provider a pasted key most likely belongs to.
_KNOWN_PREFIXES: tuple[tuple[ProviderName, str], ...] = (
    (ProviderName.ANTHROPIC, "sk-ant-"),
    (ProviderName.OPENAI, "sk-"),
    (ProviderName.GOOGLE, "AIza"),
)


class ProviderKeyFormatError(ValueError):
    """Raised when an api_key fails plausibility validation. The message
    is a machine-readable reason code — NEVER any part of the key."""


class ProviderKeysService:
    """Encrypt-on-write storage + hint-only listing + runtime decrypt."""

    def __init__(
        self,
        *,
        store: ProviderApiKeyStore,
        identity_store: IdentityStore,
        token_vault: TokenVault,
    ) -> None:
        self._store = store
        self._identity_store = identity_store
        self._token_vault = token_vault

    # ------------------------------------------------------------------
    # Public surface (Settings UI via facade)
    # ------------------------------------------------------------------

    def list_keys(
        self, *, org_id: str, user_id: str
    ) -> tuple[ProviderApiKeyRecord, ...]:
        """Hint-only listing — callers must never read ``encrypted_key``
        out of the returned records for display."""

        return self._store.list_for_user(org_id=org_id, user_id=user_id)

    def set_key(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
        api_key: str,
        request_ip: str | None = None,
        user_agent: str | None = None,
    ) -> ProviderApiKeyRecord:
        cleaned = validate_api_key_format(provider=provider, api_key=api_key)
        record = ProviderApiKeyRecord(
            org_id=org_id,
            user_id=user_id,
            provider=provider,
            encrypted_key=self._token_vault.encrypt(cleaned),
            key_hint=key_hint_for(cleaned),
        )
        with self._store.transaction() as conn:
            saved = self._store.upsert(record, conn=conn)
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=user_id,
                    subject_user_id=user_id,
                    action="settings.provider_key.set",
                    metadata={
                        "provider": provider.value,
                        "key_hint": saved.key_hint,
                    },
                    request_ip=request_ip,
                    user_agent=user_agent,
                ),
                conn=conn,
            )
        return saved

    def delete_key(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
        request_ip: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Idempotent removal. Audits only when a row actually existed."""

        existing = self._store.get(org_id=org_id, user_id=user_id, provider=provider)
        with self._store.transaction() as conn:
            removed = self._store.delete(
                org_id=org_id, user_id=user_id, provider=provider, conn=conn
            )
            if removed:
                self._identity_store.append_identity_audit(
                    IdentityAuditEventRecord(
                        org_id=org_id,
                        actor_user_id=user_id,
                        subject_user_id=user_id,
                        action="settings.provider_key.deleted",
                        metadata={
                            "provider": provider.value,
                            "key_hint": existing.key_hint if existing else "",
                        },
                        request_ip=request_ip,
                        user_agent=user_agent,
                    ),
                    conn=conn,
                )
        return removed

    # ------------------------------------------------------------------
    # Internal runtime lane (service-token-only callers)
    # ------------------------------------------------------------------

    def decrypted_keys(self, *, org_id: str, user_id: str) -> dict[str, str]:
        """``{provider: plaintext}`` for providers with stored keys.

        Consumed exclusively by ``GET /internal/v1/policies/runtime``
        (service-token lane). Never expose through a facade-reachable
        route.
        """

        return {
            record.provider.value: self._token_vault.decrypt(record.encrypted_key)
            for record in self._store.list_for_user(org_id=org_id, user_id=user_id)
        }


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def key_hint_for(api_key: str) -> str:
    """Display hint: ellipsis + last 4 characters (frozen wire contract)."""

    return "…" + api_key[-_HINT_CHARS:]


def validate_api_key_format(*, provider: ProviderName, api_key: str) -> str:
    """Return the cleaned key or raise :class:`ProviderKeyFormatError`.

    Rules (frozen wire contract): reject keys carrying a DIFFERENT
    provider's well-known prefix and implausible values (too short,
    too long, embedded whitespace); accept everything else with
    length >= 20 so unknown-but-plausible formats pass.
    """

    cleaned = api_key.strip()
    if len(cleaned) < _MIN_PLAUSIBLE_LENGTH:
        raise ProviderKeyFormatError("api_key_too_short")
    if len(cleaned) > _MAX_KEY_LENGTH:
        raise ProviderKeyFormatError("api_key_too_long")
    if any(ch.isspace() for ch in cleaned):
        raise ProviderKeyFormatError("api_key_contains_whitespace")
    detected = _detect_provider(cleaned)
    if detected is not None and detected != provider:
        raise ProviderKeyFormatError("api_key_provider_mismatch")
    return cleaned


def _detect_provider(api_key: str) -> ProviderName | None:
    for provider, prefix in _KNOWN_PREFIXES:
        if api_key.startswith(prefix):
            return provider
    return None


__all__ = [
    "ProviderKeyFormatError",
    "ProviderKeysService",
    "key_hint_for",
    "validate_api_key_format",
]
