"""PRD-09b — the worker draft-send branch applies reviewer edits into the committed draft.

``approve_with_edits`` on a draft-send approval merges the reviewer's edit deltas
(body / fields) server-side INTO the version that is marked ``sent`` — the client
never sends a merged artifact, and the base is always the server-held pending
draft. Replaying the resolution after the send is an idempotent no-op.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.surfaces.commit import SurfaceEdits
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.records import DraftStatus
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    RuntimeApiEventType,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler

from tests.unit.agent_runtime.persistence.test_drafts import _draft_id, _record

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_RUN_RECORD = SimpleNamespace(
    run_id="run_1",
    conversation_id="conv_1",
    org_id="org_acme",
    user_id="user_sarah",
    status=AgentRunStatus.WAITING_FOR_APPROVAL,
)


class _StubPersistence:
    def __init__(self) -> None:
        self.audit_calls: list[tuple[str, dict]] = []
        self.run_status_updates: list[AgentRunStatus] = []

    async def write_audit_log(self, *, event_type: str, record: dict) -> None:
        self.audit_calls.append((event_type, record))

    async def update_run_status(self, *, run_id: str, status: AgentRunStatus) -> object:
        self.run_status_updates.append(status)
        return _RUN_RECORD


class _StubEventStore:
    def __init__(self) -> None:
        self.events: list[dict] = []


def _approval(*, draft_id: str, draft_version: int) -> ApprovalRequestRecord:
    return ApprovalRequestRecord(
        run_id="run_1",
        conversation_id="conv_1",
        org_id="org_acme",
        user_id="user_sarah",
        metadata={
            "kind": "draft_send",
            "approval_kind": "action",
            "draft_id": draft_id,
            "draft_version": draft_version,
            "target_connector": "gmail",
            "target_metadata": {"to": "vip@acme.test"},
            "summary": "Send Aurora to gmail",
            "body_preview": "Aurora 4.0 launch",
        },
    )


def _handler(
    store: InMemoryDraftStore,
) -> tuple[RuntimeApprovalHandler, _StubPersistence]:
    persistence = _StubPersistence()
    handler = RuntimeApprovalHandler(
        persistence=persistence,
        event_store=_StubEventStore(),
        draft_store=store,
    )
    return handler, persistence


async def _seed_pending(store: InMemoryDraftStore) -> None:
    await store.insert_version(_record(version=1, run_id="run_existing"))
    await store.insert_version(
        _record(
            version=2,
            run_id="run_1",
            status=DraftStatus.SEND_PENDING_APPROVAL,
            content_text="Original body.",
            target_connector="gmail",
            # ``subject`` is part of the server-held proposal, so editing it is
            # within the worker-side field allowlist (defense-in-depth guard);
            # a field key absent here can never be introduced by a reviewer.
            target_metadata={"to": "vip@acme.test", "subject": "Original subject"},
        )
    )


class TestDraftSendApproveWithEdits:
    async def test_body_edit_flows_into_committed_draft(self) -> None:
        store = InMemoryDraftStore()
        await _seed_pending(store)
        handler, persistence = _handler(store)
        captured: list[dict] = []

        async def _capture_event(**kwargs: object) -> None:
            captured.append(dict(kwargs))

        handler.event_producer.append_api_event = _capture_event  # type: ignore[assignment]

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=2),
            decision=ApprovalDecision.APPROVE_WITH_EDITS,
            decided_by_user_id="user_sarah",
            edits=SurfaceEdits(body="Reviewer-edited body."),
        )

        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.version == 3
        assert latest.status is DraftStatus.SENT
        # The EDITED body is what got committed (sent), not the base.
        assert latest.content_text == "Reviewer-edited body."

        # Audit records the send-complete with the edit provenance.
        completed = [
            record
            for event_type, record in persistence.audit_calls
            if event_type == "draft.send.completed"
        ]
        assert completed
        assert completed[-1]["edited"] is True
        assert completed[-1]["edited_keys"] == ["body"]

        emitted_types = [call["event_type"] for call in captured]
        assert RuntimeApiEventType.DRAFT_UPDATED in emitted_types
        assert RuntimeApiEventType.RUN_COMPLETED in emitted_types

    async def test_field_edit_overlays_target_metadata(self) -> None:
        store = InMemoryDraftStore()
        await _seed_pending(store)
        handler, _persistence = _handler(store)
        handler.event_producer.append_api_event = _noop_emitter  # type: ignore[assignment]

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=2),
            decision=ApprovalDecision.APPROVE_WITH_EDITS,
            decided_by_user_id="user_sarah",
            edits=SurfaceEdits(fields={"subject": "Revised subject"}),
        )

        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.status is DraftStatus.SENT
        # Field overlays the target metadata; the base recipient rides through.
        assert latest.target_metadata == {
            "to": "vip@acme.test",
            "subject": "Revised subject",
        }

    async def test_field_outside_allowlist_rejected_before_mutation(self) -> None:
        # Defense in depth: a directly-enqueued command carrying a field key that
        # is NOT part of the server-held proposal (bypassing the API-edge check)
        # is rejected at the worker BEFORE any draft version is written — the
        # reviewer's delta can never introduce a brand-new metadata key.
        store = InMemoryDraftStore()
        await _seed_pending(store)
        handler, persistence = _handler(store)
        handler.event_producer.append_api_event = _noop_emitter  # type: ignore[assignment]

        with pytest.raises(AgentRuntimeError):
            await handler._resolve_draft_send_approval(
                run=_RUN_RECORD,
                approval=_approval(draft_id=_draft_id(), draft_version=2),
                decision=ApprovalDecision.APPROVE_WITH_EDITS,
                decided_by_user_id="user_sarah",
                edits=SurfaceEdits(fields={"assignee": "mallory"}),
            )

        # No mutation: the pending draft is untouched and no run/audit side effect fired.
        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.version == 2
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
        assert persistence.audit_calls == []
        assert persistence.run_status_updates == []

    async def test_replay_after_send_is_idempotent_no_op(self) -> None:
        store = InMemoryDraftStore()
        await _seed_pending(store)
        handler, persistence = _handler(store)
        handler.event_producer.append_api_event = _noop_emitter  # type: ignore[assignment]

        approval = _approval(draft_id=_draft_id(), draft_version=2)
        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=approval,
            decision=ApprovalDecision.APPROVE_WITH_EDITS,
            decided_by_user_id="user_sarah",
            edits=SurfaceEdits(body="Edited once."),
        )
        after_first = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert after_first is not None and after_first.version == 3
        audit_count_after_first = len(persistence.audit_calls)

        # Replay — the draft is no longer SEND_PENDING_APPROVAL, so nothing sends.
        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=approval,
            decision=ApprovalDecision.APPROVE_WITH_EDITS,
            decided_by_user_id="user_sarah",
            edits=SurfaceEdits(body="Edited twice."),
        )
        after_replay = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert after_replay is not None
        assert after_replay.version == 3  # no new version
        assert after_replay.content_text == "Edited once."  # first edit stands
        assert len(persistence.audit_calls) == audit_count_after_first  # no new audit

    async def test_plain_approve_commits_unedited_body(self) -> None:
        store = InMemoryDraftStore()
        await _seed_pending(store)
        handler, persistence = _handler(store)
        handler.event_producer.append_api_event = _noop_emitter  # type: ignore[assignment]

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=2),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_sarah",
        )
        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.status is DraftStatus.SENT
        assert latest.content_text == "Original body."
        completed = [
            record
            for event_type, record in persistence.audit_calls
            if event_type == "draft.send.completed"
        ]
        assert completed and completed[-1]["edited"] is False


async def _noop_emitter(**kwargs: object) -> None:
    return None
