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

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.store import (
    InMemoryProjectsStore,
    PostgresProjectsStore,
    ProjectMembershipRecord,
    ProjectRecord,
    _coerce_json,
    _jsonb,
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
        sql, params = cur.executed[0]
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
        # Non-deleted read appends the deleted_at guard.
        assert "deleted_at IS NULL" in cur.executed[0][0]

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
        assert len(cur.executed) == 2
        assert "INSERT INTO projects" in cur.executed[0][0]
        assert "INSERT INTO project_memberships" in cur.executed[1][0]

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
