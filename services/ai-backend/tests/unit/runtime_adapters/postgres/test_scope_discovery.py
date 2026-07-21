"""Tests for ``PostgresRuntimeApiStore.list_conversation_scopes``.

The method is the read-only, cross-tenant scope-discovery seam the offline
file-store migration (``runtime_adapters.file.migration.StoreMigrator``) relies
on to migrate *every* tenant without hand-passed ``--org-id``/``--user-id``
scopes, and to no-op cleanly on a brand-new install whose
``agent_conversations`` relation does not exist yet.

Two layers:

* Fast unit tests (no database) drive the store with a fake connection pool so
  the SQL wiring, row mapping, and the missing-table tolerance are asserted
  deterministically — they always run.
* A live end-to-end test seeds two tenants and asserts real ``SELECT DISTINCT``
  behaviour; it is skipped unless ``TEST_DATABASE_URL`` is set (the same gate the
  rest of this directory uses via ``conftest.py``).
"""

from __future__ import annotations

import os

import pytest
from psycopg import errors as psycopg_errors

from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest


# ==========================================================================
# Fast unit tests — no database, fake connection pool.
# ==========================================================================


class _FakeCursor:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, str]]:
        return self._rows


class _FakeConnection:
    """Records executed SQL; answers the scope query with canned rows/errors."""

    def __init__(
        self, *, rows: list[dict[str, str]] | None = None, undefined_table: bool = False
    ) -> None:
        self._rows = rows or []
        self._undefined_table = undefined_table
        self.executed: list[str] = []

    async def execute(self, sql: str, params: object = None) -> _FakeCursor:
        self.executed.append(sql)
        if "agent_conversations" in sql:
            if self._undefined_table:
                raise psycopg_errors.UndefinedTable(
                    'relation "agent_conversations" does not exist'
                )
            return _FakeCursor(self._rows)
        # The set_config('app.role', ...) stamp issued by _role_connection.
        return _FakeCursor([])


class _ConnCtx:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def connection(self) -> _ConnCtx:
        return _ConnCtx(self._conn)


def _store(conn: _FakeConnection) -> PostgresRuntimeApiStore:
    return PostgresRuntimeApiStore(pool=_FakePool(conn), role="migrate")


class TestListConversationScopesUnit:
    async def test_maps_distinct_org_user_rows_in_order(self) -> None:
        conn = _FakeConnection(
            rows=[
                {"org_id": "org_a", "user_id": "user_1"},
                {"org_id": "org_a", "user_id": "user_2"},
                {"org_id": "org_b", "user_id": "user_1"},
            ]
        )
        scopes = await _store(conn).list_conversation_scopes()

        assert scopes == (
            ("org_a", "user_1"),
            ("org_a", "user_2"),
            ("org_b", "user_1"),
        )
        # The cross-tenant DISTINCT scan actually ran (not just the role stamp).
        assert any(
            "agent_conversations" in sql and "DISTINCT" in sql for sql in conn.executed
        )

    async def test_missing_table_is_an_empty_no_op(self) -> None:
        # Fresh desktop install: the ai schema was never applied because the app
        # has only ever run the file store. "No table" must mean "no scopes",
        # not a crash — the first-file-boot import depends on this exit path.
        conn = _FakeConnection(undefined_table=True)
        scopes = await _store(conn).list_conversation_scopes()
        assert scopes == ()

    async def test_empty_table_returns_empty_tuple(self) -> None:
        conn = _FakeConnection(rows=[])
        scopes = await _store(conn).list_conversation_scopes()
        assert scopes == ()

    async def test_values_are_coerced_to_str(self) -> None:
        # dict_row can hand back non-str column types (e.g. uuid); the seam the
        # migration consumes must always be plain (str, str) pairs.
        conn = _FakeConnection(rows=[{"org_id": 123, "user_id": 456}])
        scopes = await _store(conn).list_conversation_scopes()
        assert scopes == (("123", "456"),)


# ==========================================================================
# Live end-to-end — requires TEST_DATABASE_URL.
# ==========================================================================


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for live PostgresRuntimeApiStore tests.",
)
class TestListConversationScopesLive:
    async def test_discovers_distinct_tenant_scopes(self) -> None:
        store = PostgresRuntimeApiStore(
            os.environ["TEST_DATABASE_URL"],
            role="migrate",
            pool_min_size=1,
            pool_max_size=2,
        )
        await store.open()
        try:
            await store.migrate()

            # Empty schema -> no scopes.
            assert await store.list_conversation_scopes() == ()

            # Seed two tenants, one of them with two conversations.
            for org, user in (
                ("org_a", "user_1"),
                ("org_a", "user_1"),
                ("org_b", "user_2"),
            ):
                await store.create_conversation(
                    CreateConversationRequest(
                        org_id=org, user_id=user, assistant_id="assistant", metadata={}
                    )
                )

            scopes = await store.list_conversation_scopes()
            assert scopes == (("org_a", "user_1"), ("org_b", "user_2"))
        finally:
            await store.close()
