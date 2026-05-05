"""Unit tests for the PR 1.5 workspace-feed stores (in-memory)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas.common import RuntimeActivityKind
from agent_runtime.persistence.records import (
    CitationRecord,
    SubagentLifecycleStatus,
)
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_adapters.in_memory.source_store import InMemorySourceStore
from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)


_ORG = "org_acme"
_CONV = "conv_launch"
_RUN = "run_alpha"
_RUN_OTHER = "run_beta"


@dataclass
class _StubRun:
    """Duck-typed stand-in for RunRecord (the three fields the store reads)."""

    run_id: str
    org_id: str
    conversation_id: str


@dataclass
class _StubStore:
    """Minimal in-memory shape the SubagentStore walks via getattr."""

    runs: dict[str, _StubRun] = field(default_factory=dict)
    events_by_run: dict[str, list[RuntimeEventEnvelope]] = field(default_factory=dict)


class _RuntimeStubs:
    """Constructors for the small synthetic state the in-memory stores walk."""

    @staticmethod
    def run_record(
        *,
        run_id: str = _RUN,
        org_id: str = _ORG,
        conversation_id: str = _CONV,
    ) -> _StubRun:
        return _StubRun(run_id=run_id, org_id=org_id, conversation_id=conversation_id)

    @staticmethod
    def subagent_event(
        *,
        run_id: str,
        task_id: str,
        event_type: RuntimeApiEventType,
        sequence_no: int,
        created_at: datetime,
        summary: str | None = None,
        display_title: str | None = None,
        status: str | None = None,
        subagent_id: str | None = "research",
        payload: dict | None = None,
    ) -> RuntimeEventEnvelope:
        return RuntimeEventEnvelope(
            run_id=run_id,
            conversation_id=_CONV,
            source=StreamEventSource.SUBAGENT,
            event_type=event_type,
            activity_kind=RuntimeActivityKind.SUBAGENT,
            trace_id=f"trace_{task_id}_{sequence_no}",
            task_id=task_id,
            subagent_id=subagent_id,
            display_title=display_title,
            summary=summary,
            status=status,
            sequence_no=sequence_no,
            created_at=created_at,
            payload=dict(payload or {}),
        )


@pytest.fixture
def store() -> _StubStore:
    s = _StubStore()
    s.runs[_RUN] = _RuntimeStubs.run_record()
    s.runs[_RUN_OTHER] = _RuntimeStubs.run_record(
        run_id=_RUN_OTHER, conversation_id="conv_other"
    )
    s.events_by_run[_RUN] = []
    s.events_by_run[_RUN_OTHER] = []
    return s


def _at(seconds: int) -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=seconds
    )


class TestInMemorySubagentStore:
    def test_returns_empty_when_no_events(self, store: _StubStore) -> None:
        adapter = InMemorySubagentStore(store)
        result = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=False, limit=10
        )
        assert result == ()

    def test_projects_started_progress_completed_into_one_snapshot(
        self, store: _StubStore
    ) -> None:
        store.events_by_run[_RUN].extend(
            [
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_1",
                    event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                    sequence_no=1,
                    created_at=_at(0),
                    summary="Investigate competitive frame",
                    display_title="Competitive frame",
                    subagent_id="research",
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_1",
                    event_type=RuntimeApiEventType.SUBAGENT_PROGRESS,
                    sequence_no=2,
                    created_at=_at(5),
                    display_title="Reading positioning doc",
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_1",
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    sequence_no=3,
                    created_at=_at(12),
                    summary="Glean leads on legacy search; we lead on agentic action.",
                ),
            ]
        )
        adapter = InMemorySubagentStore(store)
        result = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=False, limit=10
        )
        assert len(result) == 1
        snapshot = result[0]
        assert snapshot.task_id == "task_1"
        assert snapshot.status is SubagentLifecycleStatus.COMPLETED
        assert snapshot.subagent_name == "research"
        assert snapshot.started_at == _at(0)
        assert snapshot.completed_at == _at(12)
        assert snapshot.duration_ms == 12_000
        assert snapshot.objective_summary == "Investigate competitive frame"
        assert snapshot.result_summary == (
            "Glean leads on legacy search; we lead on agentic action."
        )

    def test_running_only_filters_completed(self, store: _StubStore) -> None:
        store.events_by_run[_RUN].extend(
            [
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_done",
                    event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                    sequence_no=1,
                    created_at=_at(0),
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_done",
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    sequence_no=2,
                    created_at=_at(2),
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_running",
                    event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                    sequence_no=3,
                    created_at=_at(3),
                ),
            ]
        )
        adapter = InMemorySubagentStore(store)
        all_snapshots = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=False, limit=10
        )
        running_only = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=True, limit=10
        )
        assert {s.task_id for s in all_snapshots} == {"task_done", "task_running"}
        assert {s.task_id for s in running_only} == {"task_running"}

    def test_recency_orders_most_recent_first(self, store: _StubStore) -> None:
        store.events_by_run[_RUN].extend(
            [
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="early",
                    event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                    sequence_no=1,
                    created_at=_at(0),
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="early",
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    sequence_no=2,
                    created_at=_at(2),
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="late",
                    event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                    sequence_no=3,
                    created_at=_at(10),
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="late",
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    sequence_no=4,
                    created_at=_at(20),
                ),
            ]
        )
        adapter = InMemorySubagentStore(store)
        result = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=False, limit=10
        )
        assert [s.task_id for s in result] == ["late", "early"]

    def test_does_not_leak_across_conversations(self, store: _StubStore) -> None:
        store.events_by_run[_RUN_OTHER].append(
            _RuntimeStubs.subagent_event(
                run_id=_RUN_OTHER,
                task_id="other_task",
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                sequence_no=1,
                created_at=_at(0),
            )
        )
        adapter = InMemorySubagentStore(store)
        result = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=False, limit=10
        )
        assert result == ()

    def test_status_cancelled_propagates(self, store: _StubStore) -> None:
        store.events_by_run[_RUN].extend(
            [
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_cancel",
                    event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                    sequence_no=1,
                    created_at=_at(0),
                ),
                _RuntimeStubs.subagent_event(
                    run_id=_RUN,
                    task_id="task_cancel",
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    sequence_no=2,
                    created_at=_at(5),
                    status="cancelled",
                ),
            ]
        )
        adapter = InMemorySubagentStore(store)
        result = adapter.list_for_conversation(
            org_id=_ORG, conversation_id=_CONV, running_only=False, limit=10
        )
        assert result[0].status is SubagentLifecycleStatus.CANCELLED


class TestInMemorySourceStore:
    def _citation(
        self,
        *,
        ordinal: int,
        connector: str = "notion",
        doc_id: str = "doc_positioning",
        run_id: str = _RUN,
        title: str = "Aurora 4.0 — Approved Positioning v3",
        created_at: datetime | None = None,
    ) -> CitationRecord:
        return CitationRecord(
            citation_id=f"c{ordinal:03d}",
            run_id=run_id,
            conversation_id=_CONV,
            org_id=_ORG,
            ordinal=ordinal,
            source_connector=connector,
            source_doc_id=doc_id,
            source_url=f"https://example.invalid/{doc_id}",
            title=title,
            snippet="Aurora 4.0 brings agentic search to every desk.",
            freshness_at=_at(ordinal * 10),
            created_at=created_at or _at(ordinal),
        )

    def test_aggregates_by_unique_doc(self) -> None:
        citations = InMemoryCitationStore()
        citations.insert_or_get(self._citation(ordinal=1))
        citations.insert_or_get(
            self._citation(
                ordinal=2,
                connector="notion",
                doc_id="doc_brand",
                title="Brand voice",
            )
        )
        # Re-cite the positioning doc in a second run — same doc, new run.
        citations.insert_or_get(
            self._citation(
                ordinal=3,
                run_id=_RUN_OTHER,
                created_at=_at(100),
            )
        )
        adapter = InMemorySourceStore(citations)
        result = adapter.aggregate_for_conversation(
            org_id=_ORG, conversation_id=_CONV, run_id=None, limit=10
        )
        # ``doc_positioning`` cited twice ranks above ``doc_brand`` cited once.
        assert [row.source_doc_id for row in result] == [
            "doc_positioning",
            "doc_brand",
        ]
        positioning = result[0]
        assert positioning.citation_count == 2
        assert positioning.last_cited_at == _at(100)

    def test_run_scope_filters(self) -> None:
        citations = InMemoryCitationStore()
        citations.insert_or_get(self._citation(ordinal=1))
        citations.insert_or_get(
            self._citation(
                ordinal=2,
                doc_id="doc_other_run",
                run_id=_RUN_OTHER,
            )
        )
        adapter = InMemorySourceStore(citations)
        result = adapter.aggregate_for_conversation(
            org_id=_ORG, conversation_id=_CONV, run_id=_RUN, limit=10
        )
        assert {row.source_doc_id for row in result} == {"doc_positioning"}

    def test_does_not_leak_across_orgs(self) -> None:
        citations = InMemoryCitationStore()
        citations.insert_or_get(self._citation(ordinal=1))
        adapter = InMemorySourceStore(citations)
        result = adapter.aggregate_for_conversation(
            org_id="org_other", conversation_id=_CONV, run_id=None, limit=10
        )
        assert result == ()
