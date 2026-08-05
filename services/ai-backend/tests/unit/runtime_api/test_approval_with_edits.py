"""PRD-09b — ``approve_with_edits`` decision handling + the propose→decision audit chain.

Covers the server-side edit merge at the API edge (the coordinator records the
edits and enqueues an APPROVE_WITH_EDITS resume command carrying them), the
fail-closed 404/422 gates, and the ordered audit records across the journey.
Nothing real is ever sent: no connector is constructed here at all.

The chain used to have a third step asserting ``SurfaceCommitExecutor`` audited
the commit. That executor was deleted — it was never constructed outside this
file and ``tests/unit/runtime_worker/test_stage_commit_handler.py`` covers the
wired lane that emits the same ``surface.commit.*`` audit actions for real.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.draft_service import DraftService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.surfaces.commit import SurfaceEdits
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalRequestRecord,
    ApprovalStatus,
    DraftSendRequest,
    MessageRecord,
    MessageRole,
    RunRecord,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_launch"
_CONV = "conv_launch"
_APPROVAL = "approval_edit_1"
_DRAFT_ID = "abcdef0123456789abcdef0123456789"


async def _seed_run(store: InMemoryRuntimeApiStore) -> None:
    await store.append_message(
        MessageRecord(
            message_id="msg_user",
            conversation_id=_CONV,
            org_id=_ORG,
            role=MessageRole.USER,
            content_text="Draft the launch email",
        )
    )
    store.runs[_RUN] = RunRecord(
        run_id=_RUN,
        conversation_id=_CONV,
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_user",
        trace_id="trace_launch",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
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
    store.events_by_run.setdefault(_RUN, [])


async def _seed_approval(
    store: InMemoryRuntimeApiStore,
    *,
    metadata: dict | None = None,
) -> ApprovalRequestRecord:
    record = ApprovalRequestRecord(
        approval_id=_APPROVAL,
        run_id=_RUN,
        conversation_id=_CONV,
        org_id=_ORG,
        user_id=_USER,
        metadata={
            "approval_kind": "action",
            "native_interrupt_id": _APPROVAL,
            "action_summary": "Send launch email",
            **(metadata or {}),
        },
    )
    await store.seed_approval_request(record)
    return record


def _coordinator(store: InMemoryRuntimeApiStore) -> ApprovalCoordinator:
    return ApprovalCoordinator(
        persistence=store,
        queue=store,
        event_producer=RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        ),
    )


class TestApprovalDecisionRequestEditsValidators:
    def test_edits_required_for_approve_with_edits(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVE_WITH_EDITS,
                decided_by_user_id=_USER,
            )

    def test_edits_rejected_on_plain_approve(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id=_USER,
                edits=SurfaceEdits(body="x"),
            )

    def test_edits_rejected_on_reject(self) -> None:
        # "edits without approve* are inert" — a client cannot slip edits past reject.
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.REJECTED,
                decided_by_user_id=_USER,
                edits=SurfaceEdits(body="x"),
            )


# Marks an approval as the draft-send / commit surface — the only edit-capable
# kind, so ``approve_with_edits`` has somewhere to apply the reviewer's deltas.
_DRAFT_SEND_KIND = {"kind": "draft_send"}


class TestApproveWithEditsDecision:
    async def test_records_decision_edits_and_enqueues_command(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store, metadata=_DRAFT_SEND_KIND)
        coordinator = _coordinator(store)

        response = await coordinator.record_approval_decision(
            org_id=_ORG,
            approval_id=_APPROVAL,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVE_WITH_EDITS,
                decided_by_user_id=_USER,
                edits=SurfaceEdits(body="Reviewer-edited body."),
            ),
        )

        # Decision is recorded as APPROVED (approve_with_edits commits).
        assert response.status is ApprovalStatus.APPROVED
        decision = store.approval_decisions[_APPROVAL]
        assert decision.status is ApprovalStatus.APPROVED
        assert decision.edited_payload == {"body": "Reviewer-edited body."}

        # A resume command is enqueued carrying decision + edits for the worker.
        commands = [c for c in store.approval_commands if c.approval_id == _APPROVAL]
        assert len(commands) == 1
        assert commands[0].decision is ApprovalDecision.APPROVE_WITH_EDITS
        assert commands[0].edits is not None
        assert commands[0].edits.body == "Reviewer-edited body."

        # APPROVAL_RESOLVED event mirrors the applied edits (audit-visible).
        resolved_events = [
            evt
            for evt in store.events_by_run[_RUN]
            if evt.event_type == "approval_resolved"
        ]
        assert resolved_events
        assert resolved_events[-1].payload.get("edits") == {
            "body": "Reviewer-edited body."
        }

        # Audit row records the accept with the edited payload.
        accepts = [
            record
            for event_type, record in store.audit_log
            if event_type == "approval.accept"
        ]
        assert accepts
        assert accepts[-1]["metadata"]["edited_payload"] == {
            "body": "Reviewer-edited body."
        }

    async def test_field_edits_are_recorded_when_allowlisted(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(
            store, metadata={**_DRAFT_SEND_KIND, "editable_fields": ["status"]}
        )
        coordinator = _coordinator(store)

        await coordinator.record_approval_decision(
            org_id=_ORG,
            approval_id=_APPROVAL,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVE_WITH_EDITS,
                decided_by_user_id=_USER,
                edits=SurfaceEdits(fields={"status": "closed"}),
            ),
        )
        decision = store.approval_decisions[_APPROVAL]
        assert decision.edited_payload == {"fields": {"status": "closed"}}


class TestApproveWithEditsFailClosed:
    async def test_unknown_edit_field_rejected_422(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        # Edit-capable approval, but no editable_fields declared ⇒ any fields
        # edit is unknown and rejected by the field allowlist (not the kind gate).
        await _seed_approval(store, metadata=_DRAFT_SEND_KIND)
        coordinator = _coordinator(store)

        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.record_approval_decision(
                org_id=_ORG,
                approval_id=_APPROVAL,
                request=ApprovalDecisionRequest(
                    decision=ApprovalDecision.APPROVE_WITH_EDITS,
                    decided_by_user_id=_USER,
                    edits=SurfaceEdits(fields={"assignee": "mallory"}),
                ),
            )
        assert exc.value.http_status == 422
        # Nothing was recorded or enqueued.
        assert _APPROVAL not in store.approval_decisions
        assert store.approval_commands == []

    async def test_approve_with_edits_rejected_on_non_editable_kind_422(self) -> None:
        # A LangGraph-resume / MCP-tool approval has NO commit-edit surface, so
        # it must never carry edits. Before this gate the worker coerced
        # ``approve_with_edits`` → ``approved`` and silently dropped the edits;
        # now the reviewer gets an explicit 422 and nothing is applied/resumed.
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store, metadata={"approval_kind": "mcp_tool"})
        coordinator = _coordinator(store)

        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.record_approval_decision(
                org_id=_ORG,
                approval_id=_APPROVAL,
                request=ApprovalDecisionRequest(
                    decision=ApprovalDecision.APPROVE_WITH_EDITS,
                    decided_by_user_id=_USER,
                    edits=SurfaceEdits(body="Reviewer-edited body."),
                ),
            )
        assert exc.value.http_status == 422
        # The reviewer got an error: edits were NOT silently applied, nothing was
        # recorded or enqueued, and the run was NOT resumed as a plain approve.
        assert _APPROVAL not in store.approval_decisions
        assert store.approval_commands == []
        resolved_events = [
            evt
            for evt in store.events_by_run[_RUN]
            if evt.event_type == "approval_resolved"
        ]
        assert resolved_events == []

    async def test_unknown_approval_404(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        coordinator = _coordinator(store)

        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.record_approval_decision(
                org_id=_ORG,
                approval_id="does_not_exist",
                request=ApprovalDecisionRequest(
                    decision=ApprovalDecision.APPROVE_WITH_EDITS,
                    decided_by_user_id=_USER,
                    edits=SurfaceEdits(body="x"),
                ),
            )
        assert exc.value.http_status == 404
        assert store.approval_commands == []


class TestProposeDecisionAuditChain:
    async def test_ordered_audit_records(self) -> None:
        store = InMemoryRuntimeApiStore()
        draft_store = InMemoryDraftStore()
        await _seed_run(store)

        # 1. PROPOSE — the draft-send service writes ``draft.send.proposed``.
        await draft_store.insert_version(
            DraftRecord(
                draft_id=_DRAFT_ID,
                version=1,
                org_id=_ORG,
                conversation_id=_CONV,
                run_id=_RUN,
                user_id=_USER,
                title="Launch",
                content_text="# Launch\n\nOriginal body.",
                status=DraftStatus.DRAFT,
            )
        )
        draft_service = DraftService(
            store=draft_store,
            persistence=store,
            auth_gate=None,  # degrade-open: connector auth is out of scope here
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
        )
        send_result = await draft_service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=_DRAFT_ID,
            request=DraftSendRequest(
                expected_version=1,
                target_connector="gmail",
                target_metadata={"to": "vip@acme.test"},
            ),
        )
        approval_id = send_result.approval_id

        # 2. DECISION — the reviewer approves with an edited body.
        coordinator = _coordinator(store)
        await coordinator.record_approval_decision(
            org_id=_ORG,
            approval_id=approval_id,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVE_WITH_EDITS,
                decided_by_user_id=_USER,
                edits=SurfaceEdits(body="Reviewer-edited body."),
            ),
        )

        # Both lifecycle records appear, in order, for this run. The third step
        # — the commit itself — is audited by the wired worker lane instead;
        # ``tests/unit/runtime_worker/test_stage_commit_handler.py`` asserts its
        # ``surface.commit.{committed,aborted_precondition_drift,failed}``
        # records against the handler production actually runs.
        actions = [event_type for event_type, _ in store.audit_log]
        propose_idx = actions.index("draft.send.proposed")
        decision_idx = actions.index("approval.accept")
        assert propose_idx < decision_idx
