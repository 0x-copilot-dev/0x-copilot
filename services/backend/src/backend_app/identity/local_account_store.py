"""Stores for the ``local_accounts`` identity edge (device account).

The row is a deployment-wide SINGLETON (unique index on a constant in the
baseline schema): "Use locally" always resolves to the one device account.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend_app.contracts import LocalAccountRecord
from backend_app.identity.principals import with_default_principal


class LocalAccountStore(Protocol):
    def get_singleton(self) -> LocalAccountRecord | None:
        """The device account edge, or None before first "Use locally"."""

    def create(self, record: LocalAccountRecord) -> LocalAccountRecord:
        """Insert the edge. Loses gracefully to a concurrent creator: on the
        singleton conflict the EXISTING row is returned (find-or-create must
        never fork a second device account)."""


class InMemoryLocalAccountStore:
    def __init__(self) -> None:
        self.rows: dict[str, LocalAccountRecord] = {}

    def get_singleton(self) -> LocalAccountRecord | None:
        return next(iter(self.rows.values()), None)

    def create(self, record: LocalAccountRecord) -> LocalAccountRecord:
        existing = self.get_singleton()
        if existing is not None:
            return existing
        record = with_default_principal(record)
        self.rows[record.local_account_id] = record
        return record


class PostgresLocalAccountStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def get_singleton(self) -> LocalAccountRecord | None:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM local_accounts LIMIT 1")
                row = cur.fetchone()
        return LocalAccountRecord.model_validate(row) if row else None

    def create(self, record: LocalAccountRecord) -> LocalAccountRecord:
        record = with_default_principal(record)
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                # The singleton unique index arbitrates the race: the loser's
                # insert is a no-op and the winner's row is returned.
                cur.execute(
                    """
                    INSERT INTO local_accounts (
                        local_account_id, org_id, user_id, principal_id,
                        created_at
                    ) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        record.local_account_id,
                        record.org_id,
                        record.user_id,
                        record.principal_id,
                        record.created_at,
                    ),
                )
        return self.get_singleton() or record
