"""Hermetic end-to-end: a subagent that CALLS A TOOL must not deadlock its run.

Every existing subagent test stops one step short of the defect. The real-graph
one (``test_stop_cancels_subagent``) blocks the *subagent's model call*, so the
child never reaches a tool call at all; the middleware and concurrency suites
drive the admission gate directly, one call at a time, with no nesting. 754
tests were green while this path was 100% broken in production.

The missing shape is nesting: the ``task`` tool is itself a graph-visible tool
call, so it takes the run's exclusive admission permit and then awaits a child
graph whose own tool calls reach the *same* run-scoped, non-reentrant gate. The
child waits for a lock its own parent holds, and the run makes no further
progress until the run timeout fires.

So this drives the **real** worker, the **real** Deep Agents graph (real ``task``
tool, real built-in ``general-purpose`` subagent, real ``TodoListMiddleware``),
and the **real** streaming executor, with only the chat model faked. The
supervisor is scripted to delegate; the child is scripted to call a tool. The
assertion is the plainest one available: the run completes, and it completes
promptly.
"""

from __future__ import annotations

import asyncio
import json
import time

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

#: Present in the supervisor's user message and absent from the subagent's task
#: description, which is how the fake model fans out exactly once instead of
#: recursing: the parent delegates, the child calls an ordinary tool.
_DELEGATE_TRIGGER = "DELEGATE-THEN-USE-A-TOOL"

#: Short enough that a deadlocked run is observed as a failure in seconds rather
#: than at the 180s production default, and far longer than the ~1s a healthy
#: run of this shape actually takes.
_RUN_TIMEOUT_SECONDS = 20.0

#: Outer bound on the whole worker drive. A run that neither completes nor times
#: out is still a bug, and this keeps it from hanging the suite.
_DRIVE_TIMEOUT_SECONDS = 90.0

_CHILD_TODOS = [{"content": "Read the launch checklist", "status": "in_progress"}]


class SubagentToolCallRunMixin:
    """Script one supervisor ``task`` dispatch whose child calls a real tool."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
                "RUNTIME_DEFAULT_TIMEOUT_SECONDS": str(_RUN_TIMEOUT_SECONDS),
                "SURFACES_V2": "false",
            }
        )

    @staticmethod
    def _script_delegation(
        monkeypatch,
        *,
        subagent_tool: str,
        subagent_args: dict,
        delegations: int = 1,
    ) -> None:
        """Fan out to ``task`` on the parent turn; call a tool on the child turn.

        ``parallel_trigger`` is the fake model's own scoping mechanism: the
        supervisor's human message carries the trigger and emits the delegation,
        while the child's prompt (its task description) does not, so the child
        falls through to the single scripted call instead of delegating again.
        """

        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_CALLS", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_NAME", subagent_tool)
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_ARGS", json.dumps(subagent_args))
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_PARALLEL_TRIGGER", _DELEGATE_TRIGGER)
        monkeypatch.setenv(
            "RUNTIME_FAKE_MODEL_PARALLEL_TOOL_CALLS",
            json.dumps(
                [
                    {
                        "name": "task",
                        "args": {
                            "description": f"Research launch topic {index}.",
                            "subagent_type": "general-purpose",
                        },
                    }
                    for index in range(delegations)
                ]
            ),
        )

    @staticmethod
    async def _enqueue_run(
        store: InMemoryRuntimeApiStore, settings: RuntimeSettings
    ) -> str:
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversation = await ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        ).create_conversation(
            CreateConversationRequest(
                org_id="org_123", user_id="user_123", assistant_id="assistant_123"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input=f"{_DELEGATE_TRIGGER}: summarize launch risks.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return response.run_id

    @classmethod
    async def _drive(
        cls, store: InMemoryRuntimeApiStore, settings: RuntimeSettings
    ) -> tuple[str, float]:
        """Run the real worker to idle, returning the run id and wall-clock seconds."""

        run_id = await cls._enqueue_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        started = time.monotonic()
        processed = await asyncio.wait_for(
            worker.run_until_idle(), timeout=_DRIVE_TIMEOUT_SECONDS
        )
        elapsed = time.monotonic() - started
        assert processed == 1
        return run_id, elapsed


class TestASubagentThatCallsAToolDoesNotDeadlockTheRun(SubagentToolCallRunMixin):
    async def test_the_run_completes_instead_of_timing_out(self, monkeypatch) -> None:
        self._script_delegation(
            monkeypatch,
            subagent_tool="write_todos",
            subagent_args={"todos": _CHILD_TODOS},
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()

        run_id, elapsed = await self._drive(store, settings)

        names = [event.event_type for event in store.events_by_run[run_id]]

        # The supervisor really did delegate — otherwise the nesting this test
        # exists for never happened and a pass would be vacuous.
        assert "subagent_started" in names, names
        # The child really did call a tool. ``write_todos`` is projected into a
        # checklist snapshot, so its presence is proof the child's tool body ran
        # rather than parking forever on the parent's admission permit.
        assert "todo_list_updated" in names, names

        assert "run_failed" not in names, names
        assert "run_completed" in names, names
        # A deadlocked run only ends when the run timeout fires; a healthy one of
        # this shape finishes in about a second.
        assert elapsed < _RUN_TIMEOUT_SECONDS, elapsed

    async def test_two_subagents_that_each_call_a_tool_still_complete(
        self, monkeypatch
    ) -> None:
        """The fleet shape: sibling delegations must not deadlock each other either."""

        self._script_delegation(
            monkeypatch,
            subagent_tool="write_todos",
            subagent_args={"todos": _CHILD_TODOS},
            delegations=2,
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()

        run_id, elapsed = await self._drive(store, settings)

        names = [event.event_type for event in store.events_by_run[run_id]]
        assert names.count("subagent_started") == 2, names
        assert "todo_list_updated" in names, names
        assert "run_failed" not in names, names
        assert "run_completed" in names, names
        assert elapsed < _RUN_TIMEOUT_SECONDS, elapsed
