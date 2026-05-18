"""Fan-in handler tests for the ApprovalBatch refactor (PR #43).

The handler is the resume gate. Before this PR it called the LangGraph
harness immediately on the first ``approval_resolved`` event, even when
the underlying interrupt had N >= 2 ``action_requests`` — that crashed
the run with ``ValueError`` inside the HITL middleware. After this PR
the handler routes through ``record_item_decision_and_maybe_lock_batch``
and resumes only on ``READY_TO_RESUME``.

Pins:
- N=1 + approve → handler invokes ``_stream_resume`` once with
  ``{decisions: [approve]}``
- N=5 + 4 approvals → 4 calls, ``_stream_resume`` not invoked
- N=5 + 5th approval → resume fires once with 5 aligned decisions
- N=5 mixed approve/reject → resume payload has the actual mix in index order
- Concurrent last-two-decisions → exactly one resume invocation
- Original bug regression: N=5 with only 1 resolved → run STAYS in
  WAITING_FOR_APPROVAL; no ``AgentRuntimeError`` raised
"""

from __future__ import annotations

import asyncio
import os

# RuntimeEventProducer's enrichment path constructs an OpenAI client.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-batch-fanin")

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
    RuntimeApprovalResolvedCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler


_ORG_ID = "org_fanin"
_USER_ID = "user_fanin"
_RUN_ID = "run_fanin"
_USER_MESSAGE_ID = "msg_user_fanin"
_CONVERSATION_ID = "conv_fanin"
_BATCH_ID = "batch_fanin"


class _FakeHarness:
    pass


