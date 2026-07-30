"""Unit tests for the citation ledger (PR 1.1).

The ledger is the only seam tools, provider adapters, and replay paths use,
so these tests cover the contract end-to-end against an in-memory store and
a recording event producer:

  * idempotency on (run, connector, doc_id)
  * monotonic ordinals + base36 token format
  * exactly one ``source_ingested`` event per unique source
  * per-run cap drops cleanly without raising
  * ``CitationLedger.cite`` no-ops when no ledger is bound (degradation)
  * sealed payloads order matches ordinal allocation
  * the projector lights up activity_kind / display_title / status
  * the payload extractor whitelists fields
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.citation_projection import CitationProjector
from agent_runtime.capabilities.tool_result_notes import ToolResultNote
from agent_runtime.capabilities.citations import CitationLedger, SourceRef
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class _RecordingPersistence:
    def __init__(self) -> None:
        self.latest_sequence_no: int | None = None

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        self.latest_sequence_no = latest_sequence_no


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


def _run_record(run_id: str = "run_cite") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id="conv_cite",
        org_id="org_cite",
        user_id="user_cite",
        user_message_id="msg_cite",
        trace_id="trace_cite",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_cite",
            org_id="org_cite",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id=run_id,
            trace_id="trace_cite",
        ),
    )


class CitationLedgerFixtureMixin:
    """Build a ledger backed by a recording producer + in-memory store."""

    def _build(
        self, *, run_id: str = "run_cite", per_run_max: int = 50
    ) -> tuple[CitationLedger, _RecordingEventStore, InMemoryCitationStore]:
        store = InMemoryCitationStore()
        events = _RecordingEventStore()
        producer = RuntimeEventProducer(
            persistence=_RecordingPersistence(),
            event_store=events,
        )
        ledger = CitationLedger(
            run=_run_record(run_id),
            store=store,
            producer=producer,
            source=StreamEventSource.TOOL,
            per_run_max=per_run_max,
        )
        return ledger, events, store


class _NotionSource(SourceRef):
    """Fixture source used by multiple tests."""

    pass


_NOTION_DOC = SourceRef(
    source_connector="notion",
    source_doc_id="page_123",
    title="Aurora 4.0 — Approved Positioning v3",
    source_url="https://example.com/notion/page_123",
    snippet="Aurora 4.0 brings agentic search to every desk.",
    source_tool_call_id="tool_call_abc",
)
_DRIVE_DOC = SourceRef(
    source_connector="drive",
    source_doc_id="file_456",
    title="FY26 Q1 GTM plan",
    source_url="https://example.com/drive/file_456",
)


class TestCitationLedger(CitationLedgerFixtureMixin):
    def test_register_returns_token_in_expected_format(self) -> None:
        ledger, _, _ = self._build()
        token = asyncio.run(ledger.register(_NOTION_DOC))
        assert token == "[c1]"

    def test_register_is_idempotent_on_run_source_pair(self) -> None:
        ledger, events, store = self._build()

        token_first = asyncio.run(ledger.register(_NOTION_DOC))
        token_second = asyncio.run(ledger.register(_NOTION_DOC))

        assert token_first == token_second == "[c1]"
        # Exactly one event + one row even though we called register twice.
        assert len(events.drafts) == 1
        assert len(store.rows) == 1

    def test_register_allocates_monotonic_ordinals(self) -> None:
        ledger, events, _ = self._build()

        first = asyncio.run(ledger.register(_NOTION_DOC))
        second = asyncio.run(ledger.register(_DRIVE_DOC))

        assert first == "[c1]"
        assert second == "[c2]"
        # Two events, each carrying the matching ordinal in the citation
        # payload (FE relies on the ordinal for chip ordering).
        assert [draft.payload["citation"]["ordinal"] for draft in events.drafts] == [
            1,
            2,
        ]

    def test_register_emits_source_ingested_with_tool_activity_kind(self) -> None:
        ledger, events, _ = self._build()

        asyncio.run(ledger.register(_NOTION_DOC))

        draft = events.drafts[0]
        assert draft.event_type is RuntimeApiEventType.SOURCE_INGESTED
        assert draft.activity_kind is RuntimeActivityKind.TOOL
        # Display title is "Cited <title>" — sanitized through the projector.
        assert draft.display_title == "Cited Aurora 4.0 — Approved Positioning v3"

    def test_register_caps_at_per_run_max_and_drops_silently(self) -> None:
        ledger, events, _ = self._build(per_run_max=2)

        asyncio.run(ledger.register(_NOTION_DOC))
        asyncio.run(ledger.register(_DRIVE_DOC))
        # Third unique source exceeds the cap; expect empty token + no event.
        token = asyncio.run(
            ledger.register(
                SourceRef(
                    source_connector="slack",
                    source_doc_id="msg_789",
                    title="Marcus on press timing",
                )
            )
        )
        assert token == ""
        assert len(events.drafts) == 2

    def test_sealed_payloads_orders_by_ordinal(self) -> None:
        ledger, _, _ = self._build()

        asyncio.run(ledger.register(_NOTION_DOC))
        asyncio.run(ledger.register(_DRIVE_DOC))

        sealed = ledger.sealed_payloads()
        assert [row["ordinal"] for row in sealed] == [1, 2]
        assert [row["source_connector"] for row in sealed] == ["notion", "drive"]

    def test_cite_classmethod_is_noop_when_no_ledger_bound(self) -> None:
        # No bind_for_run → cite returns "" (graceful degradation per spec §3.6).
        token = asyncio.run(CitationLedger.cite(_NOTION_DOC))
        assert token == ""

    def test_cite_classmethod_resolves_active_ledger(self) -> None:
        ledger, _, _ = self._build()
        token_obj = CitationLedger.bind_for_run(ledger)
        try:
            token = asyncio.run(CitationLedger.cite(_NOTION_DOC))
        finally:
            CitationLedger.unbind(token_obj)
        assert token == "[c1]"

    async def test_in_memory_store_lists_for_run_in_ordinal_order(self) -> None:
        ledger, _, store = self._build()

        await ledger.register(_NOTION_DOC)
        await ledger.register(_DRIVE_DOC)

        rows = await store.list_for_run(org_id="org_cite", run_id="run_cite")
        assert [row.ordinal for row in rows] == [1, 2]

    async def test_in_memory_store_lists_for_conversation(self) -> None:
        ledger, _, store = self._build()
        await ledger.register(_NOTION_DOC)

        rows = await store.list_for_conversation(
            org_id="org_cite",
            conversation_id="conv_cite",
        )
        assert len(rows) == 1
        assert rows[0].title == _NOTION_DOC.title


class TestCitationProjection:
    def test_activity_kind_is_tool_for_source_ingested(self) -> None:
        kind = RuntimeEventPresentationProjector.activity_kind_for(
            event_type=RuntimeApiEventType.SOURCE_INGESTED,
            source=StreamEventSource.TOOL,
        )
        assert kind is RuntimeActivityKind.TOOL

    def test_payload_extractor_whitelists_fields(self) -> None:
        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.SOURCE_INGESTED,
            payload={
                "citation": {
                    "citation_id": "c1",
                    "ordinal": 1,
                    "source_connector": "notion",
                    "source_doc_id": "page_123",
                    "source_url": "https://example.com",
                    "title": "Title",
                    "snippet": "Snippet",
                    "freshness_at": None,
                    "source_tool_call_id": None,
                    # Extra fields a future caller might smuggle in must be dropped.
                    "secret": "leak",
                },
            },
        )
        citation = projected["citation"]
        assert "secret" not in citation
        assert citation["citation_id"] == "c1"
        assert citation["ordinal"] == 1
        # None-allowed fields survive as None.
        assert citation["freshness_at"] is None

    def test_payload_extractor_returns_empty_when_citation_missing(self) -> None:
        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.SOURCE_INGESTED,
            payload={"unrelated": True},
        )
        assert projected == {}


# P7 — register_many + sources_ingested batch path.


class TestCitationLedgerRegisterMany(CitationLedgerFixtureMixin):
    """Batch-ingestion path used by CitationProjector after PR2 lands."""

    def test_register_many_returns_tokens_in_input_order(self) -> None:
        ledger, _, _ = self._build()
        tokens = asyncio.run(ledger.register_many([_NOTION_DOC, _DRIVE_DOC]))
        # Allocation order matches input order; tokens are base36 1-indexed.
        assert tokens == ["[c1]", "[c2]"]

    def test_register_many_emits_one_sources_ingested_event(self) -> None:
        ledger, events, store = self._build()
        asyncio.run(ledger.register_many([_NOTION_DOC, _DRIVE_DOC]))

        # Exactly one event for the whole batch — not one per source.
        assert len(events.drafts) == 1
        draft = events.drafts[0]
        assert draft.event_type is RuntimeApiEventType.SOURCES_INGESTED
        assert draft.activity_kind is RuntimeActivityKind.TOOL
        # Citations payload preserves allocation order; ordinals are 1, 2.
        citations = draft.payload["citations"]
        assert [c["ordinal"] for c in citations] == [1, 2]
        assert [c["source_connector"] for c in citations] == ["notion", "drive"]
        # Both rows persisted.
        assert len(store.rows) == 2

    def test_register_many_with_single_source_still_emits_sources_ingested(
        self,
    ) -> None:
        # The plural event type is the caller's intent signal — even N=1
        # batches go through SOURCES_INGESTED so replay can distinguish
        # batched vs. per-source emitters.
        ledger, events, _ = self._build()
        tokens = asyncio.run(ledger.register_many([_NOTION_DOC]))

        assert tokens == ["[c1]"]
        assert len(events.drafts) == 1
        assert events.drafts[0].event_type is RuntimeApiEventType.SOURCES_INGESTED
        citations = events.drafts[0].payload["citations"]
        assert len(citations) == 1
        assert citations[0]["ordinal"] == 1

    def test_register_many_returns_empty_for_empty_input(self) -> None:
        ledger, events, store = self._build()
        tokens = asyncio.run(ledger.register_many([]))
        assert tokens == []
        # No event, no DB write.
        assert events.drafts == []
        assert store.rows == ()

    def test_register_many_idempotent_on_duplicate_in_batch(self) -> None:
        """Re-citing the same (connector, doc_id) within one batch reuses the ordinal."""

        ledger, events, store = self._build()
        # Same _NOTION_DOC appears twice in the input.
        tokens = asyncio.run(ledger.register_many([_NOTION_DOC, _NOTION_DOC]))

        # Both tokens point at ordinal 1 — second occurrence hits cache (filled
        # by the first iteration before insert) and is NOT re-allocated.
        assert tokens == ["[c1]", "[c1]"]
        # One event with one citation; one row in the store.
        assert len(events.drafts) == 1
        citations = events.drafts[0].payload["citations"]
        assert len(citations) == 1
        assert len(store.rows) == 1

    def test_register_many_mixed_cache_hits_and_misses(self) -> None:
        """Tokens for cache hits carry over without re-emission."""

        ledger, events, store = self._build()
        # Pre-seed _NOTION_DOC via an earlier call (separate event).
        asyncio.run(ledger.register(_NOTION_DOC))
        assert len(events.drafts) == 1  # one source_ingested

        # Now batch with the cached one + a fresh one. Only the fresh one is
        # newly inserted, and the event's citations array carries only the
        # new record.
        tokens = asyncio.run(ledger.register_many([_NOTION_DOC, _DRIVE_DOC]))
        assert tokens == ["[c1]", "[c2]"]
        # One additional event (sources_ingested) carrying just the new one.
        assert len(events.drafts) == 2
        new_event = events.drafts[1]
        assert new_event.event_type is RuntimeApiEventType.SOURCES_INGESTED
        citations = new_event.payload["citations"]
        assert [c["ordinal"] for c in citations] == [2]
        assert citations[0]["source_connector"] == "drive"
        assert len(store.rows) == 2

    def test_register_many_no_event_when_all_cache_hits(self) -> None:
        ledger, events, _ = self._build()
        asyncio.run(ledger.register(_NOTION_DOC))
        asyncio.run(ledger.register(_DRIVE_DOC))
        assert len(events.drafts) == 2

        # Re-batch the same two — all hits, no new event.
        tokens = asyncio.run(ledger.register_many([_NOTION_DOC, _DRIVE_DOC]))
        assert tokens == ["[c1]", "[c2]"]
        assert len(events.drafts) == 2  # unchanged

    def test_register_many_respects_per_run_cap_within_batch(self) -> None:
        ledger, events, store = self._build(per_run_max=2)
        third = SourceRef(
            source_connector="slack",
            source_doc_id="msg_789",
            title="Marcus on press timing",
        )

        tokens = asyncio.run(ledger.register_many([_NOTION_DOC, _DRIVE_DOC, third]))
        # First two allocate; third drops at cap with empty token.
        assert tokens == ["[c1]", "[c2]", ""]
        # Event carries only the two that fit.
        assert len(events.drafts) == 1
        citations = events.drafts[0].payload["citations"]
        assert [c["ordinal"] for c in citations] == [1, 2]
        assert len(store.rows) == 2

    def test_register_after_register_many_continues_ordinals(self) -> None:
        """Mixing the two APIs preserves monotonic ordinal allocation."""

        ledger, events, _ = self._build()
        asyncio.run(ledger.register_many([_NOTION_DOC, _DRIVE_DOC]))
        token = asyncio.run(
            ledger.register(
                SourceRef(
                    source_connector="slack",
                    source_doc_id="msg_789",
                    title="Marcus on press timing",
                )
            )
        )
        assert token == "[c3]"
        # 1 sources_ingested + 1 source_ingested.
        assert [d.event_type for d in events.drafts] == [
            RuntimeApiEventType.SOURCES_INGESTED,
            RuntimeApiEventType.SOURCE_INGESTED,
        ]


class TestSourcesIngestedProjection:
    """Wire-shape projector tests for the new event type."""

    def test_activity_kind_is_tool_for_sources_ingested(self) -> None:
        kind = RuntimeEventPresentationProjector.activity_kind_for(
            event_type=RuntimeApiEventType.SOURCES_INGESTED,
            source=StreamEventSource.TOOL,
        )
        assert kind is RuntimeActivityKind.TOOL

    def test_payload_extractor_whitelists_each_citation(self) -> None:
        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.SOURCES_INGESTED,
            payload={
                "citations": [
                    {
                        "citation_id": "c1",
                        "ordinal": 1,
                        "source_connector": "notion",
                        "source_doc_id": "page_123",
                        "source_url": "https://example.com/notion/page_123",
                        "title": "Title 1",
                        "snippet": "Snippet 1",
                        "freshness_at": None,
                        "source_tool_call_id": None,
                        # Extra fields a future caller might smuggle in must be dropped.
                        "secret": "leak",
                    },
                    {
                        "citation_id": "c2",
                        "ordinal": 2,
                        "source_connector": "drive",
                        "source_doc_id": "file_456",
                        "source_url": None,
                        "title": "Title 2",
                        "snippet": None,
                        "freshness_at": None,
                        "source_tool_call_id": None,
                        "another_secret": 42,
                    },
                ],
                # Extra top-level field also dropped.
                "noise": "ignored",
            },
        )
        assert set(projected.keys()) == {"citations"}
        citations = projected["citations"]
        assert len(citations) == 2
        assert "secret" not in citations[0]
        assert "another_secret" not in citations[1]
        assert citations[0]["citation_id"] == "c1"
        assert citations[1]["citation_id"] == "c2"
        # Order preserved (FE relies on this for ordinal binding).
        assert [c["ordinal"] for c in citations] == [1, 2]
        # None-allowed fields survive as None.
        assert citations[1]["source_url"] is None
        assert citations[1]["snippet"] is None

    def test_payload_extractor_returns_empty_list_when_citations_missing(
        self,
    ) -> None:
        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.SOURCES_INGESTED,
            payload={"unrelated": True},
        )
        assert projected == {"citations": []}

    def test_payload_extractor_skips_non_dict_entries(self) -> None:
        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.SOURCES_INGESTED,
            payload={
                "citations": [
                    "not-a-dict",
                    {
                        "citation_id": "c1",
                        "title": "Real one",
                        "ordinal": 1,
                        "source_connector": "notion",
                        "source_doc_id": "page_123",
                    },
                    None,
                ],
            },
        )
        # Only the valid dict survives.
        assert len(projected["citations"]) == 1
        assert projected["citations"][0]["citation_id"] == "c1"

    @pytest.mark.parametrize(
        "count, expected",
        [(1, "Cited 1 source"), (2, "Cited 2 sources"), (50, "Cited 50 sources")],
    )
    def test_display_title_uses_count(self, count: int, expected: str) -> None:
        title = RuntimeEventPresentationProjector._display_title_for(  # noqa: SLF001
            event_type=RuntimeApiEventType.SOURCES_INGESTED,
            payload={"citations": [{"ordinal": i + 1} for i in range(count)]},
        )
        assert title == expected

    def test_display_title_falls_back_when_citations_missing(self) -> None:
        title = RuntimeEventPresentationProjector._display_title_for(  # noqa: SLF001
            event_type=RuntimeApiEventType.SOURCES_INGESTED,
            payload={},
        )
        assert title == "Cited sources"


class TestBase36Token:
    @pytest.mark.parametrize(
        ("ordinal", "expected"),
        [
            (1, "[c1]"),
            (10, "[ca]"),
            (35, "[cz]"),
            (36, "[c10]"),
            (1296, "[c100]"),
        ],
    )
    def test_token_for_ordinals(self, ordinal: int, expected: str) -> None:
        assert CitationLedger._token_for(ordinal) == expected

    def test_to_base36_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError):
            CitationLedger._to_base36(0)


# ── tool-result ENVELOPES: one SSOT, and the symmetry that must hold ─────────


_ROWS: list[dict[str, str]] = [
    {
        "title": "LangGraph",
        "link": "https://langchain-ai.github.io/langgraph/",
        "snippet": "LangGraph is a framework for agent workflows.",
    },
    {"title": "Docs", "link": "https://example.com/docs", "snippet": "Second."},
]
_MCP_BLOCKS = [{"type": "resource", "resource": {"uri": "https://a.b", "title": "T"}}]


class _DuckedEnvelope:
    """A non-Pydantic object exposing ``.content`` (custom MCP-ish client)."""

    content = _MCP_BLOCKS


class _McpBlock(BaseModel):
    type: str = "resource"
    resource: dict


class _McpCallToolResult(BaseModel):
    """Stands in for the MCP client's ``CallToolResult`` (a Pydantic model)."""

    content: list[_McpBlock]


