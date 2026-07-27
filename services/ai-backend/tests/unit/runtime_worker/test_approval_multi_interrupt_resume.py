"""Resolving ONE approval while a second interrupt is still pending.

The packaged desktop app failed the whole run here: the handler resumed with a
bare ``Command(resume=...)``, LangGraph could not attribute the decision across
two pending interrupts, and the run died holding an approval the user had
already granted.

Unlike ``test_approval_batch_fanin`` (N action_requests inside ONE interrupt),
this covers N SEPARATE interrupts parked at once — two branches of the graph
each waiting on their own approval. The handler must resume the decided
interrupt and leave the run ALIVE and parked on the other.

The resumer here is the real ``astream_runtime_resume`` driving a real compiled
LangGraph, because every existing approval test injects a fake resumer and a
fake cannot reproduce the library-side guard that caused the outage.
"""

from __future__ import annotations

import os

# RuntimeEventProducer's enrichment path constructs an OpenAI client.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-multi-interrupt")

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import Annotated, TypedDict

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.runtime import astream_runtime_resume
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
    ApprovalBatchStatus,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApprovalResolvedCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler

_ORG_ID = "org_multi"
_USER_ID = "user_multi"
_RUN_ID = "run_multi"
_CONVERSATION_ID = "conv_multi"
_USER_MESSAGE_ID = "msg_user_multi"


def _merge(left: list | None, right: list | None) -> list:
    return (left or []) + (right or [])


class _State(TypedDict):
    resolved: Annotated[list, _merge]


class TwoPendingApprovalsMixin:
    """Seeds a run parked on TWO concurrent LangGraph interrupts."""

    @staticmethod
    def _branch(name: str):
        def _run(_state: _State) -> dict[str, object]:
            decision = interrupt({"ask": name})
            return {"resolved": [f"{name}:{decision}"]}

        return _run

    @classmethod
    def _compiled_graph(cls):
        graph = StateGraph(_State)
        graph.add_node("alpha", cls._branch("alpha"))
        graph.add_node("beta", cls._branch("beta"))
        graph.add_edge(START, "alpha")
        graph.add_edge(START, "beta")
        graph.add_edge("alpha", END)
        graph.add_edge("beta", END)
        return graph.compile(checkpointer=InMemorySaver())

    @staticmethod
    def _runtime_context() -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=_USER_ID,
            org_id=_ORG_ID,
            roles=["employee"],
            run_id=_RUN_ID,
            trace_id="trace_multi",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        )

    class _Harness:
        def __init__(self, agent: object, context: AgentRuntimeContext) -> None:
            self.agent = agent
            self.context = context

    @classmethod
    async def _seed_run(cls, store: InMemoryRuntimeApiStore) -> None:
        await store.append_message(
            MessageRecord(
                message_id=_USER_MESSAGE_ID,
                conversation_id=_CONVERSATION_ID,
                org_id=_ORG_ID,
                role=MessageRole.USER,
                content_text="Do both of those things.",
            )
        )
        store.runs[_RUN_ID] = RunRecord(
            run_id=_RUN_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            user_id=_USER_ID,
            user_message_id=_USER_MESSAGE_ID,
            trace_id="trace_multi",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
            runtime_context=cls._runtime_context(),
        )
        store.events_by_run.setdefault(_RUN_ID, [])

    @staticmethod
    async def _seed_single_item_batch(
        store: InMemoryRuntimeApiStore, *, interrupt_id: str
    ) -> str:
        """Project one pending interrupt into its approval row + 1-item batch.

        Mirrors ``stream_events``: ``batch_id`` and ``native_interrupt_id`` are
        the interrupt id, and ``approval_id`` is ``<interrupt_id>:<index>``.
        """
        approval_id = f"{interrupt_id}:0"
        await store.seed_approval_request(
            ApprovalRequestRecord(
                approval_id=approval_id,
                run_id=_RUN_ID,
                conversation_id=_CONVERSATION_ID,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                metadata={
                    "approval_kind": "mcp_tool",
                    "native_interrupt_id": interrupt_id,
                    "batch_id": interrupt_id,
                    "batch_index": 0,
                    "tool_name": "create_issue",
                    "server_name": "mcp_linear_app",
                },
            )
        )
        await store.insert_approval_batch(
            spec=ApprovalBatchSpec.build(
                batch=ApprovalBatchRecord(
                    batch_id=interrupt_id, run_id=_RUN_ID, org_id=_ORG_ID
                ),
                items=[
                    ApprovalBatchItemRecord(
                        item_id=approval_id, batch_id=interrupt_id, index=0
                    )
                ],
            )
        )
        return approval_id

    @classmethod
    async def _parked_on_two_approvals(cls):
        """Return (store, handler, agent, [alpha_id, beta_id])."""
        store = InMemoryRuntimeApiStore()
        await cls._seed_run(store)

        agent = cls._compiled_graph()
        first = await agent.ainvoke(
            {"resolved": []}, config={"configurable": {"thread_id": _RUN_ID}}
        )
        pending = [item.id for item in first["__interrupt__"]]
        assert len(pending) == 2, "fixture must park on two interrupts"
        for interrupt_id in pending:
            await cls._seed_single_item_batch(store, interrupt_id=interrupt_id)

        harness = cls._Harness(agent, cls._runtime_context())
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: harness,
            runtime_resumer=astream_runtime_resume,
        )
        return store, handler, agent, pending

    @staticmethod
    async def _decide(
        handler: RuntimeApprovalHandler,
        *,
        approval_id: str,
        decision: ApprovalDecision = ApprovalDecision.APPROVED,
    ) -> None:
        await handler.handle(
            RuntimeApprovalResolvedCommand(
                approval_id=approval_id,
                run_id=_RUN_ID,
                org_id=_ORG_ID,
                decision=decision,
            )
        )


