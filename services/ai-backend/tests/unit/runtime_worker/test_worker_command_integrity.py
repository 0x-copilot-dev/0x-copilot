"""Worker command integrity (tenant-aligned payloads).

Plan 04: handlers must reject or ignore forged queue commands that disagree with
persisted run rows for org/user/conversation.
"""

from __future__ import annotations


import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType, RuntimeCancelCommand
from runtime_worker.handlers.cancel import RuntimeCancelHandler
from runtime_worker.handlers.run import RuntimeRunHandler

from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers, _TestSettings


def _fake_agent_factory(
    *,
    context: AgentRuntimeContext,
    dependencies: RuntimeDependencies,
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


async def _fake_invoker(*args: object, **kwargs: object) -> object:
    return {"messages": [{"role": "assistant", "content": "ok"}]}


async def test_run_handler_rejects_forged_conversation_id_on_command() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    await _TestHelpers.create_queued_run(store, settings)
    cmd = store.run_commands[-1].model_copy(update={"conversation_id": "wrong_conv"})
    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        agent_factory=_fake_agent_factory,
        runtime_invoker=_fake_invoker,
        settings=settings,
    )

    with pytest.raises(AgentRuntimeError, match="conversation_id"):
        await handler.handle(cmd)


async def test_cancel_handler_noops_when_requesting_user_not_run_owner() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)
    prior_status = store.runs[run_id].status

    handler = RuntimeCancelHandler(persistence=store, event_store=store)
    bad = RuntimeCancelCommand(
        run_id=run_id,
        org_id="org_123",
        requested_by_user_id="someone_else",
        reason="forge",
    )
    await handler.handle(bad)

    assert store.runs[run_id].status == prior_status


async def test_queued_cancel_derives_scope_without_creating_run_control_snapshot() -> (
    None
):
    """A run cancelled before execution must not gain an F10 control snapshot."""

    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

    class _ScopeOnlyBuilder:
        def subject_fingerprint_for(self, _run: object) -> str:
            return "a" * 64

        async def ensure_snapshot(self, **_kwargs: object) -> object:
            raise AssertionError("queued cancel must not create a snapshot")

    handler = RuntimeCancelHandler(
        persistence=store,
        event_store=store,
        run_control_builder=_ScopeOnlyBuilder(),  # type: ignore[arg-type]
    )
    await handler.handle(
        RuntimeCancelCommand(
            run_id=run_id,
            org_id="org_123",
            requested_by_user_id="user_123",
            reason="user_cancel",
        )
    )

    assert store.runs[run_id].status == "cancelled"


async def test_cancel_retries_only_terminal_projection_after_cancel_is_durable() -> (
    None
):
    """A retry must not append a second cancellation terminal event."""

    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

    class _FlakyTerminal:
        attempts = 0
        run_usage_writes = 0

        async def finalize(self, **_kwargs: object) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("projector temporary failure")

        async def record_run_usage(self, **_kwargs: object) -> int:
            self.run_usage_writes += 1
            return 0

    terminal = _FlakyTerminal()
    handler = RuntimeCancelHandler(
        persistence=store,
        event_store=store,
        model_invocation_terminal=terminal,  # type: ignore[arg-type]
    )
    command = RuntimeCancelCommand(
        run_id=run_id,
        org_id="org_123",
        requested_by_user_id="user_123",
        reason="user_cancel",
    )

    with pytest.raises(RuntimeError, match="temporary"):
        await handler.handle(command)
    await handler.handle(command)

    assert store.runs[run_id].status == "cancelled"
    event_types = [event.event_type for event in store.events_by_run[run_id]]
    assert event_types.count(RuntimeApiEventType.RUN_CANCELLED) == 1
    assert terminal.attempts == 2
    assert terminal.run_usage_writes == 1