async def _seed_run(store: InMemoryRuntimeApiStore) -> None:
    from runtime_api.schemas import MessageRecord

    await store.append_message(
        MessageRecord(
            message_id=_USER_MESSAGE_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            role=MessageRole.USER,
            content_text="Approve the 5 issue loads.",
        )
    )
    store.runs[_RUN_ID] = RunRecord(
        run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        org_id=_ORG_ID,
        user_id=_USER_ID,
        user_message_id=_USER_MESSAGE_ID,
        trace_id="trace_fanin",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_USER_ID,
            org_id=_ORG_ID,
            roles=["employee"],
            run_id=_RUN_ID,
            trace_id="trace_fanin",
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


async def _seed_batch_and_items(store: InMemoryRuntimeApiStore, *, size: int) -> None:
    """Seed N approval-request rows + the matching N-item batch."""
    for index in range(size):
        item_id = f"{_BATCH_ID}:{index}"
        await store.seed_approval_request(
            ApprovalRequestRecord(
                approval_id=item_id,
                run_id=_RUN_ID,
                conversation_id=_CONVERSATION_ID,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                metadata={
                    "approval_kind": "mcp_tool",
                    "native_interrupt_id": _BATCH_ID,
                    "batch_id": _BATCH_ID,
                    "batch_index": index,
                    "tool_name": "get_issue",
                    "server_name": "mcp_linear_app",
                },
            )
        )
    await store.insert_approval_batch(
        spec=ApprovalBatchSpec.build(
            batch=ApprovalBatchRecord(
                batch_id=_BATCH_ID, run_id=_RUN_ID, org_id=_ORG_ID
            ),
            items=[
                ApprovalBatchItemRecord(
                    item_id=f"{_BATCH_ID}:{i}", batch_id=_BATCH_ID, index=i
                )
                for i in range(size)
            ],
        )
    )


def _resume_capturing_resumer(captured: list[object]):
    async def _resumer(_harness: object, resume: object):
        captured.append(resume)
        if False:
            yield {}

    return _resumer


def _make_handler(
    store: InMemoryRuntimeApiStore,
    *,
    captured: list[object],
) -> RuntimeApprovalHandler:
    return RuntimeApprovalHandler(
        persistence=store,
        event_store=store,
        agent_factory=lambda **_: _FakeHarness(),
        runtime_resumer=_resume_capturing_resumer(captured),
    )


async def _decide(
    handler: RuntimeApprovalHandler,
    *,
    item_index: int,
    decision: ApprovalDecision,
) -> None:
    await handler.handle(
        RuntimeApprovalResolvedCommand(
            approval_id=f"{_BATCH_ID}:{item_index}",
            run_id=_RUN_ID,
            org_id=_ORG_ID,
            decision=decision,
        )
    )


class TestApprovalBatchFanin:
    async def test_n1_resumes_with_one_decision(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_batch_and_items(store, size=1)
        captured: list[object] = []
        handler = _make_handler(store, captured=captured)

        await _decide(handler, item_index=0, decision=ApprovalDecision.APPROVED)

        assert captured == [{"decisions": [{"type": "approve"}]}]

    async def test_n5_resumes_only_on_fifth_decision(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_batch_and_items(store, size=5)
        captured: list[object] = []
        handler = _make_handler(store, captured=captured)

        for index in range(4):
            await _decide(handler, item_index=index, decision=ApprovalDecision.APPROVED)
            assert captured == [], f"resumer fired prematurely after item {index}"

        await _decide(handler, item_index=4, decision=ApprovalDecision.APPROVED)

        assert captured == [{"decisions": [{"type": "approve"}] * 5}]

    async def test_n5_mixed_approve_reject_preserves_order(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_batch_and_items(store, size=5)
        captured: list[object] = []
        handler = _make_handler(store, captured=captured)

        # items 0,1=approve, 2=reject, 3,4=approve — the literal mix.
        plan = [
            (0, ApprovalDecision.APPROVED),
            (1, ApprovalDecision.APPROVED),
            (2, ApprovalDecision.REJECTED),
            (3, ApprovalDecision.APPROVED),
            (4, ApprovalDecision.APPROVED),
        ]
        for index, decision in plan:
            await _decide(handler, item_index=index, decision=decision)

        assert captured == [
            {
                "decisions": [
                    {"type": "approve"},
                    {"type": "approve"},
                    {"type": "reject"},
                    {"type": "approve"},
                    {"type": "approve"},
                ]
            }
        ]

    async def test_n5_resolves_only_one_then_walks_away(self) -> None:
        """Original bug regression.

        The runtime saw a 5-action interrupt; the user approved item 0 and
        walked away. Before PR #43 the handler resumed with a 1-element
        ``decisions[]`` against a 5-element interrupt and the run crashed
        with ``AgentRuntimeError(execution_error)``. After PR #43 the run
        stays in ``WAITING_FOR_APPROVAL``; no resume fires.
        """
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_batch_and_items(store, size=5)
        captured: list[object] = []
        handler = _make_handler(store, captured=captured)

        # User approves only item 0. The other 4 stay pending.
        await _decide(handler, item_index=0, decision=ApprovalDecision.APPROVED)

        # No resume fired.
        assert captured == []
        # Run stays in WAITING_FOR_APPROVAL — the run is not failed.
        assert store.runs[_RUN_ID].status == AgentRunStatus.WAITING_FOR_APPROVAL

    async def test_concurrent_last_two_decisions_only_one_resume(self) -> None:
        """The atomic primitive's guarantee at the handler level.

        Resolve 3 items first; then submit the last two concurrently. The
        per-batch lock serialises them inside
        ``record_item_decision_and_maybe_lock_batch``; only the caller that
        flipped the batch to RESUMING owns the resume invocation.
        """
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_batch_and_items(store, size=5)
        captured: list[object] = []
        handler = _make_handler(store, captured=captured)

        for index in range(3):
            await _decide(handler, item_index=index, decision=ApprovalDecision.APPROVED)
        assert captured == []  # still incomplete

        async def decide_4():
            await _decide(handler, item_index=3, decision=ApprovalDecision.APPROVED)

        async def decide_5():
            await _decide(handler, item_index=4, decision=ApprovalDecision.APPROVED)

        await asyncio.gather(decide_4(), decide_5())

        # Exactly one resume fired with all five decisions.
        assert len(captured) == 1
        assert captured == [{"decisions": [{"type": "approve"}] * 5}]
