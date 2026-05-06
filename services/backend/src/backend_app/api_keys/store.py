"""API-key row store + mint plan (PR B3 / 8.0.3g).

Backs ``services/backend/migrations/0023_api_keys.sql``.

Two surfaces:

* ``ApiKeyStore.list_for_user`` / ``insert`` / ``revoke`` / ``rotate``
  for the user-facing CRUD route.
* ``ApiKeyStore.find_active_by_prefix`` for the bearer-auth path —
  given the public ``key_prefix`` extracted from an incoming bearer,
  return the (single) active row so the auth middleware can verify
  the secret and mint the caller identity.

Rotation is "create new + revoke old" with the new row's
``rotated_from_id`` pointing at the old row's id for forensic
continuity (the old key keeps logging out via ``last_used_at``
until the user revokes it).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApiKeyRow(BaseModel):
    """One ``api_keys`` row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"apikey_{uuid4().hex}")
    org_id: str
    user_id: str
    label: str = Field(min_length=1, max_length=128)
    key_prefix: str
    secret_hash: str
    scopes: tuple[str, ...] = ()
    last_used_at: datetime | None = None
    last_used_ip: str | None = None
    created_at: datetime = Field(default_factory=_now)
    rotated_from_id: str | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class ApiKeyMint:
    """Result of a ``store.insert`` (or ``rotate``) call.

    ``plaintext`` is the secret half of the wire-format bearer. The
    caller is the only chance to surface this to the user — the store
    stamps the hash and forgets.
    """

    row: ApiKeyRow
    plaintext: str  # plaintext secret (NOT the prefix)


class ApiKeyStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def list_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
        include_revoked: bool = False,
    ) -> tuple[ApiKeyRow, ...]:
        """Return the user's API-key rows (newest first)."""

    def insert(
        self,
        row: ApiKeyRow,
        *,
        conn: Any | None = None,
    ) -> ApiKeyRow:
        """Insert a freshly-minted row. Caller validates uniqueness."""

    def find_active_by_prefix(self, *, key_prefix: str) -> ApiKeyRow | None:
        """Return the single active row for a public prefix or None.

        ``revoked_at IS NOT NULL`` filters the row out — the auth
        middleware MUST treat absence as "no such key" (401), not as
        "exists but revoked" (which would leak existence).
        """

    def revoke(
        self,
        *,
        org_id: str,
        user_id: str,
        api_key_id: str,
        conn: Any | None = None,
    ) -> bool:
        """Mark the row revoked. Returns True on success, False if the
        row doesn't exist or doesn't belong to the user."""

    def stamp_last_used(
        self,
        *,
        api_key_id: str,
        when: datetime,
        ip: str | None,
        conn: Any | None = None,
    ) -> None:
        """Best-effort write of ``last_used_at`` + ``last_used_ip``.

        Auth path calls this on every successful verify; failures are
        non-fatal (the caller already authenticated and can proceed).
        """


