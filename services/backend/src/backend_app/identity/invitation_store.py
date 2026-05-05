"""Invitation persistence (PR 4.2).

Mirrors :mod:`backend_app.identity.scim_store` so :class:`InvitationsService`
composes the same way :class:`ScimService` does. Two adapters: in-memory for
unit tests / dev, Postgres for production.

The token is sha256(plaintext) at rest. The plaintext is returned by the
service exactly once at create time; the row never carries the secret.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import InvitationRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class InvitationStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[Any]: ...  # pragma: no cover

    def create(
        self, record: InvitationRecord, *, conn: Any | None = None
    ) -> InvitationRecord: ...

    def get(
        self, *, org_id: str, invite_id: str, conn: Any | None = None
    ) -> InvitationRecord | None: ...

    def get_by_token_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> InvitationRecord | None: ...

    def list_pending(
        self, *, org_id: str, now: datetime | None = None
    ) -> tuple[InvitationRecord, ...]: ...

    def revoke(
        self,
        *,
        invite_id: str,
        revoked_by_user_id: str,
        conn: Any | None = None,
    ) -> bool: ...

    def mark_accepted(
        self,
        *,
        invite_id: str,
        accepted_user_id: str,
        conn: Any | None = None,
    ) -> bool: ...

    def get_active_for_email(
        self, *, org_id: str, email: str, conn: Any | None = None
    ) -> InvitationRecord | None: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryInvitationStore:
    invitations: dict[str, InvitationRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def create(
        self, record: InvitationRecord, *, conn: Any | None = None
    ) -> InvitationRecord:
        del conn
        # Enforce the partial unique-active index in code so the in-memory
        # adapter can fail the same way Postgres would.
        if self.get_active_for_email(org_id=record.org_id, email=record.email):
            raise ValueError("an active invitation for this email already exists")
        if any(r.token_hash == record.token_hash for r in self.invitations.values()):
            raise ValueError("invitation token_hash collision")
        self.invitations[record.invite_id] = record
        return record

    def get(
        self, *, org_id: str, invite_id: str, conn: Any | None = None
    ) -> InvitationRecord | None:
        del conn
        record = self.invitations.get(invite_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def get_by_token_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> InvitationRecord | None:
        del conn
        for record in self.invitations.values():
            if record.token_hash == token_hash:
                return record
        return None

    def list_pending(
        self, *, org_id: str, now: datetime | None = None
    ) -> tuple[InvitationRecord, ...]:
        cutoff = now or _now()
        rows = [
            r
            for r in self.invitations.values()
            if r.org_id == org_id
            and r.accepted_at is None
            and r.revoked_at is None
            and r.expires_at > cutoff
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return tuple(rows)

    def revoke(
        self,
        *,
        invite_id: str,
        revoked_by_user_id: str,
        conn: Any | None = None,
    ) -> bool:
        del conn
        record = self.invitations.get(invite_id)
        if (
            record is None
            or record.revoked_at is not None
            or record.accepted_at is not None
        ):
            return False
        self.invitations[invite_id] = record.model_copy(
            update={"revoked_at": _now(), "revoked_by_user_id": revoked_by_user_id}
        )
        return True

    def mark_accepted(
        self,
        *,
        invite_id: str,
        accepted_user_id: str,
        conn: Any | None = None,
    ) -> bool:
        del conn
        record = self.invitations.get(invite_id)
        if (
            record is None
            or record.accepted_at is not None
            or record.revoked_at is not None
        ):
            return False
        self.invitations[invite_id] = record.model_copy(
            update={"accepted_at": _now(), "accepted_user_id": accepted_user_id}
        )
        return True

    def get_active_for_email(
        self, *, org_id: str, email: str, conn: Any | None = None
    ) -> InvitationRecord | None:
        del conn
        normalized = email.strip().lower()
        for record in self.invitations.values():
            if (
                record.org_id == org_id
                and record.email == normalized
                and record.accepted_at is None
                and record.revoked_at is None
            ):
                return record
        return None


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresInvitationStore:
    """Postgres-backed invitation store. Uses the shared connection pool.

    Methods accept an optional ``conn`` so the service layer can wrap an
    invitations write + identity_audit_events append in one transaction
    (matches the pattern in ``PostgresScimStore`` / ``PostgresIdentityStore``).
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

    def create(
        self, record: InvitationRecord, *, conn: Any | None = None
    ) -> InvitationRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO invitations (
                    invite_id, org_id, email, role_id, token_hash, token_prefix,
                    created_by_user_id, created_at, expires_at,
                    accepted_at, accepted_user_id, revoked_at, revoked_by_user_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.invite_id,
                    record.org_id,
                    record.email,
                    record.role_id,
                    record.token_hash,
                    record.token_prefix,
                    record.created_by_user_id,
                    record.created_at,
                    record.expires_at,
                    record.accepted_at,
                    record.accepted_user_id,
                    record.revoked_at,
                    record.revoked_by_user_id,
                ),
            )
        return record

    def get(
        self, *, org_id: str, invite_id: str, conn: Any | None = None
    ) -> InvitationRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM invitations WHERE org_id = %s AND invite_id = %s",
                (org_id, invite_id),
            )
            row = cur.fetchone()
        return _row_to_invitation(row) if row else None

    def get_by_token_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> InvitationRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM invitations WHERE token_hash = %s",
                (token_hash,),
            )
            row = cur.fetchone()
        return _row_to_invitation(row) if row else None

    def list_pending(
        self, *, org_id: str, now: datetime | None = None
    ) -> tuple[InvitationRecord, ...]:
        cutoff = now or _now()
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM invitations
                WHERE org_id = %s
                  AND accepted_at IS NULL
                  AND revoked_at IS NULL
                  AND expires_at > %s
                ORDER BY created_at DESC
                """,
                (org_id, cutoff),
            )
            rows = cur.fetchall()
        return tuple(_row_to_invitation(row) for row in rows)

    def revoke(
        self,
        *,
        invite_id: str,
        revoked_by_user_id: str,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE invitations
                SET revoked_at = %s, revoked_by_user_id = %s
                WHERE invite_id = %s
                  AND revoked_at IS NULL
                  AND accepted_at IS NULL
                """,
                (_now(), revoked_by_user_id, invite_id),
            )
            return bool(cur.rowcount)

    def mark_accepted(
        self,
        *,
        invite_id: str,
        accepted_user_id: str,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE invitations
                SET accepted_at = %s, accepted_user_id = %s
                WHERE invite_id = %s
                  AND accepted_at IS NULL
                  AND revoked_at IS NULL
                """,
                (_now(), accepted_user_id, invite_id),
            )
            return bool(cur.rowcount)

    def get_active_for_email(
        self, *, org_id: str, email: str, conn: Any | None = None
    ) -> InvitationRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT * FROM invitations
                WHERE org_id = %s
                  AND lower(email) = lower(%s)
                  AND accepted_at IS NULL
                  AND revoked_at IS NULL
                LIMIT 1
                """,
                (org_id, email),
            )
            row = cur.fetchone()
        return _row_to_invitation(row) if row else None


def _row_to_invitation(row: dict[str, Any]) -> InvitationRecord:
    return InvitationRecord(
        invite_id=row["invite_id"],
        org_id=row["org_id"],
        email=row["email"],
        role_id=row["role_id"],
        token_hash=row["token_hash"],
        token_prefix=row["token_prefix"],
        created_by_user_id=row["created_by_user_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        accepted_at=row.get("accepted_at"),
        accepted_user_id=row.get("accepted_user_id"),
        revoked_at=row.get("revoked_at"),
        revoked_by_user_id=row.get("revoked_by_user_id"),
    )


__all__ = [
    "InMemoryInvitationStore",
    "InvitationStore",
    "PostgresInvitationStore",
]