# Every envelope a tool result can arrive in. The projector must see through all
# of them; the point of the table is that adding a row here is how a new shape
# gets taught, in one place, to every consumer.
_ENVELOPES: list[tuple[str, object]] = [
    # DuckDuckGo declares response_format="content_and_artifact" for ALL three
    # output_formats, so every web search is a 2-tuple. This is the shape that
    # silently registered nothing and left the Sources rail empty.
    ("ddg_output_format_list", (_ROWS, _ROWS)),
    ("ddg_output_format_string", ("title: x, link: https://a.b", _ROWS)),
    ("ddg_output_format_json", ('[{"link": "x"}]', _ROWS)),
    ("bare_results_list", _ROWS),
    ("results_key_dict", {"results": _ROWS}),
    ("single_resource_dict", {"resource": {"uri": "https://a.b", "title": "T"}}),
    # The live MCP path (operation_adapter.execute_read) passes a dict.
    ("mcp_content_blocks_dict", {"content": _MCP_BLOCKS}),
    # DEFENSIVE, reader-only: no current caller hands the projector an object
    # form, but a future MCP client that returns its model directly would
    # otherwise regress silently to zero sources. See the directional-invariant
    # note in TestNoteAndProjectorAgreeOnShapes for why reader-only is safe.
    (
        "mcp_call_tool_result_model",
        _McpCallToolResult(
            content=[_McpBlock(resource={"uri": "https://a.b", "title": "T"})]
        ),
    ),
    ("ducked_content_object", _DuckedEnvelope()),
    # Arity is not a contract: a 3-tuple must still be walked, not discarded.
    ("tuple_arity_three", (_ROWS, _ROWS, _ROWS)),
    ("nested_tuple_of_list", ((_ROWS,), _ROWS)),
]


