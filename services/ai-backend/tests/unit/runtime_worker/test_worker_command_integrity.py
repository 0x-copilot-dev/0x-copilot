"""Worker command integrity (tenant-aligned payloads).

Plan 04: handlers must reject or ignore forged queue commands that disagree with
persisted run rows for org/user/conversation.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeCancelCommand
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


def test_run_handler_rejects_forged_conversation_id_on_command() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    _TestHelpers.create_queued_run(store, settings)
    cmd = store.run_commands[-1].model_copy(update={"conversation_id": "wrong_conv"})
    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        agent_factory=_fake_agent_factory,
        runtime_invoker=_fake_invoker,
        settings=settings,
    )

    with pytest.raises(AgentRuntimeError, match="conversation_id"):
        asyncio.run(handler.handle(cmd))


def test_cancel_handler_noops_when_requesting_user_not_run_owner() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = _TestHelpers.create_queued_run(store, settings)
    prior_status = store.runs[run_id].status

    handler = RuntimeCancelHandler(persistence=store, event_store=store)
    bad = RuntimeCancelCommand(
        run_id=run_id,
        org_id="org_123",
        requested_by_user_id="someone_else",
        reason="forge",
    )
    asyncio.run(handler.handle(bad))

    assert store.runs[run_id].status == prior_status
