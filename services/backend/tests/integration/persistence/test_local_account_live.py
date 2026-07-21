"""LIVE-Postgres test for the device-account singleton (D4-A).

The in-memory store can only *imitate* the arbitration; the real guarantee is
the baseline's unique index on a constant expression — at most ONE
local_accounts row can ever exist, so concurrent "Use locally" races and
lost client state can never fork a second device account. Gated on
BACKEND_MERGE_TEST_DATABASE_URL (shares the gate cluster + CI job).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BACKEND_MERGE_TEST_DATABASE_URL"),
    reason="Set BACKEND_MERGE_TEST_DATABASE_URL to a disposable Postgres database.",
)


@pytest.fixture(scope="module")
def pool() -> Iterator[Any]:
    pytest.importorskip("psycopg")
    from backend_app.db.migrate import MigrationRunner
    from backend_app.store import PostgresConnectionPool

    database_url = os.environ["BACKEND_MERGE_TEST_DATABASE_URL"]
    MigrationRunner.apply(database_url)
    resolved = PostgresConnectionPool(database_url)
    try:
        yield resolved
    finally:
        resolved.close()


def _mk_account(pool: Any, tag: str) -> tuple[str, str]:
    from backend_app.contracts import (
        OrganizationMemberRecord,
        OrganizationMemberSource,
        OrganizationRecord,
        UserRecord,
    )
    from backend_app.identity.store import PostgresIdentityStore

    ids = PostgresIdentityStore(pool)
    suffix = uuid.uuid4().hex[:8]
    org_id, user_id = f"org_{tag}_{suffix}", f"usr_{tag}_{suffix}"
    ids.create_organization(
        OrganizationRecord(org_id=org_id, display_name=tag, slug=org_id)
    )
    ids.create_user(
        UserRecord(
            user_id=user_id,
            org_id=org_id,
            primary_email=f"{user_id}@local.invalid",
            display_name="Local account",
        )
    )
    ids.add_member(
        OrganizationMemberRecord(
            org_id=org_id, user_id=user_id, source=OrganizationMemberSource.LOCAL
        )
    )
    return org_id, user_id


class TestDeviceSingletonLive:
    def test_second_insert_loses_to_the_index_and_returns_winner(
        self, pool: Any
    ) -> None:
        from backend_app.contracts import LocalAccountRecord
        from backend_app.identity.local_account_store import (
            PostgresLocalAccountStore,
        )

        store = PostgresLocalAccountStore(pool)
        # The module shares the gate DB with other tests — only assert when
        # WE create the singleton (idempotent across gate orderings).
        first = store.get_singleton()
        if first is None:
            org_a, usr_a = _mk_account(pool, "deva")
            first = store.create(LocalAccountRecord(org_id=org_a, user_id=usr_a))
            assert first.user_id == usr_a
            assert first.principal_id == f"prn_{usr_a}"

        org_b, usr_b = _mk_account(pool, "devb")
        second = store.create(LocalAccountRecord(org_id=org_b, user_id=usr_b))
        # ON CONFLICT DO NOTHING + re-select: the WINNER's row comes back.
        assert second.local_account_id == first.local_account_id
        assert second.user_id == first.user_id
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM local_accounts")
            assert cur.fetchone()["n"] == 1
