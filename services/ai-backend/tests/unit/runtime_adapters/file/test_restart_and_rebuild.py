"""Durability: reopen a fresh store instance and rebuild the disposable index.

Covers the DoD restart test: append events across the main stream plus two
subagent streams, reopen a fresh :class:`FileRuntimeApiStore` against the same
root, and read back byte-identical envelopes in the correct order. Then delete
``index/`` and reopen — the catalog rebuilds from the JSONL and the reads are
identical, proving the index is disposable and canonical data lives in JSONL.
"""

from __future__ import annotations

import shutil

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_file"
_USER = "user_file"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


async def _seed_run(store: FileRuntimeApiStore):
    """Create a conversation + run through the real coordinators."""

    settings = _settings()
    resolver = ModelConfigResolver(settings)
    event_producer = RuntimeEventProducer(
        persistence=store, event_store=store, on_event_appended=None
    )
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=event_producer,
        settings=settings,
        model_resolver=resolver,
    )
    conv_coordinator = ConversationCoordinator(
        persistence=store, settings=settings, run_coordinator=run_coordinator
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(org_id=_ORG, user_id=_USER, assistant_id="assistant")
    )
    run = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input="Hello",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


def _draft(*, run, conversation_id, task_id):
    source = StreamEventSource.SUBAGENT if task_id else StreamEventSource.MAIN_AGENT
    event_type = (
        RuntimeApiEventType.SUBAGENT_PROGRESS
        if task_id
        else RuntimeApiEventType.MODEL_DELTA
    )
    return RuntimeEventDraft(
        org_id=_ORG,
        run_id=run.run_id,
        conversation_id=conversation_id,
        trace_id="trace_file",
        source=source,
        event_type=event_type,
        task_id=task_id,
        summary=f"chunk-{task_id or 'main'}",
    )


async def _append_three_streams(store, *, run, conversation_id) -> None:
    """Interleave main + two subagent streams so sequence_no is shared."""

    order = [None, "task-a", "task-b", None, "task-a", "task-b"]
    for task_id in order:
        await store.append_event(
            _draft(run=run, conversation_id=conversation_id, task_id=task_id)
        )


class TestFileStoreRestartAndRebuild:
    async def test_reopen_reads_identical_events_in_order(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_run(store)
        await _append_three_streams(
            store, run=run, conversation_id=conversation.conversation_id
        )

        golden = [
            e.model_dump(mode="json")
            for e in await store.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        ]
        # Sequence is strictly monotonic and gap-free across all three streams.
        sequences = [e["sequence_no"] for e in golden]
        assert sequences == list(range(1, len(golden) + 1))
        # The last six events are our interleaved streams, correctly routed.
        tail = golden[-6:]
        assert [e["task_id"] for e in tail] == [
            None,
            "task-a",
            "task-b",
            None,
            "task-a",
            "task-b",
        ]
        latest = await store.get_latest_sequence(run_id=run.run_id)
        assert latest == len(golden)
        await store.close()

        # 1) Reopen a *fresh* instance against the same root.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        after_restart = [
            e.model_dump(mode="json")
            for e in await reopened.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        ]
        assert after_restart == golden
        assert await reopened.get_latest_sequence(run_id=run.run_id) == len(golden)
        # Cross-stream cursor replay: after_sequence skips the prefix.
        partial = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=len(golden) - 3
        )
        assert [e.sequence_no for e in partial] == [
            len(golden) - 2,
            len(golden) - 1,
            len(golden),
        ]
        await reopened.close()

        # 2) Delete the disposable index and reopen -> rebuilt from JSONL.
        shutil.rmtree(root / "index")
        assert not (root / "index").exists()
        rebuilt = FileRuntimeApiStore(root)
        await rebuilt.open()
        after_rebuild = [
            e.model_dump(mode="json")
            for e in await rebuilt.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        ]
        assert after_rebuild == golden
        await rebuilt.close()

    async def test_reopen_restores_conversations_and_messages(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_run(store)
        conv_id = conversation.conversation_id
        messages_before = await store.list_messages(
            org_id=_ORG, conversation_id=conv_id, limit=50
        )
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        got_conv = await reopened.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conv_id
        )
        assert got_conv is not None
        assert got_conv.conversation_id == conv_id
        listed = await reopened.list_conversations(org_id=_ORG, user_id=_USER, limit=50)
        assert any(c.conversation_id == conv_id for c in listed)
        got_run = await reopened.get_run(org_id=_ORG, run_id=run.run_id)
        assert got_run is not None
        messages_after = await reopened.list_messages(
            org_id=_ORG, conversation_id=conv_id, limit=50
        )
        assert [m.model_dump(mode="json") for m in messages_after] == [
            m.model_dump(mode="json") for m in messages_before
        ]
        await reopened.close()
