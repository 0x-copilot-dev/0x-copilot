"""B7 — worker handler integration: preflight Deny + post-charge.

Exercises ``RuntimeRunHandler.handle()`` end-to-end against an in-memory
store: a hard-cap budget seeded BEFORE the run executes results in:

  - the run never reaches RUNNING,
  - status flips QUEUED → FAILED,
  - a ``RUN_REJECTED`` event is appended (NOT ``RUN_FAILED``),
  - no model call is invoked.

The Allow path also runs through to verify that observed spend gets
charged against the budget post-completion.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import RuntimeDependencies
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.persistence.records import (
    BudgetEnforcement,
    BudgetPeriod,
    BudgetRecord,
    BudgetScope,
    BudgetStatus,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


async def _seed_run(store: InMemoryRuntimeApiStore, settings: RuntimeSettings) -> str:
    model_resolver = ModelConfigResolver(settings)
    event_producer = RuntimeEventProducer(
        persistence=store, event_store=store, on_event_appended=None
    )
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=event_producer,
        settings=settings,
        model_resolver=model_resolver,
    )
    conv_coordinator = ConversationCoordinator(
        persistence=store, settings=settings, run_coordinator=run_coordinator
    )
    conversation = await conv_coordinator.create_conversation(
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
            user_input="A run that should be rejected by budget.",
            model={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
            },
        )
    )
    return response.run_id


def _agent_factory(*, context, dependencies: RuntimeDependencies) -> RuntimeHarness:
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


async def _never_invoker(_harness, _messages):  # pragma: no cover - asserted not-called
    raise AssertionError(
        "model invoker must not run when the budget pre-flight denied the run"
    )


async def _allow_invoker(_harness, _messages: Sequence[object]):
    return {"messages": [{"role": "assistant", "content": "Done."}]}


class TestBudgetDenyPath:
    async def test_hard_cap_rejects_run_before_model_call(self) -> None:
        store = InMemoryRuntimeApiStore()
        settings = _settings()
        # 1-token cap is impossible to fit any real run inside (the
        # estimator's worst-case is the model's max_input_tokens), so
        # this Denies regardless of whether pricing is seeded.
        await store.create_budget(
            BudgetRecord(
                org_id="org_123",
                user_id="user_123",
                scope=BudgetScope.USER,
                period=BudgetPeriod.DAY,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=None,
                limit_tokens=1,
                status=BudgetStatus.ACTIVE,
                created_by_user_id="user_123",
            )
        )
        run_id = await _seed_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                agent_factory=_agent_factory,
                runtime_invoker=_never_invoker,
            ),
        )
        processed = await worker.run_until_idle()
        assert processed == 1

        run = store.runs[run_id]
        assert run.status == AgentRunStatus.FAILED
        event_types = [e.event_type for e in store.events_by_run[run_id]]
        assert RuntimeApiEventType.RUN_REJECTED in event_types
        # Crucially distinct from RUN_FAILED so the UI shows the right
        # message; RUN_FAILED is NOT emitted on the budget reject path.
        assert RuntimeApiEventType.RUN_FAILED not in event_types
        # Ledger never advanced past RUN_QUEUED → RUN_REJECTED.
        assert RuntimeApiEventType.RUN_STARTED not in event_types


class TestBudgetAllowPath:
    async def test_allow_path_runs_to_completion_and_charges(self) -> None:
        store = InMemoryRuntimeApiStore()
        settings = _settings()
        budget = await store.create_budget(
            BudgetRecord(
                org_id="org_123",
                user_id="user_123",
                scope=BudgetScope.USER,
                period=BudgetPeriod.DAY,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=None,
                limit_tokens=10_000_000_000,  # generous
                status=BudgetStatus.ACTIVE,
                created_by_user_id="user_123",
            )
        )
        run_id = await _seed_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                agent_factory=_agent_factory,
                runtime_invoker=_allow_invoker,
            ),
        )
        processed = await worker.run_until_idle()
        assert processed == 1
        assert store.runs[run_id].status == "completed"
        # State row created with last_charged_run_id = run_id (idempotency
        # guard — confirms the post-run charge fired).
        keys_for_budget = [key for key in store.budget_states if key[0] == budget.id]
        assert len(keys_for_budget) == 1
        state = store.budget_states[keys_for_budget[0]]
        assert state.last_charged_run_id == run_id
