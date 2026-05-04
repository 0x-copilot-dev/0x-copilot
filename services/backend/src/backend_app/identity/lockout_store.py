"""Account-lockout store (A8): per-org policy + active lockouts.

Mirrors the shape of ``password_store`` and ``oidc_store`` so each adapter
sits in its own file rather than bloating ``identity/store.py``. The
``IdentityStore`` already owns ``login_attempts`` reads (where the sliding
window counts failures) — this module owns the *write* side: who is
currently locked, and what the per-org policy says.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    AccountLockoutRecord,
    LockoutPolicyRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LockoutStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Policy ------------------------------------------------------------
    def get_policy(self, *, org_id: str) -> LockoutPolicyRecord | None: ...
    def upsert_policy(
        self, record: LockoutPolicyRecord, *, conn: Any | None = None
    ) -> LockoutPolicyRecord: ...

    # Active lockouts ---------------------------------------------------
    def get_active_lockout(
        self, *, org_id: str, user_id: str
    ) -> AccountLockoutRecord | None: ...
    def create_lockout(
        self, record: AccountLockoutRecord, *, conn: Any | None = None
    ) -> AccountLockoutRecord | None:
        """INSERT ... ON CONFLICT DO NOTHING. Returns None when a concurrent
        write already created an active lockout for the same (org, user)."""

    def list_lockouts(
        self, *, org_id: str, active_only: bool = False, limit: int = 100
    ) -> tuple[AccountLockoutRecord, ...]: ...
    def unlock(
        self,
        *,
        org_id: str,
        user_id: str,
        unlocked_by_user_id: str | None,
        reason: str | None,
        conn: Any | None = None,
    ) -> AccountLockoutRecord | None:
        """Idempotent: returns the freshly-unlocked record, or None when
        there was no active lockout."""

    def count_lockouts_since(
        self, *, org_id: str, user_id: str, since: datetime
    ) -> int: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryLockoutStore:
    policies_by_org: dict[str, LockoutPolicyRecord] = field(default_factory=dict)
    lockouts: dict[str, AccountLockoutRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Policy ------------------------------------------------------------
    def get_policy(self, *, org_id: str) -> LockoutPolicyRecord | None:
        return self.policies_by_org.get(org_id)

    def upsert_policy(
        self, record: LockoutPolicyRecord, *, conn: Any | None = None
    ) -> LockoutPolicyRecord:
        del conn
        updated = record.model_copy(update={"updated_at": _now()})
        self.policies_by_org[record.org_id] = updated
        return updated

    # Active lockouts ---------------------------------------------------
    def get_active_lockout(
        self, *, org_id: str, user_id: str
    ) -> AccountLockoutRecord | None:
        for record in self.lockouts.values():
            if (
                record.org_id == org_id
                and record.user_id == user_id
                and record.unlocked_at is None
            ):
                return record
        return None

    def create_lockout(
        self, record: AccountLockoutRecord, *, conn: Any | None = None
    ) -> AccountLockoutRecord | None:
        del conn
        # Mirror the partial-unique constraint: refuse a second active
        # lockout for the same (org, user). Returns None to match the
        # Postgres ON CONFLICT DO NOTHING semantics.
        if self.get_active_lockout(org_id=record.org_id, user_id=record.user_id):
            return None
        self.lockouts[record.lockout_id] = record
        return record

    def list_lockouts(
        self, *, org_id: str, active_only: bool = False, limit: int = 100
    ) -> tuple[AccountLockoutRecord, ...]:
        rows = [
            record
            for record in self.lockouts.values()
            if record.org_id == org_id
            and (not active_only or record.unlocked_at is None)
        ]
        rows.sort(key=lambda r: r.locked_at, reverse=True)
        return tuple(rows[:limit])

    def unlock(
        self,
        *,
        org_id: str,
        user_id: str,
        unlocked_by_user_id: str | None,
        reason: str | None,
        conn: Any | None = None,
    ) -> AccountLockoutRecord | None:
        del conn
        active = self.get_active_lockout(org_id=org_id, user_id=user_id)
        if active is None:
            return None
        unlocked = active.model_copy(
            update={
                "unlocked_at": _now(),
                "unlocked_by_user_id": unlocked_by_user_id,
                "unlock_reason": reason,
            }
        )
        self.lockouts[active.lockout_id] = unlocked
        return unlocked

    def count_lockouts_since(
        self, *, org_id: str, user_id: str, since: datetime
    ) -> int:
        return sum(
            1
            for record in self.lockouts.values()
            if record.org_id == org_id
            and record.user_id == user_id
            and record.locked_at >= since
        )


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresLockoutStore:
    """Postgres-backed lockout store. Uses the shared connection pool."""

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
        with self._pool.connection() as outer:
            with outer.cursor() as cur:
                yield cur

    # Policy ------------------------------------------------------------
    def get_policy(self, *, org_id: str) -> LockoutPolicyRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM lockout_policies WHERE org_id = %s",
                (org_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return LockoutPolicyRecord.model_validate(dict(row))

    def upsert_policy(
        self, record: LockoutPolicyRecord, *, conn: Any | None = None
    ) -> LockoutPolicyRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO lockout_policies (
                    policy_id, org_id, enforce_lockout, max_failures,
                    failure_window_seconds, lockout_duration_seconds,
                    permanent_after_n_lockouts, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (org_id) DO UPDATE SET
                    enforce_lockout = EXCLUDED.enforce_lockout,
                    max_failures = EXCLUDED.max_failures,
                    failure_window_seconds = EXCLUDED.failure_window_seconds,
                    lockout_duration_seconds = EXCLUDED.lockout_duration_seconds,
                    permanent_after_n_lockouts = EXCLUDED.permanent_after_n_lockouts,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    updated.policy_id,
                    updated.org_id,
                    updated.enforce_lockout,
                    updated.max_failures,
                    updated.failure_window_seconds,
                    updated.lockout_duration_seconds,
                    updated.permanent_after_n_lockouts,
                    updated.updated_at,
                ),
            )
        return updated

    # Active lockouts ---------------------------------------------------
    def get_active_lockout(
        self, *, org_id: str, user_id: str
    ) -> AccountLockoutRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM account_lockouts
                WHERE org_id = %s AND user_id = %s AND unlocked_at IS NULL
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return AccountLockoutRecord.model_validate(dict(row))

    def create_lockout(
        self, record: AccountLockoutRecord, *, conn: Any | None = None
    ) -> AccountLockoutRecord | None:
        # ``ON CONFLICT DO NOTHING`` keyed off the partial-unique index
        # ``idx_account_lockouts_active``. Two concurrent failed-login
        # workers can both fire ``record_failure`` and only one will land
        # an active row; the other's INSERT becomes a no-op.
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO account_lockouts (
                    lockout_id, org_id, user_id, locked_at, lock_reason,
                    auto_unlock_at, unlocked_at, unlocked_by_user_id,
                    unlock_reason
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                (
                    record.lockout_id,
                    record.org_id,
                    record.user_id,
                    record.locked_at,
                    record.lock_reason,
                    record.auto_unlock_at,
                    record.unlocked_at,
                    record.unlocked_by_user_id,
                    record.unlock_reason,
                ),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return AccountLockoutRecord.model_validate(dict(row))

    def list_lockouts(
        self, *, org_id: str, active_only: bool = False, limit: int = 100
    ) -> tuple[AccountLockoutRecord, ...]:
        clause = "WHERE org_id = %s"
        params: list[Any] = [org_id]
        if active_only:
            clause += " AND unlocked_at IS NULL"
        with self._cursor(None) as cur:
            cur.execute(
                f"SELECT * FROM account_lockouts {clause} "
                "ORDER BY locked_at DESC LIMIT %s",
                tuple(params + [limit]),
            )
            rows = cur.fetchall()
        return tuple(AccountLockoutRecord.model_validate(dict(row)) for row in rows)

    def unlock(
        self,
        *,
        org_id: str,
        user_id: str,
        unlocked_by_user_id: str | None,
        reason: str | None,
        conn: Any | None = None,
    ) -> AccountLockoutRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE account_lockouts
                SET unlocked_at = %s,
                    unlocked_by_user_id = %s,
                    unlock_reason = %s
                WHERE org_id = %s AND user_id = %s AND unlocked_at IS NULL
                RETURNING *
                """,
                (
                    _now(),
                    unlocked_by_user_id,
                    reason,
                    org_id,
                    user_id,
                ),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return AccountLockoutRecord.model_validate(dict(row))

    def count_lockouts_since(
        self, *, org_id: str, user_id: str, since: datetime
    ) -> int:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT count(*) AS n FROM account_lockouts
                WHERE org_id = %s AND user_id = %s AND locked_at >= %s
                """,
                (org_id, user_id, since),
            )
            row = cur.fetchone()
        return int(row["n"]) if row else 0


__all__ = [
    "InMemoryLockoutStore",
    "LockoutStore",
    "PostgresLockoutStore",
]
