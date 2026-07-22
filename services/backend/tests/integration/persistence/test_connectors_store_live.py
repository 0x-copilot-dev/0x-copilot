"""LIVE-Postgres tests for :class:`PostgresConnectorsStore` (PRD-J FR-J2.1b).

The connectors adapter (PRD-I3, migration ``0044_connectors``) shipped
with fake-conn unit tests only; this suite executes its SQL against a
real Postgres:

1. migration ``0044_connectors`` applies (tables exist, yoyo records it),
2. the MCP write-through round-trips through the REAL service seam —
   :meth:`ConnectorsService.write_through_from_mcp` composes
   ``store.transaction()`` + ``upsert_from_mcp_registration`` +
   ``append_audit`` atomically on one connection,
3. the signed audit chain verifies over multiple events (connect →
   refresh → disconnect) through the shared :class:`AuditChainSigner`,
4. tenant isolation holds: the store's WHERE scoping AND the 0044 RLS
   policies (observed through the gate's non-superuser role), and
5. the disconnect path flips status on the durable row.

Gated on ``BACKEND_MERGE_TEST_DATABASE_URL`` (shares the merge gate's
disposable cluster + CI job — same convention as
``test_principals_live.py``). Destructive — use a throwaway database.
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
def database_url() -> str:
    return os.environ["BACKEND_MERGE_TEST_DATABASE_URL"]


@pytest.fixture(scope="module")
def app_database_url(database_url: str) -> str:
    """Non-superuser URL when the gate provides one (RLS is then real)."""

    return os.environ.get("BACKEND_MERGE_TEST_APP_DATABASE_URL", database_url)


@pytest.fixture(scope="module")
def migrated(database_url: str) -> list[str]:
    pytest.importorskip("psycopg")
    from backend_app.db.migrate import MigrationRunner

    MigrationRunner.apply(database_url)  # real runner (psycopg3), idempotent
    applied, _pending = MigrationRunner.status(database_url)
    return applied


@pytest.fixture(scope="module")
def pool(migrated: list[str], app_database_url: str) -> Iterator[Any]:
    from backend_app.store import PostgresConnectionPool

    resolved = PostgresConnectionPool(app_database_url)
    try:
        yield resolved
    finally:
        resolved.close()


@pytest.fixture(scope="module")
def store(pool: Any) -> Any:
    from backend_app.connectors.store import PostgresConnectorsStore

    return PostgresConnectorsStore(pool)


@pytest.fixture(scope="module")
def service(store: Any) -> Any:
    from backend_app.connectors.service import ConnectorsService

    return ConnectorsService(store=store)


@pytest.fixture
def admin_conn(database_url: str, migrated: list[str]) -> Iterator[Any]:
    """Superuser dict-row connection for raw pre/post-condition checks.

    Session timezone pinned to UTC: chain payloads were signed over
    ``datetime.now(timezone.utc)`` values, so a verifier reading ``ts``
    back from timestamptz columns must observe the same UTC offset for
    ``isoformat()`` to recompute the canonical bytes.
    """

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn


def _tenant(tag: str) -> str:
    return f"org_{tag}_{uuid.uuid4().hex[:8]}"


def _mcp_input(tenant_id: str, **overrides: Any) -> Any:
    from backend_app.connectors.store import ConnectorScopeEntry, McpUpsertInput

    defaults: dict[str, Any] = {
        "tenant_id": tenant_id,
        "slug": f"salesforce-{uuid.uuid4().hex[:6]}",
        "owner_user_id": "usr_owner",
        "display_name": "Salesforce",
        "description": "CRM — café Δ ünïcode",
        "status": "connected",
        "status_reason": None,
        "scopes": (
            ConnectorScopeEntry(scope="crm.read", granted=True, description="Read"),
            ConnectorScopeEntry(scope="crm.write", granted=False, description="Write"),
        ),
        "last_sync_at": None,
        "last_error_at": None,
        "vault_ref": f"vault_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    return McpUpsertInput(**defaults)


class TestMigrationApplies:
    def test_0044_connectors_is_applied(self, migrated: list[str]) -> None:
        assert "0044_connectors" in migrated

    def test_connectors_tables_exist(self, admin_conn: Any) -> None:
        with admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE tablename IN ('connectors', 'connector_audit_events')
                """
            )
            names = {row["tablename"] for row in cur.fetchall()}
        assert names == {"connectors", "connector_audit_events"}


