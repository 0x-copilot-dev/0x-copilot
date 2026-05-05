"""PR 3.5 / G11 — replay parity for the citation/sources surface.

A run that ingests N citations live (each producing a ``source_ingested``
stream event + a persisted ``runtime_citations`` row) must produce an
identical projection when the events are replayed through the FE-side
reducer **and** when the persisted store is read directly via
:class:`WorkspaceFeedService.list_sources`. Both paths feed the Sources
tab; if they drift, an archived thread renders differently from a live
one — the bug PR 3.1 §1.4 calls out.

This is a unit-level test against the in-memory adapter — no Postgres,
no FastAPI client. The contract under test is the *invariant* between
the streaming envelope's ``payload.citation`` and the row the store
hands back.
"""

from __future__ import annotations


import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.workspace_feed_service import WorkspaceFeedService
from agent_runtime.capabilities.citations import CitationLedger, SourceRef
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_adapters.in_memory.source_store import InMemorySourceStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# Three different docs across two connectors so the parity test exercises
# the deduplication path in `_SourceAggregator` and the per-citation
# ordinal path in the ledger / chip registry.
_NOTION = SourceRef(
    source_connector="notion",
    source_doc_id="page_123",
    title="Aurora 4.0 — Approved Positioning v3",
    source_url="https://example.com/notion/page_123",
    snippet="Aurora 4.0 brings agentic search to every desk.",
    source_tool_call_id="tc_abc",
)
_DRIVE = SourceRef(
    source_connector="drive",
    source_doc_id="file_456",
    title="FY26 Q1 GTM plan",
    source_url="https://example.com/drive/file_456",
    snippet="Three-phase rollout: design partners, GA, public webinar.",
)
_SLACK = SourceRef(
    source_connector="slack",
    source_doc_id="msg_789",
    title="#launch-aurora — Marcus on press timing",
    source_url=None,
    snippet="embargo lifts 9am ET on the 21st",
)


class _RecordingPersistence:
    def __init__(self) -> None:
        self.latest_sequence_no: int | None = None

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        self.latest_sequence_no = latest_sequence_no


class _RecordingEventStore:
    """Captures every draft so the test can replay them as envelopes."""

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


def _run_record(
    *,
    run_id: str = "run_replay",
    conversation_id: str = "conv_replay",
    org_id: str = "org_replay",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id=conversation_id,
        org_id=org_id,
        user_id="user_replay",
        user_message_id="msg_replay",
        trace_id="trace_replay",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_replay",
            org_id=org_id,
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
            trace_id="trace_replay",
        ),
    )


def _build_ledger() -> tuple[
    CitationLedger,
    _RecordingEventStore,
    InMemoryCitationStore,
    RunRecord,
]:
    store = InMemoryCitationStore()
    events = _RecordingEventStore()
    producer = RuntimeEventProducer(
        persistence=_RecordingPersistence(),
        event_store=events,
    )
    run = _run_record()
    ledger = CitationLedger(
        run=run,
        store=store,
        producer=producer,
        source=StreamEventSource.TOOL,
        per_run_max=50,
    )
    return ledger, events, store, run


def _projection_from_events(
    events: _RecordingEventStore,
) -> dict[str, dict[str, object]]:
    """Replay-equivalent: rebuild a citation_id-keyed projection from drafts.

    Mirrors the FE's ``buildCitationRegistry`` — every ``source_ingested``
    contributes one row keyed by its ``citation_id``; later events for
    the same id are no-ops (idempotent on replay).
    """
    out: dict[str, dict[str, object]] = {}
    for draft in events.drafts:
        if draft.event_type is not RuntimeApiEventType.SOURCE_INGESTED:
            continue
        citation = draft.payload["citation"]
        out.setdefault(citation["citation_id"], dict(citation))
    return out


def _projection_from_store(
    store: InMemoryCitationStore,
    *,
    org_id: str,
    conversation_id: str,
) -> dict[str, dict[str, object]]:
    """Direct read from the persisted store — what archived loads return."""
    rows = store.list_for_conversation(org_id=org_id, conversation_id=conversation_id)
    return {
        row.citation_id: {
            "citation_id": row.citation_id,
            "ordinal": row.ordinal,
            "source_connector": row.source_connector,
            "source_doc_id": row.source_doc_id,
            "source_url": row.source_url,
            "title": row.title,
            "snippet": row.snippet,
            "source_tool_call_id": row.source_tool_call_id,
        }
        for row in rows
    }


SHARED_FIELDS = (
    "citation_id",
    "ordinal",
    "source_connector",
    "source_doc_id",
    "source_url",
    "title",
    "snippet",
)


class TestSourcesReplayParity:
    async def test_live_events_and_persisted_store_agree(self) -> None:
        ledger, events, store, run = _build_ledger()

        await ledger.register(_NOTION)
        await ledger.register(_DRIVE)
        await ledger.register(_SLACK)
        # Replay duplicate — must be a no-op (idempotency on
        # (run, connector, doc_id) per migration 0015).
        await ledger.register(_NOTION)

        from_events = _projection_from_events(events)
        from_store = _projection_from_store(
            store,
            org_id=run.org_id,
            conversation_id=run.conversation_id,
        )

        assert set(from_events.keys()) == set(from_store.keys())
        for citation_id in from_events:
            for field in SHARED_FIELDS:
                assert (
                    from_events[citation_id][field] == from_store[citation_id][field]
                ), f"{field} disagrees for {citation_id}"

    async def test_workspace_feed_service_matches_streamed_set(self) -> None:
        ledger, events, store, run = _build_ledger()
        for source in (_NOTION, _DRIVE, _SLACK):
            await ledger.register(source)

        feed = WorkspaceFeedService(
            subagent_store=_NullSubagentStore(),
            source_store=InMemorySourceStore(citations=store),
        )
        response = await feed.list_sources(
            org_id=run.org_id,
            conversation_id=run.conversation_id,
            run_id=None,
            limit=200,
        )

        # The aggregate dedupes on (connector, doc_id); for distinct docs
        # the cardinality must match the number of source_ingested events.
        ingested = [
            d
            for d in events.drafts
            if d.event_type is RuntimeApiEventType.SOURCE_INGESTED
        ]
        assert len(response.sources) == len(ingested)

        # Round-trip the (connector, doc_id) tuples through both paths;
        # they must agree as sets.
        from_events = {
            (
                d.payload["citation"]["source_connector"],
                d.payload["citation"]["source_doc_id"],
            )
            for d in ingested
        }
        from_feed = {
            (entry.source_connector, entry.source_doc_id) for entry in response.sources
        }
        assert from_events == from_feed

    async def test_run_id_filter_narrows_feed(self) -> None:
        ledger, _events, store, run = _build_ledger()
        await ledger.register(_NOTION)
        await ledger.register(_DRIVE)

        feed = WorkspaceFeedService(
            subagent_store=_NullSubagentStore(),
            source_store=InMemorySourceStore(citations=store),
        )
        response = await feed.list_sources(
            org_id=run.org_id,
            conversation_id=run.conversation_id,
            run_id="run_does_not_exist",
            limit=200,
        )
        # Filtering by an unknown run_id removes every row.
        assert response.sources == ()


class _NullSubagentStore:
    """Stub the orthogonal subagent dependency so this test stays focused."""

    def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        running_only: bool,
        limit: int,
    ) -> tuple[()]:
        return ()
