"""DB-free contract tests for the Postgres E2 D5 mapping adapter."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationStateError,
    LegacyStageMigrationOutcome,
    LegacyStageMigrationRecord,
)
from runtime_adapters.postgres.legacy_stage_migration_store import (
    PostgresLegacyStageMigrationStore,
)


pytestmark = pytest.mark.anyio
NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _record() -> LegacyStageMigrationRecord:
    return LegacyStageMigrationRecord(
        org_id="org_e2_stage_pg",
        migration_id="e2_d5",
        run_id="run_e2_stage_pg",
        legacy_stage_id="legacy_stage_pg",
        source_digest="a" * 64,
        outcome=LegacyStageMigrationOutcome.CANONICAL_HELD,
        canonical_stage_id="stg_00000000-0000-4000-8000-000000000001",
        queue_cancelled=True,
        reconciler_frozen=False,
        revision=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _row(record: LegacyStageMigrationRecord) -> dict[str, object]:
    return record.model_dump()


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
        replacement_row: object | None = None,
    ) -> None:
        self.insert_row = insert_row
        self.selected_row = selected_row
        self.replacement_row = replacement_row
        self.executions: list[tuple[str, object]] = []

    async def execute(self, statement: str, values: object) -> _Cursor:
        self.executions.append((statement, values))
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("insert into runtime_e2_legacy_stage_migrations"):
            return _Cursor(self.insert_row)
        if normalized.startswith("select"):
            return _Cursor(self.selected_row)
        if normalized.startswith("update runtime_e2_legacy_stage_migrations"):
            return _Cursor(self.replacement_row)
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


async def test_postgres_mapping_is_worker_only_and_never_persists_source_body() -> None:
    record = _record()
    connection = _Connection(insert_row=_row(record), selected_row=_row(record))
    store = _Store(connection)
    adapter = PostgresLegacyStageMigrationStore(store=store)

    created = await adapter.load_or_create(record=record)
    loaded = await adapter.load(
        org_id=record.org_id,
        migration_id=record.migration_id,
        run_id=record.run_id,
        legacy_stage_id=record.legacy_stage_id,
    )

    assert created == loaded == record
    assert store.roles == ["worker", "worker"]
    statements = "\n".join(statement for statement, _ in connection.executions)
    assert "ON CONFLICT (org_id, migration_id, run_id, legacy_stage_id)" in statements
    assert "approval" not in statements.lower()
    assert "content_text" not in statements
    assert "target_args" not in statements


async def test_postgres_mapping_fails_closed_on_malformed_row() -> None:
    connection = _Connection(insert_row=None, selected_row={"org_id": "org"})
    adapter = PostgresLegacyStageMigrationStore(store=_Store(connection))

    with pytest.raises(LegacyMigrationStateError):
        await adapter.load(
            org_id="org", migration_id="e2_d5", run_id="run", legacy_stage_id="stage"
        )


async def test_postgres_mapping_can_replace_only_a_frozen_reconciliation() -> None:
    frozen = _record().model_copy(
        update={
            "outcome": LegacyStageMigrationOutcome.FROZEN_RECONCILE,
            "canonical_stage_id": None,
            "queue_cancelled": False,
            "reconciler_frozen": True,
        }
    )
    replacement = _record()
    connection = _Connection(
        insert_row=None,
        selected_row=_row(frozen),
        replacement_row=_row(replacement),
    )
    adapter = PostgresLegacyStageMigrationStore(store=_Store(connection))

    persisted = await adapter.replace_frozen(record=replacement)

    assert persisted == replacement
    statement = connection.executions[0][0]
    assert "AND outcome = %s" in statement
    assert connection.executions[0][1][-1] == "frozen_reconcile"
