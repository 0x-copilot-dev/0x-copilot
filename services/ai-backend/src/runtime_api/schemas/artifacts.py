"""Public HTTP schemas for the canonical Artifact Repository.

The durable entity vocabulary is owned by A1.  These models are deliberately
thin envelopes around ``Artifact`` and ``ArtifactRevision``; internal blob
keys, storage paths, and content bytes never enter the public schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_runtime.artifacts import (
    ArtifactListPage,
    ArtifactMutationResult,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from agent_runtime.surfaces_v2.ledger_models import ArtifactKind


class ArtifactCreateMetadata(BaseModel):
    """Bounded metadata fields carried beside one multipart content part."""

    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    title: str = Field(min_length=1, max_length=240)
    media_type: str = Field(min_length=1, max_length=255)
    suggested_filename: str | None = Field(default=None, min_length=1, max_length=255)
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactRevisionMetadata(BaseModel):
    """Optimistic parent for a multipart revision."""

    model_config = ConfigDict(extra="forbid")

    parent_revision: PositiveInt
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactPromotionBody(BaseModel):
    """JSON body for server-side promotion of an authorized logical source."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=255)
    source_ref: str = Field(min_length=1, max_length=2048)
    kind: ArtifactKind
    title: str | None = Field(default=None, min_length=1, max_length=240)
    media_type: str | None = Field(default=None, min_length=1, max_length=255)
    suggested_filename: str | None = Field(default=None, min_length=1, max_length=255)


class ArtifactRevisionResponse(BaseModel):
    """One immutable revision without its internal blob locator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: ArtifactRevision
    range_supported: bool

    @classmethod
    def from_stored(cls, stored: ArtifactStoredRevision) -> ArtifactRevisionResponse:
        return cls(
            revision=stored.revision,
            range_supported=stored.range_supported,
        )


class ArtifactDetailResponse(BaseModel):
    """Authoritative artifact metadata and its current immutable revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: Artifact
    current_revision: ArtifactRevision
    suggested_filename: str | None = None
    range_supported: bool

    @classmethod
    def from_record(cls, record: ArtifactStoredRecord) -> ArtifactDetailResponse:
        return cls(
            artifact=record.artifact,
            current_revision=record.current_revision.revision,
            suggested_filename=record.suggested_filename,
            range_supported=record.current_revision.range_supported,
        )


class ArtifactMutationResponse(ArtifactDetailResponse):
    """Create, revise, and promote response with idempotency replay signal."""

    replayed: bool

    @classmethod
    def from_result(cls, result: ArtifactMutationResult) -> ArtifactMutationResponse:
        detail = ArtifactDetailResponse.from_record(result.record)
        return cls(
            **detail.model_dump(),
            replayed=result.replayed,
        )


class ArtifactListResponse(BaseModel):
    """Stable cursor page ordered by the metadata repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifacts: tuple[ArtifactDetailResponse, ...]
    next_cursor: str | None = None

    @classmethod
    def from_page(cls, page: ArtifactListPage) -> ArtifactListResponse:
        return cls(
            artifacts=tuple(
                ArtifactDetailResponse.from_record(record) for record in page.artifacts
            ),
            next_cursor=page.next_cursor,
        )


__all__ = (
    "ArtifactCreateMetadata",
    "ArtifactDetailResponse",
    "ArtifactListResponse",
    "ArtifactMutationResponse",
    "ArtifactPromotionBody",
    "ArtifactRevisionMetadata",
    "ArtifactRevisionResponse",
)
