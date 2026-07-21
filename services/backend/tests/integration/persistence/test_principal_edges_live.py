"""LIVE-Postgres tests for principal/tenant separation, stage 2a (ADR 0001).

Proves against a real database the two things in-memory analogues cannot:
  1. the three edge stores' hand-written Postgres INSERTs persist principal_id
     (a column/value-count slip would only surface here), satisfying the FK, and
  2. the migration 0040 BACKFILL SQL fills each edge from its user 1:1.

Gated on BACKEND_MERGE_TEST_DATABASE_URL (shares the merge gate's cluster + CI
job). Destructive — throwaway database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BACKEND_MERGE_TEST_DATABASE_URL"),
    reason="Set BACKEND_MERGE_TEST_DATABASE_URL to a disposable Postgres database.",
)

_ADDR = "0x" + "a" * 40


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


def _seed_org_user_providers(pool: Any, tag: str) -> tuple[str, str, str, str]:
    """Create an org, a user (auto-minted principal), and oidc + saml
    providers. Returns (org_id, user_id, oidc_provider_id, saml_provider_id)."""
    from backend_app.contracts import OrganizationRecord, UserRecord
    from backend_app.identity.store import PostgresIdentityStore

    ids = PostgresIdentityStore(pool)
    suffix = uuid.uuid4().hex[:8]
    org_id = f"org_{tag}_{suffix}"
    user_id = f"usr_{tag}_{suffix}"
    ids.create_organization(
        OrganizationRecord(org_id=org_id, display_name=tag, slug=org_id)
    )
    ids.create_user(
        UserRecord(
            user_id=user_id,
            org_id=org_id,
            primary_email=f"{user_id}@x.io",
            display_name=tag,
        )
    )
    now = datetime.now(timezone.utc)
    providers = {}
    with pool.connection() as conn, conn.cursor() as cur:
        for kind in ("oidc", "saml"):
            pid = f"prov_{kind}_{suffix}"
            cur.execute(
                """
                INSERT INTO auth_providers (provider_id, org_id, kind,
                    display_name, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (pid, org_id, kind, f"{kind} provider", now, now),
            )
            providers[kind] = pid
    return org_id, user_id, providers["oidc"], providers["saml"]


class TestEdgePostgresAutoMint:
    def test_all_three_edges_persist_the_principal(self, pool: Any) -> None:
        from backend_app.contracts import (
            OidcIdentityRecord,
            SamlIdentityRecord,
            WalletIdentityRecord,
        )
        from backend_app.identity.oidc_store import PostgresOidcStore
        from backend_app.identity.saml_store import PostgresSamlStore
        from backend_app.identity.siwe_store import PostgresSiweStore

        org_id, user_id, oidc_pid, saml_pid = _seed_org_user_providers(pool, "edge")
        want = f"prn_{user_id}"

        wallet = PostgresSiweStore(pool).create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR, org_id=org_id, user_id=user_id, chain_id=8453
            )
        )
        oidc = PostgresOidcStore(pool).create_identity(
            OidcIdentityRecord(
                org_id=org_id, user_id=user_id, provider_id=oidc_pid, subject="sub"
            )
        )
        saml = PostgresSamlStore(pool).create_identity(
            SamlIdentityRecord(
                org_id=org_id,
                user_id=user_id,
                provider_id=saml_pid,
                name_id="nid",
                name_id_format="fmt",
            )
        )
        assert wallet.principal_id == want
        assert oidc.principal_id == want
        assert saml.principal_id == want

        # Persisted through the real INSERT + FK (a column slip would 500 above;
        # a NULL would show here).
        with pool.connection() as conn, conn.cursor() as cur:
            for table, key, value in (
                ("wallet_identities", "wallet_id", wallet.wallet_id),
                ("oidc_identities", "identity_id", oidc.identity_id),
                ("saml_identities", "identity_id", saml.identity_id),
            ):
                cur.execute(
                    f"SELECT principal_id FROM {table} WHERE {key} = %s", (value,)
                )
                assert cur.fetchone()["principal_id"] == want


class TestEdgeBackfillSql:
    def test_0040_backfill_fills_every_edge_from_its_user(self, pool: Any) -> None:
        org_id, user_id, oidc_pid, saml_pid = _seed_org_user_providers(pool, "bf")
        want = f"prn_{user_id}"
        now = datetime.now(timezone.utc)
        wid = f"wid_{uuid.uuid4().hex[:8]}"
        oid = f"oid_{uuid.uuid4().hex[:8]}"
        sid = f"sid_{uuid.uuid4().hex[:8]}"
        # Seed edges that PREDATE 0040: principal_id NULL (raw INSERT bypasses
        # the store's dual-write).
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO wallet_identities (wallet_id, address, org_id, "
                "user_id, chain_id, created_at, principal_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,NULL)",
                (wid, "0x" + "b" * 40, org_id, user_id, 8453, now),
            )
            cur.execute(
                "INSERT INTO oidc_identities (identity_id, org_id, user_id, "
                "provider_id, subject, linked_at, principal_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,NULL)",
                (oid, org_id, user_id, oidc_pid, "sub", now),
            )
            cur.execute(
                "INSERT INTO saml_identities (identity_id, org_id, user_id, "
                "provider_id, name_id, name_id_format, linked_at, principal_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)",
                (sid, org_id, user_id, saml_pid, "nid", "fmt", now),
            )
            # The 0040 backfill, verbatim, for each edge.
            for table in (
                "wallet_identities",
                "oidc_identities",
                "saml_identities",
            ):
                cur.execute(
                    f"""
                    UPDATE {table} e SET principal_id = u.principal_id
                    FROM users u
                    WHERE e.org_id = u.org_id AND e.user_id = u.user_id
                      AND e.principal_id IS NULL
                    """
                )
        with pool.connection() as conn, conn.cursor() as cur:
            for table, key, value in (
                ("wallet_identities", "wallet_id", wid),
                ("oidc_identities", "identity_id", oid),
                ("saml_identities", "identity_id", sid),
            ):
                cur.execute(
                    f"SELECT principal_id FROM {table} WHERE {key} = %s", (value,)
                )
                assert cur.fetchone()["principal_id"] == want
                # FK resolves to a real principal.
                cur.execute(
                    f"""
                    SELECT count(*) AS n FROM {table} e
                    LEFT JOIN principals p ON e.principal_id = p.principal_id
                    WHERE e.{key} = %s AND p.principal_id IS NULL
                    """,
                    (value,),
                )
                assert cur.fetchone()["n"] == 0
