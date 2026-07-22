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

from urllib.parse import urlsplit

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

# Ordered longest-prefix-first so ``sk-ant-`` and ``sk-or-`` win over the
# bare ``sk-`` (OpenAI) when detecting which provider a pasted key most
# likely belongs to. OpenRouter keys are ``sk-or-v1-…``.
_KNOWN_PREFIXES: tuple[tuple[ProviderName, str], ...] = (
    (ProviderName.ANTHROPIC, "sk-ant-"),
    (ProviderName.OPENROUTER, "sk-or-"),
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
        default_model: str | None = None,
        base_url: str | None = None,
        label: str | None = None,
        request_ip: str | None = None,
        user_agent: str | None = None,
    ) -> ProviderApiKeyRecord:
        """Encrypt-on-write upsert.

        ``default_model`` is the display-safe model slug to project on the
        summary (PRD-F PR-F.5). ``base_url`` + ``label`` (decision D-2) are the
        user-supplied endpoint + display name for the ``openai_compatible``
        custom provider; both are display-safe and ``None`` for native
        providers. A ``None`` for any of the three preserves any previously
        stored value on rotation (the store COALESCEs), so old callers that
        never pass them are unaffected.
        """

        cleaned = validate_api_key_format(provider=provider, api_key=api_key)
        record = ProviderApiKeyRecord(
            org_id=org_id,
            user_id=user_id,
            provider=provider,
            encrypted_key=self._token_vault.encrypt(cleaned),
            key_hint=key_hint_for(cleaned),
            default_model=default_model,
            base_url=base_url,
            label=label,
        )
        with self._store.transaction() as conn:
            saved = self._store.upsert(record, conn=conn)
            metadata: dict[str, str] = {
                "provider": provider.value,
                "key_hint": saved.key_hint,
            }
            # Custom endpoints record the NON-secret label + endpoint host so an
            # audit reviewer can see WHERE a run's traffic was pointed. The host
            # only — never a path or query — and never the key.
            if saved.label:
                metadata["label"] = saved.label
            endpoint_host = _endpoint_host(saved.base_url)
            if endpoint_host is not None:
                metadata["base_url_host"] = endpoint_host
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=user_id,
                    subject_user_id=user_id,
                    action="settings.provider_key.set",
                    metadata=metadata,
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

    def endpoint_base_urls(self, *, org_id: str, user_id: str) -> dict[str, str]:
        """``{provider: base_url}`` for providers that carry a custom endpoint.

        NON-secret (a base_url is not key material), so — unlike
        :meth:`decrypted_keys` — this rides the persistable
        ``provider_endpoints`` lane of the runtime aggregate. Only the
        ``openai_compatible`` custom provider populates ``base_url`` today.
        Consumed exclusively by ``GET /internal/v1/policies/runtime``.
        """

        return {
            record.provider.value: record.base_url
            for record in self._store.list_for_user(org_id=org_id, user_id=user_id)
            if record.base_url
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
    # A custom OpenAI-compatible endpoint (decision D-2) legitimately accepts a
    # key carrying any vendor's prefix — a self-hosted gateway commonly issues
    # ``sk-…`` tokens — so the prefix-mismatch gate is skipped for it. The
    # length/whitespace bounds above still apply.
    if provider is not ProviderName.OPENAI_COMPATIBLE:
        detected = _detect_provider(cleaned)
        if detected is not None and detected != provider:
            raise ProviderKeyFormatError("api_key_provider_mismatch")
    return cleaned


def _detect_provider(api_key: str) -> ProviderName | None:
    for provider, prefix in _KNOWN_PREFIXES:
        if api_key.startswith(prefix):
            return provider
    return None


def _endpoint_host(base_url: str | None) -> str | None:
    """Return the host of ``base_url`` for audit metadata, or ``None``.

    Host component ONLY — never a path/query/userinfo — so an audit row records
    where traffic was pointed without leaking any embedded token.
    """

    if not base_url:
        return None
    host = urlsplit(base_url.strip()).hostname
    return host or None


__all__ = [
    "ProviderKeyFormatError",
    "ProviderKeysService",
    "key_hint_for",
    "validate_api_key_format",
]