@dataclass
class InMemoryApiKeyStore:
    """Dict-backed adapter for tests + dev. Mirrors postgres semantics."""

    rows: dict[str, ApiKeyRow] = field(default_factory=dict)
    by_prefix: dict[str, str] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def list_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
        include_revoked: bool = False,
    ) -> tuple[ApiKeyRow, ...]:
        out = [
            row
            for row in self.rows.values()
            if row.org_id == org_id
            and row.user_id == user_id
            and (include_revoked or row.revoked_at is None)
        ]
        out.sort(key=lambda r: r.created_at, reverse=True)
        return tuple(out)

    def insert(
        self,
        row: ApiKeyRow,
        *,
        conn: Any | None = None,
    ) -> ApiKeyRow:
        del conn
        if row.key_prefix in self.by_prefix:
            raise ValueError("key_prefix collision")
        self.rows[row.id] = row
        self.by_prefix[row.key_prefix] = row.id
        return row

    def find_active_by_prefix(self, *, key_prefix: str) -> ApiKeyRow | None:
        row_id = self.by_prefix.get(key_prefix)
        if row_id is None:
            return None
        row = self.rows.get(row_id)
        if row is None or row.revoked_at is not None:
            return None
        return row

    def revoke(
        self,
        *,
        org_id: str,
        user_id: str,
        api_key_id: str,
        conn: Any | None = None,
    ) -> bool:
        del conn
        row = self.rows.get(api_key_id)
        if row is None or row.org_id != org_id or row.user_id != user_id:
            return False
        if row.revoked_at is not None:
            return False
        self.rows[api_key_id] = row.model_copy(update={"revoked_at": _now()})
        return True

    def stamp_last_used(
        self,
        *,
        api_key_id: str,
        when: datetime,
        ip: str | None,
        conn: Any | None = None,
    ) -> None:
        del conn
        row = self.rows.get(api_key_id)
        if row is None:
            return
        self.rows[api_key_id] = row.model_copy(
            update={"last_used_at": when, "last_used_ip": ip}
        )


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresApiKeyStore:
    """PR 8.0.5 — postgres-backed adapter for ``api_keys`` (migration 0023).

    The unique index on ``key_prefix`` is what makes the
    ``find_active_by_prefix`` lookup an O(1) probe; the partial
    indexes on ``revoked_at IS NULL`` keep the listing path off the
    revoked rows.
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

    def list_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
        include_revoked: bool = False,
    ) -> tuple[ApiKeyRow, ...]:
        sql = """
            SELECT id, org_id, user_id, label, key_prefix, secret_hash,
                   scopes, last_used_at, last_used_ip, created_at,
                   rotated_from_id, revoked_at
            FROM api_keys
            WHERE org_id = %s AND user_id = %s
        """
        params: list[Any] = [org_id, user_id]
        if not include_revoked:
            sql += " AND revoked_at IS NULL"
        sql += " ORDER BY created_at DESC"
        with self._cursor(None) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return tuple(_row_to_api_key(row) for row in rows)

    def insert(self, row: ApiKeyRow, *, conn: Any | None = None) -> ApiKeyRow:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO api_keys (
                    id, org_id, user_id, label, key_prefix, secret_hash,
                    scopes, last_used_at, last_used_ip, created_at,
                    rotated_from_id, revoked_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                """,
                (
                    row.id,
                    row.org_id,
                    row.user_id,
                    row.label,
                    row.key_prefix,
                    row.secret_hash,
                    json.dumps(list(row.scopes)),
                    row.last_used_at,
                    row.last_used_ip,
                    row.created_at,
                    row.rotated_from_id,
                    row.revoked_at,
                ),
            )
        return row

    def find_active_by_prefix(self, *, key_prefix: str) -> ApiKeyRow | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT id, org_id, user_id, label, key_prefix, secret_hash,
                       scopes, last_used_at, last_used_ip, created_at,
                       rotated_from_id, revoked_at
                FROM api_keys
                WHERE key_prefix = %s AND revoked_at IS NULL
                """,
                (key_prefix,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _row_to_api_key(row)

    def revoke(
        self,
        *,
        org_id: str,
        user_id: str,
        api_key_id: str,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE api_keys
                SET revoked_at = NOW()
                WHERE id = %s AND org_id = %s AND user_id = %s
                  AND revoked_at IS NULL
                """,
                (api_key_id, org_id, user_id),
            )
            return (cur.rowcount or 0) > 0

    def stamp_last_used(
        self,
        *,
        api_key_id: str,
        when: datetime,
        ip: str | None,
        conn: Any | None = None,
    ) -> None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE api_keys
                SET last_used_at = %s, last_used_ip = %s
                WHERE id = %s
                """,
                (when, ip, api_key_id),
            )


def _row_to_api_key(row: Any) -> ApiKeyRow:
    record = dict(row)
    raw_scopes = record.get("scopes")
    if isinstance(raw_scopes, str):
        record["scopes"] = tuple(json.loads(raw_scopes) or ())
    elif isinstance(raw_scopes, (bytes, bytearray)):
        record["scopes"] = tuple(json.loads(bytes(raw_scopes).decode("utf-8")) or ())
    elif isinstance(raw_scopes, list):
        record["scopes"] = tuple(raw_scopes)
    return ApiKeyRow.model_validate(record)


__all__ = [
    "ApiKeyMint",
    "ApiKeyRow",
    "ApiKeyStore",
    "InMemoryApiKeyStore",
    "PostgresApiKeyStore",
]