class TestToolResultEnvelopes:
    """Envelope unwrapping is delegated to ToolResultPayloads — verify the union."""

    @pytest.mark.parametrize("label,result", _ENVELOPES, ids=[e[0] for e in _ENVELOPES])
    def test_every_envelope_yields_at_least_one_source(
        self, label: str, result: object
    ) -> None:
        sources = list(CitationProjector._extract_sources("web_search", result))
        assert sources, f"{label}: envelope yielded no sources"
        assert all(s.source_url or s.source_doc_id for s in sources)

    def test_content_wins_so_the_artifact_copy_is_not_double_registered(self) -> None:
        # output_format="list" hands identical rows as content AND artifact. The
        # first payload that yields must win, or every web result registers twice.
        sources = list(CitationProjector._extract_sources("web_search", (_ROWS, _ROWS)))
        assert len(sources) == len(_ROWS)

    def test_artifact_is_the_fallback_when_content_is_opaque(self) -> None:
        # output_format="string": content is an unparseable blob, artifact holds
        # the rows. Content-only unwrapping would silently under-report here.
        sources = list(
            CitationProjector._extract_sources("web_search", ("opaque blob", _ROWS))
        )
        assert [s.source_url for s in sources] == [
            "https://langchain-ai.github.io/langgraph/",
            "https://example.com/docs",
        ]

    @pytest.mark.parametrize(
        "result",
        [None, "plain string", 42, (), {}, [], {"unrelated": "keys"}],
        ids=[
            "none",
            "str",
            "int",
            "empty_tuple",
            "empty_dict",
            "empty_list",
            "no_keys",
        ],
    )
    def test_sourceless_results_yield_nothing_without_raising(
        self, result: object
    ) -> None:
        assert list(CitationProjector._extract_sources("x", result)) == []

    def test_a_model_dump_that_raises_is_swallowed(self) -> None:
        # Enrichment must never break the tool call it is enriching.
        class Hostile:
            def model_dump(self) -> dict:
                raise RuntimeError("boom")

        assert list(CitationProjector._extract_sources("x", Hostile())) == []