class TestWriteThroughRoundTrip:
    def test_first_write_through_creates_row_and_audit(
        self, service: Any, store: Any, admin_conn: Any
    ) -> None:
        tenant = _tenant("wt")
        mcp_input = _mcp_input(tenant)
        record = service.write_through_from_mcp(
            mcp_input=mcp_input,
            actor_user_id="usr_owner",
            action="connector.connected",
            correlation_id=f"corr_{uuid.uuid4().hex[:8]}",
        )
        fetched = store.get_connector(tenant_id=tenant, connector_id=record.id)
        assert fetched == record
        assert fetched.slug == mcp_input.slug
        assert fetched.vault_ref == mcp_input.vault_ref
        assert [s.scope for s in fetched.scopes] == ["crm.read", "crm.write"]
        assert [s.granted for s in fetched.scopes] == [True, False]

        audits, _ = store.list_audit_for_connector(
            tenant_id=tenant, connector_id=record.id
        )
        assert [a.action for a in audits] == ["connector.connected"]

    def test_second_write_through_updates_same_row(
        self, service: Any, store: Any
    ) -> None:
        tenant = _tenant("upd")
        first = _mcp_input(tenant)
        created = service.write_through_from_mcp(
            mcp_input=first, actor_user_id="usr_owner", action="connector.connected"
        )
        refreshed = _mcp_input(
            tenant,
            slug=first.slug,
            display_name="Salesforce (renamed)",
            vault_ref="vault_rotated",
            existing_id=created.id,
        )
        updated = service.write_through_from_mcp(
            mcp_input=refreshed,
            actor_user_id="usr_owner",
            action="connector.token_refreshed",
        )
        assert updated.id == created.id  # natural-key stability
        fetched = store.get_connector(tenant_id=tenant, connector_id=created.id)
        assert fetched.display_name == "Salesforce (renamed)"
        assert fetched.vault_ref == "vault_rotated"
        rows, _ = store.list_connectors(tenant_id=tenant, slugs=(first.slug,))
        assert len(rows) == 1  # upsert, not a second row

    def test_natural_key_lookup_without_existing_id(
        self, service: Any, store: Any
    ) -> None:
        tenant = _tenant("nat")
        first = _mcp_input(tenant)
        created = service.write_through_from_mcp(
            mcp_input=first, actor_user_id="usr_owner", action="connector.connected"
        )
        again = service.write_through_from_mcp(
            mcp_input=_mcp_input(tenant, slug=first.slug, status="expired"),
            actor_user_id="usr_owner",
            action="connector.expired",
        )
        assert again.id == created.id
        assert (
            store.get_connector(tenant_id=tenant, connector_id=created.id).status
            == "expired"
        )


class TestDisconnectFlips:
    def test_disconnect_flips_status_and_audits(self, service: Any, store: Any) -> None:
        tenant = _tenant("dis")
        created = service.write_through_from_mcp(
            mcp_input=_mcp_input(tenant),
            actor_user_id="usr_owner",
            action="connector.connected",
        )
        record = service.disconnect(
            tenant_id=tenant,
            caller_user_id="usr_owner",
            caller_roles=("member",),
            connector_id=created.id,
        )
        assert record.status == "disconnected"
        assert record.status_reason == "user_requested_disconnect"
        fetched = store.get_connector(tenant_id=tenant, connector_id=created.id)
        assert fetched.status == "disconnected"
        # Idempotent re-disconnect returns the row without another flip.
        again = service.disconnect(
            tenant_id=tenant,
            caller_user_id="usr_owner",
            caller_roles=("member",),
            connector_id=created.id,
        )
        assert again.status == "disconnected"
        audits, _ = store.list_audit_for_connector(
            tenant_id=tenant, connector_id=created.id
        )
        assert sorted(a.action for a in audits) == [
            "connector.connected",
            "connector.disconnected",
        ]


