"""Offload + CompositeBackend read wiring for the desktop file store.

Covers DoD wirings #2 and #3:

* a large tool result offloads to a content-addressed object and the resulting
  event payload carries a bounded preview + ``output_ref`` (which flips the
  event to ``redaction_state=OFFLOADED`` through the shared projector);
* the orchestrator can read a subagent transcript and an offloaded
  ``/large_tool_results/<sha256>`` blob back through the deepagents
  ``CompositeBackend`` the factory composes.
"""

from __future__ import annotations

import json

from agent_runtime.api.constants import Keys
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.factory import _composed_deep_backend
from runtime_adapters.file import (
    FileLargeToolResultBackend,
    FileObjectStore,
    FileOffloadWriter,
    FileRuntimeApiStore,
    FileSubagentTraceBackend,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_worker.tool_result_offload import ToolResultOffloader
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventPresentationProjector,
    RuntimeEventRedactionState,
)

_ORG = "org_file"
_CONV = "conv_file"
_RUN = "run_file"
_TASK = "task-a"


def _layout(tmp_path) -> FileStoreLayout:
    layout = FileStoreLayout(tmp_path / "store")
    layout.ensure_scaffold()
    return layout


def _large_output() -> str:
    # Comfortably over the offloader's ~8k-token (~32k-char) inline budget.
    return "SEARCH RESULT LINE\n" * 5_000


# ---------------------------------------------------------------------------
# Wiring #2 — offload writer + ToolResultOffloader
# ---------------------------------------------------------------------------


class TestFileOffloadWriter:
    def test_put_returns_large_tool_results_reference(self, tmp_path) -> None:
        store = FileObjectStore(_layout(tmp_path))
        writer = FileOffloadWriter(store)
        content = _large_output()

        reference = writer(content)

        assert reference.startswith("/large_tool_results/")
        sha = reference.removeprefix("/large_tool_results/")
        assert len(sha) == 64
        # The blob is retrievable and byte-identical.
        assert store.get(sha).decode("utf-8") == content

    def test_identical_content_is_addressed_once(self, tmp_path) -> None:
        store = FileObjectStore(_layout(tmp_path))
        writer = FileOffloadWriter(store)
        content = _large_output()
        assert writer(content) == writer(content)


class TestToolResultOffloader:
    def _offloader(self, tmp_path) -> tuple[ToolResultOffloader, FileObjectStore]:
        store = FileObjectStore(_layout(tmp_path))
        return ToolResultOffloader(FileOffloadWriter(store)), store

    def test_small_output_is_left_inline(self, tmp_path) -> None:
        offloader, store = self._offloader(tmp_path)
        payload = {
            Keys.Field.TOOL_NAME: "web_search",
            Keys.Field.CALL_ID: "c1",
            Keys.Field.STATUS: "completed",
            Keys.Field.OUTPUT: "small result",
        }
        result = offloader.apply(payload, trace_id="trace-1")
        assert result == payload
        assert Keys.Field.OUTPUT_REF not in result

    def test_large_output_is_offloaded_with_ref_and_preview(self, tmp_path) -> None:
        offloader, store = self._offloader(tmp_path)
        content = _large_output()
        payload = {
            Keys.Field.TOOL_NAME: "web_search",
            Keys.Field.CALL_ID: "c1",
            Keys.Field.STATUS: "completed",
            Keys.Field.OUTPUT: content,
        }

        result = offloader.apply(payload, trace_id="trace-1")

        ref = result[Keys.Field.OUTPUT_REF]
        assert ref.startswith("/large_tool_results/")
        # Untouched identity fields.
        assert result[Keys.Field.TOOL_NAME] == "web_search"
        assert result[Keys.Field.CALL_ID] == "c1"
        # Full content no longer inline; a bounded preview took its place.
        preview = result[Keys.Field.PREVIEW]
        assert preview
        assert len(preview) < len(content)
        assert result[Keys.Field.OUTPUT] == preview
        # The full content is durable in the object store.
        sha = ref.removeprefix("/large_tool_results/")
        assert store.get(sha).decode("utf-8") == content

    def test_offloaded_payload_marks_event_offloaded(self, tmp_path) -> None:
        # The ``output_ref`` key drives the shared projector to OFFLOADED — this
        # is the "event carries a ref" half of the DoD, exercised through the
        # real presentation projector rather than a hand-set flag.
        offloader, _store = self._offloader(tmp_path)
        payload = {
            Keys.Field.TOOL_NAME: "web_search",
            Keys.Field.CALL_ID: "c1",
            Keys.Field.STATUS: "completed",
            Keys.Field.OUTPUT: _large_output(),
        }
        offloaded = offloader.apply(payload, trace_id="trace-1")

        fields = RuntimeEventPresentationProjector.presentation_fields(
            event_type=RuntimeApiEventType.TOOL_RESULT,
            source=StreamEventSource.TOOL,
            parent_task_id=None,
            payload=offloaded,
            metadata={},
        )
        assert (
            fields[Keys.Field.REDACTION_STATE]
            == RuntimeEventRedactionState.OFFLOADED.value
        )

    def test_non_string_output_is_serialized_before_measuring(self, tmp_path) -> None:
        offloader, store = self._offloader(tmp_path)
        big_list = [{"snippet": "x" * 40, "url": "https://example.com"}] * 1_000
        payload = {
            Keys.Field.TOOL_NAME: "web_search",
            Keys.Field.CALL_ID: "c1",
            Keys.Field.STATUS: "completed",
            Keys.Field.OUTPUT: big_list,
        }
        result = offloader.apply(payload, trace_id="trace-1")
        ref = result[Keys.Field.OUTPUT_REF]
        sha = ref.removeprefix("/large_tool_results/")
        assert json.loads(store.get(sha).decode("utf-8")) == big_list


