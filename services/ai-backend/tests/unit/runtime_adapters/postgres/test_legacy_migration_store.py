"""DB-free contract tests for the Postgres E2 checkpoint adapter."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationCheckpoint,
    LegacyMigrationStateError,
    LegacyMigrationStatus,
)
from runtime_adapters.postgres.legacy_migration_store import (
    PostgresLegacyMigrationCheckpointStore,
)


pytestmark = pytest.mark.anyio

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _checkpoint() -> LegacyMigrationCheckpoint:
    return LegacyMigrationCheckpoint(
        org_id="org_e2_pg",
        migration_id="e2_cohort_pg",
        source_digest="a" * 64,
        after_draft_id=None,
        status=LegacyMigrationStatus.RUNNING,
        report_digest=None,
        revision=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _row(checkpoint: LegacyMigrationCheckpoint) -> dict[str, object]:
    return {
        "org_id": checkpoint.org_id,
        "migration_id": checkpoint.migration_id,
        "source_digest": checkpoint.source_digest,
        "after_draft_id": checkpoint.after_draft_id,
        "status": checkpoint.status.value,
        "report_digest": checkpoint.report_digest,
        "revision": checkpoint.revision,
        "created_at": checkpoint.created_at,
        "updated_at": checkpoint.updated_at,
    }


class _Cursor:
    def __init__(self, row: object | None) -> None:
        self._row = row

    async def fetchone(self) -> object | None:
        return self._row


class _Connection:
    def __init__(
        self,
        *,
        insert_row: object | None,
        selected_row: object | None,
        updated_row: object | None,
    ) -> None:
        self.insert_row = insert_row
        self.selected_row = selected_row
        self.updated_row = updated_row
        self.executions: list[tuple[str, object]] = []

    async def execute(self, statement: str, values: object) -> _Cursor:
        self.executions.append((statement, values))
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("insert into runtime_e2_legacy_migrations"):
            return _Cursor(self.insert_row)
        if normalized.startswith("select"):
            return _Cursor(self.selected_row)
        if normalized.startswith("update runtime_e2_legacy_migrations"):
            return _Cursor(self.updated_row)
        raise AssertionError(f"unexpected statement: {statement}")

    @asynccontextmanager
    async def transaction(self):
        yield


class _Store:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.roles: list[str] = []

    @asynccontextmanager
    async def _role_connection(self, role: str):
        self.roles.append(role)
        yield self.connection


class TestPostgresLegacyMigrationCheckpointStore:
    async def test_creates_and_cas_advances_only_under_worker_role(self) -> None:
        initial = _checkpoint()
        advanced = initial.model_copy(
            update={
                "after_draft_id": f"{1:032x}",
                "report_digest": "b" * 64,
                "revision": 1,
                "updated_at": NOW + timedelta(seconds=1),
            }
        )
        connection = _Connection(
            insert_row=_row(initial),
            selected_row=_row(initial),
            updated_row=_row(advanced),
        )
        store = _Store(connection)
        adapter = PostgresLegacyMigrationCheckpointStore(store=store)

        created = await adapter.load_or_create(checkpoint=initial)
        updated = await adapter.compare_and_set(
            expected=created,
            after_draft_id=advanced.after_draft_id,
            status=LegacyMigrationStatus.RUNNING,
            report_digest=advanced.report_digest,
            updated_at=advanced.updated_at,
        )

        assert created == initial
        assert updated == advanced
        assert store.roles == ["worker", "worker"]
        statements = "\n".join(statement for statement, _ in connection.executions)
        assert "ON CONFLICT (org_id, migration_id) DO NOTHING" in statements
        assert "AND revision = %s" in statements
        assert "content_text" not in statements
        assert "target_args" not in statements

    async def test_malformed_persisted_row_fails_closed(self) -> None:
        connection = _Connection(
            insert_row=None,
            selected_row={"org_id": "org_e2_pg"},
            updated_row=None,
        )
        adapter = PostgresLegacyMigrationCheckpointStore(store=_Store(connection))

        with pytest.raises(LegacyMigrationStateError):
            await adapter.load(org_id="org_e2_pg", migration_id="e2_cohort_pg")
