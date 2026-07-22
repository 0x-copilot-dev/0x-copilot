"""Connectors store selection + adapter conformance (PRD-I FR-I3).

Covers:

* **In-memory is the default** — ``create_app`` (tests/dev) wires
  :class:`InMemoryConnectorsStore`; a round-trip through it survives
  within the process.
* **Store-selection switch** — the durable
  :class:`PostgresConnectorsStore` implements the whole
  :class:`ConnectorsStore` Protocol surface that the in-memory adapter
  exposes (method-for-method), so ``desktop_app`` can swap it in with no
  service-layer change.
* **Write-through composition** — ``ConnectorsService.write_through_from_mcp``
  round-trips over both adapters: the denormalized row + the signed audit
  row land atomically on one connection.
* **Hardening (FR-I3.2)** — per-tenant audit-chain signing
  (seq / prev_hash / signature / key_version via the shared audit-chain
  primitives) and RLS session-var stamping, mirroring
  ``tests/test_projects_store_selection.py`` (PR #182).
* **Migration chain (FR-I3.1)** — the connectors DDL is in the versioned
  migration chain (``0044_connectors``), not just module-local schema.

Live-Postgres SQL execution is DEFERRED to PRD-J J2 — these tests drive
the adapter's Python paths against a fake psycopg pool.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any

from copilot_audit_chain import AuditChainRow, AuditChainSigner

from backend_app.app import create_app
from backend_app.connectors.service import ConnectorsService
from backend_app.connectors.store import (
    ConnectorAuditRecord,
    ConnectorRecord,
    ConnectorScopeEntry,
    InMemoryConnectorsStore,
    McpUpsertInput,
    PostgresConnectorsStore,
    _chain_head,
    _coerce_json,
    _connector_audit_payload,
    _jsonb,
    _row_to_connector,
)
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.db.migrate import MigrationRunner
from backend_app.identity.store import InMemoryIdentityStore


# ---------------------------------------------------------------------------
# Fake psycopg pool/conn/cursor — exercises the adapter's Python paths
# (SQL param counts, contextvar plumbing, row mapping) without a live DB.
# Mirrors tests/test_projects_store_selection.py.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, results: list[Any]) -> None:
        self._results = results
        self.executed: list[tuple[str, tuple]] = []
        self.rowcount = 0
        self._last: Any = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql, params or ()))
        # Connection-setup / side-effect statements (RLS ``set_config`` and
        # the audit-chain advisory lock) don't yield a row the store fetches,
        # so they must not consume a queued result — otherwise the meaningful
        # query's result would shift out from under it.
        low = sql.lower()
        if "set_config" in low or "pg_advisory_xact_lock" in low:
            return
        self._last = self._results.pop(0) if self._results else None
        if isinstance(self._last, list):
            self.rowcount = len(self._last)
        elif self._last is not None:
            self.rowcount = 1
        else:
            self.rowcount = 0

    def fetchone(self) -> Any:
        return self._last

    def fetchall(self) -> list[Any]:
        return self._last if isinstance(self._last, list) else []


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    @contextmanager
    def transaction(self):
        yield self


class _FakePool:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._conn = _FakeConn(cursor)

    @contextmanager
    def connection(self):
        yield self._conn


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
        )
    )
    return store


def _connector(**overrides: Any) -> ConnectorRecord:
    base: dict[str, Any] = {
        "tenant_id": "org_acme",
        "slug": "gmail",
        "display_name": "Gmail",
        "owner_user_id": "usr_sarah",
        "vault_ref": "vault_1",
        "scopes": [ConnectorScopeEntry(scope="gmail.readonly")],
    }
    base.update(overrides)
    return ConnectorRecord(**base)


def _mcp_input(**overrides: Any) -> McpUpsertInput:
    base: dict[str, Any] = {
        "tenant_id": "org_acme",
        "slug": "gmail",
        "owner_user_id": "usr_sarah",
        "display_name": "Gmail",
        "description": "Google mail",
        "status": "connected",
        "status_reason": None,
        "scopes": (ConnectorScopeEntry(scope="gmail.readonly"),),
        "last_sync_at": None,
        "last_error_at": None,
        "vault_ref": "vault_1",
    }
    base.update(overrides)
    return McpUpsertInput(**base)


_CONNECTOR_ROW: dict[str, Any] = {
    "id": "conn_1",
    "tenant_id": "org_acme",
    "slug": "gmail",
    "display_name": "Gmail",
    "description": "",
    "status": "connected",
    "status_reason": None,
    "owner_user_id": "usr_sarah",
    "scopes": '[{"scope": "gmail.readonly", "granted": true, "description": ""}]',
    "last_sync_at": None,
    "last_error_at": None,
    "created_at": "2026-07-22T00:00:00+00:00",
    "updated_at": "2026-07-22T00:00:00+00:00",
    "vault_ref": "vault_1",
}


class TestStoreSelection:
    def test_create_app_defaults_to_in_memory(self) -> None:
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
        )
        assert isinstance(app.state.connectors_store, InMemoryConnectorsStore)

    def test_injected_store_is_used(self) -> None:
        store = InMemoryConnectorsStore()
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
            connectors_store=store,
        )
        assert app.state.connectors_store is store

    def test_postgres_adapter_covers_the_protocol_surface(self) -> None:
        """Every public store method on the in-memory adapter exists on the
        Postgres adapter with a matching signature — the switch is safe."""

        pg = PostgresConnectorsStore(pool=object())
        for name, member in inspect.getmembers(
            InMemoryConnectorsStore, predicate=inspect.isfunction
        ):
            if name.startswith("_"):
                continue
            assert hasattr(pg, name), f"PostgresConnectorsStore missing {name}"
            in_mem_sig = inspect.signature(member)
            pg_sig = inspect.signature(getattr(type(pg), name))
            assert list(in_mem_sig.parameters) == list(pg_sig.parameters), name


class TestMigrationChain:
    """FR-I3.1 — connectors DDL lives in the versioned migration chain."""

    def test_0044_connectors_is_in_the_manifest(self) -> None:
        assert "0044_connectors" in MigrationRunner.expected_manifest()

    def test_0043_creates_both_tables_and_has_rollback(self) -> None:
        migrations_dir = MigrationRunner.migrations_dir()
        up = (migrations_dir / "0044_connectors.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS connectors" in up
        assert "CREATE TABLE IF NOT EXISTS connector_audit_events" in up
        # Chain columns + RLS from day one.
        for chain_col in ("seq", "prev_hash", "signature", "key_version"):
            assert chain_col in up
        assert "ENABLE ROW LEVEL SECURITY" in up
        down = (migrations_dir / "0044_connectors.rollback.sql").read_text()
        assert "DROP TABLE IF EXISTS connector_audit_events" in down
        assert "DROP TABLE IF EXISTS connectors" in down


class TestInMemoryRoundTrip:
    def test_insert_get_update_tenant_isolation(self) -> None:
        store = InMemoryConnectorsStore()
        record = _connector()
        store.insert_connector(record)
        assert (
            store.get_connector(tenant_id="org_acme", connector_id=record.id)
            is not None
        )
        # Cross-tenant read is invisible.
        assert store.get_connector(tenant_id="org_zeta", connector_id=record.id) is None
        updated = record.model_copy(update={"status": "error"})
        store.update_connector(updated)
        assert (
            store.get_connector(tenant_id="org_acme", connector_id=record.id).status
            == "error"
        )
        rows, _ = store.list_connectors(tenant_id="org_zeta")
        assert rows == ()

    def test_upsert_reuses_row_by_natural_key(self) -> None:
        store = InMemoryConnectorsStore()
        first = store.upsert_from_mcp_registration(_mcp_input())
        second = store.upsert_from_mcp_registration(
            _mcp_input(status="expired", status_reason="token_expired")
        )
        assert second.id == first.id
        assert second.status == "expired"
        assert len(store.connectors) == 1


class TestWriteThroughRoundTrip:
    """FR-I3.4 — the service's write-through composes over both adapters."""

    def test_in_memory_write_through_round_trips(self) -> None:
        store = InMemoryConnectorsStore()
        service = ConnectorsService(store=store)
        record = service.write_through_from_mcp(
            mcp_input=_mcp_input(),
            actor_user_id="usr_sarah",
            action="connector.connected",
        )
        got = store.get_connector(tenant_id="org_acme", connector_id=record.id)
        assert got is not None and got.status == "connected"
        audits, _ = store.list_audit_for_connector(
            tenant_id="org_acme", connector_id=record.id
        )
        assert [a.action for a in audits] == ["connector.connected"]

    def test_postgres_write_through_lands_row_and_audit_on_one_conn(self) -> None:
        # Queue: natural-key SELECT (miss) -> connector INSERT -> chain-head
        # SELECT (empty) -> audit INSERT.
        cur = _FakeCursor(results=[None, None, None, None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        service = ConnectorsService(store=store)
        service.write_through_from_mcp(
            mcp_input=_mcp_input(),
            actor_user_id="usr_sarah",
            action="connector.connected",
        )
        statements = [sql for sql, _ in cur.executed]
        insert_idx = next(
            i for i, s in enumerate(statements) if "INSERT INTO connectors" in s
        )
        audit_idx = next(
            i
            for i, s in enumerate(statements)
            if "INSERT INTO connector_audit_events" in s
        )
        # Both writes landed on the same cursor (one shared connection),
        # row before audit.
        assert insert_idx < audit_idx
        # The audit insert carries the signed chain columns (seq opens at 1).
        _, audit_params = next(
            (s, p) for s, p in cur.executed if "INSERT INTO connector_audit_events" in s
        )
        seq, prev_hash, signature, key_version = audit_params[-4:]
        assert seq == 1
        assert prev_hash is None
        assert isinstance(signature, (bytes, bytearray))
        assert isinstance(key_version, int)


class TestPostgresHelpers:
    def test_jsonb_none_stays_null(self) -> None:
        assert _jsonb(None) is None

    def test_jsonb_serialises_list(self) -> None:
        assert _jsonb([{"scope": "a"}]) == '[{"scope": "a"}]'

    def test_coerce_json_from_string_and_bytes(self) -> None:
        assert _coerce_json('["a"]') == ["a"]
        assert _coerce_json(b'{"k":1}') == {"k": 1}
        assert _coerce_json(["already"]) == ["already"]

    def test_row_to_connector_parses_jsonb_scopes(self) -> None:
        record = _row_to_connector(_CONNECTOR_ROW)
        assert record.id == "conn_1"
        assert [s.scope for s in record.scopes] == ["gmail.readonly"]
        assert record.scopes[0].granted is True


class TestPostgresAdapterPythonPaths:
    """Exercise the adapter against a fake psycopg pool (no live DB).

    These assert the Python plumbing — SQL param binding, contextvar
    connection sharing, row mapping — is sound; the SQL semantics
    themselves are PRD-J J2's job (live-PG verification).
    """

    def test_insert_connector_binds_all_columns(self) -> None:
        cur = _FakeCursor(results=[None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        record = _connector()
        store.insert_connector(record)
        sql, params = next(
            (s, p) for s, p in cur.executed if "INSERT INTO connectors" in s
        )
        # 14 columns bound in order; the JSONB scopes are json-encoded.
        assert len(params) == 14
        assert params[0] == record.id
        assert '"gmail.readonly"' in params[8]
        assert params[-1] == "vault_1"

    def test_get_connector_maps_row(self) -> None:
        cur = _FakeCursor(results=[_CONNECTOR_ROW])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        got = store.get_connector(tenant_id="org_acme", connector_id="conn_1")
        assert got is not None
        assert got.slug == "gmail"
        select_sql, select_params = next(
            (s, p) for s, p in cur.executed if "FROM connectors" in s
        )
        assert "tenant_id = %s" in select_sql
        assert select_params[0] == "org_acme"

    def test_list_connectors_pushes_filters_into_sql(self) -> None:
        cur = _FakeCursor(results=[[_CONNECTOR_ROW]])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        page, next_cursor = store.list_connectors(
            tenant_id="org_acme",
            statuses=("connected",),
            slugs=("gmail",),
            owner_user_id="usr_sarah",
            q="mail",
            limit=10,
        )
        assert [r.id for r in page] == ["conn_1"]
        assert next_cursor is None
        sql, params = next((s, p) for s, p in cur.executed if "FROM connectors" in s)
        assert "status = ANY(%s)" in sql
        assert "slug = ANY(%s)" in sql
        assert "owner_user_id = %s" in sql
        assert "ILIKE %s" in sql
        assert params[0] == "org_acme"
        # limit+1 row fetched to derive the next cursor without COUNT(*).
        assert params[-1] == 11

    def test_upsert_miss_inserts_fresh_row(self) -> None:
        # Natural-key SELECT misses -> INSERT.
        cur = _FakeCursor(results=[None, None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        record = store.upsert_from_mcp_registration(_mcp_input())
        assert record.status == "connected"
        assert any("INSERT INTO connectors" in s for s, _ in cur.executed)
        assert not any("UPDATE connectors" in s for s, _ in cur.executed)

    def test_upsert_hit_updates_in_place(self) -> None:
        # Natural-key SELECT hits -> UPDATE preserving id + created_at.
        cur = _FakeCursor(results=[_CONNECTOR_ROW, None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        record = store.upsert_from_mcp_registration(
            _mcp_input(status="expired", status_reason="token_expired")
        )
        assert record.id == "conn_1"
        assert record.status == "expired"
        update_sql, update_params = next(
            (s, p) for s, p in cur.executed if "UPDATE connectors" in s
        )
        assert update_params[-1] == "conn_1"
        assert not any("INSERT INTO connectors" in s for s, _ in cur.executed)

    def test_transaction_shares_one_connection(self) -> None:
        cur = _FakeCursor(results=[None, None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        with store.transaction():
            store.insert_connector(_connector())
            store.insert_connector(_connector(slug="salesforce"))
        writes = [sql for sql, _ in cur.executed if "set_config" not in sql.lower()]
        assert len(writes) == 2
        assert all("INSERT INTO connectors" in s for s in writes)


# ---------------------------------------------------------------------------
# FR-I3.2 hardening: audit-chain signing + RLS session-var stamping.
# Mirrors the PRD-H.3 lock-ins in tests/test_projects_store_selection.py.
# ---------------------------------------------------------------------------


def _audit_record(
    audit_id: str, action: str = "connector.connected"
) -> ConnectorAuditRecord:
    return ConnectorAuditRecord(
        audit_id=audit_id,
        tenant_id="org_acme",
        actor_user_id="usr_sarah",
        action=action,
        target_id="conn_1",
        before_state={"status": "disconnected"},
        after_state={"status": "connected"},
        correlation_id="c1",
    )


class TestAuditPayloadAndChainHead:
    """The pure signing helpers that the PG adapter composes."""

    def test_connector_audit_payload_carries_business_fields_only(self) -> None:
        record = _audit_record("audcon_1")
        payload = _connector_audit_payload(record)
        for key in (
            "audit_id",
            "tenant_id",
            "actor_user_id",
            "action",
            "target_kind",
            "target_id",
            "before_state",
            "after_state",
            "correlation_id",
            "ts",
        ):
            assert key in payload
        for chain_col in ("seq", "prev_hash", "signature", "key_version"):
            assert chain_col not in payload

    def test_chain_head_empty_chain(self) -> None:
        assert _chain_head(None) == (0, None)
        assert _chain_head({}) == (0, None)

    def test_chain_head_reads_seq_and_prev_hash(self) -> None:
        last_seq, prev_hash = _chain_head({"seq": 4, "signature": b"\x01\x02"})
        assert last_seq == 4
        assert prev_hash == b"\x01\x02"


class TestPostgresAuditChainSigning:
    """Drive ``append_audit`` through the fake pool and prove the chain."""

    def _append_and_capture_insert(
        self, head_row: Any, record: ConnectorAuditRecord
    ) -> tuple[str, tuple]:
        cur = _FakeCursor(results=[head_row])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        store.append_audit(record)
        return next(
            (sql, params)
            for sql, params in cur.executed
            if "INSERT INTO connector_audit_events" in sql
        )

    def test_append_audit_takes_lock_before_head_read(self) -> None:
        cur = _FakeCursor(results=[None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        store.append_audit(_audit_record("audcon_1"))
        statements = [sql for sql, _ in cur.executed]
        lock_idx = next(
            i for i, s in enumerate(statements) if "pg_advisory_xact_lock" in s
        )
        head_idx = next(
            i for i, s in enumerate(statements) if "SELECT seq, signature" in s
        )
        insert_idx = next(
            i
            for i, s in enumerate(statements)
            if "INSERT INTO connector_audit_events" in s
        )
        # Lock is acquired first so two concurrent appends can't fork the
        # chain; the head read precedes the signed insert.
        assert lock_idx < head_idx < insert_idx

    def test_append_audit_signs_a_verifiable_two_row_chain(self) -> None:
        r1 = _audit_record("audcon_1", action="connector.connected")
        r2 = _audit_record("audcon_2", action="connector.token_refreshed")

        # Row 1 lands on an empty chain (head SELECT returns nothing).
        _, p1 = self._append_and_capture_insert(None, r1)
        seq1, prev1, sig1, kver1 = p1[-4], p1[-3], p1[-2], p1[-1]

        # Row 2 sees row 1 as the chain head.
        head_after_1 = {"seq": seq1, "signature": sig1}
        _, p2 = self._append_and_capture_insert(head_after_1, r2)
        seq2, prev2, sig2, kver2 = p2[-4], p2[-3], p2[-2], p2[-1]

        # Per-tenant seq increments monotonically from 1.
        assert seq1 == 1
        assert seq2 == 2
        # prev_hash links: row 1 opens the chain, row 2 points at row 1's sig.
        assert prev1 is None
        assert prev2 == sig1

        # The signatures recompute + chain verifies under the same signer the
        # adapter used (dev-sentinel key, or AUDIT_HMAC_KEY when configured —
        # from_env resolves identically in the store and here).
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        rows = [
            AuditChainRow(
                seq=seq1,
                payload=_connector_audit_payload(r1),
                prev_hash=prev1,
                signature=sig1,
                key_version=kver1,
            ),
            AuditChainRow(
                seq=seq2,
                payload=_connector_audit_payload(r2),
                prev_hash=prev2,
                signature=sig2,
                key_version=kver2,
            ),
        ]
        result = signer.verify_chain(rows)
        assert result.ok is True
        assert result.broken_at_seq is None

    def test_append_audit_detects_payload_tamper(self) -> None:
        r1 = _audit_record("audcon_1")
        _, p1 = self._append_and_capture_insert(None, r1)
        seq1, prev1, sig1, kver1 = p1[-4], p1[-3], p1[-2], p1[-1]

        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        tampered = _connector_audit_payload(r1)
        tampered["action"] = "connector.disconnected"  # post-hoc row edit
        rows = [
            AuditChainRow(
                seq=seq1,
                payload=tampered,
                prev_hash=prev1,
                signature=sig1,
                key_version=kver1,
            )
        ]
        assert signer.verify_chain(rows).ok is False

    def test_append_audit_returns_record_unchanged(self) -> None:
        # The chain columns are DB-only; ConnectorAuditRecord
        # (extra='forbid') does not carry them, so the returned record is
        # the input record.
        cur = _FakeCursor(results=[None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        record = _audit_record("audcon_1")
        assert store.append_audit(record) is record

    def test_chains_are_per_tenant(self) -> None:
        """Two tenants each open their own chain at seq=1 — no cross-tenant
        linkage (the head SELECT is tenant-scoped)."""

        r_acme = _audit_record("audcon_1")
        r_zeta = ConnectorAuditRecord(
            audit_id="audcon_2",
            tenant_id="org_zeta",
            actor_user_id="usr_zoe",
            action="connector.connected",
            target_id="conn_9",
        )
        _, p_acme = self._append_and_capture_insert(None, r_acme)
        _, p_zeta = self._append_and_capture_insert(None, r_zeta)
        assert p_acme[-4] == 1 and p_zeta[-4] == 1
        assert p_acme[-3] is None and p_zeta[-3] is None
        # The head read binds the record's own tenant.
        cur = _FakeCursor(results=[None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        store.append_audit(r_zeta)
        head_sql, head_params = next(
            (s, p) for s, p in cur.executed if "SELECT seq, signature" in s
        )
        assert head_params == ("org_zeta",)


class TestPostgresRlsStamping:
    """The store stamps ``app.current_org_id`` / ``app.role`` on its conns.

    ``_apply_rls_session_vars`` (reused from ``backend_app.store``) embeds
    the session-var name as a literal in ``set_config('<name>', %s, true)``
    and binds the value as the single positional param — so we match on the
    SQL text plus the ``(value,)`` params tuple.
    """

    def _stamped(self, cur: _FakeCursor) -> dict[str, tuple]:
        stamped: dict[str, tuple] = {}
        for sql, params in cur.executed:
            if "set_config" not in sql.lower():
                continue
            for name in ("app.current_org_id", "app.role"):
                if name in sql:
                    stamped[name] = params
        return stamped

    def test_fresh_read_stamps_rls_session_vars(self) -> None:
        cur = _FakeCursor(results=[_CONNECTOR_ROW])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        store.get_connector(tenant_id="org_acme", connector_id="conn_1")
        stamped = self._stamped(cur)
        assert stamped.get("app.current_org_id") == ("org_acme",)
        assert stamped.get("app.role") == ("api",)
        # The stamp precedes the tenant-scoped SELECT so RLS is in effect.
        first_select = next(
            i for i, (sql, _) in enumerate(cur.executed) if "FROM connectors" in sql
        )
        last_set_config = max(
            i for i, (sql, _) in enumerate(cur.executed) if "set_config" in sql.lower()
        )
        assert last_set_config < first_select

    def test_transaction_stamps_org_id_and_role(self) -> None:
        cur = _FakeCursor(results=[None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        with store.transaction(org_id="org_acme"):
            store.insert_connector(_connector())
        stamped = self._stamped(cur)
        assert stamped.get("app.current_org_id") == ("org_acme",)
        assert stamped.get("app.role") == ("api",)

    def test_transaction_without_org_still_stamps_role(self) -> None:
        # Backward-compatible default: callers not yet passing a tenant get
        # role='api' stamped but no org id (mirrors _apply_rls_session_vars).
        cur = _FakeCursor(results=[None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        with store.transaction():
            store.insert_connector(_connector())
        stamped = self._stamped(cur)
        assert stamped.get("app.role") == ("api",)
        assert "app.current_org_id" not in stamped

    def test_upsert_opens_tenant_stamped_transaction_when_standalone(self) -> None:
        # A standalone upsert (no service transaction open) stamps the
        # input's tenant so the RLS policies back the write.
        cur = _FakeCursor(results=[None, None])
        store = PostgresConnectorsStore(pool=_FakePool(cur))
        store.upsert_from_mcp_registration(_mcp_input())
        stamped = self._stamped(cur)
        assert stamped.get("app.current_org_id") == ("org_acme",)
        assert stamped.get("app.role") == ("api",)
