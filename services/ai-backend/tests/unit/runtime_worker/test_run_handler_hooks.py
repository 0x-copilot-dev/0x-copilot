"""``run.start`` / ``run.end`` fire from the real worker, not from a fake.

The chain under test is the shipped one: queue → :class:`RuntimeWorker` →
``RuntimeRunHandler.handle`` → ``RuntimeHookContext.bind_for_run`` →
``HookDispatch.observe``. Nothing here constructs the hook session itself, so
the test fails if the handler stops binding it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import RuntimeDependencies
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.hooks import HookPhase, RuntimeHooks
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker


@pytest.fixture(autouse=True)
def _clean_hook_table():
    RuntimeHooks.clear()
    try:
        yield
    finally:
        RuntimeHooks.clear()


class WorkerRunMixin:
    """Seeds and drives one run through the real queue → worker → handler path."""

    SUMMARY_PREFIX = "runtime_hooks.run_summary"
    RUN_LOGGER = "runtime_worker.handlers.run"

    @staticmethod
    def settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    @staticmethod
    def agent_factory(
        *, context, dependencies: RuntimeDependencies
    ) -> RuntimeHarness:
        return RuntimeHarness(
            agent=object(),
            context=context,
            dependencies=dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )

    @staticmethod
    async def ok_invoker(_harness, _messages: Sequence[object]):
        return {"messages": [{"role": "assistant", "content": "Done."}]}

    @staticmethod
    async def failing_invoker(_harness, _messages: Sequence[object]):
        raise RuntimeError("model exploded")

    @classmethod
    async def seed_run(
        cls,
        store: InMemoryRuntimeApiStore,
        settings: RuntimeSettings,
    ) -> str:
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
        conversation = await ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        ).create_conversation(
            CreateConversationRequest(
                org_id="org_123",
                user_id="user_123",
                assistant_id="assistant_123",
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="A run whose lifecycle hooks must fire.",
                model={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128_000,
                },
            )
        )
        return response.run_id

    @classmethod
    async def drive_run(
        cls, invoker
    ) -> tuple[str, InMemoryRuntimeApiStore]:
        """Drive one run to completion; return its id and the store."""

        store = InMemoryRuntimeApiStore()
        settings = cls.settings()
        run_id = await cls.seed_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                agent_factory=cls.agent_factory,
                runtime_invoker=invoker,
            ),
        )
        assert await worker.run_until_idle() == 1
        return (run_id, store)

    @classmethod
    async def observed_lifecycle(
        cls, invoker
    ) -> list[tuple[HookPhase, str, str | None]]:
        """Register a lifecycle probe, drive a run, return what it saw."""

        observed: list[tuple[HookPhase, str, str | None]] = []

        def record(payload) -> None:
            observed.append((payload.phase, payload.run_id, payload.status))
            return None

        RuntimeHooks.register(phase=HookPhase.RUN_START, name="probe", handler=record)
        RuntimeHooks.register(phase=HookPhase.RUN_END, name="probe", handler=record)
        run_id, _ = await cls.drive_run(invoker)
        assert all(entry[1] == run_id for entry in observed)
        return observed

    @classmethod
    def summaries(
        cls, caplog: pytest.LogCaptureFixture
    ) -> list[logging.LogRecord]:
        return [
            record
            for record in caplog.records
            if record.getMessage().startswith(cls.SUMMARY_PREFIX)
        ]

    @staticmethod
    def exploding_hook(_payload) -> None:
        raise RuntimeError("plugin bug")


class TestRunLifecycleHooks(WorkerRunMixin):
    async def test_start_and_end_fire_around_a_completed_run(self) -> None:
        observed = await self.observed_lifecycle(self.ok_invoker)

        assert [entry[0] for entry in observed] == [
            HookPhase.RUN_START,
            HookPhase.RUN_END,
        ]
        assert observed[0][2] is None
        assert observed[1][2] == AgentRunStatus.COMPLETED.value

    async def test_end_still_fires_and_reports_failure(self) -> None:
        observed = await self.observed_lifecycle(self.failing_invoker)

        assert [entry[0] for entry in observed] == [
            HookPhase.RUN_START,
            HookPhase.RUN_END,
        ]
        assert observed[1][2] == AgentRunStatus.FAILED.value

    async def test_a_raising_lifecycle_hook_does_not_fail_the_run(self) -> None:
        RuntimeHooks.register(
            phase=HookPhase.RUN_START, name="broken", handler=self.exploding_hook
        )

        run_id, store = await self.drive_run(self.ok_invoker)

        assert store.runs[run_id].status == AgentRunStatus.COMPLETED


class TestHookLedgerIsConsumed(WorkerRunMixin):
    """The per-invocation ledger has a real reader on the shipped run path.

    Without this the dispatcher would be writing records nothing ever reads —
    a mechanism that exists and is unreachable, which is the failure mode this
    seam is least allowed to have.
    """

    async def test_a_failing_hook_is_summarized_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        RuntimeHooks.register(
            phase=HookPhase.RUN_START, name="broken", handler=self.exploding_hook
        )
        RuntimeHooks.register(
            phase=HookPhase.RUN_END, name="quiet", handler=lambda _payload: None
        )

        with caplog.at_level(logging.INFO, logger=self.RUN_LOGGER):
            await self.drive_run(self.ok_invoker)

        summaries = self.summaries(caplog)
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.WARNING
        fields = summaries[0].args[1]
        assert fields["hook_invocations"] == 2
        assert fields["hook_failed"] == 1
        assert fields["hook_by_status"] == {"failed": 1, "ok": 1}
        assert fields["hook_by_phase"] == {"run.end": 1, "run.start": 1}
        # No hook-authored text reaches the operator's logs.
        assert "plugin bug" not in caplog.text

    async def test_an_unhooked_run_logs_no_summary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=self.RUN_LOGGER):
            await self.drive_run(self.ok_invoker)

        assert not self.summaries(caplog)
