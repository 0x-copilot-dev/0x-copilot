"""Unit tests for the PR 1.5 :class:`WorkspaceFeedService`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import pytest

from agent_runtime.api.workspace_feed_service import WorkspaceFeedService
from agent_runtime.persistence.records import (
    SourceAggregate,
    SubagentLifecycleStatus,
    SubagentSnapshot,
)
from runtime_api.schemas import SubagentStatusFilter


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


class _StubSubagentStore:
    def __init__(self, snapshots: Sequence[SubagentSnapshot]) -> None:
        self._snapshots = tuple(snapshots)
        self.last_running_only: bool | None = None
        self.last_limit: int | None = None

    async def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        running_only: bool,
        limit: int,
    ) -> Sequence[SubagentSnapshot]:
        self.last_running_only = running_only
        self.last_limit = limit
        return self._snapshots


class _StubSourceStore:
    def __init__(self, rows: Sequence[SourceAggregate]) -> None:
        self._rows = tuple(rows)
        self.last_run_id: str | None = None
        self.last_limit: int | None = None

    async def aggregate_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str | None,
        limit: int,
    ) -> Sequence[SourceAggregate]:
        self.last_run_id = run_id
        self.last_limit = limit
        return self._rows


def _snapshot(*, task_id: str = "task_1") -> SubagentSnapshot:
    return SubagentSnapshot(
        task_id=task_id,
        parent_run_id="run_1",
        conversation_id="conv_1",
        org_id="org_acme",
        subagent_name="research",
        status=SubagentLifecycleStatus.COMPLETED,
        display_title="Reviewed positioning",
        objective_summary="A" * 4096,  # at the record cap
        started_at=_NOW,
        completed_at=_NOW,
        duration_ms=1234,
        result_summary="B" * 1000,  # over the 280-char wire budget
    )


def _aggregate() -> SourceAggregate:
    return SourceAggregate(
        citation_id="c001",
        conversation_id="conv_1",
        org_id="org_acme",
        source_connector="notion",
        source_doc_id="doc_positioning",
        source_url="https://example.invalid/doc",
        title="Aurora 4.0 — Approved Positioning",
        snippet="C" * 1024,  # over the 280-char public budget
        freshness_at=_NOW,
        citation_count=3,
        last_cited_at=_NOW,
    )


class TestListSubagents:
    async def test_truncates_objective_and_result(self) -> None:
        store = _StubSubagentStore([_snapshot()])
        service = WorkspaceFeedService(
            subagent_store=store, source_store=_StubSourceStore([])
        )
        response = await service.list_subagents(
            org_id="org_acme",
            conversation_id="conv_1",
            status_filter=SubagentStatusFilter.ALL,
            limit=50,
        )
        entry = response.subagents[0]
        assert len(entry.objective_summary or "") <= 4096
        assert len(entry.result_summary or "") <= 280
        assert entry.duration_ms == 1234
        assert response.truncated is False

    async def test_truncated_when_at_limit(self) -> None:
        snapshots = [_snapshot(task_id=f"task_{i}") for i in range(50)]
        service = WorkspaceFeedService(
            subagent_store=_StubSubagentStore(snapshots),
            source_store=_StubSourceStore([]),
        )
        response = await service.list_subagents(
            org_id="org_acme",
            conversation_id="conv_1",
            status_filter=SubagentStatusFilter.ALL,
            limit=50,
        )
        assert response.truncated is True

    async def test_running_filter_propagates(self) -> None:
        store = _StubSubagentStore([])
        service = WorkspaceFeedService(
            subagent_store=store, source_store=_StubSourceStore([])
        )
        await service.list_subagents(
            org_id="org_acme",
            conversation_id="conv_1",
            status_filter=SubagentStatusFilter.RUNNING,
            limit=50,
        )
        assert store.last_running_only is True

    async def test_clamps_huge_limit(self) -> None:
        store = _StubSubagentStore([])
        service = WorkspaceFeedService(
            subagent_store=store, source_store=_StubSourceStore([])
        )
        await service.list_subagents(
            org_id="org_acme",
            conversation_id="conv_1",
            status_filter=SubagentStatusFilter.ALL,
            limit=10_000,
        )
        assert store.last_limit == 200


class TestListSources:
    async def test_truncates_snippet(self) -> None:
        store = _StubSourceStore([_aggregate()])
        service = WorkspaceFeedService(
            subagent_store=_StubSubagentStore([]), source_store=store
        )
        response = await service.list_sources(
            org_id="org_acme",
            conversation_id="conv_1",
            run_id=None,
            limit=200,
        )
        entry = response.sources[0]
        assert len(entry.snippet or "") <= 280
        assert entry.citation_count == 3
        assert response.truncated is False

    async def test_run_scope_propagates(self) -> None:
        store = _StubSourceStore([])
        service = WorkspaceFeedService(
            subagent_store=_StubSubagentStore([]), source_store=store
        )
        await service.list_sources(
            org_id="org_acme",
            conversation_id="conv_1",
            run_id="run_alpha",
            limit=10,
        )
        assert store.last_run_id == "run_alpha"
