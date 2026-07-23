"""PRD-08 D1 — the Activity meta counters projected onto the run-history page.

`ConversationQueryService.list_run_history` attaches
``step_count`` / ``connector_count`` / ``pending_approval_count`` from two grouped
aggregates over ``runtime_tool_invocations`` / ``runtime_approval_requests``. A run
with tool invocations reports real counts; a run with NONE reports ``None`` (never
``0``) so the client omits the meta clause rather than lying about a run it can't
measure.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.persistence.records import ToolInvocationRecord
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    ApprovalRequestRecord,
    CreateConversationRequest,
    CreateRunRequest,
)

_ORG = "org_meta"
_USER = "user_meta"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


@pytest.fixture
async def store():
    instance = InMemoryRuntimeApiStore()
    await instance.open()
    try:
        yield instance
    finally:
        await instance.close()


def _cqs(store) -> ConversationQueryService:
    settings = _settings()
    return ConversationQueryService(
        persistence=store,
        event_store=store,
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )


async def _new_run(store, *, idem):
    settings = _settings()
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
        CreateConversationRequest(org_id=_ORG, user_id=_USER, assistant_id="assistant")
    )
    run = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input="hello",
            idempotency_key=idem,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


async def test_run_with_invocations_and_pending_approval_projects_counts(store):
    conversation, run = await _new_run(store, idem="meta-1")

    # 7 tool invocations across 4 distinct connectors (native tools → None).
    connectors = ["sheets", "safe", "dune", "sheets", "docs", None, None]
    for i, slug in enumerate(connectors):
        await store.record_tool_invocation(
            ToolInvocationRecord(
                run_id=run.run_id,
                org_id=_ORG,
                tool_name=f"tool_{i}",
                connector_slug=slug,
                call_id=f"call_{i}",
            )
        )
    await store.create_approval_request(
        record=ApprovalRequestRecord(
            approval_id="appr_meta_1",
            run_id=run.run_id,
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            metadata={"message": "approve a swap", "risk_level": "low"},
        )
    )

    page = await _cqs(store).list_run_history(org_id=_ORG, user_id=_USER)
    entry = next(e for e in page.runs if e.run_id == run.run_id)
    assert entry.step_count == 7
    assert entry.connector_count == 4
    assert entry.pending_approval_count == 1


async def test_run_without_invocations_projects_none_not_zero(store):
    _, run = await _new_run(store, idem="meta-2")

    page = await _cqs(store).list_run_history(org_id=_ORG, user_id=_USER)
    entry = next(e for e in page.runs if e.run_id == run.run_id)
    # None (unknown), NEVER 0 — the client omits the clause for such a run.
    assert entry.step_count is None
    assert entry.connector_count is None
    # No pending approvals is a FACT (approvals persist since 0001), so 0 here.
    assert entry.pending_approval_count == 0
