"""Pure artifact compatibility projection over Work Ledger events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactCreatedPayload,
    ArtifactKind,
    ArtifactPromotedPayload,
    ArtifactRevisedPayload,
    LedgerEventType,
)


class ProjectedArtifactRevision(RuntimeContract):
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    content_ref: str
    content_digest: str
    author: ArtifactAuthor


class ProjectedArtifact(RuntimeContract):
    artifact_id: str
    kind: ArtifactKind
    revisions: tuple[ProjectedArtifactRevision, ...]
    source_ref: str | None = None

    @property
    def current_revision(self) -> int:
        return self.revisions[-1].revision


class ArtifactProjectionState(RuntimeContract):
    artifacts: tuple[ProjectedArtifact, ...] = ()
    ignored_malformed_events: int = 0


class ArtifactProjection:
    """Rebuild a metadata-light reference view without becoming repository truth."""

    @classmethod
    def fold(
        cls, events: Sequence[Mapping[str, object] | object]
    ) -> ArtifactProjectionState:
        artifacts: dict[str, ProjectedArtifact] = {}
        ignored = 0
        for event in events:
            event_type, payload = cls._event_fields(event)
            if event_type is None or payload is None:
                continue
            try:
                if event_type == LedgerEventType.ARTIFACT_CREATED.value:
                    created = ArtifactCreatedPayload.model_validate(payload)
                    artifacts.setdefault(
                        created.artifact_id,
                        ProjectedArtifact(
                            artifact_id=created.artifact_id,
                            kind=created.kind,
                            revisions=(
                                ProjectedArtifactRevision(
                                    revision=created.revision,
                                    parent_revision=None,
                                    content_ref=created.content_ref,
                                    content_digest=created.content_digest,
                                    author=created.author,
                                ),
                            ),
                        ),
                    )
                elif event_type == LedgerEventType.ARTIFACT_REVISED.value:
                    revised = ArtifactRevisedPayload.model_validate(payload)
                    current = artifacts.get(revised.artifact_id)
                    if current is None:
                        ignored += 1
                        continue
                    if any(
                        item.revision == revised.revision for item in current.revisions
                    ):
                        continue
                    if current.current_revision != revised.parent_revision:
                        ignored += 1
                        continue
                    artifacts[revised.artifact_id] = current.model_copy(
                        update={
                            "revisions": (
                                *current.revisions,
                                ProjectedArtifactRevision(
                                    revision=revised.revision,
                                    parent_revision=revised.parent_revision,
                                    content_ref=revised.content_ref,
                                    content_digest=revised.content_digest,
                                    author=revised.author,
                                ),
                            )
                        }
                    )
                elif event_type == LedgerEventType.ARTIFACT_PROMOTED.value:
                    promoted = ArtifactPromotedPayload.model_validate(payload)
                    current = artifacts.get(promoted.artifact_id)
                    if current is None or current.kind is not promoted.kind:
                        ignored += 1
                        continue
                    artifacts[promoted.artifact_id] = current.model_copy(
                        update={"source_ref": promoted.source_ref}
                    )
            except (TypeError, ValueError):
                ignored += 1
        return ArtifactProjectionState(
            artifacts=tuple(
                sorted(artifacts.values(), key=lambda item: item.artifact_id)
            ),
            ignored_malformed_events=ignored,
        )

    @staticmethod
    def _event_fields(
        event: Mapping[str, object] | object,
    ) -> tuple[str | None, Mapping[str, object] | None]:
        if isinstance(event, Mapping):
            raw_type = event.get("event_type")
            raw_payload = event.get("payload")
        else:
            raw_type = getattr(event, "event_type", None)
            raw_payload = getattr(event, "payload", None)
        event_type = (
            raw_type.value
            if isinstance(raw_type, LedgerEventType)
            else raw_type
            if isinstance(raw_type, str)
            else None
        )
        payload = raw_payload if isinstance(raw_payload, Mapping) else None
        return event_type, payload


__all__ = (
    "ArtifactProjection",
    "ArtifactProjectionState",
    "ProjectedArtifact",
    "ProjectedArtifactRevision",
)
