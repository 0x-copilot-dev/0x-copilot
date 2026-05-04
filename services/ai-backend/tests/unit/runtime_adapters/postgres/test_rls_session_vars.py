"""Unit tests for the C5 connection-checkout helpers in the postgres store.

The integration test in tests/integration/persistence/test_rls_isolation.py
proves end-to-end isolation against a real Postgres. These unit tests prove
the *helper-shape* contract — that ``_tenant_connection`` and
``_role_connection`` execute the right ``set_config`` SQL — without needing
a live database.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.executed.append((query, params))

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeAcquire:
    """Simulates ``self._pool.connection()`` returning an async ctx manager."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def connection(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


@pytest.fixture
def store() -> tuple[PostgresRuntimeApiStore, _FakeConn]:
    conn = _FakeConn()
    fake_pool = _FakePool(conn)
    instance = PostgresRuntimeApiStore.__new__(PostgresRuntimeApiStore)
    instance._pool = fake_pool  # type: ignore[attr-defined]
    instance._role = "api"  # type: ignore[attr-defined]
    return instance, conn


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


class TestTenantConnection:
    def test_stamps_org_id_and_role(
        self, store: tuple[PostgresRuntimeApiStore, _FakeConn]
    ) -> None:
        instance, conn = store

        async def _drive() -> None:
            async with instance._tenant_connection(org_id="org_a") as got:
                assert got is conn

        _run(_drive())

        queries = [q for q, _ in conn.executed]
        params = [p for _, p in conn.executed]
        assert any("app.current_org_id" in q for q in queries)
        assert any("app.role" in q for q in queries)
        # Verify the org_a value reaches the bind list, not just the SQL text.
        assert ("org_a",) in params
        assert ("api",) in params

    def test_omits_org_id_when_none(
        self, store: tuple[PostgresRuntimeApiStore, _FakeConn]
    ) -> None:
        instance, conn = store

        async def _drive() -> None:
            async with instance._tenant_connection() as _:
                pass

        _run(_drive())

        queries = [q for q, _ in conn.executed]
        # role still stamped; org_id skipped.
        assert all("app.current_org_id" not in q for q in queries)
        assert any("app.role" in q for q in queries)

    def test_role_override_wins_over_self_role(
        self, store: tuple[PostgresRuntimeApiStore, _FakeConn]
    ) -> None:
        instance, conn = store

        async def _drive() -> None:
            async with instance._tenant_connection(org_id="org_a", role="worker") as _:
                pass

        _run(_drive())

        params = [p for _, p in conn.executed]
        assert ("worker",) in params
        assert ("api",) not in params


class TestRoleConnection:
    def test_role_only_stamp(
        self, store: tuple[PostgresRuntimeApiStore, _FakeConn]
    ) -> None:
        instance, conn = store

        async def _drive() -> None:
            async with instance._role_connection("worker") as _:
                pass

        _run(_drive())

        queries = [q for q, _ in conn.executed]
        params = [p for _, p in conn.executed]
        assert all("app.current_org_id" not in q for q in queries)
        assert ("worker",) in params
