"""DB-free behavior tests for the C1 PostgreSQL workspace-overlay adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import pytest

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from runtime_adapters.postgres.workspace_overlay_store import (
    PostgresWorkspaceOverlayStore,
)


class _FakeCursor:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row


class _FakeDatabase:
    def __init__(self) -> None:
        self.runs = {"run-one": "org-one", "run-two": "org-two"}
        self.rows: dict[str, dict[str, object]] = {}


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self._database = database
        self.statements: list[str] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(
        self, sql: str, params: tuple[object, ...] | None = None
    ) -> _FakeCursor:
        self.statements.append(sql)
        normalized = " ".join(sql.split()).lower()
        values = params or ()
        if normalized.startswith("select org_id from agent_runs"):
            run_id = str(values[0])
            org_id = self._database.runs.get(run_id)
            return _FakeCursor({"org_id": org_id} if org_id is not None else None)
        if normalized.startswith("insert into runtime_workspace_overlay_manifests"):
            org_id, run_id, version, manifest_json, updated_at = values
            existing = self._database.rows.get(str(run_id))
            if existing is None:
                self._database.rows[str(run_id)] = {
                    "org_id": org_id,
                    "run_id": run_id,
                    "version": version,
                    "manifest_json": _json_payload(manifest_json),
                    "updated_at": updated_at,
                }
            return _FakeCursor(None)
        if normalized.startswith(
            "select org_id, run_id, version, manifest_json, updated_at"
        ):
            if "where org_id = %s and run_id = %s" in normalized:
                org_id, run_id = values
                row = self._database.rows.get(str(run_id))
                if row is not None and row["org_id"] != org_id:
                    row = None
            else:
                run_id = values[0]
                row = self._database.rows.get(str(run_id))
            return _FakeCursor(dict(row) if row is not None else None)
        if normalized.startswith("update runtime_workspace_overlay_manifests"):
            version, manifest_json, updated_at, org_id, run_id, expected = values
            row = self._database.rows.get(str(run_id))
            if row is None or row["org_id"] != org_id or row["version"] != expected:
                return _FakeCursor(None)
            row.update(
                {
                    "version": version,
                    "manifest_json": _json_payload(manifest_json),
                    "updated_at": updated_at,
                }
            )
            return _FakeCursor(dict(row))
        raise AssertionError(f"Unexpected SQL: {sql}")


class _FakeStore:
    def __init__(self, database: _FakeDatabase) -> None:
        self.connection = _FakeConnection(database)
        self.roles: list[str] = []

    @asynccontextmanager
    async def _role_connection(self, role: str) -> AsyncIterator[_FakeConnection]:
        self.roles.append(role)
        yield self.connection


def _json_payload(value: object) -> object:
    """Extract the object inside psycopg's JSON wrapper for the fake driver."""

    return getattr(value, "obj", value)


def _mutation(path: str = "/workspace/project/report.csv") -> OverlayMutation:
    return OverlayMutation(
        kind=OverlayMutationKind.UPSERT,
        virtual_path=path,
        entry=OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.FILE,
            operation=WorkspaceOperation.CREATE,
            content_ref=f"artifact-blob://sha256/{'a' * 64}",
            content_digest="a" * 64,
            byte_size=7,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            stage_id="stg_00000000-0000-4000-8000-000000000123",
            stage_revision=2,
            author="agent",
        ),
    )


async def test_postgres_overlay_derives_scope_and_survives_adapter_restart() -> None:
    database = _FakeDatabase()
    first_store = _FakeStore(database)
    first = PostgresWorkspaceOverlayStore(first_store)

    stored = await first.append_revision(
        run_id="run-one", expected_version=0, mutations=(_mutation(),)
    )

    reopened_store = _FakeStore(database)
    reopened = PostgresWorkspaceOverlayStore(reopened_store)
    restored = await reopened.get_manifest(run_id="run-one")

    assert restored == stored
    assert database.rows["run-one"]["org_id"] == "org-one"
    assert restored.entry_at("/workspace/project/report.csv") is not None
    assert set(first_store.roles + reopened_store.roles) == {"worker"}


async def test_postgres_overlay_enforces_expected_version_and_preserves_winner() -> (
    None
):
    database = _FakeDatabase()
    store = PostgresWorkspaceOverlayStore(_FakeStore(database))
    winner = await store.append_revision(
        run_id="run-one", expected_version=0, mutations=(_mutation(),)
    )

    with pytest.raises(WorkspaceOverlayConflictError):
        await store.append_revision(
            run_id="run-one",
            expected_version=0,
            mutations=(_mutation("/workspace/project/next"),),
        )

    assert await store.get_manifest(run_id="run-one") == winner


async def test_postgres_overlay_never_persists_an_unknown_run() -> None:
    database = _FakeDatabase()
    store = PostgresWorkspaceOverlayStore(_FakeStore(database))

    empty = await store.get_manifest(run_id="run-unknown")
    assert empty.run_id == "run-unknown"
    assert empty.version == 0
    assert empty.entries == ()
    with pytest.raises(WorkspaceOverlayConflictError):
        await store.append_revision(
            run_id="run-unknown", expected_version=0, mutations=(_mutation(),)
        )

    assert database.rows == {}


async def test_postgres_overlay_rejects_a_corrupt_manifest_row() -> None:
    database = _FakeDatabase()
    database.rows["run-one"] = {
        "org_id": "org-one",
        "run_id": "run-one",
        "version": 1,
        "manifest_json": OverlayManifest(run_id="other-run", version=1).model_dump(
            mode="json"
        ),
        "updated_at": datetime.now().astimezone(),
    }
    store = PostgresWorkspaceOverlayStore(_FakeStore(database))

    with pytest.raises(WorkspaceOverlayConflictError):
        await store.get_manifest(run_id="run-one")
