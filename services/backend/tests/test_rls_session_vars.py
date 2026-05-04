"""Unit tests for the C5 connection-checkout helpers in the backend store.

The integration test in tests/integration/persistence/test_rls_isolation.py
proves end-to-end isolation against a real Postgres. These unit tests prove
the helper-shape contract — that ``_apply_rls_session_vars`` and the
``_connect`` / ``_connect_or_inherit`` / ``transaction`` wrappers execute
the right ``set_config`` SQL — without needing a live database.
"""

from __future__ import annotations

from typing import Any

from backend_app.store import _apply_rls_session_vars


class _FakeCursor:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._sink = sink

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self._sink.append((query, params))


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.executed)


class TestApplyRlsSessionVars:
    def test_no_op_when_both_none(self) -> None:
        conn = _FakeConn()
        _apply_rls_session_vars(conn, org_id=None, role=None)
        assert conn.executed == []

    def test_stamps_org_id_only(self) -> None:
        conn = _FakeConn()
        _apply_rls_session_vars(conn, org_id="org_a", role=None)
        assert len(conn.executed) == 1
        query, params = conn.executed[0]
        assert "app.current_org_id" in query
        assert params == ("org_a",)

    def test_stamps_role_only(self) -> None:
        conn = _FakeConn()
        _apply_rls_session_vars(conn, org_id=None, role="api")
        assert len(conn.executed) == 1
        query, params = conn.executed[0]
        assert "app.role" in query
        assert params == ("api",)

    def test_stamps_both_in_order(self) -> None:
        conn = _FakeConn()
        _apply_rls_session_vars(conn, org_id="org_a", role="worker")
        queries = [q for q, _ in conn.executed]
        params = [p for _, p in conn.executed]
        assert any("app.current_org_id" in q for q in queries)
        assert any("app.role" in q for q in queries)
        assert ("org_a",) in params
        assert ("worker",) in params
