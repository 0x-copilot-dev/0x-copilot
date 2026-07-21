"""Postgres Row-Level Security integration test for backend (C5).

Skipped when ``BACKEND_RLS_TEST_DATABASE_URL`` is unset. Mirrors
services/ai-backend/tests/integration/persistence/test_rls_isolation.py for
the backend's tenant-scoped tables (MCP, identity, sessions). The test is
destructive: it inserts then deletes rows in the listed tables, so use a
disposable database.
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
    not os.environ.get("BACKEND_RLS_TEST_DATABASE_URL"),
    reason=(
        "Set BACKEND_RLS_TEST_DATABASE_URL to a disposable Postgres database "
        "to exercise the C5 row-level security isolation test."
    ),
)


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


@pytest.fixture(scope="module")
def database_url() -> str:
    return os.environ["BACKEND_RLS_TEST_DATABASE_URL"]


@pytest.fixture(scope="module")
def admin_conn(database_url: str) -> Iterator[Any]:
    """Connection with BYPASSRLS for setup/teardown."""

    psycopg = pytest.importorskip("psycopg")
    import yoyo

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
    psycopg = pytest.importorskip("psycopg")
    app_url = os.environ.get("BACKEND_RLS_TEST_APP_DATABASE_URL", database_url)
    with psycopg.connect(app_url, autocommit=True) as conn:
        yield conn


def _set_org(conn: Any, org_id: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.current_org_id', %s, false)",
            (org_id if org_id is not None else "",),
        )


class TestRlsIsolationMcpServers:
    def test_org_b_cannot_read_org_a_server(
        self, admin_conn: Any, app_conn: Any
    ) -> None:
        server_id = f"srv-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)

        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_servers (
                  server_id, org_id, user_id, name, display_name, url,
                  transport, auth_mode, auth_state, health, enabled,
                  required_scopes, last_discovery, oauth_client,
                  created_at, updated_at
                ) VALUES (%s, 'org_a', 'user_1', 'demo', 'Demo',
                          'https://example.invalid', 'http', 'none', 'none',
                          'unknown', TRUE, '[]'::jsonb, '{}'::jsonb, NULL,
                          %s, %s)
                """,
                (server_id, now, now),
            )

        _set_org(app_conn, "org_b")
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mcp_servers WHERE server_id = %s", (server_id,))
            assert cur.fetchone() is None

            cur.execute("DELETE FROM mcp_servers WHERE server_id = %s", (server_id,))
            assert cur.rowcount == 0

        _set_org(app_conn, "org_a")
        with app_conn.cursor() as cur:
            cur.execute("DELETE FROM mcp_servers WHERE server_id = %s", (server_id,))


class TestRlsRolesPolicy:
    """``roles`` allows NULL ``org_id`` so system roles stay visible."""

    def test_system_role_visible_to_any_tenant(
        self, admin_conn: Any, app_conn: Any
    ) -> None:
        role_id = f"role-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)

        # Insert system role via BYPASSRLS admin connection.
        with admin_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO roles (role_id, org_id, name, display_name,
                                   description, is_system, permission_scopes,
                                   created_at, updated_at)
                VALUES (%s, NULL, %s, %s, 'system role', TRUE, '[]'::jsonb,
                        %s, %s)
                """,
                (role_id, f"sys-{role_id}", f"sys-{role_id}", now, now),
            )

        try:
            _set_org(app_conn, "org_a")
            with app_conn.cursor() as cur:
                cur.execute("SELECT 1 FROM roles WHERE role_id = %s", (role_id,))
                assert cur.fetchone() is not None
        finally:
            with admin_conn.cursor() as cur:
                cur.execute("DELETE FROM roles WHERE role_id = %s", (role_id,))
