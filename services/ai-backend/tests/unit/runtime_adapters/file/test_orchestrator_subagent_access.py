"""Orchestrator reads a subagent's trace through the ``/subagents/`` composite route.

DoD #16 wiring #1: prove end-to-end that the orchestrator (the supervisor deep
agent) can read a subagent's four virtual files —
``conversation.md`` / ``tool_calls.json`` / ``summary.md`` / ``events.jsonl`` —
through the deepagents ``CompositeBackend`` the factory composes, sourced from
the desktop file store's canonical per-conversation JSONL. Also proves an
*in-progress* subagent (no ``SUBAGENT_COMPLETED`` yet) exposes its committed
prefix, since the append-only JSONL read returns whatever has been fsynced so far.
"""

from __future__ import annotations

import json

from agent_runtime.api.constants import Keys
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.factory import _composed_deep_backend
from runtime_adapters.file import FileRuntimeApiStore, FileSubagentTraceBackend
from runtime_api.schemas import RuntimeApiEventType, RuntimeEventDraft

_ORG = "org_orch"
_CONV = "conv_orch"
_RUN = "run_orch"
_TASK = "task-research"


class _SubagentTraceSeeder:
    """Append subagent lifecycle + tool events to a real file store.

    ``seed`` drives the store one committed append at a time (each ``append_event``
    fsyncs), so an ``include_completed=False`` seed leaves a genuinely in-progress
    trace: the STARTED + tool events are durably committed, the COMPLETED is not.
    """

    async def seed(self, tmp_path, *, include_completed: bool) -> FileRuntimeApiStore:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        await store.append_event(
            self._draft(
                RuntimeApiEventType.SUBAGENT_STARTED,
                source=StreamEventSource.SUBAGENT,
                payload={
                    Keys.Field.TASK_ID: _TASK,
                    Keys.Field.SUBAGENT_NAME: "researcher",
                    Keys.Field.SUMMARY: "Investigate deep agents",
                    Keys.Field.STATUS: "running",
                },
            )
        )
        await store.append_event(
            self._draft(
                RuntimeApiEventType.TOOL_CALL_STARTED,
                payload={
                    Keys.Field.TOOL_NAME: "web_search",
                    Keys.Field.CALL_ID: "call-1",
                    Keys.Field.ARGS: {"query": "langgraph deep agents"},
                },
                parent_task_id=_TASK,
                task_id=_TASK,
            )
        )
        await store.append_event(
            self._draft(
                RuntimeApiEventType.TOOL_RESULT,
                payload={
                    Keys.Field.TOOL_NAME: "web_search",
                    Keys.Field.CALL_ID: "call-1",
                    Keys.Field.OUTPUT: [{"snippet": "deep agents is a harness"}],
                    Keys.Field.STATUS: "completed",
                },
                parent_task_id=_TASK,
                task_id=_TASK,
            )
        )
        if include_completed:
            await store.append_event(
                self._draft(
                    RuntimeApiEventType.SUBAGENT_COMPLETED,
                    source=StreamEventSource.SUBAGENT,
                    payload={
                        Keys.Field.TASK_ID: _TASK,
                        Keys.Field.SUBAGENT_NAME: "researcher",
                        Keys.Field.SUMMARY: "Deep agents is a LangGraph harness.",
                        Keys.Field.STATUS: "completed",
                    },
                )
            )
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
            trace_id="trace_orch",
            source=source,
            event_type=event_type,
            parent_task_id=parent_task_id,
            task_id=effective_task_id,
            payload=payload,
        )

    @staticmethod
    def composite(store: FileRuntimeApiStore) -> object:
        """Compose the same ``/subagents/`` route the factory builds on desktop."""

        return _composed_deep_backend(
            FileSubagentTraceBackend(
                layout=store.layout, org_id=_ORG, conversation_id=_CONV
            )
        )


class TestOrchestratorReadsCompletedSubagent(_SubagentTraceSeeder):
    async def test_reads_all_four_virtual_files_through_composite(
        self, tmp_path
    ) -> None:
        store = await self.seed(tmp_path, include_completed=True)
        composite = self.composite(store)
        assert "/subagents/" in composite.routes

        # ls lists the subagent directory.
        listing = await composite.als("/subagents/")
        assert f"/subagents/{_TASK}/" in {
            entry["path"] for entry in (listing.entries or [])
        }

        # conversation.md — human transcript carries the tool exchange.
        conversation = await composite.aread(f"/subagents/{_TASK}/conversation.md")
        assert conversation.error is None
        assert "web_search" in conversation.file_data["content"]
        assert "deep agents is a harness" in conversation.file_data["content"]

        # tool_calls.json — the verbatim tool arguments the subagent issued.
        tool_calls = await composite.aread(f"/subagents/{_TASK}/tool_calls.json")
        assert tool_calls.error is None
        rendered = json.loads(tool_calls.file_data["content"])
        assert rendered[0]["args"] == {"query": "langgraph deep agents"}

        # summary.md — the final subagent summary.
        summary = await composite.aread(f"/subagents/{_TASK}/summary.md")
        assert summary.error is None
        assert "Deep agents is a LangGraph harness." in summary.file_data["content"]

        # events.jsonl — the raw envelope stream, one JSON object per line.
        events = await composite.aread(f"/subagents/{_TASK}/events.jsonl")
        assert events.error is None
        lines = [
            line for line in events.file_data["content"].splitlines() if line.strip()
        ]
        assert lines, "events.jsonl should not be empty"
        for line in lines:
            json.loads(line)  # every line is valid JSON
        await store.close()

    async def test_survives_catalog_index_wipe(self, tmp_path) -> None:
        # The read sources canonical JSONL, not the disposable catalog index.
        import shutil

        store = await self.seed(tmp_path, include_completed=True)
        await store.close()
        shutil.rmtree(store.layout.index_dir)
        composite = self.composite(store)
        summary = await composite.aread(f"/subagents/{_TASK}/summary.md")
        assert summary.error is None
        assert "Deep agents is a LangGraph harness." in summary.file_data["content"]


class TestOrchestratorReadsInProgressSubagent(_SubagentTraceSeeder):
    async def test_reads_committed_prefix_before_completion(self, tmp_path) -> None:
        store = await self.seed(tmp_path, include_completed=False)
        composite = self.composite(store)

        # The in-progress subagent is already listed.
        listing = await composite.als("/subagents/")
        assert f"/subagents/{_TASK}/" in {
            entry["path"] for entry in (listing.entries or [])
        }

        # The committed tool call is readable even though the subagent has not
        # emitted SUBAGENT_COMPLETED — the orchestrator sees the fsynced prefix.
        tool_calls = await composite.aread(f"/subagents/{_TASK}/tool_calls.json")
        assert tool_calls.error is None
        rendered = json.loads(tool_calls.file_data["content"])
        assert rendered[0]["args"] == {"query": "langgraph deep agents"}

        # The transcript exposes the in-progress objective, and the final
        # completion summary is absent (nothing fabricated for an open run).
        conversation = await composite.aread(f"/subagents/{_TASK}/conversation.md")
        assert conversation.error is None
        assert (
            "Deep agents is a LangGraph harness."
            not in (conversation.file_data["content"])
        )
        await store.close()