# ---------------------------------------------------------------------------
# Wiring #3 — FileLargeToolResultBackend + FileSubagentTraceBackend + composite
# ---------------------------------------------------------------------------


class TestFileLargeToolResultBackend:
    async def test_reads_offloaded_blob_by_reference(self, tmp_path) -> None:
        store = FileObjectStore(_layout(tmp_path))
        content = _large_output()
        reference = FileOffloadWriter(store)(content)
        backend = FileLargeToolResultBackend(store)

        result = await backend.aread(reference)
        assert result.error is None
        assert result.file_data["content"] == content

    async def test_reads_prefix_stripped_reference(self, tmp_path) -> None:
        # CompositeBackend strips the ``/large_tool_results/`` route prefix.
        store = FileObjectStore(_layout(tmp_path))
        reference = FileOffloadWriter(store)("payload " * 5_000)
        sha = reference.removeprefix("/large_tool_results/")
        backend = FileLargeToolResultBackend(store)

        result = await backend.aread(f"/{sha}")
        assert result.error is None
        assert result.file_data["content"].startswith("payload ")

    async def test_unknown_reference_returns_error(self, tmp_path) -> None:
        backend = FileLargeToolResultBackend(FileObjectStore(_layout(tmp_path)))
        result = await backend.aread("/large_tool_results/" + "0" * 64)
        assert result.error is not None
        assert result.file_data is None

    async def test_non_reference_path_returns_error(self, tmp_path) -> None:
        backend = FileLargeToolResultBackend(FileObjectStore(_layout(tmp_path)))
        result = await backend.aread("/large_tool_results/not-a-digest")
        assert result.error is not None

    async def test_writes_are_rejected(self, tmp_path) -> None:
        backend = FileLargeToolResultBackend(FileObjectStore(_layout(tmp_path)))
        result = await backend.awrite("/large_tool_results/x", "data")
        assert result.error is not None


