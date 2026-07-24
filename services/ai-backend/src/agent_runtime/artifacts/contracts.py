"""Storage-facing contracts for the canonical artifact repository.

The public artifact vocabulary remains the A1 ``Artifact`` /
``ArtifactRevision`` contract.  This module adds only repository concerns:
scope, blob indirection, idempotency, pagination, outbox, and retention.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, ClassVar

from pydantic import Field, PositiveInt, field_validator, model_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    ArtifactIdCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactKind,
    LedgerEventType,
    WorkLedgerVocabulary,
)

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafeTitle = Annotated[str, Field(min_length=1, max_length=240)]
SafeMediaType = Annotated[str, Field(min_length=1, max_length=255)]
SafeFilename = Annotated[str, Field(min_length=1, max_length=255)]
SafeCursor = Annotated[str, Field(min_length=1, max_length=2048)]


class ArtifactKindLimit(RuntimeContract):
    maximum_bytes: PositiveInt
    inline_preview_bytes: PositiveInt

    @model_validator(mode="after")
    def _preview_fits_artifact(self) -> ArtifactKindLimit:
        if self.inline_preview_bytes > self.maximum_bytes:
            raise ValueError("inline_preview_bytes must not exceed maximum_bytes")
        return self


class ArtifactLimits(RuntimeContract):
    """Centralized launch limits from PRD-A2 D3."""

    code: ArtifactKindLimit = ArtifactKindLimit(
        maximum_bytes=10 * 1024 * 1024,
        inline_preview_bytes=1 * 1024 * 1024,
    )
    document: ArtifactKindLimit = ArtifactKindLimit(
        maximum_bytes=20 * 1024 * 1024,
        inline_preview_bytes=2 * 1024 * 1024,
    )
    dataset: ArtifactKindLimit = ArtifactKindLimit(
        maximum_bytes=100 * 1024 * 1024,
        inline_preview_bytes=2 * 1024 * 1024,
    )
    file: ArtifactKindLimit = ArtifactKindLimit(
        maximum_bytes=250 * 1024 * 1024,
        inline_preview_bytes=512 * 1024,
    )
    maximum_title_bytes: PositiveInt = 16 * 1024
    maximum_title_characters: PositiveInt = 240
    maximum_list_page: PositiveInt = 100

    def for_kind(self, kind: ArtifactKind) -> ArtifactKindLimit:
        return getattr(self, kind.value)


class ArtifactScope(RuntimeContract):
    """Verified run ownership used by every mutation and dereference."""

    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    trace_id: str = Field(min_length=1, max_length=255)


class ArtifactCreateRequest(RuntimeContract):
    run_id: str = Field(min_length=1, max_length=255)
    kind: ArtifactKind
    title: SafeTitle
    media_type: SafeMediaType
    suggested_filename: SafeFilename | None = None
    author: ArtifactAuthor
    source_ref: str | None = Field(default=None, min_length=1, max_length=2048)
    expected_digest: Sha256Hex | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)


class ArtifactRevisionRequest(RuntimeContract):
    artifact_id: str
    parent_revision: PositiveInt
    author: ArtifactAuthor
    source_ref: str | None = Field(default=None, min_length=1, max_length=2048)
    expected_digest: Sha256Hex | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("artifact_id")
    @classmethod
    def _valid_artifact_id(cls, value: str) -> str:
        ArtifactIdCodec.parse(value)
        return value


class ArtifactPromotionRequest(RuntimeContract):
    run_id: str = Field(min_length=1, max_length=255)
    source_ref: str = Field(min_length=1, max_length=2048)
    kind: ArtifactKind
    title: SafeTitle | None = None
    media_type: SafeMediaType | None = None
    suggested_filename: SafeFilename | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)


class ArtifactBlobWriteResult(RuntimeContract):
    """Internal content-addressed result; ``blob_key`` never leaves the service."""

    blob_key: Sha256Hex
    content_digest: Sha256Hex
    byte_size: int = Field(ge=0)
    range_supported: bool
    created: bool

    @model_validator(mode="after")
    def _key_matches_digest(self) -> ArtifactBlobWriteResult:
        if self.blob_key != self.content_digest:
            raise ValueError("blob_key must equal content_digest")
        return self


class ArtifactBlobStat(RuntimeContract):
    blob_key: Sha256Hex
    byte_size: int = Field(ge=0)
    range_supported: bool
    created_at: datetime


class ByteRange(RuntimeContract):
    """Inclusive byte range after HTTP syntax has been parsed."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> ByteRange:
        if self.end < self.start:
            raise ValueError("range end must be greater than or equal to start")
        return self

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class ArtifactStoredRevision(RuntimeContract):
    revision: ArtifactRevision
    blob_key: Sha256Hex
    range_supported: bool

    @model_validator(mode="after")
    def _blob_matches_revision(self) -> ArtifactStoredRevision:
        if self.revision.content_digest != self.blob_key:
            raise ValueError("blob_key must match revision content_digest")
        return self


