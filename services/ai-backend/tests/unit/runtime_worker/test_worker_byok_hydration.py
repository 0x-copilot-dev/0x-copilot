"""BYOK Phase-2 — worker re-hydrates user provider keys at claim time.

The queue payload round-trips through JSON, which (by design) drops the
serialization-excluded ``AgentRuntimeContext.provider_keys`` field. The run
handler must re-fetch the policy snapshot and hand the harness factory a
context that carries the keys in memory — while the key value still never
lands in events, messages, persisted records, or logs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker

_ORG_ID = "org_byok_worker"
_USER_ID = "user_byok_worker"
_SECRET_KEY = "sk-unit-test-byok-worker-secret-00000000"


class FakePoliciesResolver:
    """Snapshot resolver used by both run-create and worker hydration."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, object]:
        self.calls.append((org_id, user_id))
        return {"privacy": {}, "provider_keys": {"openai": _SECRET_KEY}}


class WorkerByokMixin:
    """Fixture: queued run created with a user key and NO env provider keys."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    async def _create_queued_run(
        self,
        store: InMemoryRuntimeApiStore,
        settings: RuntimeSettings,
        resolver: FakePoliciesResolver,
    ) -> str:
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings=settings),
            user_policies_resolver=resolver,
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID, user_id=_USER_ID, assistant_id="assistant_byok"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return response.run_id


class TestWorkerHydratesProviderKeys(WorkerByokMixin):
    async def test_harness_context_carries_keys_after_queue_round_trip(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        resolver = FakePoliciesResolver()
        run_id = await self._create_queued_run(store, settings, resolver)

        captured_contexts: list[AgentRuntimeContext] = []

        def fake_agent_factory(
            *,
            context: AgentRuntimeContext,
            dependencies: RuntimeDependencies,
        ) -> RuntimeHarness:
            captured_contexts.append(context)
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

        async def fake_invoker(
            _harness: RuntimeHarness, _messages: Sequence[object]
        ) -> object:
            return {"messages": [{"role": "assistant", "content": "done"}]}

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                settings=settings,
                agent_factory=fake_agent_factory,
                runtime_invoker=fake_invoker,
                user_policies_resolver=resolver,
            ),
        )

        with caplog.at_level(logging.DEBUG):
            processed = await worker.run_until_idle()

        assert processed == 1
        assert store.runs[run_id].status == "completed"
        # Hydration restored the keys the JSON queue hop dropped.
        assert captured_contexts[0].provider_keys == {"openai": _SECRET_KEY}
        # Resolver fired at run-create AND at worker claim time.
        assert resolver.calls == [(_ORG_ID, _USER_ID), (_ORG_ID, _USER_ID)]

        # Redaction sweep across every surface the run produced.
        for event in store.events_by_run[run_id]:
            assert _SECRET_KEY not in event.model_dump_json()
        for message in store.messages.values():
            assert _SECRET_KEY not in message.model_dump_json()
        assert _SECRET_KEY not in store.runs[run_id].runtime_context.model_dump_json()
        assert _SECRET_KEY not in json.dumps(store.audit_log, default=str)
        assert _SECRET_KEY not in caplog.text

    async def test_no_resolver_leaves_context_without_keys(self) -> None:
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        run_id = await self._create_queued_run(store, settings, FakePoliciesResolver())

        captured_contexts: list[AgentRuntimeContext] = []

        def fake_agent_factory(
            *,
            context: AgentRuntimeContext,
            dependencies: RuntimeDependencies,
        ) -> RuntimeHarness:
            captured_contexts.append(context)
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

        async def fake_invoker(
            _harness: RuntimeHarness, _messages: Sequence[object]
        ) -> object:
            return {"messages": [{"role": "assistant", "content": "done"}]}

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                settings=settings,
                agent_factory=fake_agent_factory,
                runtime_invoker=fake_invoker,
            ),
        )

        processed = await worker.run_until_idle()

        assert processed == 1
        assert store.runs[run_id].status == "completed"
        # Without a wired resolver the queue hop's key drop is permanent —
        # the run falls back to deployment env keys (pre-BYOK behaviour).
        assert captured_contexts[0].provider_keys == {}