class _SubagentSeedMixin:
    """Persist a subagent trace to a real file store for read-back tests."""

    async def _seed_store(self, tmp_path) -> FileRuntimeApiStore:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        # The run record is unnecessary: append_event only advances a run's
        # sequence cursor when the run is registered, and the trace backend reads
        # the canonical JSONL directly rather than any run projection.
        drafts = [
            self._draft(
                RuntimeApiEventType.SUBAGENT_STARTED,
                source=StreamEventSource.SUBAGENT,
                payload={
                    Keys.Field.TASK_ID: _TASK,
                    Keys.Field.SUBAGENT_NAME: "researcher",
                    Keys.Field.SUMMARY: "Research deep agents",
                    Keys.Field.STATUS: "queued",
                },
            ),
            self._draft(
                RuntimeApiEventType.TOOL_CALL_STARTED,
                payload={
                    Keys.Field.TOOL_NAME: "web_search",
                    Keys.Field.CALL_ID: "c1",
                    Keys.Field.ARGS: {"query": "langchain deep agents"},
                },
                parent_task_id=_TASK,
                task_id=_TASK,
            ),
            self._draft(
                RuntimeApiEventType.TOOL_RESULT,
                payload={
                    Keys.Field.TOOL_NAME: "web_search",
                    Keys.Field.CALL_ID: "c1",
                    Keys.Field.OUTPUT: [{"snippet": "deep agents harness"}],
                    Keys.Field.STATUS: "completed",
                },
                parent_task_id=_TASK,
                task_id=_TASK,
            ),
            self._draft(
                RuntimeApiEventType.SUBAGENT_COMPLETED,
                source=StreamEventSource.SUBAGENT,
                payload={
                    Keys.Field.TASK_ID: _TASK,
                    Keys.Field.SUBAGENT_NAME: "researcher",
                    Keys.Field.SUMMARY: "Deep agents is an agent harness.",
                    Keys.Field.STATUS: "completed",
                },
            ),
        ]
        for draft in drafts:
            await store.append_event(draft)
        return store

    @staticmethod
    def _draft(
        event_type: RuntimeApiEventType,
        *,
        payload: dict[str, object],
        source: StreamEventSource = StreamEventSource.TOOL,
        parent_task_id: str | None = None,
        task_id: str | None = None,
    ) -> RuntimeEventDraft:
        # SUBAGENT_* lifecycle events carry their task in the payload; the file
        # store routes by the envelope ``task_id`` so set it for those too.
        effective_task_id = task_id
        if effective_task_id is None and event_type in {
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        }:
            effective_task_id = str(payload.get(Keys.Field.TASK_ID))
        return RuntimeEventDraft(
            org_id=_ORG,
            run_id=_RUN,
            conversation_id=_CONV,
            trace_id="trace_file",
            source=source,
            event_type=event_type,
            parent_task_id=parent_task_id,
            task_id=effective_task_id,
            payload=payload,
        )


class TestFileSubagentTraceBackend(_SubagentSeedMixin):
    async def test_lists_seeded_subagent(self, tmp_path) -> None:
        store = await self._seed_store(tmp_path)
        backend = FileSubagentTraceBackend(
            layout=store.layout, org_id=_ORG, conversation_id=_CONV
        )
        result = await backend.als("/subagents/")
        paths = {entry["path"] for entry in (result.entries or [])}
        assert f"/{_TASK}/" in paths
        await store.close()

    async def test_reads_tool_calls_with_verbatim_query(self, tmp_path) -> None:
        store = await self._seed_store(tmp_path)
        backend = FileSubagentTraceBackend(
            layout=store.layout, org_id=_ORG, conversation_id=_CONV
        )
        result = await backend.aread(f"/subagents/{_TASK}/tool_calls.json")
        assert result.error is None
        rendered = json.loads(result.file_data["content"])
        assert rendered[0]["args"] == {"query": "langchain deep agents"}
        await store.close()

    async def test_survives_catalog_index_wipe(self, tmp_path) -> None:
        # The file-native backend reads canonical JSONL, not the disposable
        # index — so a wiped catalog does not affect the projection.
        import shutil

        store = await self._seed_store(tmp_path)
        await store.close()
        shutil.rmtree(store.layout.index_dir)
        backend = FileSubagentTraceBackend(
            layout=store.layout, org_id=_ORG, conversation_id=_CONV
        )
        result = await backend.aread(f"/subagents/{_TASK}/summary.md")
        assert result.error is None
        assert "Deep agents is an agent harness." in result.file_data["content"]


class TestCompositeBackendReadsFromFileStore(_SubagentSeedMixin):
    async def test_reads_subagent_and_large_result_through_composite(
        self, tmp_path
    ) -> None:
        store = await self._seed_store(tmp_path)
        object_store = store.object_store
        reference = FileOffloadWriter(object_store)(_large_output())

        composite = _composed_deep_backend(
            FileSubagentTraceBackend(
                layout=store.layout, org_id=_ORG, conversation_id=_CONV
            ),
            large_tool_results_backend=FileLargeToolResultBackend(object_store),
        )
        assert "/large_tool_results/" in composite.routes
        assert "/subagents/" in composite.routes

        subagent_read = await composite.aread(f"/subagents/{_TASK}/summary.md")
        assert subagent_read.error is None
        assert "Deep agents is an agent harness." in subagent_read.file_data["content"]

        blob_read = await composite.aread(reference)
        assert blob_read.error is None
        assert blob_read.file_data["content"] == _large_output()
        await store.close()
