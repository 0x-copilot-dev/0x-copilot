"""Provider API key store (Phase 2 BYOK).

Backs ``services/backend/migrations/0034_provider_api_keys.sql``.

One row per ``(org_id, user_id, provider)``. The store persists ONLY
the TokenVault ciphertext plus a last-4-chars ``key_hint`` — plaintext
never touches an adapter. Encryption/decryption is the service layer's
job (:mod:`backend_app.provider_keys.service`); adapters move opaque
strings.

Adapter shapes mirror ``backend_app.privacy.store``: a Protocol, a
dict-backed in-memory adapter for tests/dev, and a psycopg-pool
Postgres adapter for production.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderName(StrEnum):
    """Closed set of providers the runtime supports — wire-aligned with
    the CHECK constraint in migrations 0034 (openai/anthropic/google),
    0036 (openrouter) and 0045 (openai_compatible).

    ``OPENAI_COMPATIBLE`` (decision D-2) is the ONE generic member backing the
    "any OpenAI-compatible endpoint" custom add-flow: a single per-user endpoint
    whose ``base_url`` + display ``label`` are user-supplied and live in the two
    nullable columns below. The four native providers never populate them."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    OPENAI_COMPATIBLE = "openai_compatible"


class ProviderApiKeyRecord(BaseModel):
    """One ``provider_api_keys`` row. ``encrypted_key`` is an opaque
    TokenVault envelope; ``key_hint`` is display-safe (last 4 chars).

    ``default_model`` is the display-safe model slug chosen for this key
    (PRD-F PR-F.5) — never key material. ``None`` for keys stored without a
    model (or by older clients); a rotation that omits it preserves the
    previously-stored value (see ``upsert``).

    ``base_url`` + ``label`` (decision D-2) are populated ONLY for the
    ``openai_compatible`` custom endpoint — the user-supplied endpoint and a
    display name. Both are display-safe (never key material) and stay ``None``
    for the four native providers. Like ``default_model`` they are preserved on
    a rotation that omits them (COALESCE)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    user_id: str
    provider: ProviderName
    encrypted_key: str
    key_hint: str
    default_model: str | None = None
    base_url: str | None = None
    label: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ProviderApiKeyStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def get(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
    ) -> ProviderApiKeyRecord | None:
        """Return the row for one (org, user, provider) or ``None``."""

    def list_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> tuple[ProviderApiKeyRecord, ...]:
        """Every stored key for one user, ordered by provider name."""

    def upsert(
        self,
        record: ProviderApiKeyRecord,
        *,
        conn: Any | None = None,
    ) -> ProviderApiKeyRecord:
        """Insert or replace the (org, user, provider) row. The original
        ``created_at`` survives a replace; ``updated_at`` is bumped."""

    def delete(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
        conn: Any | None = None,
    ) -> bool:
        """Drop the row. Returns True if a row was removed."""


@dataclass
class InMemoryProviderApiKeyStore:
    """Dict-backed adapter for tests + dev. Mirrors postgres semantics."""

    rows: dict[tuple[str, str, str], ProviderApiKeyRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def get(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
    ) -> ProviderApiKeyRecord | None:
        return self.rows.get((org_id, user_id, provider.value))

    def list_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> tuple[ProviderApiKeyRecord, ...]:
        matches = [
            record
            for (row_org, row_user, _), record in self.rows.items()
            if row_org == org_id and row_user == user_id
        ]
        return tuple(sorted(matches, key=lambda record: record.provider.value))

    def upsert(
        self,
        record: ProviderApiKeyRecord,
        *,
        conn: Any | None = None,
    ) -> ProviderApiKeyRecord:
        del conn
        key = (record.org_id, record.user_id, record.provider.value)
        existing = self.rows.get(key)
        # A rotation that omits ``default_model`` / ``base_url`` / ``label``
        # preserves the stored value — mirrors the ``created_at`` carry-over and
        # the postgres COALESCE.
        default_model = record.default_model
        if default_model is None and existing is not None:
            default_model = existing.default_model
        base_url = record.base_url
        if base_url is None and existing is not None:
            base_url = existing.base_url
        label = record.label
        if label is None and existing is not None:
            label = existing.label
        saved = record.model_copy(
            update={
                "default_model": default_model,
                "base_url": base_url,
                "label": label,
                "created_at": existing.created_at if existing else record.created_at,
                "updated_at": _now(),
            }
        )
        self.rows[key] = saved
        return saved

    def delete(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
        conn: Any | None = None,
    ) -> bool:
        del conn
        return self.rows.pop((org_id, user_id, provider.value), None) is not None


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresProviderApiKeyStore:
    """psycopg-pool adapter for ``provider_api_keys``.

    The composite primary key ``(org_id, user_id, provider)`` from
    migration 0034 drives the single ``INSERT … ON CONFLICT`` upsert;
    ``created_at`` is deliberately NOT in the update list so the first
    write's timestamp survives key rotation.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    def get(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
    ) -> ProviderApiKeyRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT org_id, user_id, provider, encrypted_key, key_hint,
                       default_model, base_url, label, created_at, updated_at
                FROM provider_api_keys
                WHERE org_id = %s AND user_id = %s AND provider = %s
                """,
                (org_id, user_id, provider.value),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return ProviderApiKeyRecord.model_validate(dict(row))

    def list_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> tuple[ProviderApiKeyRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT org_id, user_id, provider, encrypted_key, key_hint,
                       default_model, base_url, label, created_at, updated_at
                FROM provider_api_keys
                WHERE org_id = %s AND user_id = %s
                ORDER BY provider
                """,
                (org_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(ProviderApiKeyRecord.model_validate(dict(row)) for row in rows)

    def upsert(
        self,
        record: ProviderApiKeyRecord,
        *,
        conn: Any | None = None,
    ) -> ProviderApiKeyRecord:
        saved = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO provider_api_keys (
                    org_id, user_id, provider, encrypted_key, key_hint,
                    default_model, base_url, label, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id, user_id, provider) DO UPDATE SET
                    encrypted_key = EXCLUDED.encrypted_key,
                    key_hint = EXCLUDED.key_hint,
                    -- A rotation that omits the model / endpoint preserves the
                    -- stored value; a fresh value overwrites it.
                    default_model = COALESCE(
                        EXCLUDED.default_model, provider_api_keys.default_model
                    ),
                    base_url = COALESCE(
                        EXCLUDED.base_url, provider_api_keys.base_url
                    ),
                    label = COALESCE(EXCLUDED.label, provider_api_keys.label),
                    updated_at = EXCLUDED.updated_at
                RETURNING created_at, default_model, base_url, label
                """,
                (
                    saved.org_id,
                    saved.user_id,
                    saved.provider.value,
                    saved.encrypted_key,
                    saved.key_hint,
                    saved.default_model,
                    saved.base_url,
                    saved.label,
                    saved.created_at,
                    saved.updated_at,
                ),
            )
            returned = cur.fetchone()
        if returned is not None:
            if isinstance(returned, dict):
                created_at = returned["created_at"]
                default_model = returned["default_model"]
                base_url = returned["base_url"]
                label = returned["label"]
            else:
                created_at, default_model, base_url, label = (
                    returned[0],
                    returned[1],
                    returned[2],
                    returned[3],
                )
            saved = saved.model_copy(
                update={
                    "created_at": created_at,
                    "default_model": default_model,
                    "base_url": base_url,
                    "label": label,
                }
            )
        return saved

    def delete(
        self,
        *,
        org_id: str,
        user_id: str,
        provider: ProviderName,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                DELETE FROM provider_api_keys
                WHERE org_id = %s AND user_id = %s AND provider = %s
                """,
                (org_id, user_id, provider.value),
            )
            return (cur.rowcount or 0) > 0


__all__ = [
    "InMemoryProviderApiKeyStore",
    "PostgresProviderApiKeyStore",
    "ProviderApiKeyRecord",
    "ProviderApiKeyStore",
    "ProviderName",
]
