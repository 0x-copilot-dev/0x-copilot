"""Stores for PR 5.1 login email-first.

Two narrow stores live here so the discovery + magic-link services don't
need to touch :class:`IdentityStore`:

* :class:`AuthProviderDomainStore` — domain → (org, provider) claim lookups.
* :class:`MagicLinkTokenStore` — sha256-keyed magic-link rows.

Both follow the same shape as
:mod:`backend_app.identity.invitation_store` (in-memory + Postgres
adapters; ``transaction()`` context manager for write composition).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    AuthProviderDomainRecord,
    MagicLinkTokenRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# AuthProviderDomain store
# ---------------------------------------------------------------------------


class AuthProviderDomainStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[Any]: ...  # pragma: no cover

    def upsert(
        self, record: AuthProviderDomainRecord, *, conn: Any | None = None
    ) -> AuthProviderDomainRecord: ...

    def get_active_by_domain(
        self, *, domain: str, conn: Any | None = None
    ) -> tuple[AuthProviderDomainRecord, ...]: ...

    def list_for_org(
        self, *, org_id: str, include_deleted: bool = False, conn: Any | None = None
    ) -> tuple[AuthProviderDomainRecord, ...]: ...

    def soft_delete(
        self, *, domain: str, org_id: str, provider_id: str, conn: Any | None = None
    ) -> bool: ...


@dataclass
class InMemoryAuthProviderDomainStore:
    rows: dict[tuple[str, str, str], AuthProviderDomainRecord] = field(
        default_factory=dict
    )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def upsert(
        self, record: AuthProviderDomainRecord, *, conn: Any | None = None
    ) -> AuthProviderDomainRecord:
        del conn
        key = (record.domain, record.org_id, record.provider_id)
        # Refresh updated_at on any upsert (matches the SQL DEFAULT NOW()
        # we'd also bump in the Postgres adapter on conflict).
        stored = record.model_copy(update={"updated_at": _now(), "deleted_at": None})
        self.rows[key] = stored
        return stored

    def get_active_by_domain(
        self, *, domain: str, conn: Any | None = None
    ) -> tuple[AuthProviderDomainRecord, ...]:
        del conn
        normalised = domain.strip().lower()
        return tuple(
            r
            for r in self.rows.values()
            if r.domain == normalised and r.deleted_at is None
        )

    def list_for_org(
        self, *, org_id: str, include_deleted: bool = False, conn: Any | None = None
    ) -> tuple[AuthProviderDomainRecord, ...]:
        del conn
        return tuple(
            r
            for r in self.rows.values()
            if r.org_id == org_id and (include_deleted or r.deleted_at is None)
        )

    def soft_delete(
        self, *, domain: str, org_id: str, provider_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        key = (domain.strip().lower(), org_id, provider_id)
        existing = self.rows.get(key)
        if existing is None or existing.deleted_at is not None:
            return False
        self.rows[key] = existing.model_copy(update={"deleted_at": _now()})
        return True


@dataclass
class PostgresAuthProviderDomainStore:
    pool: Any  # PostgresConnectionPool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.pool.transaction() as conn:
            yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self.pool.transaction() as txn:
            with txn.cursor() as cur:
                yield cur

    def upsert(
        self, record: AuthProviderDomainRecord, *, conn: Any | None = None
    ) -> AuthProviderDomainRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO auth_provider_domains (
                    domain, org_id, provider_id, sso_enforced,
                    created_at, updated_at, created_by_user_id, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)
                ON CONFLICT (domain, org_id, provider_id) DO UPDATE SET
                    sso_enforced = EXCLUDED.sso_enforced,
                    updated_at = EXCLUDED.updated_at,
                    deleted_at = NULL
                """,
                (
                    record.domain,
                    record.org_id,
                    record.provider_id,
                    record.sso_enforced,
                    record.created_at,
                    _now(),
                    record.created_by_user_id,
                ),
            )
        return record.model_copy(update={"updated_at": _now(), "deleted_at": None})

    def get_active_by_domain(
        self, *, domain: str, conn: Any | None = None
    ) -> tuple[AuthProviderDomainRecord, ...]:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT domain, org_id, provider_id, sso_enforced,
                       created_at, updated_at, created_by_user_id, deleted_at
                  FROM auth_provider_domains
                 WHERE domain = %s AND deleted_at IS NULL
                """,
                (domain.strip().lower(),),
            )
            rows = cur.fetchall()
        return tuple(_row_to_domain(row) for row in rows)

    def list_for_org(
        self, *, org_id: str, include_deleted: bool = False, conn: Any | None = None
    ) -> tuple[AuthProviderDomainRecord, ...]:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        with self._cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT domain, org_id, provider_id, sso_enforced,
                       created_at, updated_at, created_by_user_id, deleted_at
                  FROM auth_provider_domains
                 WHERE org_id = %s {clause}
                """,
                (org_id,),
            )
            rows = cur.fetchall()
        return tuple(_row_to_domain(row) for row in rows)

    def soft_delete(
        self, *, domain: str, org_id: str, provider_id: str, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE auth_provider_domains
                   SET deleted_at = NOW()
                 WHERE domain = %s AND org_id = %s AND provider_id = %s
                   AND deleted_at IS NULL
                """,
                (domain.strip().lower(), org_id, provider_id),
            )
            return bool(cur.rowcount)


def _row_to_domain(row: Any) -> AuthProviderDomainRecord:
    return AuthProviderDomainRecord(
        domain=row[0],
        org_id=row[1],
        provider_id=row[2],
        sso_enforced=row[3],
        created_at=row[4],
        updated_at=row[5],
        created_by_user_id=row[6],
        deleted_at=row[7],
    )


# ---------------------------------------------------------------------------
# MagicLinkToken store
# ---------------------------------------------------------------------------


class MagicLinkTokenStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[Any]: ...  # pragma: no cover

    def create(
        self, record: MagicLinkTokenRecord, *, conn: Any | None = None
    ) -> MagicLinkTokenRecord: ...

    def get_by_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> MagicLinkTokenRecord | None: ...

    def mark_consumed(
        self,
        *,
        token_id: str,
        consumed_session_id: str | None,
        conn: Any | None = None,
    ) -> bool: ...

    def sweep_expired(self, *, before: datetime, conn: Any | None = None) -> int: ...


@dataclass
class InMemoryMagicLinkTokenStore:
    rows: dict[str, MagicLinkTokenRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def create(
        self, record: MagicLinkTokenRecord, *, conn: Any | None = None
    ) -> MagicLinkTokenRecord:
        del conn
        for existing in self.rows.values():
            if existing.token_hash == record.token_hash:
                raise ValueError("magic_link token_hash collision")
        self.rows[record.token_id] = record
        return record

    def get_by_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> MagicLinkTokenRecord | None:
        del conn
        for record in self.rows.values():
            if record.token_hash == token_hash:
                return record
        return None

    def mark_consumed(
        self,
        *,
        token_id: str,
        consumed_session_id: str | None,
        conn: Any | None = None,
    ) -> bool:
        del conn
        record = self.rows.get(token_id)
        if record is None or record.consumed_at is not None:
            return False
        self.rows[token_id] = record.model_copy(
            update={
                "consumed_at": _now(),
                "consumed_session_id": consumed_session_id,
            }
        )
        return True

    def sweep_expired(self, *, before: datetime, conn: Any | None = None) -> int:
        del conn
        to_drop = [
            tid
            for tid, r in self.rows.items()
            if r.consumed_at is not None or r.expires_at < before
        ]
        for tid in to_drop:
            del self.rows[tid]
        return len(to_drop)


@dataclass
class PostgresMagicLinkTokenStore:
    pool: Any  # PostgresConnectionPool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.pool.transaction() as conn:
            yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self.pool.transaction() as txn:
            with txn.cursor() as cur:
                yield cur

    def create(
        self, record: MagicLinkTokenRecord, *, conn: Any | None = None
    ) -> MagicLinkTokenRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO magic_link_tokens (
                    token_id, org_id, user_id, email_lower, token_hash,
                    candidate_orgs, return_to, requested_ip, requested_ua,
                    created_at, expires_at, consumed_at, consumed_session_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL)
                """,
                (
                    record.token_id,
                    record.org_id,
                    record.user_id,
                    record.email_lower,
                    record.token_hash,
                    json.dumps(record.candidate_orgs),
                    record.return_to,
                    record.requested_ip,
                    record.requested_ua,
                    record.created_at,
                    record.expires_at,
                ),
            )
        return record

    def get_by_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> MagicLinkTokenRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT token_id, org_id, user_id, email_lower, token_hash,
                       candidate_orgs, return_to, requested_ip, requested_ua,
                       created_at, expires_at, consumed_at, consumed_session_id
                  FROM magic_link_tokens
                 WHERE token_hash = %s
                """,
                (token_hash,),
            )
            row = cur.fetchone()
        return _row_to_magic_link(row) if row else None

    def mark_consumed(
        self,
        *,
        token_id: str,
        consumed_session_id: str | None,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE magic_link_tokens
                   SET consumed_at = NOW(),
                       consumed_session_id = %s
                 WHERE token_id = %s
                   AND consumed_at IS NULL
                """,
                (consumed_session_id, token_id),
            )
            return bool(cur.rowcount)

    def sweep_expired(self, *, before: datetime, conn: Any | None = None) -> int:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                DELETE FROM magic_link_tokens
                 WHERE consumed_at IS NOT NULL
                    OR expires_at < %s
                """,
                (before,),
            )
            return cur.rowcount or 0


def _row_to_magic_link(row: Any) -> MagicLinkTokenRecord:
    candidate_orgs = row[5]
    if isinstance(candidate_orgs, str):
        candidate_orgs = json.loads(candidate_orgs)
    return MagicLinkTokenRecord(
        token_id=row[0],
        org_id=row[1],
        user_id=row[2],
        email_lower=row[3],
        token_hash=row[4],
        candidate_orgs=candidate_orgs or [],
        return_to=row[6],
        requested_ip=row[7],
        requested_ua=row[8],
        created_at=row[9],
        expires_at=row[10],
        consumed_at=row[11],
        consumed_session_id=row[12],
    )


__all__ = [
    "AuthProviderDomainStore",
    "InMemoryAuthProviderDomainStore",
    "PostgresAuthProviderDomainStore",
    "MagicLinkTokenStore",
    "InMemoryMagicLinkTokenStore",
    "PostgresMagicLinkTokenStore",
]
