"""Fail-closed JSONL reads: torn-final tolerated, interior corruption raises.

Bug 1 (data loss): ``JsonlIo.iter_lines`` used to ``break`` on the first
malformed line, silently dropping every valid record after an *interior* corrupt
line and returning a truncated prefix with no error — the opposite of
fail-closed. These tests pin the corrected contract:

* a **torn final line** (an incomplete last append from a crash) is still
  tolerated and dropped;
* an **interior** malformed line — one with committed records after it — makes
  the read raise :class:`JsonlCorruptionError` rather than return a truncated
  prefix, threaded through the file store replay path (``events.jsonl`` /
  ``messages.jsonl`` / ``runs.jsonl``) and the file subagent-trace backend.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._jsonl import JsonlCorruptionError, JsonlIo
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.file.subagent_trace_backend import FileSubagentTraceBackend
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_corrupt"
_USER = "user_corrupt"


# ---------------------------------------------------------------------------
# Unit: iter_lines torn-final vs interior distinction
# ---------------------------------------------------------------------------


class TestIterLinesCorruption:
    def test_clean_file_yields_all_records(self, tmp_path) -> None:
        path = tmp_path / "clean.jsonl"
        path.write_text('{"a":1}\n{"a":2}\n{"a":3}\n', encoding="utf-8")
        assert list(JsonlIo.iter_lines(path)) == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_torn_final_line_is_dropped(self, tmp_path) -> None:
        # Crash mid-append: the last line is a partial JSON object with no
        # trailing newline. It was never durably committed — drop it silently.
        path = tmp_path / "torn.jsonl"
        path.write_text('{"a":1}\n{"a":2}\n{"a":3', encoding="utf-8")
        assert list(JsonlIo.iter_lines(path)) == [{"a": 1}, {"a": 2}]

    def test_torn_final_line_with_trailing_blank_lines_is_dropped(
        self, tmp_path
    ) -> None:
        path = tmp_path / "torn_blanks.jsonl"
        # A trailing partial followed by only blank lines is still a torn tail.
        path.write_text('{"a":1}\n{"bad\n\n  \n', encoding="utf-8")
        assert list(JsonlIo.iter_lines(path)) == [{"a": 1}]

    def test_interior_corruption_raises_and_does_not_truncate(self, tmp_path) -> None:
        # A malformed line with a VALID record after it: interior corruption.
        path = tmp_path / "interior.jsonl"
        path.write_text('{"a":1}\n{bad json\n{"a":3}\n', encoding="utf-8")
        collected: list[dict] = []
        with pytest.raises(JsonlCorruptionError) as excinfo:
            for doc in JsonlIo.iter_lines(path):
                collected.append(doc)
        # It fails closed *before* yielding anything past the corruption point,
        # so the consumer never sees the truncated prefix as a completed read.
        assert collected == [{"a": 1}]
        assert excinfo.value.line_number == 2

    def test_two_consecutive_bad_lines_is_interior_corruption(self, tmp_path) -> None:
        # Real appends leave at most ONE partial trailing line; two consecutive
        # malformed lines mean content followed the first bad line.
        path = tmp_path / "two_bad.jsonl"
        path.write_text('{"a":1}\n{bad1\n{bad2\n', encoding="utf-8")
        with pytest.raises(JsonlCorruptionError):
            list(JsonlIo.iter_lines(path))

    def test_missing_file_is_empty(self, tmp_path) -> None:
        assert list(JsonlIo.iter_lines(tmp_path / "nope.jsonl")) == []


# ---------------------------------------------------------------------------
# Store replay: events.jsonl / messages.jsonl / runs.jsonl fail closed
# ---------------------------------------------------------------------------


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


async def _seed_conversation_with_events(store: FileRuntimeApiStore):
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
    # Append a few main-stream events so events.jsonl has an interior to corrupt.
    for i in range(3):
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                trace_id="trace_corrupt",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                summary=f"chunk-{i}",
            )
        )
    return conversation, run


def _corrupt_interior_line(path) -> None:
    """Rewrite ``path`` so an interior line is malformed but valid data follows."""

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2, f"need >=2 lines to corrupt an interior of {path}"
    lines[0] = "{ this is not json"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestFileStoreReplayFailsClosed:
    async def test_interior_corruption_in_events_jsonl_raises_on_reopen(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, _run = await _seed_conversation_with_events(store)
        await store.close()

        events_path = store.layout.events_path(_ORG, conversation.conversation_id)
        _corrupt_interior_line(events_path)

        reopened = FileRuntimeApiStore(root)
        with pytest.raises(JsonlCorruptionError):
            await reopened.open()

    async def test_interior_corruption_in_messages_jsonl_raises_on_reopen(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, _run = await _seed_conversation_with_events(store)
        await store.close()

        messages_path = store.layout.messages_path(_ORG, conversation.conversation_id)
        # Seeding a run writes the user message; add a second so there is an
        # interior line with committed data after it.
        with messages_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"filler": "row"}) + "\n")
        _corrupt_interior_line(messages_path)

        reopened = FileRuntimeApiStore(root)
        with pytest.raises(JsonlCorruptionError):
            await reopened.open()

    async def test_interior_corruption_in_runs_jsonl_raises_on_reopen(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_conversation_with_events(store)
        # A status update appends a second runs.jsonl line (last-write-wins),
        # giving us an interior line to corrupt with committed data after it.
        from runtime_api.schemas import AgentRunStatus

        await store.update_run_status(run_id=run.run_id, status=AgentRunStatus.RUNNING)
        await store.close()

        runs_path = store.layout.runs_path(_ORG, conversation.conversation_id)
        _corrupt_interior_line(runs_path)

        reopened = FileRuntimeApiStore(root)
        with pytest.raises(JsonlCorruptionError):
            await reopened.open()

    async def test_torn_trailing_line_still_reopens_cleanly(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_conversation_with_events(store)
        committed = len(
            await store.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        )
        await store.close()

        # Simulate a crash mid-append: a partial final line on events.jsonl.
        events_path = store.layout.events_path(_ORG, conversation.conversation_id)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write('{"partial": "torn crash write')  # no closing / newline

        reopened = FileRuntimeApiStore(root)
        await reopened.open()  # tolerated — must not raise
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        # Every durably-committed event survives; only the torn tail is dropped.
        assert len(events) == committed
        await reopened.close()


# ---------------------------------------------------------------------------
# Subagent trace backend: a corrupt per-subagent stream fails closed
# ---------------------------------------------------------------------------


async def _seed_subagent_stream(store: FileRuntimeApiStore, conversation_id: str):
    task_id = "task-corrupt"
    drafts = [
        RuntimeEventDraft(
            org_id=_ORG,
            run_id="run_corrupt",
            conversation_id=conversation_id,
            trace_id="trace_corrupt",
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            task_id=task_id,
            payload={"task_id": task_id, "subagent_name": "researcher"},
        ),
        RuntimeEventDraft(
            org_id=_ORG,
            run_id="run_corrupt",
            conversation_id=conversation_id,
            trace_id="trace_corrupt",
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            task_id=task_id,
            parent_task_id=task_id,
            payload={"tool_name": "web_search", "call_id": "c1", "args": {}},
        ),
        RuntimeEventDraft(
            org_id=_ORG,
            run_id="run_corrupt",
            conversation_id=conversation_id,
            trace_id="trace_corrupt",
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
            task_id=task_id,
            payload={"task_id": task_id, "subagent_name": "researcher"},
        ),
    ]
    for draft in drafts:
        await store.append_event(draft)
    return task_id


class TestSubagentTraceBackendFailsClosed:
    async def test_interior_corruption_in_subagent_stream_fails_closed(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, _run = await _seed_conversation_with_events(store)
        task_id = await _seed_subagent_stream(store, conversation.conversation_id)

        # Corrupt an interior line of the per-subagent JSONL (valid data after).
        sub_path = store.layout.subagent_path(
            _ORG, conversation.conversation_id, task_id
        )
        _corrupt_interior_line(sub_path)

        backend = FileSubagentTraceBackend(
            layout=store.layout,
            org_id=_ORG,
            conversation_id=conversation.conversation_id,
        )
        result = await backend.aread(f"/subagents/{task_id}/summary.md")
        # Fails closed: an error result, NOT a truncated projection.
        assert result.error is not None
        assert result.file_data is None
        # ls fails closed too.
        listed = await backend.als("/subagents/")
        assert listed.error is not None
        await store.close()

    async def test_torn_trailing_line_in_subagent_stream_is_tolerated(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, _run = await _seed_conversation_with_events(store)
        task_id = await _seed_subagent_stream(store, conversation.conversation_id)

        sub_path = store.layout.subagent_path(
            _ORG, conversation.conversation_id, task_id
        )
        with sub_path.open("a", encoding="utf-8") as handle:
            handle.write('{"partial": "torn')  # torn tail, no newline

        backend = FileSubagentTraceBackend(
            layout=store.layout,
            org_id=_ORG,
            conversation_id=conversation.conversation_id,
        )
        result = await backend.aread(f"/subagents/{task_id}/summary.md")
        assert result.error is None  # torn tail tolerated
        assert result.file_data is not None
        await store.close()
