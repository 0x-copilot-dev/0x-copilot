"""PR 3.2.5 Phase 3 — `subagent_resumed` is emitted by the approval handler
before the LangGraph resumer runs whenever the resolved approval was
subagent-scoped.

Pairs with `test_streaming_executor_isolation.py` which covers the
`subagent_paused` emit on the interrupt side. Together they form the
matched lifecycle the FE reducer expects.
"""

from __future__ import annotations

import os

# RuntimeEventProducer's enrichment path constructs an OpenAI client; this
# test runs hermetically so a placeholder key is enough.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-resume")

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler


_ORG_ID = "org_phase3"
_USER_ID = "user_1"
_CONVERSATION_ID = "conv_phase3"
_RUN_ID = "run_phase3"
_USER_MESSAGE_ID = "msg_user_phase3"
_APPROVAL_ID = "appr_subagent"
_PARENT_TASK_ID = "subagent_call_id_xyz"


class _FakeHarness:
    pass


async def _empty_resumer(_: object, __: object):
    """Resumer that yields nothing — graph completes immediately so the
    handler runs the post-resume cleanup path."""
    if False:
        yield {}


async def _seed_run_and_subagent_approval(store: InMemoryRuntimeApiStore) -> None:
    from runtime_api.schemas import MessageRecord

    await store.append_message(
        MessageRecord(
            message_id=_USER_MESSAGE_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            role=MessageRole.USER,
            content_text="Post the report draft to Slack.",
        )
    )
    store.runs[_RUN_ID] = RunRecord(
        run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        org_id=_ORG_ID,
        user_id=_USER_ID,
        user_message_id=_USER_MESSAGE_ID,
        trace_id="trace_phase3",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_USER_ID,
            org_id=_ORG_ID,
            roles=["employee"],
            run_id=_RUN_ID,
            trace_id="trace_phase3",
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
    store.events_by_run.setdefault(_RUN_ID, [])
    await store.seed_approval_request(
        ApprovalRequestRecord(
            approval_id=_APPROVAL_ID,
            run_id=_RUN_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            user_id=_USER_ID,
            metadata={
                "approval_kind": "action",
                "native_interrupt_id": _APPROVAL_ID,
                "tool_name": "post_to_slack",
                # Phase 3 — set by `stream_events.create_approval_request`
                # whenever the interrupt fired inside a subagent.
                "parent_task_id": _PARENT_TASK_ID,
            },
        )
    )
    # PR #43 — seed the 1-item ApprovalBatch so the handler's atomic
    # transition gate completes and proceeds to the resume path.
    await store.insert_approval_batch(
        spec=ApprovalBatchSpec.build(
            batch=ApprovalBatchRecord(
                batch_id=_APPROVAL_ID,
                run_id=_RUN_ID,
                org_id=_ORG_ID,
            ),
            items=[
                ApprovalBatchItemRecord(
                    item_id=_APPROVAL_ID,
                    batch_id=_APPROVAL_ID,
                    index=0,
                ),
            ],
        )
    )


def _make_handler(store: InMemoryRuntimeApiStore) -> RuntimeApprovalHandler:
    return RuntimeApprovalHandler(
        persistence=store,
        event_store=store,
        agent_factory=lambda **_: _FakeHarness(),
        runtime_resumer=_empty_resumer,
    )


async def test_handle_emits_subagent_resumed_for_subagent_scoped_approval() -> None:
    store = InMemoryRuntimeApiStore()
    await _seed_run_and_subagent_approval(store)
    handler = _make_handler(store)
    command = RuntimeApprovalResolvedCommand(
        approval_id=_APPROVAL_ID,
        run_id=_RUN_ID,
        org_id=_ORG_ID,
        decision=ApprovalDecision.APPROVED,
    )

    await handler.handle(command)

    persisted = store.events_by_run[_RUN_ID]
    resumed = [
        event
        for event in persisted
        if event.event_type is RuntimeApiEventType.SUBAGENT_RESUMED
    ]
    assert len(resumed) == 1, [(e.event_type, e.task_id) for e in persisted]
    payload = dict(resumed[0].payload)
    assert payload["task_id"] == _PARENT_TASK_ID
    assert payload["reason"] == "approved"
    assert payload["approval_id"] == _APPROVAL_ID
    # The reducer keys on `task_id` — make sure the envelope itself also
    # carries `parent_task_id` so the API replay endpoint exposes it.
    assert resumed[0].parent_task_id == _PARENT_TASK_ID


async def test_handle_skips_subagent_resumed_when_metadata_lacks_parent_task_id() -> (
    None
):
    """Supervisor-scoped approvals have no `parent_task_id` on metadata —
    the handler should not emit a stray resume event."""

    store = InMemoryRuntimeApiStore()
    await _seed_run_and_subagent_approval(store)
    # Strip the parent_task_id, simulating a supervisor-scoped interrupt.
    approval = store.approval_requests[_APPROVAL_ID]
    metadata = dict(approval.metadata)
    metadata.pop("parent_task_id", None)
    store.approval_requests[_APPROVAL_ID] = approval.model_copy(
        update={"metadata": metadata}
    )

    handler = _make_handler(store)
    command = RuntimeApprovalResolvedCommand(
        approval_id=_APPROVAL_ID,
        run_id=_RUN_ID,
        org_id=_ORG_ID,
        decision=ApprovalDecision.APPROVED,
    )

    await handler.handle(command)

    resumed = [
        event
        for event in store.events_by_run[_RUN_ID]
        if event.event_type is RuntimeApiEventType.SUBAGENT_RESUMED
    ]
    assert resumed == []


async def test_handle_emits_subagent_resumed_with_rejected_reason_on_reject() -> None:
    store = InMemoryRuntimeApiStore()
    await _seed_run_and_subagent_approval(store)
    handler = _make_handler(store)
    command = RuntimeApprovalResolvedCommand(
        approval_id=_APPROVAL_ID,
        run_id=_RUN_ID,
        org_id=_ORG_ID,
        decision=ApprovalDecision.REJECTED,
    )

    await handler.handle(command)

    resumed = [
        event
        for event in store.events_by_run[_RUN_ID]
        if event.event_type is RuntimeApiEventType.SUBAGENT_RESUMED
    ]
    assert len(resumed) == 1
    assert dict(resumed[0].payload)["reason"] == "rejected"


async def test_subagent_resumed_idempotent_on_handler_replay() -> None:
    """AC-5 — a transient retry of ``handle()`` for the same approval must
    not re-emit ``SUBAGENT_RESUMED``. The handler tracks
    ``(run_id, parent_task_id)`` per-instance and skips duplicates.
    """

    store = InMemoryRuntimeApiStore()
    await _seed_run_and_subagent_approval(store)
    handler = _make_handler(store)
    command = RuntimeApprovalResolvedCommand(
        approval_id=_APPROVAL_ID,
        run_id=_RUN_ID,
        org_id=_ORG_ID,
        decision=ApprovalDecision.APPROVED,
    )

    # First invocation emits the resume.
    await handler.handle(command)
    # Second invocation on the same handler instance for the same approval
    # must NOT re-emit. Many upstream paths short-circuit before reaching
    # the resume code (e.g. the run is no longer WAITING_FOR_APPROVAL), but
    # this dedup is the belt-and-braces inside the handler itself.
    await handler.handle(command)

    resumed = [
        event
        for event in store.events_by_run[_RUN_ID]
        if event.event_type is RuntimeApiEventType.SUBAGENT_RESUMED
    ]
    assert len(resumed) == 1, (
        "Expected exactly one SUBAGENT_RESUMED across two handle() calls; "
        f"saw {len(resumed)}"
    )