class TestNoteAndProjectorAgreeOnShapes:
    """The invariant whose violation caused the empty-Sources bug.

    ``ToolResultNote`` (write side: where to append the ``[[N]]`` hint) and the
    projector (read side: what to scan for sources) are two halves of the same
    tool wrapper. The asymmetry is DIRECTIONAL, and only one direction is a bug:

    * writer understands, reader does not  → the bug we shipped. The model is
      told "cite this as [[1]]", emits the marker, the chip resolves… and no
      source was ever registered, so the Sources rail is empty. Silent and
      maximally confusing, because the visible half works.
    * reader understands, writer does not  → merely degraded. No hint means the
      model does not emit ``[[N]]``, but sources still register and still show up
      in Sources. Nothing lies to the user.

    So the invariant is implication, not equality: anything the writer can
    annotate, the reader MUST be able to see into. A reader that knows more
    shapes is strictly safe, which is why the two defensive object envelopes in
    the corpus (Pydantic ``CallToolResult`` / duck-typed ``.content``) are
    allowed to be reader-only — the live MCP path passes a dict (verified at
    ``operation_adapter.execute_read``), so the writer never meets them.
    """

    @pytest.mark.parametrize("label,result", _ENVELOPES, ids=[e[0] for e in _ENVELOPES])
    def test_anything_the_writer_annotates_the_reader_can_read(
        self, label: str, result: object
    ) -> None:
        annotated = ToolResultNote.append(result, note="[note]", dict_key="_k")
        writer_understood = annotated is not result
        reader_understood = bool(
            list(CitationProjector._extract_sources("web_search", result))
        )
        if not writer_understood:
            pytest.skip(f"{label}: writer does not annotate this shape")
        assert reader_understood, (
            f"{label}: the note writer annotates this envelope but the source "
            f"reader cannot see into it — this is the exact asymmetry that made "
            f"[[N]] chips work while the Sources rail stayed empty"
        )


class TestWebSearchTupleEndToEnd(CitationLedgerFixtureMixin):
    """The reported bug, through the real ledger: one sources_ingested event."""

    def test_web_search_tuple_emits_sources_ingested_with_tool_call_id(self) -> None:
        ledger, events, store = self._build()
        token = CitationLedger.bind_for_run(ledger)
        try:
            asyncio.run(
                CitationProjector.project(
                    connector="web_search",
                    tool_call_id="toolu_abc",
                    result=(_ROWS, _ROWS),
                )
            )
        finally:
            CitationLedger.unbind(token)

        assert len(events.drafts) == 1
        draft = events.drafts[0]
        assert draft.event_type is RuntimeApiEventType.SOURCES_INGESTED
        citations = draft.payload["citations"]
        assert [c["source_url"] for c in citations] == [
            "https://langchain-ai.github.io/langgraph/",
            "https://example.com/docs",
        ]
        # Stamped so a Sources row ties back to the call that produced it.
        assert {c["source_tool_call_id"] for c in citations} == {"toolu_abc"}
        assert len(store.rows) == 2
