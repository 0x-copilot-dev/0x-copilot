"""Shared helpers + autouse fixtures for the postgres adapter tests.

The two test files in this directory (sync ``PostgresRuntimeApiStore`` and
async ``AsyncPostgresRuntimeApiStore``) both target the same Postgres schema
and the same docker volume. Without isolation, leftover rows from one test
(particularly outbox commands) leak into the next and cause flaky failures
that look like real bugs.

The ``_truncate_runtime_tables_before_each_test`` autouse fixture clears all
runtime-owned tables at the start of every test in this directory. We
truncate after schema migration (the migration has to run at least once so
the tables exist), and only when ``TEST_DATABASE_URL`` is set — otherwise
the whole test class is skipped anyway.
"""

from __future__ import annotations

import os

import psycopg
import pytest


# Tables truncated between tests. Listed CASCADE-safely; psycopg runs the
# TRUNCATE in one statement so order doesn't matter.
_RUNTIME_TABLES = (
    "runtime_deletion_evidence",
    "runtime_legal_holds",
    "runtime_audit_log",
    "runtime_capability_snapshots",
    "runtime_compression_events",
    "runtime_context_payloads",
    "runtime_memory_items",
    "runtime_memory_scopes",
    "runtime_tool_invocations",
    "runtime_approval_requests",
    "runtime_subagent_results",
    "runtime_async_tasks",
    "runtime_consumer_cursors",
    "runtime_outbox_events",
    "runtime_events",
    "agent_runs",
    "agent_messages",
    "agent_conversations",
)


def _truncate_runtime_tables(database_url: str) -> None:
    """Wipe every runtime-owned table — fast, idempotent, FK-safe."""

    statement = (
        "TRUNCATE TABLE " + ", ".join(_RUNTIME_TABLES) + " RESTART IDENTITY CASCADE"
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        # If migration hasn't been applied yet (e.g. the very first test), the
        # tables won't exist; that's fine — the migration runs in the per-test
        # fixture immediately afterward.
        try:
            conn.execute(statement)
        except psycopg.errors.UndefinedTable:
            pass


@pytest.fixture(autouse=True)
def _truncate_runtime_tables_before_each_test() -> None:
    """Truncate runtime tables before every postgres adapter test."""

    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        # The test classes themselves skip on this; nothing to do.
        return
    _truncate_runtime_tables(database_url)
