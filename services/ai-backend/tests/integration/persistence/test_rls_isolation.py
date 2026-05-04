"""Postgres Row-Level Security integration test for ai-backend (C5).

Skipped when ``RUNTIME_RLS_TEST_DATABASE_URL`` is unset — RLS isolation can
only be observed against a real Postgres instance with the migration applied
and ``do_rls.sql`` enabled. CI configures the env var when running the
``rls-isolation`` job; locally, set it to a disposable database created
specifically for this test (it is destructive: the test mutates rows in
every tenant-scoped table).

Test plan (mirrors docs/roadmap/15-c5-rls-tenant-isolation.md §3.2):

1. Apply yoyo migrations (0001 .. 0008) and ``staged/do_rls.sql``.
2. Connect as ``enterprise_app`` (RLS-enforced).
3. For every tenant-scoped table:
   - Insert a row with ``app.current_org_id='org_a'``.
   - Switch to ``app.current_org_id='org_b'`` and assert SELECT yields zero
     rows, UPDATE matches zero rows, DELETE matches zero rows.
4. Negative test: open a fresh session without setting the var and assert
   SELECT yields zero rows for every tenant-scoped table.
5. Worker test: with ``app.role='worker'`` set, the worker can drain the
   outbox across tenants; without it, the outbox query yields zero rows.

The harness uses raw psycopg (sync) for clarity. The full async store is
exercised by unit tests; this file is purely about the DB-level guarantee.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("RUNTIME_RLS_TEST_DATABASE_URL"),
    reason=(
        "Set RUNTIME_RLS_TEST_DATABASE_URL to a disposable Postgres database "
        "to exercise the C5 row-level security isolation test."
    ),
)


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


@pytest.fixture(scope="module")
def database_url() -> str:
    return os.environ["RUNTIME_RLS_TEST_DATABASE_URL"]


@pytest.fixture(scope="module")
def admin_conn(database_url: str) -> Iterator[Any]:
    """Connection with BYPASSRLS for setup/teardown."""

    psycopg = pytest.importorskip("psycopg")
    import yoyo  # noqa: F401  (psycopg+yoyo are deps of the service)

    backend = yoyo.get_backend(database_url)
    migrations = yoyo.read_migrations(str(MIGRATIONS_DIR))
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))

    do_rls = (MIGRATIONS_DIR / "staged" / "do_rls.sql").read_text()
    undo_rls = (MIGRATIONS_DIR / "staged" / "undo_rls.sql").read_text()
    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(do_rls)
        try:
            yield conn
        finally:
            with conn.cursor() as cur:
                cur.execute(undo_rls)


@pytest.fixture
def app_conn(database_url: str) -> Iterator[Any]:
    """Connection authenticating as ``enterprise_app`` (RLS-enforced).

    The CI fixture creates a database user GRANT'd into ``enterprise_app``;
    locally, set ``RUNTIME_RLS_TEST_APP_DATABASE_URL`` to such a user to
    avoid editing your default role membership.
    """

    psycopg = pytest.importorskip("psycopg")
    app_url = os.environ.get("RUNTIME_RLS_TEST_APP_DATABASE_URL", database_url)
    with psycopg.connect(app_url, autocommit=True) as conn:
        yield conn


def _set_org(conn: Any, org_id: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.current_org_id', %s, false)",
            (org_id if org_id is not None else "",),
        )


def _set_role(conn: Any, role: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.role', %s, false)",
            (role if role is not None else "",),
        )


class TestRlsIsolationAgentConversations:
    """Single-table proof. Wider table coverage is exercised below."""

    def test_org_b_cannot_read_org_a_conversation(
        self, admin_conn: Any, app_conn: Any
    ) -> None:
        conv_id = f"conv-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)

        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_conversations (
                    id, org_id, user_id, assistant_id, title, status,
                    created_at, updated_at, metadata_json, schema_version
                ) VALUES (%s, 'org_a', 'user_1', 'assistant_x', 't', 'active',
                          %s, %s, '{}'::jsonb, 1)
                """,
                (conv_id, now, now),
            )

        _set_org(app_conn, "org_b")
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM agent_conversations WHERE id = %s", (conv_id,))
            assert cur.fetchone() is None

            cur.execute(
                "UPDATE agent_conversations SET title='hijack' WHERE id = %s",
                (conv_id,),
            )
            assert cur.rowcount == 0

            cur.execute("DELETE FROM agent_conversations WHERE id = %s", (conv_id,))
            assert cur.rowcount == 0

        # Cleanup as org_a.
        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute("DELETE FROM agent_conversations WHERE id = %s", (conv_id,))

    def test_unset_org_id_returns_zero_rows(
        self, admin_conn: Any, app_conn: Any
    ) -> None:
        conv_id = f"conv-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)

        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_conversations (
                    id, org_id, user_id, assistant_id, title, status,
                    created_at, updated_at, metadata_json, schema_version
                ) VALUES (%s, 'org_a', 'user_1', 'assistant_x', 't', 'active',
                          %s, %s, '{}'::jsonb, 1)
                """,
                (conv_id, now, now),
            )

        _set_org(app_conn, None)
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM agent_conversations WHERE id = %s", (conv_id,))
            assert cur.fetchone() is None

        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute("DELETE FROM agent_conversations WHERE id = %s", (conv_id,))


class TestRlsIsolationOutboxWorker:
    """The outbox uses ``tenant_or_worker`` instead of plain tenant_isolation."""

    def test_worker_role_bypasses_tenant_filter(
        self, admin_conn: Any, app_conn: Any
    ) -> None:
        out_id = f"out-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)

        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runtime_outbox_events (
                    id, aggregate_type, aggregate_id, org_id, event_type,
                    payload_json, status, attempts, available_at,
                    created_at, updated_at
                ) VALUES (%s, 'agent_run', %s, 'org_a', 'run_requested',
                          '{}'::jsonb, 'pending', 0, %s, %s, %s)
                """,
                (out_id, out_id, now, now, now),
            )

        # Without app.role='worker' and as a different tenant: invisible.
        _set_org(app_conn, "org_b")
        _set_role(app_conn, None)
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM runtime_outbox_events WHERE id = %s", (out_id,))
            assert cur.fetchone() is None

        # With app.role='worker': visible across tenants.
        _set_role(app_conn, "worker")
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM runtime_outbox_events WHERE id = %s", (out_id,))
            assert cur.fetchone() is not None

        # Cleanup as the owning tenant.
        _set_role(app_conn, None)
        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute("DELETE FROM runtime_outbox_events WHERE id = %s", (out_id,))
