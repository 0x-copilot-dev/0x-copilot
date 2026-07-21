"""Crash-atomicity of the file store's ``append_events_batch``.

Parity target: the Postgres adapter persists an event batch in one transaction.
Under ``file`` the batch is written as ONE fsynced append per target stream, so
a crash mid-write leaves either **all** of the batch's lines on disk or **none**
— never the "partial coalesced flush" (a durably-committed proper prefix) the
previous per-event loop could leave, one committed line per fsync.

This suite pins:

* the durable write is a *single* ``append_lines`` call per stream (not N
  ``append_line`` + N ``fsync``);
* an interrupted durable write leaves ``events.jsonl`` clean with none of the
  batch's lines, the in-memory view unchanged, and a reopened store in
  agreement;
* a successful batch persists all N with contiguous ``sequence_no``, advances
  ``events_by_run`` / the catalog / the run cursor, and reopens intact;
* the preserved contract: monotonic sequence continues after prior single
  appends, mixed subagent streams route correctly, a multi-run batch is
  rejected, and an empty batch is a no-op.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file import _jsonl
from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_batch"
_USER = "user_batch"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


async def _seed_run(store: FileRuntimeApiStore):
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


def _delta(*, run, conversation_id, i, task_id: str | None = None):
    source = StreamEventSource.SUBAGENT if task_id else StreamEventSource.MAIN_AGENT
    return RuntimeEventDraft(
        org_id=_ORG,
        run_id=run.run_id,
        conversation_id=conversation_id,
        trace_id="trace_batch",
        source=source,
        event_type=RuntimeApiEventType.MODEL_DELTA,
        task_id=task_id,
        summary=f"chunk-{i}",
        payload={"delta": f"chunk-{i}"},
    )


class TestBatchIsSingleFsyncedWrite:
    """The batch's durable write is one ``append_lines`` per stream — not N."""

    async def test_batch_events_use_one_append_lines_not_per_event_appends(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_run(store)
        events_path = store.layout.events_path(_ORG, conversation.conversation_id)

        append_lines_calls: list[tuple[str, int]] = []
        append_line_paths: list[str] = []
        real_append_lines = JsonlIo.append_lines.__func__
        real_append_line = JsonlIo.append_line.__func__

        def _spy_append_lines(cls, path, objs, *, fsync=True):
            objs = list(objs)
            append_lines_calls.append((str(path), len(objs)))
            return real_append_lines(cls, path, objs, fsync=fsync)

        def _spy_append_line(cls, path, obj, *, fsync=True):
            append_line_paths.append(str(path))
            return real_append_line(cls, path, obj, fsync=fsync)

        monkeypatch.setattr(JsonlIo, "append_lines", classmethod(_spy_append_lines))
        monkeypatch.setattr(JsonlIo, "append_line", classmethod(_spy_append_line))

        drafts = [
            _delta(run=run, conversation_id=conversation.conversation_id, i=i)
            for i in range(5)
        ]
        await store.append_events_batch(drafts)

        # Exactly one fsynced multi-line append covering all 5 events, on the
        # run's events stream — the crux of the atomicity fix.
        events_appends = [c for c in append_lines_calls if c[0] == str(events_path)]
        assert events_appends == [(str(events_path), 5)]
        # The events stream is NEVER touched by the per-line append path.
        assert str(events_path) not in append_line_paths
        await store.close()


class TestInterruptedBatchWriteLeavesNone:
    """A crash during the durable write leaves none of the batch on disk."""

    async def test_interrupted_write_leaves_clean_file_and_reopen_agrees(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_run(store)
        events_path = store.layout.events_path(_ORG, conversation.conversation_id)

        # Two committed baseline events on top of the seeded run_created event.
        for i in range(2):
            await store.append_event(
                _delta(run=run, conversation_id=conversation.conversation_id, i=i)
            )
        baseline = await store.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        baseline_seqs = [e.sequence_no for e in baseline]
        baseline_summaries = [e.summary for e in baseline]
        top_seq = baseline_seqs[-1]
        baseline_bytes = events_path.read_bytes()

        # Simulate a crash *during* the batch's single durable write: the one
        # fsynced append never lands. Because the store performs the batch write
        # as a single ``append_lines`` call before mutating anything in memory,
        # failing it must leave zero batch bytes on disk.
        class _SimulatedCrash(RuntimeError):
            pass

        def _boom(cls, path, objs, *, fsync=True):
            raise _SimulatedCrash("write interrupted")

        monkeypatch.setattr(JsonlIo, "append_lines", classmethod(_boom))

        batch = [
            _delta(run=run, conversation_id=conversation.conversation_id, i=i)
            for i in range(2, 7)
        ]
        with pytest.raises(_SimulatedCrash):
            await store.append_events_batch(batch)

        # On-disk residue: unchanged — all complete lines, none from the batch.
        assert events_path.read_bytes() == baseline_bytes
        # In-memory view untouched — no phantom events ahead of disk.
        assert [e.sequence_no for e in store.events_by_run[run.run_id]] == baseline_seqs
        assert await store.get_latest_sequence(run_id=run.run_id) == top_seq
        await store.close()

        # A reopened store agrees: exactly the pre-batch events, nothing more.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        assert [e.sequence_no for e in events] == baseline_seqs
        assert [e.summary for e in events] == baseline_summaries
        await reopened.close()


class TestSuccessfulBatchPersistsAll:
    """A committed batch persists all N and reopens with contiguous sequence."""

    async def test_batch_after_single_appends_is_contiguous_and_durable(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_run(store)

        # Two single appends first, so the batch must continue from there.
        for i in range(2):
            await store.append_event(
                _delta(run=run, conversation_id=conversation.conversation_id, i=i)
            )
        start = await store.get_latest_sequence(run_id=run.run_id)
        expected_batch_seqs = [start + 1, start + 2, start + 3, start + 4]
        full_seqs = list(range(1, start + 5))

        batch = [
            _delta(run=run, conversation_id=conversation.conversation_id, i=i)
            for i in range(2, 6)
        ]
        envelopes = await store.append_events_batch(batch)

        # Returned envelopes carry the assigned monotonic sequence numbers.
        assert [e.sequence_no for e in envelopes] == expected_batch_seqs
        assert [e.summary for e in envelopes] == [f"chunk-{i}" for i in range(2, 6)]
        # In-memory bucket, catalog cursor, and run cursor all advanced once.
        assert [e.sequence_no for e in store.events_by_run[run.run_id]] == full_seqs
        assert await store.get_latest_sequence(run_id=run.run_id) == start + 4
        run_after = await store.get_run(org_id=_ORG, run_id=run.run_id)
        assert run_after is not None
        assert run_after.latest_sequence_no == start + 4
        await store.close()

        # Reopen a fresh instance: every line replays with contiguous sequence.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        assert [e.sequence_no for e in events] == full_seqs
        # The last four events are the batch, in order.
        assert [e.summary for e in events[-4:]] == [f"chunk-{i}" for i in range(2, 6)]
        assert await reopened.get_latest_sequence(run_id=run.run_id) == start + 4
        reopened_run = await reopened.get_run(org_id=_ORG, run_id=run.run_id)
        assert reopened_run is not None
        assert reopened_run.latest_sequence_no == start + 4
        await reopened.close()

    async def test_batch_returns_envelopes_identical_to_sequential_appends(
        self, tmp_path
    ) -> None:
        """Batched envelopes are byte-identical to N ``append_event`` calls."""

        # Sequential control store.
        seq_store = FileRuntimeApiStore(tmp_path / "seq")
        await seq_store.open()
        seq_conv, seq_run = await _seed_run(seq_store)
        seq_envelopes = [
            await seq_store.append_event(
                _delta(run=seq_run, conversation_id=seq_conv.conversation_id, i=i)
            )
            for i in range(4)
        ]
        await seq_store.close()

        # Batched store.
        batch_store = FileRuntimeApiStore(tmp_path / "batch")
        await batch_store.open()
        batch_conv, batch_run = await _seed_run(batch_store)
        batch_envelopes = await batch_store.append_events_batch(
            [
                _delta(run=batch_run, conversation_id=batch_conv.conversation_id, i=i)
                for i in range(4)
            ]
        )
        await batch_store.close()

        def _shape(env):
            data = env.model_dump(mode="json")
            # ids/timestamps differ per run; compare the projection that matters.
            return {
                k: data[k]
                for k in (
                    "sequence_no",
                    "source",
                    "event_type",
                    "activity_kind",
                    "summary",
                    "payload",
                    "task_id",
                    "visibility",
                )
            }

        assert [_shape(e) for e in batch_envelopes] == [
            _shape(e) for e in seq_envelopes
        ]


class TestBatchGroupsSubagentStreams:
    """A batch mixing subagent task_ids routes each to its own stream."""

    async def test_mixed_streams_route_and_reopen_replays(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await _seed_run(store)

        main_path = store.layout.events_path(_ORG, conversation.conversation_id)
        sub_path = store.layout.subagent_path(
            _ORG, conversation.conversation_id, "task-a"
        )
        main_before = len(list(JsonlIo.iter_lines(main_path)))
        start = await store.get_latest_sequence(run_id=run.run_id)

        batch = [
            _delta(run=run, conversation_id=conversation.conversation_id, i=0),
            _delta(
                run=run,
                conversation_id=conversation.conversation_id,
                i=1,
                task_id="task-a",
            ),
            _delta(run=run, conversation_id=conversation.conversation_id, i=2),
            _delta(
                run=run,
                conversation_id=conversation.conversation_id,
                i=3,
                task_id="task-a",
            ),
        ]
        envelopes = await store.append_events_batch(batch)
        assert [e.sequence_no for e in envelopes] == [start + i for i in range(1, 5)]

        # Two of the batch's events routed to the main stream, two to the
        # subagent stream — grouping wrote each to its own file.
        assert len(list(JsonlIo.iter_lines(main_path))) == main_before + 2
        assert len(list(JsonlIo.iter_lines(sub_path))) == 2
        await store.close()

        # Reopen merges both streams back into one gap-free sequence.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        assert [e.sequence_no for e in events] == list(range(1, start + 5))
        assert [e.task_id for e in events[-4:]] == [None, "task-a", None, "task-a"]
        await reopened.close()


class TestBatchContractPreserved:
    """Preserved edge behavior: multi-run rejection and empty no-op."""

    async def test_multi_run_batch_is_rejected(self, tmp_path) -> None:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        conversation, run = await _seed_run(store)
        mixed = [
            _delta(run=run, conversation_id=conversation.conversation_id, i=0),
            RuntimeEventDraft(
                org_id=_ORG,
                run_id="some-other-run",
                conversation_id=conversation.conversation_id,
                trace_id="trace_batch",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                summary="stray",
            ),
        ]
        with pytest.raises(ValueError, match="share one run_id"):
            await store.append_events_batch(mixed)
        await store.close()

    async def test_empty_batch_is_noop(self, tmp_path, monkeypatch) -> None:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()

        def _fail(*args, **kwargs):
            raise AssertionError("empty batch must not touch disk")

        monkeypatch.setattr(_jsonl.JsonlIo, "append_lines", classmethod(_fail))
        result = await store.append_events_batch([])
        assert result == ()
        await store.close()
