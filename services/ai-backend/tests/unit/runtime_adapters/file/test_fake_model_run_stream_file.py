"""Hermetic end-to-end on the FILE store — the desktop DEFAULT (AC2b).

Tier A (``test_fake_model_run_stream``) proved a real run streams to completion
through the real worker/graph/streamer — but only against the in-memory store.
The file-native store is the desktop *default*, yet nothing drove a real run
through it: a file-store execution or persistence regression would ship silently.

This drives an actual queued run through the real worker + real Deep Agents graph
+ real streaming executor against a real ``FileRuntimeApiStore`` (fake model only,
no key, no network), then **reopens a fresh store at the same root** and asserts
the streamed run persisted durably to disk — the property that makes the default
store trustworthy.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)
from runtime_adapters.factory import RuntimeAdapterFactory, RuntimePorts
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

_ORG = "org_123"
_USER = "user_123"


class FileStoreRunMixin:
    @staticmethod
    def _settings(root: Path) -> RuntimeSettings:
        # File backend, single_user_desktop, and deliberately NO provider key —
        # the fake-model gate bypass must let run-create + execution succeed.
        return RuntimeSettings.load(
            environ={
                "RUNTIME_STORE_BACKEND": "file",
                "RUNTIME_FILE_STORE_ROOT": str(root),
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
            }
        )

    @staticmethod
    def _ports(settings: RuntimeSettings) -> RuntimePorts:
        return RuntimeAdapterFactory.from_settings(settings)

    @classmethod
    async def _enqueue_run(
        cls, ports: RuntimePorts, settings: RuntimeSettings
    ) -> tuple[str, str]:
        """Create a conversation + a queued run; return (conversation_id, run_id)."""
        store = ports.persistence  # FileRuntimeApiStore also IS event_store + queue
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=ports.event_store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=ports.queue,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant_123"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="Say hello.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return conversation.conversation_id, response.run_id

    @staticmethod
    async def _run_worker(ports: RuntimePorts, settings: RuntimeSettings) -> int:
        worker = RuntimeWorker(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            # Wire the real DynamicMcpRegistry exactly as the in-process worker
            # does (app.py) so the graph builds identically to production.
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        return await worker.run_until_idle()


class TestFakeModelRunStreamFileStore(FileStoreRunMixin):
    async def test_real_run_streams_and_persists_on_file_store(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, PROFILE_SINGLE_USER_DESKTOP)
        root = tmp_path / "agent-data"

        # --- run against a real file store ---
        settings = self._settings(root)
        ports = self._ports(settings)
        await ports.lifecycle.open()
        try:
            conversation_id, run_id = await self._enqueue_run(ports, settings)
            processed = await self._run_worker(ports, settings)
            assert processed == 1

            events = await ports.event_store.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=0
            )
            names = [e.event_type for e in events]
            assert "run_failed" not in names, names
            assert "run_started" in names
            assert names.count("model_delta") >= 1, names
            assert "final_response" in names
            assert "run_completed" in names
            assert (
                names.index("run_started")
                < names.index("model_delta")
                < names.index("final_response")
                < names.index("run_completed")
            ), names
            assert any(
                n in ("reasoning_summary", "reasoning_summary_delta") for n in names
            ), names
        finally:
            await ports.lifecycle.close()

        # --- reopen a FRESH store at the same root: the run must be durable ---
        reopened = self._ports(self._settings(root))
        await reopened.lifecycle.open()
        try:
            replayed = await reopened.event_store.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=0
            )
            replayed_names = [e.event_type for e in replayed]
            assert "run_completed" in replayed_names, replayed_names
            assert replayed_names.count("model_delta") >= 1, replayed_names
            # Monotonic sequence integrity survived the reopen.
            seqs = [e.sequence_no for e in replayed]
            assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)), seqs

            messages = await reopened.persistence.list_messages(
                org_id=_ORG, conversation_id=conversation_id, limit=50
            )
            assistant = [m for m in messages if m.role == "assistant"]
            assert assistant, "assistant message did not persist to the file store"
            assert "fake model" in (assistant[0].content_text or "").lower()
        finally:
            await reopened.lifecycle.close()
