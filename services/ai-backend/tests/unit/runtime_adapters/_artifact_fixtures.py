from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from agent_runtime.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactIdempotencyBinding,
    ArtifactLedgerEvent,
    ArtifactScope,
    ArtifactSoftDeleteCommand,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.artifacts.execution_mode import ArtifactExecutionMode
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactCreatedPayload,
    ArtifactKind,
    ArtifactRevisedPayload,
    LedgerEventType,
)

NOW = datetime(2026, 7, 24, 8, 30, tzinfo=timezone.utc)
# Every artifact command states the mode it ran under. The service derives it;
# these store-level fixtures state it literally, and `staged` is the only value
# anything produces today.
EXECUTION_MODE = ArtifactExecutionMode.STAGED
SCOPE = ArtifactScope(
    org_id="org_artifacts",
    user_id="user_artifacts",
    conversation_id="conv_artifacts",
    run_id="run_artifacts",
    trace_id="trace_artifacts",
)


def artifact_id(ordinal: int) -> str:
    return f"art_00000000-0000-4000-8000-{ordinal:012d}"


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _event_id(artifact_ordinal: int, revision: int) -> str:
    value = hashlib.sha256(
        f"artifact:{artifact_ordinal}:revision:{revision}".encode()
    ).hexdigest()
    return f"artevt_{value}"


def _binding(
    *,
    route: str,
    key: str,
    request_digest: str | None,
    scope: ArtifactScope = SCOPE,
) -> ArtifactIdempotencyBinding:
    return ArtifactIdempotencyBinding(
        org_id=scope.org_id,
        user_id=scope.user_id,
        route=route,
        key=key,
        request_digest=request_digest or digest(f"{route}:{key}".encode()),
    )


def make_create_command(
    ordinal: int = 1,
    *,
    body: bytes = b"revision one",
    key: str | None = None,
    request_digest: str | None = None,
    created_at: datetime = NOW,
    scope: ArtifactScope = SCOPE,
) -> ArtifactCreateCommand:
    artifact = artifact_id(ordinal)
    content_digest = digest(body)
    revision = ArtifactRevision(
        artifact_id=artifact,
        revision=1,
        parent_revision=None,
        content_ref=ArtifactContentRefCodec.format(artifact, 1),
        content_digest=content_digest,
        byte_size=len(body),
        author=ArtifactAuthor.MODEL,
        source_ref=None,
        created_at=created_at.isoformat(),
    )
    stored_revision = ArtifactStoredRevision(
        revision=revision,
        blob_key=content_digest,
        range_supported=True,
    )
    record = ArtifactStoredRecord(
        artifact=Artifact(
            artifact_id=artifact,
            org_id=scope.org_id,
            user_id=scope.user_id,
            conversation_id=scope.conversation_id,
            run_id=scope.run_id,
            kind=ArtifactKind.DOCUMENT,
            title=f"Artifact {ordinal}",
            media_type="text/plain",
            current_revision=1,
            created_by=ArtifactAuthor.MODEL,
            created_at=created_at.isoformat(),
            updated_at=created_at.isoformat(),
            deleted_at=None,
        ),
        current_revision=stored_revision,
        suggested_filename=f"artifact-{ordinal}.txt",
    )
    payload = ArtifactCreatedPayload(
        v=1,
        artifact_id=artifact,
        kind=ArtifactKind.DOCUMENT,
        revision=1,
        content_ref=revision.content_ref,
        content_digest=content_digest,
        author=ArtifactAuthor.MODEL,
    )
    event = ArtifactLedgerEvent(
        event_id=_event_id(ordinal, 1),
        scope=scope,
        event_type=LedgerEventType.ARTIFACT_CREATED,
        payload=payload.model_dump(mode="json", by_alias=True),
        created_at=created_at,
    )
    return ArtifactCreateCommand(
        record=record,
        idempotency=_binding(
            route="POST:/artifacts",
            key=key or f"create-{ordinal}",
            request_digest=request_digest,
            scope=scope,
        ),
        ledger_events=(event,),
        execution_mode=EXECUTION_MODE,
    )


def make_append_command(
    ordinal: int = 1,
    *,
    body: bytes = b"revision two",
    key: str | None = None,
    created_at: datetime = NOW + timedelta(seconds=1),
    scope: ArtifactScope = SCOPE,
) -> ArtifactAppendCommand:
    artifact = artifact_id(ordinal)
    content_digest = digest(body)
    revision = ArtifactRevision(
        artifact_id=artifact,
        revision=2,
        parent_revision=1,
        content_ref=ArtifactContentRefCodec.format(artifact, 2),
        content_digest=content_digest,
        byte_size=len(body),
        author=ArtifactAuthor.USER,
        source_ref=None,
        created_at=created_at.isoformat(),
    )
    stored_revision = ArtifactStoredRevision(
        revision=revision,
        blob_key=content_digest,
        range_supported=True,
    )
    payload = ArtifactRevisedPayload(
        v=1,
        artifact_id=artifact,
        revision=2,
        parent_revision=1,
        content_ref=revision.content_ref,
        content_digest=content_digest,
        author=ArtifactAuthor.USER,
    )
    return ArtifactAppendCommand(
        scope=scope,
        artifact_id=artifact,
        expected_revision=1,
        revision=stored_revision,
        idempotency=_binding(
            route="POST:/artifact/revisions",
            key=key or f"append-{ordinal}",
            request_digest=None,
            scope=scope,
        ),
        ledger_event=ArtifactLedgerEvent(
            event_id=_event_id(ordinal, 2),
            scope=SCOPE,
            event_type=LedgerEventType.ARTIFACT_REVISED,
            payload=payload.model_dump(mode="json", by_alias=True),
            created_at=created_at,
        ),
        execution_mode=EXECUTION_MODE,
    )


def make_delete_command(
    ordinal: int = 1,
    *,
    key: str | None = None,
    deleted_at: datetime = NOW + timedelta(seconds=2),
    scope: ArtifactScope = SCOPE,
) -> ArtifactSoftDeleteCommand:
    return ArtifactSoftDeleteCommand(
        org_id=scope.org_id,
        user_id=scope.user_id,
        artifact_id=artifact_id(ordinal),
        deleted_at=deleted_at,
        idempotency=_binding(
            route="DELETE:/artifact",
            key=key or f"delete-{ordinal}",
            request_digest=None,
            scope=scope,
        ),
        execution_mode=EXECUTION_MODE,
    )