class TestResolvingOneOfTwoPendingApprovals(TwoPendingApprovalsMixin):
    async def test_run_stays_alive_and_parked_on_the_second(self) -> None:
        store, handler, agent, pending = await self._parked_on_two_approvals()
        alpha_id, beta_id = pending

        await self._decide(handler, approval_id=f"{alpha_id}:0")

        # 1. The run did NOT fail. Before the fix this raised AgentRuntimeError
        #    and the run was marked FAILED with the user's approval lost.
        run = store.runs[_RUN_ID]
        assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL

        # 2. The approved interrupt actually ran its branch...
        state = agent.get_state({"configurable": {"thread_id": _RUN_ID}})
        assert state.values["resolved"] == [
            "alpha:{'decisions': [{'type': 'approve'}]}"
        ]

        # 3. ...and the graph is still parked on the untouched sibling.
        #    Asserted via ``next`` (the task still to run), not
        #    ``state.interrupts`` — that view keeps listing an interrupt after
        #    it has been resumed, so it cannot distinguish resolved from pending.
        assert state.next == ("beta",)
        assert beta_id != alpha_id

    async def test_resolved_batch_closes_and_sibling_stays_pending(self) -> None:
        store, handler, _agent, pending = await self._parked_on_two_approvals()
        alpha_id, beta_id = pending

        await self._decide(handler, approval_id=f"{alpha_id}:0")

        resolved = await store.get_approval_batch(org_id=_ORG_ID, batch_id=alpha_id)
        sibling = await store.get_approval_batch(org_id=_ORG_ID, batch_id=beta_id)
        # The decided interrupt's batch is closed so a retry cannot double-resume,
        # while the sibling remains the live gate for the remaining approval.
        assert resolved.status is ApprovalBatchStatus.RESOLVED
        assert sibling.status is ApprovalBatchStatus.PENDING

    async def test_second_approval_then_completes_the_run(self) -> None:
        store, handler, agent, pending = await self._parked_on_two_approvals()
        alpha_id, beta_id = pending

        await self._decide(handler, approval_id=f"{alpha_id}:0")
        await self._decide(
            handler, approval_id=f"{beta_id}:0", decision=ApprovalDecision.REJECTED
        )

        # Each branch received its OWN decision — the targeted resumes did not
        # leak one approval's value into the other interrupt.
        state = agent.get_state({"configurable": {"thread_id": _RUN_ID}})
        assert sorted(state.values["resolved"]) == [
            "alpha:{'decisions': [{'type': 'approve'}]}",
            "beta:{'decisions': [{'type': 'reject'}]}",
        ]
        assert state.next == ()
        assert store.runs[_RUN_ID].status is AgentRunStatus.COMPLETED

    async def test_either_order_of_resolution_works(self) -> None:
        # Nothing should depend on the user approving in interrupt order.
        store, handler, agent, pending = await self._parked_on_two_approvals()
        alpha_id, beta_id = pending

        await self._decide(handler, approval_id=f"{beta_id}:0")
        assert store.runs[_RUN_ID].status is AgentRunStatus.WAITING_FOR_APPROVAL

        await self._decide(handler, approval_id=f"{alpha_id}:0")
        state = agent.get_state({"configurable": {"thread_id": _RUN_ID}})
        assert sorted(state.values["resolved"]) == [
            "alpha:{'decisions': [{'type': 'approve'}]}",
            "beta:{'decisions': [{'type': 'approve'}]}",
        ]
        assert store.runs[_RUN_ID].status is AgentRunStatus.COMPLETED