class ArtifactStoredRecord(RuntimeContract):
    artifact: Artifact
    current_revision: ArtifactStoredRevision
    suggested_filename: SafeFilename | None = None

    @model_validator(mode="after")
    def _current_revision_matches(self) -> ArtifactStoredRecord:
        if (
            self.current_revision.revision.artifact_id != self.artifact.artifact_id
            or self.current_revision.revision.revision != self.artifact.current_revision
        ):
            raise ValueError("current_revision must match artifact")
        return self


class ArtifactIdempotencyBinding(RuntimeContract):
    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    route: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=255)
    request_digest: Sha256Hex


class ArtifactLedgerEvent(RuntimeContract):
    """Event command persisted atomically beside artifact metadata."""

    event_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^artevt_[0-9a-f]{32,64}$",
    )
    scope: ArtifactScope
    event_type: LedgerEventType
    payload: JsonObject
    created_at: datetime

    @model_validator(mode="after")
    def _validate_artifact_payload(self) -> ArtifactLedgerEvent:
        if self.event_type not in {
            LedgerEventType.ARTIFACT_CREATED,
            LedgerEventType.ARTIFACT_REVISED,
            LedgerEventType.ARTIFACT_PROMOTED,
        }:
            raise ValueError("artifact repository outbox accepts artifact events only")
        WorkLedgerVocabulary.validate_payload(self.event_type, self.payload)
        return self


class ArtifactCreateCommand(RuntimeContract):
    record: ArtifactStoredRecord
    idempotency: ArtifactIdempotencyBinding
    ledger_events: tuple[ArtifactLedgerEvent, ...]

    @model_validator(mode="after")
    def _is_revision_one(self) -> ArtifactCreateCommand:
        artifact = self.record.artifact
        revision = self.record.current_revision.revision
        if artifact.current_revision != 1 or revision.revision != 1:
            raise ValueError("artifact creation must persist revision 1")
        if revision.parent_revision is not None:
            raise ValueError("artifact creation cannot have a parent revision")
        if not self.ledger_events:
            raise ValueError("artifact creation requires a ledger event")
        return self


class ArtifactAppendCommand(RuntimeContract):
    scope: ArtifactScope
    artifact_id: str
    expected_revision: PositiveInt
    revision: ArtifactStoredRevision
    idempotency: ArtifactIdempotencyBinding
    ledger_event: ArtifactLedgerEvent

    @field_validator("artifact_id")
    @classmethod
    def _valid_artifact_id(cls, value: str) -> str:
        ArtifactIdCodec.parse(value)
        return value

    @model_validator(mode="after")
    def _revision_chain_matches(self) -> ArtifactAppendCommand:
        revision = self.revision.revision
        if revision.artifact_id != self.artifact_id:
            raise ValueError("revision must reference artifact_id")
        if revision.parent_revision != self.expected_revision:
            raise ValueError("parent_revision must equal expected_revision")
        if revision.revision != self.expected_revision + 1:
            raise ValueError("revision must increment expected_revision by one")
        return self


class ArtifactSoftDeleteCommand(RuntimeContract):
    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    artifact_id: str
    deleted_at: datetime
    idempotency: ArtifactIdempotencyBinding

    @field_validator("artifact_id")
    @classmethod
    def _valid_artifact_id(cls, value: str) -> str:
        ArtifactIdCodec.parse(value)
        return value


