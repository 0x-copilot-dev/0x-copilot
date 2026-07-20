"""Account-merge saga persistence (PRD docs/plan/account-linking §6.3).

Mirrors ``siwe_store.py``: a ``Protocol`` with an in-memory adapter for
tests/dev and a Postgres adapter for production (table from
``migrations/0038_account_merge.sql``). The record's ``state`` is the last
COMPLETED saga checkpoint; the partial unique index on
``(absorbed_org_id, absorbed_user_id) WHERE state <> 'completed'`` is the DB
guard against two concurrent merges of the same absorbed account.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import AccountMergeRecord, AccountMergeState


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccountMergeStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def create_merge(
        self, record: AccountMergeRecord, *, conn: Any | None = None
    ) -> AccountMergeRecord: ...

    def get_merge(self, *, merge_id: str) -> AccountMergeRecord | None: ...

    def update_merge(
        self, record: AccountMergeRecord, *, conn: Any | None = None
    ) -> AccountMergeRecord:
        """Persist state/error/counts/completed_at (stamps updated_at)."""

    def find_by_absorbed(
        self, *, absorbed_org_id: str, absorbed_user_id: str
    ) -> tuple[AccountMergeRecord, ...]:
        """All merges for an absorbed account, newest first (idempotency +
        resume lookups)."""


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryAccountMergeStore:
    merges: dict[str, AccountMergeRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def create_merge(
        self, record: AccountMergeRecord, *, conn: Any | None = None
    ) -> AccountMergeRecord:
        del conn
        active = [
            row
            for row in self.merges.values()
            if row.absorbed_org_id == record.absorbed_org_id
            and row.absorbed_user_id == record.absorbed_user_id
            and row.state != AccountMergeState.COMPLETED
        ]
        if active:
            raise ValueError("a merge for this absorbed account is already active")
        self.merges[record.merge_id] = record
        return record

    def get_merge(self, *, merge_id: str) -> AccountMergeRecord | None:
        return self.merges.get(merge_id)

    def update_merge(
        self, record: AccountMergeRecord, *, conn: Any | None = None
    ) -> AccountMergeRecord:
        del conn
        updated = record.model_copy(update={"updated_at": _now()})
        self.merges[record.merge_id] = updated
        return updated

    def find_by_absorbed(
        self, *, absorbed_org_id: str, absorbed_user_id: str
    ) -> tuple[AccountMergeRecord, ...]:
        rows = [
            row
            for row in self.merges.values()
            if row.absorbed_org_id == absorbed_org_id
            and row.absorbed_user_id == absorbed_user_id
        ]
        return tuple(sorted(rows, key=lambda r: r.started_at, reverse=True))


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresAccountMergeStore:
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

    def create_merge(
        self, record: AccountMergeRecord, *, conn: Any | None = None
    ) -> AccountMergeRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO account_merges (
                    merge_id, survivor_org_id, survivor_user_id,
                    absorbed_org_id, absorbed_user_id, state, proof_ref,
                    error, counts, started_at, updated_at, completed_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.merge_id,
                    record.survivor_org_id,
                    record.survivor_user_id,
                    record.absorbed_org_id,
                    record.absorbed_user_id,
                    record.state.value,
                    record.proof_ref,
                    record.error,
                    json.dumps(record.counts),
                    record.started_at,
                    record.updated_at,
                    record.completed_at,
                ),
            )
        return record

    def get_merge(self, *, merge_id: str) -> AccountMergeRecord | None:
        with self._cursor(None) as cur:
            cur.execute("SELECT * FROM account_merges WHERE merge_id = %s", (merge_id,))
            row = cur.fetchone()
        return AccountMergeRecord.model_validate(row) if row else None

    def update_merge(
        self, record: AccountMergeRecord, *, conn: Any | None = None
    ) -> AccountMergeRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE account_merges SET
                    state = %s, error = %s, counts = %s,
                    updated_at = %s, completed_at = %s
                WHERE merge_id = %s
                """,
                (
                    updated.state.value,
                    updated.error,
                    json.dumps(updated.counts),
                    updated.updated_at,
                    updated.completed_at,
                    updated.merge_id,
                ),
            )
        return updated

    def find_by_absorbed(
        self, *, absorbed_org_id: str, absorbed_user_id: str
    ) -> tuple[AccountMergeRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM account_merges
                WHERE absorbed_org_id = %s AND absorbed_user_id = %s
                ORDER BY started_at DESC
                """,
                (absorbed_org_id, absorbed_user_id),
            )
            rows = cur.fetchall()
        return tuple(AccountMergeRecord.model_validate(row) for row in rows)


__all__ = [
    "AccountMergeStore",
    "InMemoryAccountMergeStore",
    "PostgresAccountMergeStore",
]
