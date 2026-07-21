"""Projects store selection + adapter conformance (PRD-H FR-H.3).

Covers:

* **In-memory is the default** — ``create_app`` (tests/dev) wires
  :class:`InMemoryProjectsStore`; a round-trip through it survives within
  the process.
* **Store-selection switch** — the durable :class:`PostgresProjectsStore`
  implements the whole :class:`ProjectsStore` Protocol surface that the
  in-memory adapter exposes (method-for-method), so ``desktop_app`` can
  swap it in with no service-layer change.
* **Pure Postgres helpers** — JSONB (de)serialisation, row→record
  mapping, and the ``ORDER BY`` whitelist are exercised without a live
  DB (live-Postgres verification is deferred — see FR-H.3).
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any

from copilot_audit_chain import AuditChainRow, AuditChainSigner

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.store import (
    InMemoryProjectsStore,
    PostgresProjectsStore,
    ProjectAuditRecord,
    ProjectMembershipRecord,
    ProjectRecord,
    _chain_head,
    _coerce_json,
    _jsonb,
    _project_audit_payload,
    _row_to_project,
    _sql_order_by,
)


# ---------------------------------------------------------------------------
# Fake psycopg pool/conn/cursor — exercises the adapter's Python paths
# (SQL param counts, contextvar plumbing, row mapping) without a live DB.
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


class TestStoreSelection:
    def test_create_app_defaults_to_in_memory(self) -> None:
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
        )
        assert isinstance(app.state.projects_store, InMemoryProjectsStore)

    def test_injected_store_is_used(self) -> None:
        store = InMemoryProjectsStore()
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_seeded_identity(),
            projects_store=store,
        )
        assert app.state.projects_store is store

    def test_postgres_adapter_covers_the_protocol_surface(self) -> None:
        """Every public store method on the in-memory adapter exists on the
        Postgres adapter with a matching signature — the switch is safe."""

        pg = PostgresProjectsStore(pool=object())
        for name, member in inspect.getmembers(
            InMemoryProjectsStore, predicate=inspect.isfunction
        ):
            if name.startswith("_"):
                continue
            assert hasattr(pg, name), f"PostgresProjectsStore missing {name}"
            in_mem_sig = inspect.signature(member)
            pg_sig = inspect.signature(getattr(type(pg), name))
            assert list(in_mem_sig.parameters) == list(pg_sig.parameters), name


class TestInMemoryRoundTrip:
    def test_insert_get_update_soft_delete(self) -> None:
        store = InMemoryProjectsStore()
        record = ProjectRecord(
            tenant_id="org_acme", owner_user_id="usr_sarah", name="Acme"
        )
        store.insert_project(record)
        assert store.get_project(tenant_id="org_acme", project_id=record.id) is not None
        # Cross-tenant read is invisible.
        assert store.get_project(tenant_id="org_zeta", project_id=record.id) is None
        renamed = record.model_copy(update={"name": "Acme 2"})
        store.update_project(renamed)
        assert (
            store.get_project(tenant_id="org_acme", project_id=record.id).name
            == "Acme 2"
        )
        assert store.soft_delete_project(tenant_id="org_acme", project_id=record.id)
        assert store.get_project(tenant_id="org_acme", project_id=record.id) is None
        assert (
            store.get_project(
                tenant_id="org_acme", project_id=record.id, include_deleted=True
            )
            is not None
        )


class TestPostgresHelpers:
    def test_jsonb_none_stays_null(self) -> None:
        assert _jsonb(None) is None

    def test_jsonb_serialises_list(self) -> None:
        assert _jsonb(["gmail", "salesforce"]) == '["gmail", "salesforce"]'

    def test_coerce_json_from_string_and_bytes(self) -> None:
        assert _coerce_json('["a"]') == ["a"]
        assert _coerce_json(b'{"k":1}') == {"k": 1}
        assert _coerce_json(["already"]) == ["already"]

    def test_row_to_project_parses_jsonb_allowlist(self) -> None:
        row = {
            "id": "prj_1",
            "tenant_id": "org_acme",
            "owner_user_id": "usr_sarah",
            "name": "Acme",
            "description": "",
            "icon_emoji": "📁",
            "color_hue": 210,
            "status": "active",
            "archived_at": None,
            "last_activity_at": None,
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T00:00:00+00:00",
            "deleted_at": None,
            "default_connector_allowlist": '["gmail"]',
        }
        record = _row_to_project(row)
        assert record.id == "prj_1"
        assert record.default_connector_allowlist == ["gmail"]

    def test_sql_order_by_whitelist(self) -> None:
        assert _sql_order_by("name:asc") == "lower(name) ASC, id ASC"
        assert (
            _sql_order_by("last_activity_at:desc")
            == "last_activity_at DESC NULLS LAST, id DESC"
        )
        # Unknown sort falls back to updated_at DESC (never interpolates
        # attacker-controlled column names).
        assert _sql_order_by("id; DROP TABLE projects") == "updated_at DESC, id DESC"


class TestPostgresAdapterPythonPaths:
    """Exercise the adapter against a fake psycopg pool (no live DB).

    These assert the Python plumbing — SQL param binding, contextvar
    connection sharing, row mapping — is sound; the SQL semantics
    themselves are verified on the supervised-boot smoke (deferred here).
    """

    def test_insert_project_binds_all_columns(self) -> None:
        cur = _FakeCursor(results=[None])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        record = ProjectRecord(
            tenant_id="org_acme",
            owner_user_id="usr_sarah",
            name="Acme",
            default_connector_allowlist=["gmail"],
        )
        store.insert_project(record)
        # Skip the RLS ``set_config`` stamp the fresh connection issues first.
        sql, params = next(
            (s, p) for s, p in cur.executed if "INSERT INTO projects" in s
        )
        assert "INSERT INTO projects" in sql
        # 14 columns bound in order; the JSONB allowlist is json-encoded.
        assert len(params) == 14
        assert params[0] == record.id
        assert params[-1] == '["gmail"]'

    def test_get_project_maps_row(self) -> None:
        row = {
            "id": "prj_1",
            "tenant_id": "org_acme",
            "owner_user_id": "usr_sarah",
            "name": "Acme",
            "description": "",
            "icon_emoji": "📁",
            "color_hue": 210,
            "status": "active",
            "archived_at": None,
            "last_activity_at": None,
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T00:00:00+00:00",
            "deleted_at": None,
            "default_connector_allowlist": None,
        }
        cur = _FakeCursor(results=[row])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        got = store.get_project(tenant_id="org_acme", project_id="prj_1")
        assert got is not None
        assert got.id == "prj_1"
        # Non-deleted read appends the deleted_at guard (skip the RLS stamp).
        select_sql = next(s for s, _ in cur.executed if "FROM projects" in s)
        assert "deleted_at IS NULL" in select_sql

    def test_transaction_shares_one_connection(self) -> None:
        cur = _FakeCursor(results=[None, None])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        record = ProjectRecord(
            tenant_id="org_acme", owner_user_id="usr_sarah", name="Acme"
        )
        with store.transaction():
            store.insert_project(record)
            store.insert_membership(
                ProjectMembershipRecord(
                    project_id=record.id,
                    user_id="usr_sarah",
                    tenant_id="org_acme",
                    role="owner",
                    added_by="usr_sarah",
                )
            )
        # Both writes landed on the same cursor (one shared connection).
        # Filter out the RLS ``set_config`` stamp the transaction issues.
        writes = [sql for sql, _ in cur.executed if "set_config" not in sql.lower()]
        assert len(writes) == 2
        assert "INSERT INTO projects" in writes[0]
        assert "INSERT INTO project_memberships" in writes[1]

    def test_is_starred_true_false(self) -> None:
        cur = _FakeCursor(results=[{"?column?": 1}])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        assert store.is_starred(
            tenant_id="org_acme", project_id="prj_1", user_id="usr_sarah"
        )
        cur2 = _FakeCursor(results=[None])
        store2 = PostgresProjectsStore(pool=_FakePool(cur2))
        assert not store2.is_starred(
            tenant_id="org_acme", project_id="prj_1", user_id="usr_sarah"
        )

    def test_soft_delete_returns_rowcount_bool(self) -> None:
        cur = _FakeCursor(results=[{"id": "prj_1"}])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        assert store.soft_delete_project(tenant_id="org_acme", project_id="prj_1")


# ---------------------------------------------------------------------------
# PRD-H.3 hardening: audit-chain signing + RLS session-var stamping.
#
# Mirrors ``PostgresMcpStore.append_audit`` (backend_app/store.py:658) for the
# chain, and the C5 ``_apply_rls_session_vars`` unit contract in
# ``tests/test_rls_session_vars.py`` for the RLS stamp. Live-Postgres SQL
# execution stays DEFERRED (no live DB here) — the supervised-boot smoke
# exercises the real RLS policies + chain.
# ---------------------------------------------------------------------------


def _audit_record(audit_id: str, action: str = "project.updated") -> ProjectAuditRecord:
    return ProjectAuditRecord(
        audit_id=audit_id,
        tenant_id="org_acme",
        actor_user_id="usr_sarah",
        action=action,
        target_id="prj_1",
        before_state={"status": "active"},
        after_state={"status": "archived"},
        context={"correlation": "c1"},
        correlation_id="c1",
    )


class TestAuditPayloadAndChainHead:
    """The pure signing helpers that the PG adapter composes."""

    def test_project_audit_payload_carries_business_fields_only(self) -> None:
        record = _audit_record("aud_1")
        payload = _project_audit_payload(record)
        # Business fields the chain must protect are present...
        for key in (
            "audit_id",
            "tenant_id",
            "actor_user_id",
            "action",
            "target_kind",
            "target_id",
            "before_state",
            "after_state",
            "context",
            "correlation_id",
            "ts",
        ):
            assert key in payload
        # ...and the chain envelope columns are NOT part of the signed payload
        # (they wrap it, per AuditChainSigner._canonicalize).
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
        self, head_row: Any, record: ProjectAuditRecord
    ) -> tuple[str, tuple]:
        cur = _FakeCursor(results=[head_row])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        store.append_audit(record)
        insert = next(
            (sql, params)
            for sql, params in cur.executed
            if "INSERT INTO project_audit_events" in sql
        )
        return insert

    def test_append_audit_takes_lock_before_head_read(self) -> None:
        cur = _FakeCursor(results=[None])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        store.append_audit(_audit_record("aud_1"))
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
            if "INSERT INTO project_audit_events" in s
        )
        # Lock is acquired first so two concurrent appends can't fork the
        # chain; the head read precedes the signed insert.
        assert lock_idx < head_idx < insert_idx

    def test_append_audit_signs_a_verifiable_two_row_chain(self) -> None:
        r1 = _audit_record("aud_1", action="project.created")
        r2 = _audit_record("aud_2", action="project.archived")

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
                payload=_project_audit_payload(r1),
                prev_hash=prev1,
                signature=sig1,
                key_version=kver1,
            ),
            AuditChainRow(
                seq=seq2,
                payload=_project_audit_payload(r2),
                prev_hash=prev2,
                signature=sig2,
                key_version=kver2,
            ),
        ]
        result = signer.verify_chain(rows)
        assert result.ok is True
        assert result.broken_at_seq is None

    def test_append_audit_detects_payload_tamper(self) -> None:
        r1 = _audit_record("aud_1")
        _, p1 = self._append_and_capture_insert(None, r1)
        seq1, prev1, sig1, kver1 = p1[-4], p1[-3], p1[-2], p1[-1]

        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        tampered = _project_audit_payload(r1)
        tampered["action"] = "project.deleted"  # someone edits the row post-hoc
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
        # The chain columns are DB-only; ProjectAuditRecord (extra='forbid')
        # does not carry them, so the returned record is the input record.
        cur = _FakeCursor(results=[None])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        record = _audit_record("aud_1")
        returned = store.append_audit(record)
        assert returned is record


class TestPostgresRlsStamping:
    """The store stamps ``app.current_org_id`` / ``app.role`` on its conns.

    ``_apply_rls_session_vars`` (reused from ``backend_app.store``) embeds the
    session-var name as a literal in ``set_config('<name>', %s, true)`` and
    binds the value as the single positional param — so we match on the SQL
    text plus the ``(value,)`` params tuple.
    """

    def _set_config_calls(self, cur: _FakeCursor) -> list[tuple[str, tuple]]:
        return [
            (sql, params) for sql, params in cur.executed if "set_config" in sql.lower()
        ]

    def _stamped(self, cur: _FakeCursor) -> dict[str, tuple]:
        """Map each stamped session-var name → its bound params tuple."""

        stamped: dict[str, tuple] = {}
        for sql, params in self._set_config_calls(cur):
            for name in ("app.current_org_id", "app.role"):
                if name in sql:
                    stamped[name] = params
        return stamped

    def test_fresh_read_stamps_rls_session_vars(self) -> None:
        row = {
            "id": "prj_1",
            "tenant_id": "org_acme",
            "owner_user_id": "usr_sarah",
            "name": "Acme",
            "description": "",
            "icon_emoji": "📁",
            "color_hue": 210,
            "status": "active",
            "archived_at": None,
            "last_activity_at": None,
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T00:00:00+00:00",
            "deleted_at": None,
            "default_connector_allowlist": None,
        }
        cur = _FakeCursor(results=[row])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        store.get_project(tenant_id="org_acme", project_id="prj_1")
        stamped = self._stamped(cur)
        # Both the org id and the role are stamped for the standalone read.
        assert stamped.get("app.current_org_id") == ("org_acme",)
        assert stamped.get("app.role") == ("api",)
        # The stamp precedes the tenant-scoped SELECT so RLS is in effect.
        first_select = next(
            i for i, (sql, _) in enumerate(cur.executed) if "FROM projects" in sql
        )
        last_set_config = max(
            i for i, (sql, _) in enumerate(cur.executed) if "set_config" in sql.lower()
        )
        assert last_set_config < first_select

    def test_transaction_stamps_org_id_and_role(self) -> None:
        cur = _FakeCursor(results=[None, None])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        with store.transaction(org_id="org_acme"):
            store.insert_project(
                ProjectRecord(
                    tenant_id="org_acme", owner_user_id="usr_sarah", name="Acme"
                )
            )
        stamped = self._stamped(cur)
        assert stamped.get("app.current_org_id") == ("org_acme",)
        assert stamped.get("app.role") == ("api",)

    def test_transaction_without_org_still_stamps_role(self) -> None:
        # Backward-compatible default: callers not yet passing a tenant get
        # role='api' stamped but no org id (mirrors _apply_rls_session_vars).
        cur = _FakeCursor(results=[None])
        store = PostgresProjectsStore(pool=_FakePool(cur))
        with store.transaction():
            store.insert_project(
                ProjectRecord(
                    tenant_id="org_acme", owner_user_id="usr_sarah", name="Acme"
                )
            )
        stamped = self._stamped(cur)
        assert stamped.get("app.role") == ("api",)
        assert "app.current_org_id" not in stamped
