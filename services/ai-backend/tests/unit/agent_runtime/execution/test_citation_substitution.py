"""Provider-agnostic citation substitution tests (PR 1.1 follow-up D).

Both the Anthropic citations_delta adapter and the OpenAI Responses
annotations drainer rely on the substitution helper. Locking its contract
here means the per-provider adapters reduce to small input shims —
swapping in the real model invocation path is a wiring change, not a
design change.
"""

from __future__ import annotations

import asyncio

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.citations import CitationLedger, SourceRef
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.execution.providers.anthropic_stream_adapter import (
    AnthropicCitationStreamAdapter,
)
from agent_runtime.execution.providers.citation_substitution import (
    CitationCandidate,
    CitationSubstitution,
)
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class _StubPersistence:
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
        run_id="run_sub",
        conversation_id="conv_sub",
        org_id="org_sub",
        user_id="user_sub",
        user_message_id="msg_sub",
        trace_id="trace_sub",
        model_provider="anthropic",
        model_name="claude-sonnet-4-6",
        runtime_context=AgentRuntimeContext(
            user_id="user_sub",
            org_id="org_sub",
            roles=["employee"],
            model_profile={
                "provider": "anthropic",
                "model_name": "claude-sonnet-4-6",
                "max_input_tokens": 200_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_sub",
            trace_id="trace_sub",
        ),
    )


def _bind_ledger() -> tuple[CitationLedger, _RecordingEventStore, object]:
    store = InMemoryCitationStore()
    events = _RecordingEventStore()
    producer = RuntimeEventProducer(
        persistence=_StubPersistence(),
        event_store=events,
    )
    ledger = CitationLedger(
        run=_run_record(),
        store=store,
        producer=producer,
        source=StreamEventSource.MODEL,
    )
    token = CitationLedger.bind_for_run(ledger)
    return ledger, events, token


class TestCitationSubstitution:
    def test_no_op_when_no_ledger_bound(self) -> None:
        result = asyncio.run(
            CitationSubstitution.apply(
                text="Hello world",
                candidates=(
                    CitationCandidate(
                        source=SourceRef(
                            source_connector="web",
                            source_doc_id="doc",
                            title="Doc",
                        ),
                        span=(0, 11),
                    ),
                ),
            )
        )
        assert result == "Hello world"

    def test_inserts_token_after_grounded_span(self) -> None:
        _, events, token = _bind_ledger()
        try:
            result = asyncio.run(
                CitationSubstitution.apply(
                    text="The sky is blue.",
                    candidates=(
                        CitationCandidate(
                            source=SourceRef(
                                source_connector="web",
                                source_doc_id="doc-1",
                                title="Sky color",
                                source_url="https://example.com/1",
                            ),
                            span=(0, 16),
                        ),
                    ),
                )
            )
        finally:
            CitationLedger.unbind(token)
        assert result == "The sky is blue.[c1]"
        assert len(events.drafts) == 1

    def test_walks_right_to_left_so_earlier_spans_stay_valid(self) -> None:
        _, _, token = _bind_ledger()
        try:
            result = asyncio.run(
                CitationSubstitution.apply(
                    text="alpha beta",
                    candidates=(
                        CitationCandidate(
                            source=SourceRef(
                                source_connector="web",
                                source_doc_id="alpha",
                                title="Alpha",
                            ),
                            span=(0, 5),
                        ),
                        CitationCandidate(
                            source=SourceRef(
                                source_connector="web",
                                source_doc_id="beta",
                                title="Beta",
                            ),
                            span=(6, 10),
                        ),
                    ),
                )
            )
        finally:
            CitationLedger.unbind(token)
        # Right-to-left insertion preserves earlier spans, then earlier insert.
        assert result == "alpha[c1] beta[c2]"


class TestAnthropicCitationStreamAdapter:
    def test_text_deltas_pass_through_unchanged(self) -> None:
        adapter = AnthropicCitationStreamAdapter()
        events = [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "world."},
            },
        ]

        async def consume() -> list[object]:
            out: list[object] = []
            async for event in adapter.aiter(_async_iter(events)):
                out.append(event)
            return out

        result = asyncio.run(consume())
        assert result == events

    def test_citations_delta_registers_source_against_ledger(self) -> None:
        _, events, token = _bind_ledger()
        adapter = AnthropicCitationStreamAdapter()
        stream = [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "The launch is on April 21."},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "citations_delta",
                    "citation": {
                        "url": "https://example.com/launch",
                        "title": "FY26 Q1 GTM plan",
                        "cited_text": "The launch is on April 21.",
                    },
                },
            },
        ]

        async def drain() -> None:
            async for _event in adapter.aiter(_async_iter(stream)):
                pass

        try:
            asyncio.run(drain())
        finally:
            CitationLedger.unbind(token)

        # Exactly one source_ingested event from the adapter's run through.
        assert len(events.drafts) == 1
        citation_payload = events.drafts[0].payload["citation"]
        assert citation_payload["source_connector"] == "anthropic"
        assert citation_payload["source_url"] == "https://example.com/launch"

    def test_citations_delta_without_url_or_title_is_skipped(self) -> None:
        _, events, token = _bind_ledger()
        adapter = AnthropicCitationStreamAdapter()
        stream = [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "x"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "citations_delta", "citation": {}},
            },
        ]

        async def drain() -> None:
            async for _event in adapter.aiter(_async_iter(stream)):
                pass

        try:
            asyncio.run(drain())
        finally:
            CitationLedger.unbind(token)
        assert events.drafts == []


async def _async_iter(items):  # type: ignore[no-untyped-def]
    for item in items:
        yield item
