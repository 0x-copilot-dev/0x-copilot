"""LIVE-Postgres tests for principal/tenant separation, stage 1 (ADR 0001).

Two things in-memory analogues cannot prove:
  1. the migration 0039 BACKFILL SQL is correct against the real schema
     (per-user principals + survivor-linked lineage, FK-safe), and
  2. the Postgres ``create_user`` dual-writes the principal atomically and
     satisfies the real FK.

Gated on BACKEND_MERGE_TEST_DATABASE_URL (shares the merge gate's disposable
cluster + CI job). Destructive — uses a throwaway database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
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


class TestBackfillSql:
    def test_0039_backfill_links_absorbed_to_survivor(self, pool: Any) -> None:
        # Seed rows that PREDATE the backfill: raw INSERT with principal_id
        # NULL (bypassing the store's auto-mint), a survivor + an absorbed
        # user, then run the exact 0039 backfill statements.
        org_id = _mk_org(pool, "bf")
        surv = f"usr_surv_{uuid.uuid4().hex[:8]}"
        absb = f"usr_abs_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                for uid, absorbed, status in (
                    (surv, None, "active"),
                    (absb, surv, "disabled"),
                ):
                    cur.execute(
                        """
                        INSERT INTO users (user_id, org_id, primary_email,
                            display_name, status, created_at, updated_at,
                            absorbed_into_user_id, merged_at, principal_id)
                        VALUES (%s,%s,%s,%s,%s, now(), now(), %s, %s, NULL)
                        """,
                        (
                            uid,
                            org_id,
                            f"{uid}@x.io",
                            uid,
                            status,
                            absorbed,
                            None if absorbed is None else now,
                        ),
                    )
                # The 0039 backfill, verbatim.
                cur.execute(
                    """
                    INSERT INTO principals (principal_id, display_name,
                        created_at, updated_at)
                    SELECT 'prn_' || user_id, display_name, created_at, created_at
                    FROM users WHERE principal_id IS NULL
                    ON CONFLICT (principal_id) DO NOTHING
                    """
                )
                cur.execute(
                    "UPDATE users SET principal_id = 'prn_' || user_id "
                    "WHERE principal_id IS NULL"
                )
                cur.execute(
                    """
                    UPDATE principals p
                    SET absorbed_into_principal_id = 'prn_' || u.absorbed_into_user_id,
                        merged_at = u.merged_at
                    FROM users u
                    WHERE p.principal_id = 'prn_' || u.user_id
                      AND u.absorbed_into_user_id IS NOT NULL
                    """
                )
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, principal_id FROM users WHERE user_id = ANY(%s)",
                ([surv, absb],),
            )
            by_user = {r["user_id"]: r["principal_id"] for r in cur.fetchall()}
            assert by_user[surv] == f"prn_{surv}"
            assert by_user[absb] == f"prn_{absb}"
            # Absorbed principal's lineage points at the SURVIVOR's principal.
            cur.execute(
                "SELECT absorbed_into_principal_id FROM principals "
                "WHERE principal_id = %s",
                (f"prn_{absb}",),
            )
            assert cur.fetchone()["absorbed_into_principal_id"] == f"prn_{surv}"
            cur.execute(
                "SELECT absorbed_into_principal_id FROM principals "
                "WHERE principal_id = %s",
                (f"prn_{surv}",),
            )
            assert cur.fetchone()["absorbed_into_principal_id"] is None
            # No orphans: every seeded user resolves to a real principal (FK).
            cur.execute(
                """
                SELECT count(*) AS n FROM users u
                LEFT JOIN principals p ON u.principal_id = p.principal_id
                WHERE u.user_id = ANY(%s) AND p.principal_id IS NULL
                """,
                ([surv, absb],),
            )
            assert cur.fetchone()["n"] == 0
