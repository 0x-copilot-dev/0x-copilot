"""SIWE persistence: single-use nonces + wallet → user identity links.

Mirrors ``oidc_store.py``: a ``Protocol`` with an in-memory adapter for
tests/dev and a Postgres adapter for production (tables from
``migrations/0035_siwe.sql``). ``consume_nonce`` MUST be an atomic
compare-and-set so two concurrent verifies for the same nonce can never
both succeed.
"""

from __future__ import annotations

import secrets as _secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import SiweNonceRecord, WalletIdentityRecord
from backend_app.identity.principals import with_default_principal


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SiweStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Nonces ------------------------------------------------------------
    def create_nonce(
        self, record: SiweNonceRecord, *, conn: Any | None = None
    ) -> SiweNonceRecord: ...

    def consume_nonce(
        self, *, nonce: str, conn: Any | None = None
    ) -> SiweNonceRecord | None:
        """Atomic compare-and-set: marks the row consumed and returns it.

        Returns ``None`` when the nonce is unknown or already consumed
        (replay defense). Expired-but-unconsumed rows ARE returned with
        ``consumed_at`` stamped — the caller distinguishes ``nonce_expired``
        from ``nonce_invalid`` by checking ``expires_at`` itself.
        """

    # Wallet identities ---------------------------------------------------
    def create_wallet_identity(
        self, record: WalletIdentityRecord, *, conn: Any | None = None
    ) -> WalletIdentityRecord: ...

    def get_wallet_identity(self, *, address: str) -> WalletIdentityRecord | None: ...

    def get_wallet_identity_by_user(
        self, *, org_id: str, user_id: str
    ) -> WalletIdentityRecord | None:
        """Reverse lookup: the wallet linked to a user (by org + user id).

        The by-address ``get_wallet_identity`` cannot answer "does the current
        user have a wallet?" for the profile route. Returns the first-linked
        wallet when a user has more than one (deterministic "the" profile
        wallet), or ``None`` for a non-wallet account.
        """

    def list_wallets_by_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[WalletIdentityRecord, ...]:
        """All wallets linked to a user, oldest first.

        Account-linking (PRD FR-L4): once a user can link multiple wallets, the
        profile's "Linked accounts" list needs every wallet — the singular
        ``get_wallet_identity_by_user`` remains for "the" profile wallet.
        """
        ...

    def delete_wallet_identity(
        self, *, wallet_id: str, org_id: str, user_id: str
    ) -> bool:
        """Unlink (FR-L5): delete the row IF it belongs to the caller.

        Owner-scoped so a caller can never unlink someone else's wallet.
        Returns ``False`` for unknown/foreign rows.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemorySiweStore:
    nonces: dict[str, SiweNonceRecord] = field(default_factory=dict)
    wallet_identities: dict[str, WalletIdentityRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Nonces ------------------------------------------------------------
    def create_nonce(
        self, record: SiweNonceRecord, *, conn: Any | None = None
    ) -> SiweNonceRecord:
        del conn
        if any(row.nonce == record.nonce for row in self.nonces.values()):
            raise ValueError("SIWE nonce already exists")
        self.nonces[record.nonce_id] = record
        return record

    def consume_nonce(
        self, *, nonce: str, conn: Any | None = None
    ) -> SiweNonceRecord | None:
        del conn
        for nonce_id, row in list(self.nonces.items()):
            # Constant-time comparison: the nonce is a bearer secret for
            # the duration of its TTL, so the lookup must not leak prefix
            # matches through timing.
            if not _secrets.compare_digest(row.nonce, nonce):
                continue
            if row.consumed_at is not None:
                # Replay attempt — refuse.
                return None
            consumed = row.model_copy(update={"consumed_at": _now()})
            self.nonces[nonce_id] = consumed
            return consumed
        return None

    # Wallet identities ---------------------------------------------------
    def create_wallet_identity(
        self, record: WalletIdentityRecord, *, conn: Any | None = None
    ) -> WalletIdentityRecord:
        del conn
        record = with_default_principal(record)
        if any(
            row.address == record.address for row in self.wallet_identities.values()
        ):
            raise ValueError("wallet identity already linked for this address")
        self.wallet_identities[record.wallet_id] = record
        return record

    def get_wallet_identity(self, *, address: str) -> WalletIdentityRecord | None:
        needle = address.lower()
        for row in self.wallet_identities.values():
            if row.address == needle:
                return row
        return None

    def get_wallet_identity_by_user(
        self, *, org_id: str, user_id: str
    ) -> WalletIdentityRecord | None:
        matches = self.list_wallets_by_user(org_id=org_id, user_id=user_id)
        # First-linked wins (deterministic "the" profile wallet).
        return matches[0] if matches else None

    def list_wallets_by_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[WalletIdentityRecord, ...]:
        matches = [
            row
            for row in self.wallet_identities.values()
            if row.org_id == org_id and row.user_id == user_id
        ]
        return tuple(sorted(matches, key=lambda row: row.created_at))

    def delete_wallet_identity(
        self, *, wallet_id: str, org_id: str, user_id: str
    ) -> bool:
        row = self.wallet_identities.get(wallet_id)
        if row is None or row.org_id != org_id or row.user_id != user_id:
            return False
        del self.wallet_identities[wallet_id]
        return True


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresSiweStore:
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

    # Nonces ------------------------------------------------------------
    def create_nonce(
        self, record: SiweNonceRecord, *, conn: Any | None = None
    ) -> SiweNonceRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO siwe_nonces (
                    nonce_id, nonce, address, chain_id,
                    issued_at, expires_at, consumed_at, ip, user_agent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.nonce_id,
                    record.nonce,
                    record.address,
                    record.chain_id,
                    record.issued_at,
                    record.expires_at,
                    record.consumed_at,
                    record.ip,
                    record.user_agent,
                ),
            )
        return record

    def consume_nonce(
        self, *, nonce: str, conn: Any | None = None
    ) -> SiweNonceRecord | None:
        # Atomic compare-and-set. Unlike oidc_authentications we do NOT
        # filter on expires_at here: the caller wants to tell
        # ``nonce_expired`` apart from ``nonce_invalid``, so expired rows
        # are consumed and returned for inspection.
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE siwe_nonces
                SET consumed_at = now()
                WHERE nonce = %s
                  AND consumed_at IS NULL
                RETURNING *
                """,
                (nonce,),
            )
            row = cur.fetchone()
        return SiweNonceRecord.model_validate(row) if row else None

    # Wallet identities ---------------------------------------------------
    def create_wallet_identity(
        self, record: WalletIdentityRecord, *, conn: Any | None = None
    ) -> WalletIdentityRecord:
        record = with_default_principal(record)
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO wallet_identities (
                    wallet_id, address, org_id, user_id, chain_id, created_at,
                    principal_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.wallet_id,
                    record.address,
                    record.org_id,
                    record.user_id,
                    record.chain_id,
                    record.created_at,
                    record.principal_id,
                ),
            )
        return record

    def get_wallet_identity(self, *, address: str) -> WalletIdentityRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM wallet_identities WHERE address = %s",
                (address.lower(),),
            )
            row = cur.fetchone()
        return WalletIdentityRecord.model_validate(row) if row else None

    def get_wallet_identity_by_user(
        self, *, org_id: str, user_id: str
    ) -> WalletIdentityRecord | None:
        # First-linked wallet is "the" profile wallet. wallet_identities has a
        # UNIQUE index on address; the (org_id, user_id) filter is a small scan
        # today (a personal org has ~1 wallet), so no new index is required for
        # the single-user desktop. Add one if team wallet-linking ever grows.
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM wallet_identities "
                "WHERE org_id = %s AND user_id = %s "
                "ORDER BY created_at ASC LIMIT 1",
                (org_id, user_id),
            )
            row = cur.fetchone()
        return WalletIdentityRecord.model_validate(row) if row else None

    def list_wallets_by_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[WalletIdentityRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM wallet_identities "
                "WHERE org_id = %s AND user_id = %s "
                "ORDER BY created_at ASC",
                (org_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(WalletIdentityRecord.model_validate(row) for row in rows)

    def delete_wallet_identity(
        self, *, wallet_id: str, org_id: str, user_id: str
    ) -> bool:
        with self._cursor(None) as cur:
            cur.execute(
                "DELETE FROM wallet_identities "
                "WHERE wallet_id = %s AND org_id = %s AND user_id = %s",
                (wallet_id, org_id, user_id),
            )
            return bool(cur.rowcount)


__all__ = [
    "InMemorySiweStore",
    "PostgresSiweStore",
    "SiweStore",
]
