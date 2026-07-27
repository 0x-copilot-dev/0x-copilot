"""End-to-end: a REAL run that parks on TWO interrupts survives resolving one.

Reproduces the packaged-desktop outage on the real topology — real queued run,
real worker, real Deep Agents graph, real ``ask_a_question`` built-in, real
approval coordinator, real streaming executor. Only the chat model is the
env-gated deterministic fake.

The shape is taken from the failing machine's own store: two runs there each
held MORE THAN ONE concurrently-pending interrupt (one held four), every one an
``ask_a_question`` raised in the same turn, and every batch id a native 32-hex
LangGraph interrupt id. Resolving one raised

    RuntimeError: When there are multiple pending interrupts, you must specify
    the interrupt id when resuming.

and the run was marked FAILED with the user's answer already given.

Two questions asked in ONE turn become two parallel graph branches, so each
parks in its own task namespace — genuinely concurrent interrupts, as opposed to
several ``action_requests`` inside a single interrupt (``test_approval_batch_fanin``).
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.membership import InMemoryWorkspaceMembershipResolver
from agent_runtime.api.notifications import LoggingNotificationDispatcher
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.fake_model import FakeModelProvider
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.persistence.records import ApprovalBatchStatus
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRequest,
    CreateConversationRequest,
    CreateRunRequest,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

_ORG_ID = "org_123"
_USER_ID = "user_123"
_TRIGGER = "ASK-TWO-THINGS"


class RealRunMixin:
    """Drive a real queued run through the real worker with the fake model."""

    QUESTIONS = ("Ship the draft?", "Notify the team?")

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
            }
        )

    @pytest.fixture(autouse=True)
    def _fake_model(self, monkeypatch):
        """Script ONE turn that asks both questions in parallel."""
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_CALLS, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_PARALLEL_TRIGGER, _TRIGGER)
        monkeypatch.setenv(
            FakeModelProvider.ENV_PARALLEL_TOOL_CALLS,
            json.dumps(
                [
                    {"name": "ask_a_question", "args": {"question": question}}
                    for question in self.QUESTIONS
                ]
            ),
        )

    @classmethod
    def _coordinators(cls, store, settings):
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversations = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        approvals = ApprovalCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            membership_resolver=InMemoryWorkspaceMembershipResolver(),
            notification_dispatcher=LoggingNotificationDispatcher(),
        )
        return run_coordinator, conversations, approvals

    @staticmethod
    def _drain(store, settings) -> None:
        """Run the real worker until the queue is idle."""
        return RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        ).run_until_idle()

    @classmethod
    async def _parked_run(cls):
        """Return (store, settings, approvals, run_id, pending approvals)."""
        store = InMemoryRuntimeApiStore()
        settings = cls._settings()
        runs, conversations, approvals = cls._coordinators(store, settings)

        conversation = await conversations.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID, user_id=_USER_ID, assistant_id="assistant_123"
            )
        )
        created = await runs.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input=f"Two things please {_TRIGGER}",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        await cls._drain(store, settings)

        pending = sorted(
            (a for a in store.approval_requests.values() if a.run_id == created.run_id),
            key=lambda a: a.approval_id,
        )
        return store, settings, approvals, created.run_id, pending

    @staticmethod
    async def _answer(approvals, *, approval_id: str, answer: str = "yes") -> None:
        await approvals.record_approval_decision(
            org_id=_ORG_ID,
            approval_id=approval_id,
            request=ApprovalDecisionRequest(
                decision="approved", decided_by_user_id=_USER_ID, answer=answer
            ),
        )


class TestTwoConcurrentApprovalsOnARealRun(RealRunMixin):
    async def test_one_turn_parks_on_two_distinct_interrupts(self) -> None:
        store, _settings, _approvals, run_id, pending = await self._parked_run()

        # The production precondition: ONE turn, TWO separate LangGraph
        # interrupts, each with its own native 32-hex id.
        assert len(pending) == 2, [a.approval_id for a in pending]
        interrupt_ids = {a.metadata["native_interrupt_id"] for a in pending}
        assert len(interrupt_ids) == 2, interrupt_ids
        assert store.runs[run_id].status is AgentRunStatus.WAITING_FOR_APPROVAL

    async def test_answering_one_keeps_the_run_alive_and_parked(self) -> None:
        store, settings, approvals, run_id, pending = await self._parked_run()
        first, second = pending

        await self._answer(approvals, approval_id=first.approval_id)
        await self._drain(store, settings)

        events = [e.event_type for e in store.events_by_run[run_id]]
        # THE REGRESSION: the resume used to raise, failing a run the user had
        # already answered. It must neither fail NOR complete — the second
        # question is still outstanding.
        assert "run_failed" not in events, events
        assert store.runs[run_id].status is AgentRunStatus.WAITING_FOR_APPROVAL

        # The answered interrupt's batch is closed; the untouched one is still
        # the live gate holding the run.
        answered = store.approval_batches[first.metadata["native_interrupt_id"]]
        outstanding = store.approval_batches[second.metadata["native_interrupt_id"]]
        assert answered.status is ApprovalBatchStatus.RESOLVED
        assert outstanding.status is ApprovalBatchStatus.PENDING

    async def test_answering_both_completes_the_run(self) -> None:
        store, settings, approvals, run_id, pending = await self._parked_run()

        for approval in pending:
            await self._answer(approvals, approval_id=approval.approval_id)
            await self._drain(store, settings)

        events = [e.event_type for e in store.events_by_run[run_id]]
        assert "run_failed" not in events, events
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        assert "run_completed" in events

    async def test_either_answer_order_works(self) -> None:
        store, settings, approvals, run_id, pending = await self._parked_run()

        for approval in reversed(pending):
            await self._answer(approvals, approval_id=approval.approval_id)
            await self._drain(store, settings)

        assert "run_failed" not in [e.event_type for e in store.events_by_run[run_id]]
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
