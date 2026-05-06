"""Provider citation stream pipeline tests (PRDs 01 / 02 / 03).

Covers the foundation dispatcher, the three provider adapters
(Anthropic / OpenAI Responses / Gemini grounding), and an integration
loop through :class:`StreamingExecutor` to assert that the pipeline is
wired between :meth:`StreamOrchestrator.stream_delta` and the
``MODEL_DELTA`` event-producer call.

Fakes only — no real LangChain provider calls. The fixtures synthesise
``AIMessageChunk``-shaped objects (plain ``dict`` chunks with attribute-
style access via ``SimpleNamespace`` where needed) using the documented
content-block shapes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.citations import CitationLedger
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.execution.providers.anthropic_stream_adapter import (
    AnthropicCitationStreamAdapter,
)
from agent_runtime.execution.providers.citation_pipeline import (
    CitationStreamPipeline,
    NoopCitationAdapter,
)
from agent_runtime.execution.providers.gemini_grounding_stream_adapter import (
    GeminiGroundingCitationStreamAdapter,
)
from agent_runtime.execution.providers.openai_responses_stream_adapter import (
    OpenAIResponsesCitationStreamAdapter,
)
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


# ---------------------------------------------------------------------------
# Mixins (per tests/CLAUDE.md): fakes, builders, and shared fixtures live here
# so concrete test classes only contain ``test_*`` methods.
# ---------------------------------------------------------------------------


class _StubPersistenceMixin:
    class _StubPersistence:
        async def set_run_latest_sequence(
            self, *, run_id: str, latest_sequence_no: int
        ) -> None:
            del run_id, latest_sequence_no


class _RecordingEventStoreMixin:
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


class _LedgerBuilderMixin(_StubPersistenceMixin, _RecordingEventStoreMixin):
    @classmethod
    def _run_record(cls, *, provider: str = "anthropic") -> RunRecord:
        return RunRecord(
            run_id="run_pipe",
            conversation_id="conv_pipe",
            org_id="org_pipe",
            user_id="user_pipe",
            user_message_id="msg_pipe",
            trace_id="trace_pipe",
            model_provider=provider,
            model_name="model-x",
            runtime_context=AgentRuntimeContext(
                user_id="user_pipe",
                org_id="org_pipe",
                roles=["employee"],
                model_profile={
                    "provider": provider,
                    "model_name": "model-x",
                    "max_input_tokens": 200_000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id="run_pipe",
                trace_id="trace_pipe",
            ),
        )

    @classmethod
    def _bind_ledger(
        cls, *, provider: str = "anthropic"
    ) -> tuple[
        CitationLedger,
        "_RecordingEventStoreMixin._RecordingEventStore",
        object,
    ]:
        store = InMemoryCitationStore()
        events = cls._RecordingEventStore()
        producer = RuntimeEventProducer(
            persistence=cls._StubPersistence(),
            event_store=events,
        )
        ledger = CitationLedger(
            run=cls._run_record(provider=provider),
            store=store,
            producer=producer,
            source=StreamEventSource.MODEL,
        )
        token = CitationLedger.bind_for_run(ledger)
        return ledger, events, token


# ---------------------------------------------------------------------------
# Foundation: pipeline dispatcher + protocol surface.
# ---------------------------------------------------------------------------


class TestCitationStreamPipeline(_LedgerBuilderMixin):
    def test_unknown_provider_resolves_to_noop_adapter(self) -> None:
        pipeline = CitationStreamPipeline.for_provider("does-not-exist")
        assert isinstance(pipeline.adapter, NoopCitationAdapter)

    def test_none_provider_resolves_to_noop_adapter(self) -> None:
        pipeline = CitationStreamPipeline.for_provider(None)
        assert isinstance(pipeline.adapter, NoopCitationAdapter)

    def test_anthropic_provider_resolves_to_anthropic_adapter(self) -> None:
        pipeline = CitationStreamPipeline.for_provider("anthropic")
        assert isinstance(pipeline.adapter, AnthropicCitationStreamAdapter)

    def test_openai_provider_resolves_to_openai_adapter(self) -> None:
        pipeline = CitationStreamPipeline.for_provider("openai")
        assert isinstance(pipeline.adapter, OpenAIResponsesCitationStreamAdapter)

    def test_gemini_provider_resolves_to_gemini_adapter(self) -> None:
        pipeline = CitationStreamPipeline.for_provider("gemini")
        assert isinstance(pipeline.adapter, GeminiGroundingCitationStreamAdapter)

    def test_noop_adapter_is_passthrough(self) -> None:
        pipeline = CitationStreamPipeline.for_provider(None)
        assert asyncio.run(pipeline.adapt_chunk(chunk={}, raw_delta="hi")) == "hi"
        assert asyncio.run(pipeline.adapt_chunk(chunk={}, raw_delta=None)) is None

    def test_unbound_ledger_is_passthrough_for_every_real_adapter(self) -> None:
        # No ``bind_for_run`` call here. Each adapter must return raw_delta
        # unchanged when there's no ledger to register against.
        for provider in ("anthropic", "openai", "gemini"):
            pipeline = CitationStreamPipeline.for_provider(provider)
            assert (
                asyncio.run(pipeline.adapt_chunk(chunk={}, raw_delta="prose"))
                == "prose"
            )


# ---------------------------------------------------------------------------
# Anthropic adapter (PRD 01).
# ---------------------------------------------------------------------------


class TestAnthropicCitationStreamAdapter(_LedgerBuilderMixin):
    def test_text_only_chunk_returns_raw_delta(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = AnthropicCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[{"type": "text", "text": "Hello world", "index": 0}]
            )
            result = asyncio.run(
                adapter.adapt_chunk(chunk=chunk, raw_delta="Hello world")
            )
        finally:
            CitationLedger.unbind(token)
        assert result == "Hello world"
        assert events.drafts == []

    def test_citations_block_appends_chip_and_registers_source(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = AnthropicCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "",
                        "citations": [
                            {
                                "type": "char_location",
                                "url": "https://example.com/launch",
                                "title": "FY26 Q1 GTM plan",
                                "cited_text": "April 21",
                                "document_index": 0,
                                "start_char_index": 0,
                                "end_char_index": 24,
                            }
                        ],
                        "index": 0,
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        assert result == "[c1]"
        assert len(events.drafts) == 1
        citation = events.drafts[0].payload["citation"]
        assert citation["source_connector"] == "anthropic"
        assert citation["source_url"] == "https://example.com/launch"

    def test_text_and_citations_in_same_block_appends_chip_after_text(
        self,
    ) -> None:
        _, _, token = self._bind_ledger()
        try:
            adapter = AnthropicCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "April 21",
                        "citations": [
                            {
                                "url": "https://example.com/launch",
                                "title": "FY26 Q1 GTM plan",
                            }
                        ],
                        "index": 0,
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="April 21"))
        finally:
            CitationLedger.unbind(token)
        assert result == "April 21[c1]"

    def test_citation_without_url_or_title_is_skipped(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = AnthropicCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[{"type": "text", "text": "x", "citations": [{}]}]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="x"))
        finally:
            CitationLedger.unbind(token)
        assert result == "x"
        assert events.drafts == []

    def test_repeated_citation_to_same_source_dedupes(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = AnthropicCitationStreamAdapter()
            citation = {
                "url": "https://example.com/a",
                "title": "Doc A",
            }
            for _ in range(3):
                chunk = SimpleNamespace(
                    content=[{"type": "text", "text": "", "citations": [citation]}]
                )
                asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        assert len(events.drafts) == 1

    def test_chunk_with_dict_content_envelope(self) -> None:
        # The chunk arrives as a mapping with a ``message`` mapping carrying
        # the content (event-stream envelope shape).
        _, _, token = self._bind_ledger()
        try:
            adapter = AnthropicCitationStreamAdapter()
            chunk = {
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "",
                            "citations": [
                                {
                                    "url": "https://example.com/b",
                                    "title": "Doc B",
                                }
                            ],
                        }
                    ]
                }
            }
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        assert result == "[c1]"


# ---------------------------------------------------------------------------
# OpenAI Responses adapter (PRD 02).
# ---------------------------------------------------------------------------


class TestOpenAIResponsesCitationStreamAdapter(_LedgerBuilderMixin):
    def test_text_delta_passthrough(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = OpenAIResponsesCitationStreamAdapter()
            chunk = SimpleNamespace(content=[{"type": "text", "text": "fragment"}])
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="fragment"))
        finally:
            CitationLedger.unbind(token)
        assert result == "fragment"
        assert events.drafts == []

    def test_url_citation_registers_source_and_appends_chip(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = OpenAIResponsesCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://news.example.com/launch",
                                "title": "Launch news",
                                "start_index": 0,
                                "end_index": 30,
                            }
                        ],
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        assert result == "[c1]"
        assert len(events.drafts) == 1
        citation = events.drafts[0].payload["citation"]
        assert citation["source_connector"] == "openai_web"
        assert citation["source_url"] == "https://news.example.com/launch"

    def test_file_citation_registers_source_and_appends_chip(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = OpenAIResponsesCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "",
                        "annotations": [
                            {
                                "type": "file_citation",
                                "file_id": "file_abc",
                                "filename": "GTM-plan.pdf",
                            }
                        ],
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        assert result == "[c1]"
        assert len(events.drafts) == 1
        citation = events.drafts[0].payload["citation"]
        assert citation["source_connector"] == "openai_file"
        assert citation["title"] == "GTM-plan.pdf"

    def test_mixed_annotations_emit_chips_in_order(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = OpenAIResponsesCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://a.example.com",
                                "title": "A",
                            },
                            {
                                "type": "url_citation",
                                "url": "https://b.example.com",
                                "title": "B",
                            },
                            {
                                "type": "file_citation",
                                "file_id": "file_c",
                                "filename": "c.pdf",
                            },
                        ],
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        assert result == "[c1][c2][c3]"
        assert len(events.drafts) == 3

    def test_duplicate_annotation_dedupes_via_ledger(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = OpenAIResponsesCitationStreamAdapter()
            ann = {
                "type": "url_citation",
                "url": "https://repeat.example.com",
                "title": "Repeat",
            }
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "",
                        "annotations": [ann, ann, ann],
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta=None))
        finally:
            CitationLedger.unbind(token)
        # Same source key → same token, three times.
        assert result == "[c1][c1][c1]"
        # But only one source_ingested event.
        assert len(events.drafts) == 1

    def test_missing_required_fields_skip_silently(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = OpenAIResponsesCitationStreamAdapter()
            chunk = SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": "",
                        "annotations": [
                            {"type": "url_citation"},
                            {"type": "file_citation"},
                            {"type": "unknown_kind", "url": "x"},
                        ],
                    }
                ]
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="x"))
        finally:
            CitationLedger.unbind(token)
        assert result == "x"
        assert events.drafts == []


# ---------------------------------------------------------------------------
# Gemini grounding adapter (PRD 03).
# ---------------------------------------------------------------------------


class TestGeminiGroundingCitationStreamAdapter(_LedgerBuilderMixin):
    def test_chunk_with_no_metadata_passes_through(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(content="prose", response_metadata={})
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="prose"))
        finally:
            CitationLedger.unbind(token)
        assert result == "prose"
        assert events.drafts == []

    def test_web_grounding_chunk_appends_chip_and_registers_source(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(
                content="answer",
                response_metadata={
                    "grounding_metadata": {
                        "grounding_chunks": [
                            {"web": {"uri": "https://w.example.com", "title": "Web"}}
                        ],
                        "grounding_supports": [
                            {
                                "segment": {
                                    "start_index": 0,
                                    "end_index": 6,
                                    "text": "answer",
                                },
                                "grounding_chunk_indices": [0],
                            }
                        ],
                    }
                },
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="answer"))
        finally:
            CitationLedger.unbind(token)
        assert result == "answer[c1]"
        assert len(events.drafts) == 1
        citation = events.drafts[0].payload["citation"]
        assert citation["source_connector"] == "gemini_web"
        assert citation["source_url"] == "https://w.example.com"

    def test_retrieved_context_grounding(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(
                content="x",
                response_metadata={
                    "grounding_metadata": {
                        "grounding_chunks": [
                            {
                                "retrieved_context": {
                                    "uri": "vertex://corpus/doc-7",
                                    "title": "Doc 7",
                                }
                            }
                        ],
                        "grounding_supports": [
                            {"grounding_chunk_indices": [0]},
                        ],
                    }
                },
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="x"))
        finally:
            CitationLedger.unbind(token)
        assert result == "x[c1]"
        assert events.drafts[0].payload["citation"]["source_connector"] == (
            "gemini_retrieved"
        )

    def test_metadata_under_additional_kwargs_legacy_shape(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(
                content="y",
                additional_kwargs={
                    "grounding_metadata": {
                        "grounding_chunks": [
                            {"web": {"uri": "https://legacy.example.com", "title": "L"}}
                        ],
                        "grounding_supports": [
                            {"grounding_chunk_indices": [0]},
                        ],
                    }
                },
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="y"))
        finally:
            CitationLedger.unbind(token)
        assert result == "y[c1]"
        assert len(events.drafts) == 1

    def test_supports_indexing_into_multiple_chunks(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(
                content="z",
                response_metadata={
                    "grounding_metadata": {
                        "grounding_chunks": [
                            {"web": {"uri": "https://a.example", "title": "A"}},
                            {"web": {"uri": "https://b.example", "title": "B"}},
                        ],
                        "grounding_supports": [
                            {"grounding_chunk_indices": [0, 1]},
                        ],
                    }
                },
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="z"))
        finally:
            CitationLedger.unbind(token)
        assert result == "z[c1][c2]"
        assert len(events.drafts) == 2

    def test_repeated_chunk_index_across_supports_dedupes(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(
                content="q",
                response_metadata={
                    "grounding_metadata": {
                        "grounding_chunks": [
                            {"web": {"uri": "https://q.example", "title": "Q"}}
                        ],
                        "grounding_supports": [
                            {"grounding_chunk_indices": [0]},
                            {"grounding_chunk_indices": [0]},
                        ],
                    }
                },
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="q"))
        finally:
            CitationLedger.unbind(token)
        # Index 0 cited twice; the registry's seen-set prunes duplicates so
        # we emit exactly one chip and one event.
        assert result == "q[c1]"
        assert len(events.drafts) == 1

    def test_empty_supports_falls_back_to_citing_all_chunks(self) -> None:
        _, events, token = self._bind_ledger()
        try:
            adapter = GeminiGroundingCitationStreamAdapter()
            chunk = SimpleNamespace(
                content="r",
                response_metadata={
                    "grounding_metadata": {
                        "grounding_chunks": [
                            {"web": {"uri": "https://r.example", "title": "R"}}
                        ],
                    }
                },
            )
            result = asyncio.run(adapter.adapt_chunk(chunk=chunk, raw_delta="r"))
        finally:
            CitationLedger.unbind(token)
        assert result == "r[c1]"
        assert len(events.drafts) == 1
