"""Conversation-scoped canvas subjects (PRD-02, GS-ARCH-05).

The canvas answers "what can I open?" for a *conversation*, while
``projectCanvasLifecycle`` answers "what is this run doing?" for a *run*.
Conflating them is why a chat-only follow-up wiped an open surface.

Two properties matter most here and are easy to get wrong:

* **membership** — an artifact is canvas material only because a run emitted
  ``artifact.presentation_decided {decision: canvas}``. Re-deriving that rule
  here would create a second definition that can drift from the client fold.
* **scope** — this is the first read that crosses runs, so it is a new place
  tenant isolation can be lost. A conversation outside the caller's scope must
  404 rather than 403: a 403 confirms the conversation exists.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_runtime.api.conversation_canvas_service import ConversationCanvasService
from agent_runtime.artifacts.contracts import ArtifactListQuery
from agent_runtime.surfaces_v2.ledger_models import SurfaceAccent
from runtime_api.http.errors import RuntimeApiError


class _Artifact:
    def __init__(
        self,
        *,
        artifact_id: str,
        run_id: str,
        conversation_id: str = "conv-1",
        kind: str = "dataset",
        title: str = "forecast",
        accent: SurfaceAccent | None = None,
    ) -> None:
        self.artifact_id = artifact_id
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.user_id = "user-1"
        self.title = title
        self.current_revision = 1
        self.accent = accent
        self.created_at = datetime(2026, 7, 28, tzinfo=UTC)
        self.kind = type("K", (), {"value": kind})()


class _Record:
    def __init__(self, artifact: _Artifact) -> None:
        self.artifact = artifact


class _Page:
    def __init__(self, artifacts: tuple[_Record, ...]) -> None:
        self.artifacts = artifacts
        self.next_cursor = None


class _Envelope:
    def __init__(self, event_type: str, payload: dict) -> None:
        self.event_type = type("T", (), {"value": event_type})()
        self.payload = payload


class CanvasServiceMixin:
    """Build the service over recorded stores, with a controllable scope gate."""

    DECIDED = "art_decided"
    UNDECIDED = "art_undecided"

    @staticmethod
    def _decision(artifact_id: str, decision: str) -> _Envelope:
        return _Envelope(
            "artifact.presentation_decided",
            {"v": 1, "artifact_id": artifact_id, "decision": decision},
        )

    @classmethod
    def _service(
        cls,
        *,
        records: tuple[_Record, ...],
        events_by_run: dict[str, list[_Envelope]],
        scope_error: Exception | None = None,
    ) -> tuple[ConversationCanvasService, list[ArtifactListQuery]]:
        queries: list[ArtifactListQuery] = []

        class _Artifacts:
            async def list_artifacts(self, query: ArtifactListQuery):
                queries.append(query)
                return _Page(records)

        class _Events:
            async def list_events_after(self, *, org_id, run_id, after_sequence):
                del org_id, after_sequence
                return events_by_run.get(run_id, [])

        async def _scope(*, org_id, user_id, conversation_id):
            del org_id, user_id, conversation_id
            if scope_error is not None:
                raise scope_error
            return object()

        return (
            ConversationCanvasService(
                artifacts=_Artifacts(),
                events=_Events(),
                conversation_scope=_scope,
            ),
            queries,
        )


class TestCanvasMembership(CanvasServiceMixin):
    async def test_only_artifacts_decided_onto_the_canvas_are_returned(self) -> None:
        service, _ = self._service(
            records=(
                _Record(_Artifact(artifact_id=self.DECIDED, run_id="run-1")),
                _Record(_Artifact(artifact_id=self.UNDECIDED, run_id="run-1")),
            ),
            events_by_run={
                "run-1": [
                    self._decision(self.DECIDED, "canvas"),
                    # publish_artifact can also produce a chat-card artifact; it
                    # is real and durable but is not canvas material.
                    self._decision(self.UNDECIDED, "chat_card"),
                ]
            },
        )

        result = await service.list_subjects(
            org_id="org-1", user_id="user-1", conversation_id="conv-1"
        )

        assert [s.subject_id for s in result.subjects] == [self.DECIDED]

    async def test_subject_carries_its_producing_run_as_provenance(self) -> None:
        """``run_id`` identifies where it came from — never what it may do."""

        service, _ = self._service(
            records=(_Record(_Artifact(artifact_id=self.DECIDED, run_id="run-7")),),
            events_by_run={"run-7": [self._decision(self.DECIDED, "canvas")]},
        )

        subject = (
            await service.list_subjects(
                org_id="org-1", user_id="user-1", conversation_id="conv-1"
            )
        ).subjects[0]

        assert subject.run_id == "run-7"
        # The key must match the client fold's key byte-for-byte, or live and
        # archived subjects cannot merge without a reconciliation table.
        assert subject.subject_key == f"artifact:{self.DECIDED}"
        assert subject.renderer_hint == "artifact-dataset"

    async def test_an_artifact_from_an_earlier_run_survives_a_later_turn(self) -> None:
        """The defect this PRD exists for, at the service boundary."""

        service, _ = self._service(
            records=(_Record(_Artifact(artifact_id=self.DECIDED, run_id="run-1")),),
            events_by_run={
                "run-1": [self._decision(self.DECIDED, "canvas")],
                # run-2 is the chat-only follow-up: no artifact, no decision.
                "run-2": [],
            },
        )

        result = await service.list_subjects(
            org_id="org-1", user_id="user-1", conversation_id="conv-1"
        )

        assert len(result.subjects) == 1


class TestCanvasScope(CanvasServiceMixin):
    async def test_the_query_is_conversation_scoped_not_run_scoped(self) -> None:
        service, queries = self._service(records=(), events_by_run={})

        await service.list_subjects(
            org_id="org-1", user_id="user-1", conversation_id="conv-1"
        )

        assert len(queries) == 1
        assert queries[0].conversation_id == "conv-1"
        assert queries[0].run_id is None
        assert queries[0].org_id == "org-1"
        assert queries[0].user_id == "user-1"
        # Deleted artifacts must not reappear as openable tabs.
        assert queries[0].include_deleted is False

    async def test_a_foreign_conversation_is_refused_before_any_read(self) -> None:
        """404, and the artifact store is never consulted."""

        refusal = RuntimeApiError(
            code="capability_not_found",
            safe_message="Conversation was not found for this scope.",
            http_status=404,
        )
        service, queries = self._service(
            records=(_Record(_Artifact(artifact_id=self.DECIDED, run_id="run-1")),),
            events_by_run={},
            scope_error=refusal,
        )

        with pytest.raises(RuntimeApiError) as caught:
            await service.list_subjects(
                org_id="org-1", user_id="attacker", conversation_id="conv-1"
            )

        assert caught.value.http_status == 404
        # The safe public message says "not found", never "not permitted" — a
        # 403-shaped answer would confirm the conversation exists.
        assert "not found" in caught.value.envelope.safe_message.lower()
        assert queries == []


class TestSubjectAccent:
    """Display identity travels with the artifact, not with a run's events."""

    DECIDED = "art_11111111111111111111111111111111"

    async def test_a_chosen_accent_reaches_the_subject(self) -> None:
        service, _ = TestCanvasMembership._service(
            records=(
                _Record(
                    _Artifact(
                        artifact_id=self.DECIDED,
                        run_id="run-1",
                        accent=SurfaceAccent.EMBER,
                    )
                ),
            ),
            events_by_run={
                "run-1": [TestCanvasMembership._decision(self.DECIDED, "canvas")]
            },
        )

        response = await service.list_subjects(
            org_id="org-1", user_id="user-1", conversation_id="conv-1"
        )

        assert response.subjects[0].accent is SurfaceAccent.EMBER

    async def test_no_choice_stays_none_so_the_client_can_derive(self) -> None:
        """Unset must not become a colour here.

        The client turns absence into a hue derived from the artifact's kind. If
        this projected a default instead, every artifact would look chosen and
        the derivation rule could never change without rewriting stored rows.
        """

        service, _ = TestCanvasMembership._service(
            records=(_Record(_Artifact(artifact_id=self.DECIDED, run_id="run-1")),),
            events_by_run={
                "run-1": [TestCanvasMembership._decision(self.DECIDED, "canvas")]
            },
        )

        response = await service.list_subjects(
            org_id="org-1", user_id="user-1", conversation_id="conv-1"
        )

        assert response.subjects[0].accent is None
