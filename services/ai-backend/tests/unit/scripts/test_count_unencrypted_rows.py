"""C7 phase 3: count_unencrypted_rows operator script.

The script is a precondition gate before flipping
``RUNTIME_FIELD_ENCRYPTION_STRICT_READS=true``. Its exit code drives
the deploy: 0 = safe, 2 = backfill not yet complete, 1 = error.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from importlib import util
from pathlib import Path

import psycopg
import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "count_unencrypted_rows.py"
)


def _load_module():
    # The script lives outside the importable package tree; load it via
    # spec so this test doesn't require an extra ``scripts/__init__.py``.
    spec = util.spec_from_file_location("count_unencrypted_rows", _SCRIPT_PATH)
    assert spec is not None
    module = util.module_from_spec(spec)
    sys.modules["count_unencrypted_rows"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


class _FakeCursor:
    """Returns a configurable count per table."""

    def __init__(self, *, counts: dict[str, int], missing: set[str] = frozenset()):
        self._counts = counts
        self._missing = missing
        self._last_table: str | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        # SQL shape: SELECT COUNT(*) FROM <table> WHERE encryption_version = 0
        for table in list(self._counts) + list(self._missing):
            if f"FROM {table}" in sql:
                self._last_table = table
                break
        if self._last_table in self._missing:
            raise psycopg.errors.UndefinedColumn("missing encryption_version")

    def fetchone(self) -> tuple[int]:
        assert self._last_table is not None
        return (self._counts[self._last_table],)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self._cursor


@pytest.fixture
def patched_psycopg(monkeypatch):
    def _make(counts: dict[str, int], missing: set[str] = frozenset()):
        cursor = _FakeCursor(counts=counts, missing=missing)
        conn = _FakeConn(cursor)

        def _connect(*args, **kwargs):
            return conn

        monkeypatch.setattr(MODULE.psycopg, "connect", _connect)
        return cursor

    return _make


class TestExitCodes:
    def test_all_zero_returns_zero(self, patched_psycopg) -> None:
        counts = {table: 0 for table, _ in MODULE._TARGET_TABLES}
        patched_psycopg(counts)
        with redirect_stdout(io.StringIO()):
            rc = MODULE.main(["--db-url", "postgres://ignored"])
        assert rc == 0

    def test_nonzero_count_returns_two(self, patched_psycopg) -> None:
        counts = {table: 0 for table, _ in MODULE._TARGET_TABLES}
        counts["agent_messages"] = 17
        patched_psycopg(counts)
        with redirect_stdout(io.StringIO()):
            rc = MODULE.main(["--db-url", "postgres://ignored"])
        assert rc == 2

    def test_missing_column_returns_one(self, patched_psycopg) -> None:
        counts = {table: 0 for table, _ in MODULE._TARGET_TABLES}
        # Simulate the migration 0011 not having been applied to one
        # table — strict reads cannot be safely flipped on.
        patched_psycopg(counts, missing={"agent_messages"})
        with redirect_stdout(io.StringIO()):
            rc = MODULE.main(["--db-url", "postgres://ignored"])
        assert rc == 1


class TestOutput:
    def test_json_mode_emits_machine_payload(self, patched_psycopg) -> None:
        counts = {table: 0 for table, _ in MODULE._TARGET_TABLES}
        counts["runtime_events"] = 3
        patched_psycopg(counts)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = MODULE.main(["--db-url", "postgres://ignored", "--json"])
        assert rc == 2
        payload = json.loads(buf.getvalue())
        assert payload["unencrypted_row_counts"]["runtime_events"] == 3
        assert payload["unencrypted_row_counts"]["agent_messages"] == 0

    def test_human_report_lists_every_target_table(self, patched_psycopg) -> None:
        counts = {table: 0 for table, _ in MODULE._TARGET_TABLES}
        patched_psycopg(counts)
        buf = io.StringIO()
        with redirect_stdout(buf):
            MODULE.main(["--db-url", "postgres://ignored"])
        report = buf.getvalue()
        for table, _ in MODULE._TARGET_TABLES:
            assert table in report


class TestMissingDbUrl:
    def test_no_db_url_returns_one(self, monkeypatch) -> None:
        monkeypatch.delenv("RUNTIME_DATABASE_URL", raising=False)
        rc = MODULE.main([])
        assert rc == 1
