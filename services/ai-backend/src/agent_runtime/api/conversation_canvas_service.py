"""Canvas subjects for a whole conversation, not one run (PRD-02, GS-ARCH-05).

The Studio canvas answers two different questions that were being served by one
run-scoped projection:

===========================  ============  ==============================
Question                     Scope         Source
===========================  ============  ==============================
What can I open?             conversation  this service
What is this run doing?      run           ``projectCanvasLifecycle``
===========================  ============  ==============================

Conflating them is why a chat-only follow-up wiped an open surface: the client
folds ``session.events``, and binding a new run replaces that stream, so an
artifact published two turns ago simply ceased to exist as far as the canvas was
concerned.

Membership authority is unchanged. An artifact reaches the canvas because a run
emitted ``artifact.presentation_decided {decision: canvas}``; this service reads
that decision from the ledger rather than re-deriving it, so "what belongs on the
canvas" has exactly one definition.

Scope is the conversation, always proved before any read (see
``_conversation_for_scope``). ``run_id`` on a returned subject is *provenance* —
which run produced it — and must never be accepted back from a client as a scope
widener.
"""

from __future__ import annotations

from agent_runtime.artifacts.contracts import ArtifactListQuery
from agent_runtime.artifacts.ports import ArtifactMetadataStorePort
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from runtime_api.schemas.conversation_canvas import (
    ConversationCanvasResponse,
    ConversationCanvasSubject,
)


class ConversationCanvasService:
    """Fold a conversation's runs into the set of openable canvas subjects."""

    #: Cap on runs inspected for canvas decisions per request. A conversation can
    #: hold hundreds of runs; the canvas only ever shows a strip. Truncation is
    #: reported through ``next_cursor`` rather than silently dropping the tail.
    RUN_SCAN_LIMIT = 50

    def __init__(
        self,
        *,
        artifacts: ArtifactMetadataStorePort,
        events,
        conversation_scope,
    ) -> None:
        self._artifacts = artifacts
        self._events = events
        self._conversation_scope = conversation_scope

    async def list_subjects(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        limit: int = 50,
    ) -> ConversationCanvasResponse:
        """Return canvas subjects for the conversation, newest first.

        Ownership is proved first: a conversation outside the caller's scope
        raises the same not-found the rest of the conversation surface uses, so
        this endpoint cannot confirm the existence of another tenant's data.
        """

        await self._conversation_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        page = await self._artifacts.list_artifacts(
            ArtifactListQuery(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                include_deleted=False,
                limit=limit,
            )
        )
        canvas_artifact_ids = await self._canvas_decisions(
            org_id=org_id,
            records=page.artifacts,
        )
        subjects = tuple(
            ConversationCanvasSubject(
                subject_key=f"artifact:{record.artifact.artifact_id}",
                kind="artifact",
                subject_id=record.artifact.artifact_id,
                run_id=record.artifact.run_id,
                title=record.artifact.title,
                revision=record.artifact.current_revision,
                renderer_hint=f"artifact-{record.artifact.kind.value}",
                created_at=record.artifact.created_at,
            )
            for record in page.artifacts
            if record.artifact.artifact_id in canvas_artifact_ids
        )
        return ConversationCanvasResponse(
            conversation_id=conversation_id,
            subjects=subjects,
            next_cursor=page.next_cursor,
        )

    async def _canvas_decisions(self, *, org_id: str, records) -> frozenset[str]:
        """Return the artifact ids a run explicitly decided onto the canvas.

        Reads the ledger rather than assuming every artifact is canvas material:
        ``publish_artifact`` can produce a chat-card or hidden artifact, and the
        client fold applies exactly this rule. Duplicating the decision here would
        create a second definition of canvas membership that could drift.
        """

        decided: set[str] = set()
        for run_id in self._recent_run_ids(records):
            for envelope in await self._events.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=0,
            ):
                if (
                    envelope.event_type.value
                    != LedgerEventType.ARTIFACT_PRESENTATION_DECIDED.value
                ):
                    continue
                payload = envelope.payload
                if payload.get("decision") == "canvas":
                    artifact_id = payload.get("artifact_id")
                    if isinstance(artifact_id, str):
                        decided.add(artifact_id)
        return frozenset(decided)

    def _recent_run_ids(self, records) -> tuple[str, ...]:
        """Distinct producing runs, newest first, bounded by ``RUN_SCAN_LIMIT``."""

        seen: list[str] = []
        for record in records:
            run_id = record.artifact.run_id
            if run_id not in seen:
                seen.append(run_id)
            if len(seen) >= self.RUN_SCAN_LIMIT:
                break
        return tuple(seen)


__all__ = ("ConversationCanvasService",)