class TestAuditChainSigned:
    def test_chain_verifies_over_connect_refresh_disconnect(
        self, service: Any, admin_conn: Any
    ) -> None:
        from copilot_audit_chain import AuditChainRow, AuditChainSigner

        from backend_app.connectors.store import (
            _connector_audit_payload,
            _row_to_conn_audit,
        )

        tenant = _tenant("chain")
        created = service.write_through_from_mcp(
            mcp_input=_mcp_input(tenant),
            actor_user_id="usr_owner",
            action="connector.connected",
        )
        service.refresh_token(
            tenant_id=tenant,
            caller_user_id="usr_owner",
            caller_roles=("member",),
            connector_id=created.id,
        )
        service.disconnect(
            tenant_id=tenant,
            caller_user_id="usr_owner",
            caller_roles=("member",),
            connector_id=created.id,
        )

        with admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM connector_audit_events
                WHERE tenant_id = %s ORDER BY seq ASC
                """,
                (tenant,),
            )
            rows = cur.fetchall()
        assert [row["seq"] for row in rows] == [1, 2, 3]
        assert [row["action"] for row in rows] == [
            "connector.connected",
            "connector.token_refreshed",
            "connector.disconnected",
        ]
        assert rows[0]["prev_hash"] is None
        assert bytes(rows[1]["prev_hash"]) == bytes(rows[0]["signature"])
        assert bytes(rows[2]["prev_hash"]) == bytes(rows[1]["signature"])

        # Rebuild the signed payload from the DB rows alone — proves the
        # JSONB round-trip preserves what was signed.
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        chain = [
            AuditChainRow(
                seq=int(row["seq"]),
                payload=_connector_audit_payload(_row_to_conn_audit(row)),
                prev_hash=(
                    bytes(row["prev_hash"]) if row["prev_hash"] is not None else None
                ),
                signature=bytes(row["signature"]),
                key_version=int(row["key_version"]),
            )
            for row in rows
        ]
        assert signer.verify_chain(chain).ok is True

        # Tampering breaks it (in-memory copy — the signature really covers
        # the business payload, not just the row's presence).
        tampered = list(chain)
        tampered[0] = AuditChainRow(
            seq=tampered[0].seq,
            payload={**tampered[0].payload, "action": "connector.disconnected"},
            prev_hash=tampered[0].prev_hash,
            signature=tampered[0].signature,
            key_version=tampered[0].key_version,
        )
        result = signer.verify_chain(tampered)
        assert result.ok is False and result.broken_at_seq == 1


class TestTenantIsolation:
    def test_store_reads_are_tenant_scoped(self, service: Any, store: Any) -> None:
        tenant_a = _tenant("isoa")
        tenant_b = _tenant("isob")
        created = service.write_through_from_mcp(
            mcp_input=_mcp_input(tenant_a),
            actor_user_id="usr_owner",
            action="connector.connected",
        )
        assert store.get_connector(tenant_id=tenant_b, connector_id=created.id) is None
        page, _ = store.list_connectors(tenant_id=tenant_b)
        assert created.id not in {c.id for c in page}
        audits, _ = store.list_audit_for_connector(
            tenant_id=tenant_b, connector_id=created.id
        )
        assert audits == ()

    def test_rls_blocks_cross_tenant_raw_reads(
        self, service: Any, app_database_url: str, database_url: str
    ) -> None:
        if app_database_url == database_url:
            pytest.skip(
                "BACKEND_MERGE_TEST_APP_DATABASE_URL unset — no non-superuser "
                "role available, RLS enforcement cannot be observed."
            )
        import psycopg
        from psycopg.rows import dict_row

        tenant_a = _tenant("rlsa")
        tenant_b = _tenant("rlsb")
        created = service.write_through_from_mcp(
            mcp_input=_mcp_input(tenant_a),
            actor_user_id="usr_owner",
            action="connector.connected",
        )
        with psycopg.connect(
            app_database_url, autocommit=True, row_factory=dict_row
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.current_org_id', %s, false)", (tenant_b,)
                )
                cur.execute("SELECT 1 FROM connectors WHERE id = %s", (created.id,))
                assert cur.fetchone() is None
                cur.execute("DELETE FROM connectors WHERE id = %s", (created.id,))
                assert cur.rowcount == 0
                cur.execute(
                    "SELECT 1 FROM connector_audit_events WHERE target_id = %s",
                    (created.id,),
                )
                assert cur.fetchone() is None

                cur.execute(
                    "SELECT set_config('app.current_org_id', %s, false)", (tenant_a,)
                )
                cur.execute("SELECT 1 FROM connectors WHERE id = %s", (created.id,))
                assert cur.fetchone() is not None
