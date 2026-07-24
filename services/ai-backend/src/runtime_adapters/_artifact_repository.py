"""Shared adapter-only helpers for the canonical artifact repository."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from agent_runtime.artifacts.contracts import (
    ArtifactGcCandidate,
    ArtifactLedgerEvent,
    ArtifactStoredRecord,
)
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import OutboxStatus
from runtime_api.schemas.commands import RuntimeArtifactEventCommand

ARTIFACT_EVENT_COMMAND_TYPE = (
    PersistenceValues.EventType.ARTIFACT_EVENT_PUBLISH_REQUESTED
)
ARTIFACT_AGGREGATE_TYPE = PersistenceValues.AggregateType.ARTIFACT


def artifact_event_outbox_row(
    event: ArtifactLedgerEvent,
    *,
    artifact_id: str,
) -> dict[str, Any]:
    """Build the exact existing-runtime-outbox row required by A2."""

    scope = event.scope
    payload = RuntimeArtifactEventCommand(
        command_id=event.event_id,
        event_id=event.event_id,
        org_id=scope.org_id,
        user_id=scope.user_id,
        run_id=scope.run_id,
        conversation_id=scope.conversation_id,
        trace_id=scope.trace_id,
        event_type=event.event_type,
        payload=event.payload,
        created_at=event.created_at,
        trace_propagation={},
    ).model_dump(mode="json")
    timestamp = payload["created_at"]
    return {
        "id": event.event_id,
        "aggregate_type": ARTIFACT_AGGREGATE_TYPE,
        "aggregate_id": artifact_id,
        "org_id": scope.org_id,
        "event_type": ARTIFACT_EVENT_COMMAND_TYPE,
        "payload_json": payload,
        "status": "pending",
        "attempts": 0,
        "available_at": timestamp,
        "locked_by": None,
        "lock_expires_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


@dataclass(frozen=True, slots=True)
class ArtifactRetentionScope:
    """Explicit adapter-local scope for destructive retention work."""

    org_id: str
    user_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRetentionPurgeResult:
    """Metadata removed by retention and newly durable GC eligibility."""

    purged_artifact_ids: tuple[str, ...] = ()
    eligible_candidates: tuple[ArtifactGcCandidate, ...] = ()


class ArtifactRetentionPurger(Protocol):
    async def purge_tombstones(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_before: datetime,
        limit: int,
    ) -> ArtifactRetentionPurgeResult: ...


@runtime_checkable
class ArtifactCanonicalOutboxPort(Protocol):
    """Canonical artifact intent ledger committed with repository metadata."""

    async def pending_artifact_events(
        self,
    ) -> tuple[RuntimeArtifactEventCommand, ...]: ...

    async def acknowledge_artifact_event(
        self,
        *,
        event_id: str,
        status: OutboxStatus,
    ) -> None: ...


@runtime_checkable
class ArtifactQueueMirrorPort(Protocol):
    """Public idempotent queue capability used by the artifact bridge."""

    async def enqueue_artifact_event(
        self,
        command: RuntimeArtifactEventCommand,
    ) -> None: ...

    async def artifact_event_status(
        self,
        *,
        event_id: str,
    ) -> OutboxStatus | None: ...


@dataclass(frozen=True, slots=True)
class ArtifactQuarantineReapResult:
    """Digest-only reaper outcome; tenant metadata is intentionally absent."""

    reaped_blob_keys: tuple[str, ...] = ()
    restored_blob_keys: tuple[str, ...] = ()


class ArtifactQuarantineReaper(Protocol):
    async def reap_quarantine(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> ArtifactQuarantineReapResult: ...


@dataclass(frozen=True, slots=True)
class ArtifactRepositoryBundle:
    """One cohesive enabled-backend repository composition."""

    coordinator: object
    metadata_store: object
    blob_store: object
    reference_provider: object
    garbage_collector: object
    retention_purger: ArtifactRetentionPurger
    quarantine_reaper: ArtifactQuarantineReaper
    canonical_outbox: ArtifactCanonicalOutboxPort
    lifecycle_jobs: object


def record_sort_key(record: ArtifactStoredRecord) -> tuple[float, str]:
    """Order records by updated_at descending and artifact_id ascending."""

    updated = parse_datetime(record.artifact.updated_at)
    return (-updated.timestamp(), record.artifact.artifact_id)


def encode_cursor(record: ArtifactStoredRecord) -> str:
    raw = json.dumps(
        {
            "updated_at": record.artifact.updated_at,
            "artifact_id": record.artifact.artifact_id,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(
            base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        )
        updated_at = parse_datetime(value["updated_at"])
        artifact_id = str(value["artifact_id"])
        if not isinstance(value["artifact_id"], str) or not artifact_id:
            raise ValueError
    except (KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        # The canonical class is supplied by the API-hardening main lane.
        # Keep this import local so this bounded storage commit remains
        # cherry-pickable onto that lane without adding a competing domain
        # compatibility class to this older base.
        from agent_runtime.artifacts import ArtifactInvalidCursorError

        raise ArtifactInvalidCursorError() from exc
    return updated_at, artifact_id


def is_after_cursor(
    record: ArtifactStoredRecord,
    cursor: tuple[datetime, str],
) -> bool:
    updated_at = parse_datetime(record.artifact.updated_at)
    cursor_updated_at, cursor_artifact_id = cursor
    return updated_at < cursor_updated_at or (
        updated_at == cursor_updated_at
        and record.artifact.artifact_id > cursor_artifact_id
    )


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = (
    "ARTIFACT_AGGREGATE_TYPE",
    "ARTIFACT_EVENT_COMMAND_TYPE",
    "artifact_event_outbox_row",
    "ArtifactCanonicalOutboxPort",
    "ArtifactQuarantineReaper",
    "ArtifactQuarantineReapResult",
    "ArtifactQueueMirrorPort",
    "ArtifactRepositoryBundle",
    "ArtifactRetentionPurger",
    "ArtifactRetentionPurgeResult",
    "ArtifactRetentionScope",
    "decode_cursor",
    "encode_cursor",
    "is_after_cursor",
    "parse_datetime",
    "record_sort_key",
)
