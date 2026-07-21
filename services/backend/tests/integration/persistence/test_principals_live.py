"""LIVE-Postgres tests for principal/tenant separation, stage 1 (ADR 0001).

Two things in-memory analogues cannot prove:
  1. the baseline schema ENFORCES the principal invariant (users.principal_id
     is NOT NULL + FK — a row without a principal is impossible), and
  2. the Postgres ``create_user`` dual-writes the principal atomically and
     satisfies the real FK.

Gated on BACKEND_MERGE_TEST_DATABASE_URL (shares the merge gate's disposable
cluster + CI job). Destructive — uses a throwaway database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BACKEND_MERGE_TEST_DATABASE_URL"),
    reason="Set BACKEND_MERGE_TEST_DATABASE_URL to a disposable Postgres database.",
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


@pytest.fixture(scope="module")
def pool() -> Iterator[Any]:
    pytest.importorskip("psycopg")
    from backend_app.db.migrate import MigrationRunner
    from backend_app.store import PostgresConnectionPool

    database_url = os.environ["BACKEND_MERGE_TEST_DATABASE_URL"]
    MigrationRunner.apply(database_url)  # real runner (psycopg3), idempotent
    resolved = PostgresConnectionPool(database_url)
    try:
        yield resolved
    finally:
        resolved.close()


def _mk_org(pool: Any, tag: str) -> str:
    from backend_app.contracts import OrganizationRecord
    from backend_app.identity.store import PostgresIdentityStore

    org_id = f"org_{tag}_{uuid.uuid4().hex[:8]}"
    PostgresIdentityStore(pool).create_organization(
        OrganizationRecord(org_id=org_id, display_name=tag, slug=org_id)
    )
    return org_id


class TestPostgresAutoMint:
    def test_create_user_dual_writes_the_principal(self, pool: Any) -> None:
        from backend_app.contracts import UserRecord
        from backend_app.identity.store import PostgresIdentityStore

        store = PostgresIdentityStore(pool)
        org_id = _mk_org(pool, "mint")
        uid = f"usr_{uuid.uuid4().hex[:8]}"
        user = store.create_user(
            UserRecord(
                user_id=uid,
                org_id=org_id,
                primary_email=f"{uid}@x.io",
                display_name="Mint",
            )
        )
        assert user.principal_id == f"prn_{uid}"
        # The principal row really exists (FK would have rejected the user
        # insert otherwise) and is fetchable through the store.
        principal = store.get_principal(principal_id=f"prn_{uid}")
        assert principal is not None
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT principal_id FROM users WHERE user_id = %s", (uid,))
            assert cur.fetchone()["principal_id"] == f"prn_{uid}"


class TestPrincipalNotNullEnforced:
    def test_user_without_principal_is_rejected_by_schema(self, pool: Any) -> None:
        # The pre-squash expand stage allowed NULL principal_id (backfilled by
        # 0039). The baseline bakes the invariant in: the INSERT itself fails.
        import psycopg

        org_id = _mk_org(pool, "nn")
        uid = f"usr_nn_{uuid.uuid4().hex[:8]}"
        with pytest.raises(psycopg.errors.NotNullViolation):
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (user_id, org_id, primary_email,
                        display_name, status, created_at, updated_at,
                        principal_id)
                    VALUES (%s,%s,%s,%s,'active', now(), now(), NULL)
                    """,
                    (uid, org_id, f"{uid}@x.io", uid),
                )

    def test_principal_fk_rejects_unknown_principal(self, pool: Any) -> None:
        import psycopg

        org_id = _mk_org(pool, "fk")
        uid = f"usr_fk_{uuid.uuid4().hex[:8]}"
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (user_id, org_id, primary_email,
                        display_name, status, created_at, updated_at,
                        principal_id)
                    VALUES (%s,%s,%s,%s,'active', now(), now(), %s)
                    """,
                    (uid, org_id, f"{uid}@x.io", uid, "prn_does_not_exist"),
                )
