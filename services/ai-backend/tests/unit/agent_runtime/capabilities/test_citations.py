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

from agent_runtime.api.events import RuntimeEventProducer
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

    def test_in_memory_store_lists_for_run_in_ordinal_order(self) -> None:
        ledger, _, store = self._build()

        asyncio.run(ledger.register(_NOTION_DOC))
        asyncio.run(ledger.register(_DRIVE_DOC))

        rows = store.list_for_run(org_id="org_cite", run_id="run_cite")
        assert [row.ordinal for row in rows] == [1, 2]

    def test_in_memory_store_lists_for_conversation(self) -> None:
        ledger, _, store = self._build()
        asyncio.run(ledger.register(_NOTION_DOC))

        rows = store.list_for_conversation(
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
