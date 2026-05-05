"""PR 1.4 — Two-stage approval forwarding tests.

The forward decision is API-edge bookkeeping: it never reaches the
LangGraph harness, the run stays paused, and resume hangs off the leaf
child's eventual approve/reject. These tests cover the contract,
service-level wiring, persistence atomicity, and worker skip-resume
semantics that the design depends on.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalForwardTarget,
    ApprovalRequestRecord,
    ApprovalStatus,
    RuntimeApprovalResolvedCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler


class _Values:
    ORG_ID = "org_acme"
    REQUESTER_USER_ID = "user_sarah"
    FORWARD_TARGET_USER_ID = "user_marcus"
    RUN_ID = "run_launch_announcement"
    CONVERSATION_ID = "conv_launch"
    PARENT_APPROVAL_ID = "approval_parent_1"
    USER_MESSAGE_ID = "msg_user"


def _seed_run_and_pending_approval(
    store: InMemoryRuntimeApiStore,
    *,
    approval_kind: str = "action",
) -> ApprovalRequestRecord:
    """Stand up a run + pending approval mirroring the launch-flow scenario."""

    from agent_runtime.execution.contracts import AgentRuntimeContext
    from runtime_api.schemas import MessageRecord, MessageRole, RunRecord

    store.append_message(
        MessageRecord(
            message_id=_Values.USER_MESSAGE_ID,
            conversation_id=_Values.CONVERSATION_ID,
            org_id=_Values.ORG_ID,
            role=MessageRole.USER,
            content_text="Draft the FY26 Q1 launch announcement",
        )
    )
    store.runs[_Values.RUN_ID] = RunRecord(
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.REQUESTER_USER_ID,
        user_message_id=_Values.USER_MESSAGE_ID,
        trace_id="trace_launch",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_Values.REQUESTER_USER_ID,
            org_id=_Values.ORG_ID,
            roles=["employee"],
            run_id=_Values.RUN_ID,
            trace_id="trace_launch",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )
    store.events_by_run.setdefault(_Values.RUN_ID, [])
    record = ApprovalRequestRecord(
        approval_id=_Values.PARENT_APPROVAL_ID,
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.REQUESTER_USER_ID,
        metadata={
            "approval_kind": approval_kind,
            "native_interrupt_id": _Values.PARENT_APPROVAL_ID,
            "tool_name": "post_to_slack",
            "action_summary": "Post draft to #launch-aurora",
        },
    )
    store.seed_approval_request(record)
    return record


def _make_service(store: InMemoryRuntimeApiStore) -> RuntimeApiService:
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_PARALLEL_TASKS": "4",
        }
    )
    return RuntimeApiService(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Contract: ApprovalDecisionRequest validators
# ---------------------------------------------------------------------------


class TestApprovalDecisionRequestForwardValidators:
    """The forward variant carries its own validation surface."""

    def test_forward_requires_target(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.FORWARDED,
                decided_by_user_id="user_sarah",
            )

    def test_target_only_allowed_with_forward_decision(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id="user_sarah",
                forward_to=ApprovalForwardTarget(
                    kind="workspace_user", user_id="user_marcus"
                ),
            )

    def test_self_forward_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.FORWARDED,
                decided_by_user_id="user_sarah",
                forward_to=ApprovalForwardTarget(
                    kind="workspace_user", user_id="user_sarah"
                ),
            )

    def test_valid_forward_request_round_trips(self) -> None:
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id="user_sarah",
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id="user_marcus"
            ),
            reason="Marcus owns press timing",
        )
        assert request.decision is ApprovalDecision.FORWARDED
        assert request.forward_to is not None
        assert request.forward_to.user_id == "user_marcus"


# ---------------------------------------------------------------------------
# Persistence: in-memory atomic forward
# ---------------------------------------------------------------------------


class TestInMemoryForwardApproval:
    """The atomic parent→FORWARDED + child INSERT is the persistence anchor."""

    def test_forward_persists_chain(self) -> None:
        from datetime import datetime, timezone

        store = InMemoryRuntimeApiStore()
        parent = _seed_run_and_pending_approval(store)
        now = datetime.now(timezone.utc)
        child = ApprovalRequestRecord(
            approval_id="approval_child_1",
            run_id=parent.run_id,
            conversation_id=parent.conversation_id,
            org_id=parent.org_id,
            user_id=_Values.FORWARD_TARGET_USER_ID,
            metadata=parent.metadata,
        )
        updated_parent, inserted_child = store.forward_approval_request(
            parent_approval_id=parent.approval_id,
            org_id=parent.org_id,
            decided_by_user_id=parent.user_id,
            forwarded_to_user_id=_Values.FORWARD_TARGET_USER_ID,
            decision_reason=None,
            child=child,
            now=now,
        )

        assert updated_parent.status is ApprovalStatus.FORWARDED
        assert updated_parent.forwarded_to_user_id == _Values.FORWARD_TARGET_USER_ID
        assert inserted_child.chain_parent_approval_id == parent.approval_id
        assert inserted_child.user_id == _Values.FORWARD_TARGET_USER_ID
        # The decision row exists for the parent so audit/read-back paths
        # observe a coherent resolution.
        assert (
            store.approval_decisions[parent.approval_id].status
            is ApprovalStatus.FORWARDED
        )

    def test_forward_idempotent_on_replay(self) -> None:
        from datetime import datetime, timezone

        store = InMemoryRuntimeApiStore()
        parent = _seed_run_and_pending_approval(store)
        child = ApprovalRequestRecord(
            approval_id="approval_child_idempotent",
            run_id=parent.run_id,
            conversation_id=parent.conversation_id,
            org_id=parent.org_id,
            user_id=_Values.FORWARD_TARGET_USER_ID,
            metadata=parent.metadata,
        )
        now = datetime.now(timezone.utc)
        first_parent, first_child = store.forward_approval_request(
            parent_approval_id=parent.approval_id,
            org_id=parent.org_id,
            decided_by_user_id=parent.user_id,
            forwarded_to_user_id=_Values.FORWARD_TARGET_USER_ID,
            decision_reason=None,
            child=child,
            now=now,
        )
        second_parent, second_child = store.forward_approval_request(
            parent_approval_id=parent.approval_id,
            org_id=parent.org_id,
            decided_by_user_id=parent.user_id,
            forwarded_to_user_id=_Values.FORWARD_TARGET_USER_ID,
            decision_reason=None,
            child=child,
            now=now,
        )

        assert first_parent.approval_id == second_parent.approval_id
        assert first_child.approval_id == second_child.approval_id
        # Only one child row exists for the chain.
        assert (
            sum(
                1
                for record in store.approval_requests.values()
                if record.chain_parent_approval_id == parent.approval_id
            )
            == 1
        )


# ---------------------------------------------------------------------------
# API service: _decide_forwarded happy path + guards
# ---------------------------------------------------------------------------


class TestServiceDecideForwarded:
    """The service emits the three events + audit row + leaves run paused."""

    def test_decide_forwarded_emits_chain_events(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(store)

        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
            reason="Marcus must approve",
        )
        response = asyncio.run(
            service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
        )

        assert response.status is ApprovalStatus.FORWARDED
        assert response.forwarded_to_user_id == _Values.FORWARD_TARGET_USER_ID
        assert response.child_approval_id is not None

        events = store.events_by_run[_Values.RUN_ID]
        event_types = [event.event_type.value for event in events]
        # The trio lands in stream order: parent resolution → forwarded
        # annotation → child request. Replay reproduces the chain.
        assert event_types[-3:] == [
            "approval_resolved",
            "approval_forwarded",
            "approval_requested",
        ]
        forwarded_payload = events[-2].payload
        assert (
            forwarded_payload["chain_parent_approval_id"] == _Values.PARENT_APPROVAL_ID
        )
        assert (
            forwarded_payload["forwarded_to_user_id"] == _Values.FORWARD_TARGET_USER_ID
        )

        # Run stays WAITING_FOR_APPROVAL: the LangGraph interrupt is not
        # resolved by a forward, only by a leaf approver's decision.
        assert store.runs[_Values.RUN_ID].status is AgentRunStatus.WAITING_FOR_APPROVAL
        # Worker is *not* enqueued for the parent — the next resume
        # command will arrive from the child's eventual decision.
        assert all(
            cmd.approval_id != _Values.PARENT_APPROVAL_ID
            for cmd in store.approval_commands
        )

    def test_decide_forwarded_writes_audit_row(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(store)

        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        asyncio.run(
            service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
        )

        events = [(name, payload) for name, payload in store.audit_log]
        forward_audits = [
            payload for name, payload in events if name == "approval.forward"
        ]
        assert len(forward_audits) == 1
        meta = forward_audits[0]["metadata"]
        assert meta["chain_parent_approval_id"] == _Values.PARENT_APPROVAL_ID
        assert meta["forwarded_to_user_id"] == _Values.FORWARD_TARGET_USER_ID

    def test_decide_forwarded_rejects_ask_a_question_kind(self) -> None:
        from runtime_api.http.errors import RuntimeApiError

        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store, approval_kind="ask_a_question")
        service = _make_service(store)

        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        with pytest.raises(RuntimeApiError) as exc:
            asyncio.run(
                service.record_approval_decision(
                    org_id=_Values.ORG_ID,
                    approval_id=_Values.PARENT_APPROVAL_ID,
                    request=request,
                )
            )
        assert exc.value.http_status == 422

    def test_decide_forwarded_rejects_already_resolved_parent(self) -> None:
        from runtime_api.http.errors import RuntimeApiError

        store = InMemoryRuntimeApiStore()
        parent = _seed_run_and_pending_approval(store)
        store.approval_requests[parent.approval_id] = parent.model_copy(
            update={"status": ApprovalStatus.APPROVED}
        )
        service = _make_service(store)

        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        with pytest.raises(RuntimeApiError) as exc:
            asyncio.run(
                service.record_approval_decision(
                    org_id=_Values.ORG_ID,
                    approval_id=_Values.PARENT_APPROVAL_ID,
                    request=request,
                )
            )
        assert exc.value.http_status == 409


# ---------------------------------------------------------------------------
# Worker: skip-resume on FORWARDED
# ---------------------------------------------------------------------------


class TestWorkerForwardedSkip:
    """The worker discriminates on decision so the graph stays paused."""

    def test_handle_returns_without_resume_on_forwarded(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        captured_resumes: list[object] = []

        async def _capturing_resumer(harness: object, resume: object):
            # Should NEVER fire on a forwarded command — assertions below
            # rely on capture being empty.
            captured_resumes.append(resume)
            if False:
                yield {}

        class _FakeHarness:
            pass

        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_capturing_resumer,
        )

        command = RuntimeApprovalResolvedCommand(
            approval_id=_Values.PARENT_APPROVAL_ID,
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.FORWARDED,
        )
        asyncio.run(handler.handle(command))

        assert captured_resumes == []
        # Run remains paused — the worker did not flip status to RUNNING.
        assert store.runs[_Values.RUN_ID].status is AgentRunStatus.WAITING_FOR_APPROVAL
