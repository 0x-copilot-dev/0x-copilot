"""Unit tests for the generic MCP citation projector (PR 1.1 follow-up C).

Three recognized result shapes + the no-ledger / unrecognized degradation
paths. The projector is best-effort: a failure or no-match must never
raise into the tool path.
"""

from __future__ import annotations

import asyncio

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.citations import CitationLedger
from agent_runtime.capabilities.mcp.middleware.cite_mcp import (
    CitationProjectingMcpMiddleware,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class _RecordingPersistence:
    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        del run_id, latest_sequence_no


class _RecordingEventStore:
    def __init__(self) -> None:
        self.drafts: list[RuntimeEventDraft] = []

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        self.drafts.append(event)
        return RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=len(self.drafts),
            source=event.source,
            event_type=event.event_type,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_task_id=event.parent_task_id,
            task_id=event.task_id,
            subagent_id=event.subagent_id,
            display_title=event.display_title,
            summary=event.summary,
            status=event.status,
            activity_kind=event.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=event.event_type,
                source=event.source,
            ),
            visibility=event.visibility,
            redaction_state=event.redaction_state,
            presentation=event.presentation,
            payload=event.payload,
            metadata=event.metadata,
        )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run_mcp_cite",
        conversation_id="conv_mcp_cite",
        org_id="org_mcp_cite",
        user_id="user_mcp_cite",
        user_message_id="msg_mcp_cite",
        trace_id="trace_mcp_cite",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_mcp_cite",
            org_id="org_mcp_cite",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_mcp_cite",
            trace_id="trace_mcp_cite",
        ),
    )


class CitationProjectorFixtureMixin:
    def _bind_ledger(
        self,
    ) -> tuple[CitationLedger, _RecordingEventStore, InMemoryCitationStore, object]:
        store = InMemoryCitationStore()
        events = _RecordingEventStore()
        producer = RuntimeEventProducer(
            persistence=_RecordingPersistence(),
            event_store=events,
        )
        ledger = CitationLedger(
            run=_run_record(),
            store=store,
            producer=producer,
            source=StreamEventSource.TOOL,
        )
        token = CitationLedger.bind_for_run(ledger)
        return ledger, events, store, token


class TestProjectorContentBlocks(CitationProjectorFixtureMixin):
    def test_anthropic_text_block_with_url_emits_one_citation(self) -> None:
        _, events, store, token = self._bind_ledger()
        try:
            asyncio.run(
                CitationProjectingMcpMiddleware.project(
                    connector="notion",
                    tool_call_id="call_42",
                    result={
                        "content": [
                            {
                                "type": "text",
                                "text": "Aurora 4.0 brings agentic search to every desk.",
                                "url": "https://example.com/notion/page-1",
                                "title": "Aurora 4.0 — Approved Positioning v3",
                            },
                        ],
                    },
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert len(store.rows) == 1
        assert store.rows[0].source_connector == "notion"
        assert store.rows[0].source_tool_call_id == "call_42"
        # Exactly one source_ingested event fired.
        assert len(events.drafts) == 1

    def test_resource_block_inside_content(self) -> None:
        _, _, store, token = self._bind_ledger()
        try:
            asyncio.run(
                CitationProjectingMcpMiddleware.project(
                    connector="drive",
                    tool_call_id="call_43",
                    result={
                        "content": [
                            {
                                "type": "resource",
                                "resource": {
                                    "uri": "drive://file/123",
                                    "name": "FY26 Q1 GTM plan",
                                    "description": "Three-phase rollout plan.",
                                },
                            },
                        ],
                    },
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert len(store.rows) == 1
        assert store.rows[0].source_doc_id == "drive://file/123"
        assert store.rows[0].title == "FY26 Q1 GTM plan"


class TestProjectorResultsList(CitationProjectorFixtureMixin):
    def test_generic_results_list_emits_one_per_entry(self) -> None:
        _, events, store, token = self._bind_ledger()
        try:
            asyncio.run(
                CitationProjectingMcpMiddleware.project(
                    connector="web",
                    tool_call_id="call_44",
                    result={
                        "results": [
                            {
                                "id": "r1",
                                "title": "Result 1",
                                "url": "https://example.com/1",
                                "snippet": "Snippet 1.",
                            },
                            {
                                "id": "r2",
                                "title": "Result 2",
                                "url": "https://example.com/2",
                                "snippet": "Snippet 2.",
                            },
                        ],
                    },
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert len(store.rows) == 2
        assert [row.ordinal for row in store.rows] == [1, 2]
        assert len(events.drafts) == 2


class TestProjectorSingleResource(CitationProjectorFixtureMixin):
    def test_single_resource_read(self) -> None:
        _, _, store, token = self._bind_ledger()
        try:
            asyncio.run(
                CitationProjectingMcpMiddleware.project(
                    connector="confluence",
                    tool_call_id="call_45",
                    result={
                        "resource": {
                            "uri": "confluence://page/789",
                            "title": "Brand voice guidelines — 2026",
                            "content": "Plain, confident, never breathless.",
                        },
                    },
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert len(store.rows) == 1
        assert store.rows[0].title == "Brand voice guidelines — 2026"


class TestProjectorDegradation(CitationProjectorFixtureMixin):
    def test_unrecognized_shape_passes_through_silently(self) -> None:
        _, events, store, token = self._bind_ledger()
        try:
            asyncio.run(
                CitationProjectingMcpMiddleware.project(
                    connector="custom",
                    tool_call_id="call_46",
                    result={"foo": "bar"},
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert store.rows == ()
        assert events.drafts == []

    def test_no_op_when_no_ledger_bound(self) -> None:
        # No bind_for_run — projector silently returns.
        asyncio.run(
            CitationProjectingMcpMiddleware.project(
                connector="notion",
                tool_call_id="call_47",
                result={
                    "content": [
                        {"type": "text", "text": "x", "url": "https://example.com"},
                    ],
                },
            )
        )

    def test_non_dict_result_is_ignored(self) -> None:
        _, _, store, token = self._bind_ledger()
        try:
            asyncio.run(
                CitationProjectingMcpMiddleware.project(
                    connector="notion",
                    tool_call_id="call_48",
                    result="not a dict",
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert store.rows == ()