class ArtifactMutationResult(RuntimeContract):
    record: ArtifactStoredRecord
    replayed: bool = False


class ArtifactListQuery(RuntimeContract):
    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    kind: ArtifactKind | None = None
    include_deleted: bool = False
    limit: PositiveInt = Field(default=50, le=100)
    cursor: SafeCursor | None = None


class ArtifactListPage(RuntimeContract):
    artifacts: tuple[ArtifactStoredRecord, ...]
    next_cursor: SafeCursor | None = None


class ArtifactSourceDescriptor(RuntimeContract):
    """Authorized server-side source metadata; never a local filesystem path."""

    source_ref: str = Field(min_length=1, max_length=2048)
    byte_size: int | None = Field(default=None, ge=0)
    content_digest: Sha256Hex | None = None
    media_type: SafeMediaType | None = None
    title: SafeTitle | None = None
    suggested_filename: SafeFilename | None = None

    _ALLOWED_SOURCE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:"
        r"message://[A-Za-z0-9._:-]+"
        r"|operation://op_[0-9a-f-]+/result"
        r"|payload://[A-Za-z0-9._:/-]+"
        r")$"
    )

    @field_validator("source_ref")
    @classmethod
    def _allowed_source_ref(cls, value: str) -> str:
        if cls._ALLOWED_SOURCE.fullmatch(value) is None:
            raise ValueError(
                "source_ref must be a message, operation result, or payload"
            )
        return value


class ArtifactGcCandidate(RuntimeContract):
    blob_key: Sha256Hex
    unreferenced_since: datetime


class ArtifactReferenceSnapshot(RuntimeContract):
    """All references that prevent physical blob collection."""

    artifact_blob_keys: frozenset[Sha256Hex] = Field(default_factory=frozenset)
    effect_blob_keys: frozenset[Sha256Hex] = Field(default_factory=frozenset)
    receipt_blob_keys: frozenset[Sha256Hex] = Field(default_factory=frozenset)
    audit_blob_keys: frozenset[Sha256Hex] = Field(default_factory=frozenset)
    legal_hold_blob_keys: frozenset[Sha256Hex] = Field(default_factory=frozenset)

    @property
    def live_blob_keys(self) -> frozenset[str]:
        return frozenset().union(
            self.artifact_blob_keys,
            self.effect_blob_keys,
            self.receipt_blob_keys,
            self.audit_blob_keys,
            self.legal_hold_blob_keys,
        )


class ArtifactContractValidator:
    """Cross-field checks reused by adapters and service tests."""

    @classmethod
    def validate_record(cls, record: ArtifactStoredRecord) -> ArtifactStoredRecord:
        artifact_id = record.artifact.artifact_id
        ArtifactIdCodec.parse(artifact_id)
        revision = record.current_revision.revision
        parsed = ArtifactContentRefCodec.parse(revision.content_ref)
        if parsed.artifact_id != artifact_id:
            raise ValueError("revision content_ref must reference artifact")
        return record


__all__ = (
    "ArtifactAppendCommand",
    "ArtifactBlobStat",
    "ArtifactBlobWriteResult",
    "ArtifactContractValidator",
    "ArtifactCreateCommand",
    "ArtifactCreateRequest",
    "ArtifactGcCandidate",
    "ArtifactIdempotencyBinding",
    "ArtifactKindLimit",
    "ArtifactLedgerEvent",
    "ArtifactLimits",
    "ArtifactListPage",
    "ArtifactListQuery",
    "ArtifactMutationResult",
    "ArtifactPromotionRequest",
    "ArtifactReferenceSnapshot",
    "ArtifactRevisionRequest",
    "ArtifactScope",
    "ArtifactSoftDeleteCommand",
    "ArtifactSourceDescriptor",
    "ArtifactStoredRecord",
    "ArtifactStoredRevision",
    "ByteRange",
    "SafeFilename",
    "SafeMediaType",
    "SafeTitle",
    "Sha256Hex",
)
