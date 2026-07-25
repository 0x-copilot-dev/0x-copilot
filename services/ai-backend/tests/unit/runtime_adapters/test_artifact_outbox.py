"""Stable artifact command envelope and atomic Postgres insertion."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime_adapters._artifact_repository import (
    ARTIFACT_AGGREGATE_TYPE,
    ARTIFACT_EVENT_COMMAND_TYPE,
    artifact_event_outbox_row,
)
from agent_runtime.persistence.records import RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from copilot_service_contracts.deployment_profile import ENV_DEPLOYMENT_PROFILE
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.postgres.artifact_store import (
    PostgresArtifactMetadataStore,
)
from runtime_api.schemas.commands import RuntimeArtifactEventCommand
from tests.unit.runtime_adapters._artifact_fixtures import make_create_command
from runtime_adapters.in_memory import InMemoryRuntimeApiStore


def test_artifact_outbox_envelope_is_exact_and_reference_only() -> None:
    command = make_create_command()
    event = command.ledger_events[0]
    artifact_id = command.record.artifact.artifact_id

    row = artifact_event_outbox_row(event, artifact_id=artifact_id)
    timestamp = RuntimeArtifactEventCommand.model_validate(
        row["payload_json"]
    ).model_dump(mode="json")["created_at"]

    assert row == {
        "id": event.event_id,
        "aggregate_type": ARTIFACT_AGGREGATE_TYPE,
        "aggregate_id": artifact_id,
        "org_id": event.scope.org_id,
        "event_type": ARTIFACT_EVENT_COMMAND_TYPE,
        "payload_json": {
            "command_id": event.event_id,
            "event_id": event.event_id,
            "org_id": event.scope.org_id,
            "user_id": event.scope.user_id,
            "run_id": event.scope.run_id,
            "conversation_id": event.scope.conversation_id,
            "trace_id": event.scope.trace_id,
            "event_type": event.event_type.value,
            "payload": event.payload,
            "created_at": timestamp,
            "trace_propagation": {},
        },
        "status": "pending",
        "attempts": 0,
        "available_at": timestamp,
        "locked_by": None,
        "lock_expires_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    assert "blob_key" not in row["payload_json"]
    assert "content" not in row["payload_json"]
    assert (
        RuntimeArtifactEventCommand.model_validate(row["payload_json"]).model_dump(
            mode="json"
        )
        == row["payload_json"]
    )


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    async def fetchone(self):
        return self._row


class _Transaction:
    def __init__(self, connection: "_Connection") -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.transaction_depth += 1

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._connection.transaction_depth -= 1


class _Connection:
    def __init__(self) -> None:
        self.transaction_depth = 0
        self.executions: list[tuple[str, tuple[Any, ...], int]] = []

    def transaction(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.executions.append((sql, params, self.transaction_depth))
        return _Cursor()


class _TenantConnection:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Parent:
    def __init__(self) -> None:
        self.connection = _Connection()
        self.org_ids: list[str] = []

    def _tenant_connection(self, *, org_id: str) -> _TenantConnection:
        self.org_ids.append(org_id)
        return _TenantConnection(self.connection)


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def test_postgres_metadata_and_outbox_share_one_transaction(
    tmp_path,
) -> None:
    parent = _Parent()
    blobs = FileArtifactBlobStore(FileStoreLayout(tmp_path / "shared-blobs"))
    store = PostgresArtifactMetadataStore(parent, blobs)
    command = make_create_command()
    body = b"revision one"
    await blobs.put_stream(
        expected_digest=command.record.current_revision.blob_key,
        chunks=_chunks(body),
        byte_limit=len(body),
    )

    await store.create_artifact(command)

    assert parent.org_ids == [command.record.artifact.org_id]
    assert parent.connection.transaction_depth == 0
    assert parent.connection.executions
    assert all(depth == 1 for _, _, depth in parent.connection.executions)
    statements = [" ".join(sql.split()) for sql, _, _ in parent.connection.executions]
    artifact_index = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith("INSERT INTO runtime_artifacts")
    )
    revision_index = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith("INSERT INTO runtime_artifact_revisions")
    )
    outbox_index = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith("INSERT INTO runtime_outbox_events")
    )
    idempotency_index = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith("INSERT INTO runtime_artifact_idempotency")
    )
    assert artifact_index < revision_index < outbox_index < idempotency_index

    _, outbox_params, _ = parent.connection.executions[outbox_index]
    expected = artifact_event_outbox_row(
        command.ledger_events[0],
        artifact_id=command.record.artifact.artifact_id,
    )
    assert outbox_params[0:5] == (
        expected["id"],
        expected["aggregate_type"],
        expected["aggregate_id"],
        expected["org_id"],
        expected["event_type"],
    )
    assert outbox_params[5].obj == expected["payload_json"]


async def _exercise_existing_queue(ports) -> None:
    command = make_create_command()
    body = b"revision one"
    await ports.artifact_blob_store.put_stream(
        expected_digest=command.record.current_revision.blob_key,
        chunks=_chunks(body),
        byte_limit=len(body),
    )
    await ports.artifact_metadata_store.create_artifact(command)
    claim = await ports.queue.claim_next(
        worker_id="artifact-worker",
        lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert claim is not None
    assert claim.command_id == command.ledger_events[0].event_id
    assert claim.command_type == ARTIFACT_EVENT_COMMAND_TYPE
    command_payload = {
        key: value
        for key, value in claim.payload.items()
        if key != "command_type" and not (key == "approval_id" and value is None)
    }
    assert (
        RuntimeArtifactEventCommand.model_validate(command_payload).event_id
        == command.ledger_events[0].event_id
    )
    await ports.queue.mark_complete(
        result=RuntimeWorkerResult(command_id=claim.command_id, succeeded=True)
    )
    assert ports.artifact_metadata_store.pending_outbox_rows == ()


async def test_in_memory_artifact_intent_uses_existing_claim_queue() -> None:
    ports = RuntimeAdapterFactory.from_store(
        InMemoryRuntimeApiStore(),
        artifact_effects_v2=True,
    )

    await _exercise_existing_queue(ports)
    assert (
        await ports.queue.claim_next(
            worker_id="artifact-worker",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        is None
    )


async def test_file_queue_completion_survives_repository_restart(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "single_user_desktop")
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_STORE_BACKEND": "file",
            "RUNTIME_FILE_STORE_ROOT": str(tmp_path / "runtime"),
            "ARTIFACT_EFFECTS_V2": "true",
        }
    )
    first = RuntimeAdapterFactory.from_settings(settings)
    await _exercise_existing_queue(first)

    reopened = RuntimeAdapterFactory.from_settings(settings)
    assert reopened.artifact_metadata_store.pending_outbox_rows == ()
    assert (
        await reopened.queue.claim_next(
            worker_id="artifact-worker",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        is None
    )
