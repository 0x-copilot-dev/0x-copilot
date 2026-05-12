"""Unit tests for the PR 1.3.5 draft-send branch in RuntimeApprovalHandler.

The handler's ``handle`` method walks through a lot of LangGraph machinery
that isn't relevant to the draft-send transition. We exercise the branch
through ``_resolve_draft_send_approval`` directly with focused stubs so the
tests stay fast and deterministic.
"""

from __future__ import annotations

import pytest

from agent_runtime.persistence.records import DraftStatus
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from types import SimpleNamespace

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


class _StubPersistence:
    """Minimal async persistence-port stub for the draft-send tests."""

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


# Duck-typed RunRecord stand-in. The handler reads only ``org_id``,
# ``run_id``, ``user_id``, and ``conversation_id`` off the run; full
# Pydantic validation costs aren't worth paying for a unit test.
_RUN_RECORD = SimpleNamespace(
    run_id="run_1",
    conversation_id="conv_1",
    org_id="org_acme",
    user_id="user_sarah",
    status=AgentRunStatus.WAITING_FOR_APPROVAL,
)


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
            "target_connector": "slack",
            "target_metadata": {"channel": "#test"},
            "summary": "Send Aurora to slack",
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


class TestDraftSendResolution:
    async def test_approve_transitions_to_sent_and_audits_completed(self) -> None:
        store = InMemoryDraftStore()
        await store.insert_version(_record(version=1, run_id="run_existing"))
        await store.insert_version(
            _record(
                version=2,
                run_id="run_1",
                status=DraftStatus.SEND_PENDING_APPROVAL,
                target_connector="slack",
                target_metadata={"channel": "#test"},
            )
        )
        handler, persistence = _handler(store)
        captured: list[dict] = []

        async def _capture_event(**kwargs: object) -> None:
            captured.append(dict(kwargs))

        handler.event_producer.append_api_event = _capture_event  # type: ignore[assignment]

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=2),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_marcus",
        )

        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.version == 3
        assert latest.status is DraftStatus.SENT

        # Audit chain has draft.send.completed.
        audit_calls = persistence.audit_calls
        assert any(call[0] == "draft.send.completed" for call in audit_calls)

        # Two events: DRAFT_UPDATED + RUN_COMPLETED.
        emitted_types = [call["event_type"] for call in captured]
        assert RuntimeApiEventType.DRAFT_UPDATED in emitted_types
        assert RuntimeApiEventType.RUN_COMPLETED in emitted_types

    async def test_reject_reverts_to_draft_and_audits_rejected(self) -> None:
        store = InMemoryDraftStore()
        await store.insert_version(_record(version=1, run_id="run_existing"))
        await store.insert_version(
            _record(
                version=2,
                run_id="run_1",
                status=DraftStatus.SEND_PENDING_APPROVAL,
            )
        )
        handler, persistence = _handler(store)
        handler.event_producer.append_api_event = (  # type: ignore[assignment]
            _noop_emitter
        )

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=2),
            decision=ApprovalDecision.REJECTED,
            decided_by_user_id="user_marcus",
        )

        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.version == 3
        assert latest.status is DraftStatus.DRAFT

        audit_calls = persistence.audit_calls
        assert any(call[0] == "draft.send.rejected" for call in audit_calls)

    async def test_skips_when_status_is_not_pending(self) -> None:
        store = InMemoryDraftStore()
        await store.insert_version(_record(version=1, run_id="run_existing"))
        # Status is plain DRAFT — no pending approval to resolve.
        handler, persistence = _handler(store)
        handler.event_producer.append_api_event = (  # type: ignore[assignment]
            _noop_emitter
        )

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=1),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_marcus",
        )

        latest = await store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.version == 1
        # No state mutation; no audit.
        assert persistence.audit_calls == []

    async def test_skips_without_draft_store(self) -> None:
        handler = RuntimeApprovalHandler(
            persistence=_StubPersistence(),
            event_store=_StubEventStore(),
            draft_store=None,
        )
        # Should not raise; just no-op.
        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=_approval(draft_id=_draft_id(), draft_version=2),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_marcus",
        )


async def _noop_emitter(**kwargs: object) -> None:
    return None
