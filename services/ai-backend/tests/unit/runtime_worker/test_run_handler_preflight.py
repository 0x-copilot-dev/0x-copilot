"""LITELLM Slice 3 — worker preflight uses real-message token counting.

Exercises ``RuntimeRunHandler._preflight_budgets`` end-to-end against an
in-memory store with an injected ``TokenCounterPort`` fake, proving:

  - the estimate is built from the REAL first-call messages (a small count
    Allows a run the old ``max_input_tokens * 4`` proxy would have Denied —
    the behavioural-change guard),
  - the no-active-budget path skips tokenization entirely (lazy gate),
  - a raising primary counter falls through to the char/4 heuristic (still a
    real token-shaped estimate, NOT a fail-open Allow),
  - a hard failure in the estimate path fails OPEN (Allow), never blocking a run
    on a transient preflight error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import RuntimeDependencies
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
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

_USER_INPUT = "PREFLIGHT_PROBE: a short user question for token counting."


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
            user_input=_USER_INPUT,
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


async def _allow_invoker(_harness, _messages: Sequence[object]):
    return {"messages": [{"role": "assistant", "content": "Done."}]}


async def _never_invoker(_harness, _messages):  # pragma: no cover - asserted not-called
    raise AssertionError("model invoker must not run when the budget denied the run")


class RecordingTokenCounter:
    """Deterministic ``TokenCounterPort`` fake that records every call."""

    def __init__(self, count: int | None) -> None:
        self._count = count
        self.calls: list[tuple[str, tuple[Mapping[str, str], ...]]] = []

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        self.calls.append((model, tuple(messages)))
        return self._count


class RaisingTokenCounter:
    """``TokenCounterPort`` fake that always raises — exercises the fallback chain."""

    def __init__(self) -> None:
        self.calls = 0

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        self.calls += 1
        raise RuntimeError("token counter unavailable")


def _worker(
    store: InMemoryRuntimeApiStore,
    settings: RuntimeSettings,
    handler: RuntimeRunHandler,
) -> RuntimeWorker:
    return RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=handler,
    )


async def _seed_budget(store: InMemoryRuntimeApiStore, *, limit_tokens: int) -> str:
    budget = await store.create_budget(
        BudgetRecord(
            org_id="org_123",
            user_id="user_123",
            scope=BudgetScope.USER,
            period=BudgetPeriod.DAY,
            enforcement=BudgetEnforcement.HARD,
            limit_micro_usd=None,
            limit_tokens=limit_tokens,
            status=BudgetStatus.ACTIVE,
            created_by_user_id="user_123",
        )
    )
    return budget.id


class TestPreflightRealMessageCount:
    async def test_small_real_count_allows_run_the_proxy_would_have_denied(
        self,
    ) -> None:
        # Old behaviour: input estimate ≈ max_input_tokens (128_000) → ~138k
        # tokens, which busts a 50k cap. New behaviour: the real message count
        # (100) → ~4.2k tokens, comfortably under the cap → Allow.
        store = InMemoryRuntimeApiStore()
        settings = _settings()
        await _seed_budget(store, limit_tokens=50_000)
        run_id = await _seed_run(store, settings)
        counter = RecordingTokenCounter(100)
        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=_agent_factory,
            runtime_invoker=_allow_invoker,
            token_counter=counter,
        )
        processed = await _worker(store, settings, handler).run_until_idle()

        assert processed == 1
        assert store.runs[run_id].status == AgentRunStatus.COMPLETED
        # The counter saw the REAL first-call messages (the seeded user input).
        assert counter.calls, "token counter was never invoked"
        _model, messages = counter.calls[0]
        joined = " ".join(m.get("content", "") for m in messages)
        assert _USER_INPUT in joined


class TestPreflightNoBudgetSkipsTokenization:
    async def test_no_active_budget_never_tokenizes(self) -> None:
        store = InMemoryRuntimeApiStore()
        settings = _settings()
        run_id = await _seed_run(store, settings)  # no budgets seeded
        counter = RecordingTokenCounter(100)
        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=_agent_factory,
            runtime_invoker=_allow_invoker,
            token_counter=counter,
        )
        processed = await _worker(store, settings, handler).run_until_idle()

        assert processed == 1
        assert store.runs[run_id].status == AgentRunStatus.COMPLETED
        # Lazy gate: with no active budgets the estimate is never resolved, so
        # the counter is never called (no message read, no tokenization).
        assert counter.calls == []


class TestPreflightFallbackChain:
    async def test_raising_primary_counter_falls_to_char_heuristic(self) -> None:
        # A tiny 1-token cap: if the char/4 fallback is consulted, its non-zero
        # count busts the cap → Deny. If the handler instead swallowed the raise
        # into a fail-open Allow, the run would complete — so a Deny here proves
        # the char heuristic really ran.
        store = InMemoryRuntimeApiStore()
        settings = _settings()
        await _seed_budget(store, limit_tokens=1)
        run_id = await _seed_run(store, settings)
        counter = RaisingTokenCounter()
        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=_agent_factory,
            runtime_invoker=_never_invoker,
            token_counter=counter,
        )
        processed = await _worker(store, settings, handler).run_until_idle()

        assert processed == 1
        assert counter.calls == 1
        assert store.runs[run_id].status == AgentRunStatus.FAILED
        event_types = [e.event_type for e in store.events_by_run[run_id]]
        assert RuntimeApiEventType.RUN_REJECTED in event_types
        assert RuntimeApiEventType.RUN_STARTED not in event_types

    async def test_hard_estimate_failure_fails_open(self) -> None:
        # A transient failure while building the estimate (e.g. message read /
        # pricing lookup down) must Allow the run, never block it — even under a
        # 1-token cap that would otherwise Deny.
        store = InMemoryRuntimeApiStore()
        settings = _settings()
        await _seed_budget(store, limit_tokens=1)
        run_id = await _seed_run(store, settings)
        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=_agent_factory,
            runtime_invoker=_allow_invoker,
        )

        async def _boom(_run, _command):
            raise RuntimeError("estimate assembly failed")

        handler._build_preflight_estimate = _boom  # type: ignore[method-assign]
        processed = await _worker(store, settings, handler).run_until_idle()

        assert processed == 1
        assert store.runs[run_id].status == AgentRunStatus.COMPLETED
        event_types = [e.event_type for e in store.events_by_run[run_id]]
        assert RuntimeApiEventType.RUN_REJECTED not in event_types
