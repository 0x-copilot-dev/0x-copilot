"""Hermetic end-to-end: a REAL run streams to completion via the fake model.

This is the keystone the AC2b worker-gate escape needed and no test had: it
drives an actual queued run through the **real** worker, the **real** Deep
Agents graph, and the **real** streaming executor — only the concrete chat
model is the deterministic fake (env-gated at the single construction funnel).
No network, no provider key. It asserts the streamed event sequence
(``run_started`` → ``model_delta`` → reasoning → ``final_response`` →
``run_completed``), so a wiring defect that stops runs from executing/streaming
fails here instead of reaching a user.

It also proves the credential-gate bypass: the settings carry NO provider key,
yet run-create and execution both succeed under ``RUNTIME_FAKE_MODEL``.
"""

from __future__ import annotations

import inspect

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker


class FakeModelRunMixin:
    """Build an in-memory store, enqueue a real run, run the real worker."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        # Deliberately NO provider key — the fake-model gate bypass must let
        # run-create and execution succeed regardless.
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
            }
        )

    @classmethod
    async def _enqueue_run(
        cls, store: InMemoryRuntimeApiStore, settings: RuntimeSettings
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
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id="org_123", user_id="user_123", assistant_id="assistant_123"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="Say hello.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return response.run_id


class TestFakeModelRunStream(FakeModelRunMixin):
    async def test_real_run_streams_to_completion_via_fake_model(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        run_id = await self._enqueue_run(store, settings)

        # Real worker → real default handler → real graph → real streamer. Pass
        # the MCP discovery cache exactly as the in-process worker does
        # (app.py), so the real DynamicMcpRegistry is wired.
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        processed = await worker.run_until_idle()

        assert processed == 1
        names = [event.event_type for event in store.events_by_run[run_id]]

        # The run executed and streamed — not queued-and-hung, not failed.
        assert "run_failed" not in names, names
        assert "run_started" in names
        assert names.count("model_delta") >= 1, names
        assert "final_response" in names
        assert "run_completed" in names

        # Correct streamed ordering.
        assert (
            names.index("run_started")
            < names.index("model_delta")
            < names.index("final_response")
            < names.index("run_completed")
        ), names

        # Reasoning/thinking was streamed too.
        assert any(
            n in ("reasoning_summary", "reasoning_summary_delta") for n in names
        ), names

        # The assistant message persisted with the fake's deterministic text.
        assistant = [m for m in store.messages.values() if m.role == "assistant"]
        assert assistant, "no assistant message persisted"
        assert "fake model" in (assistant[0].content_text or "").lower()

    async def test_run_never_completes_without_a_worker(self, monkeypatch) -> None:
        """The AC2b escape, guarded: no executor ⇒ the run does NOT stream/complete."""
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        run_id = await self._enqueue_run(store, settings)

        # No worker is run — exactly the desktop state before the AC2b fix.
        names = [event.event_type for event in store.events_by_run[run_id]]
        assert "run_completed" not in names, names
        assert "model_delta" not in names, names


async def test_empty_mcp_registry_honors_async_contract() -> None:
    """Regression: the no-provider registry must be awaitable (factory gathers it)."""
    from runtime_worker.dependencies import EmptyMcpRegistry

    pending = EmptyMcpRegistry().list_available_servers(None)
    assert inspect.isawaitable(pending)
    assert await pending == ()
