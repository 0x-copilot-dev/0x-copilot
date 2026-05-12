"""In-memory ``SourceStorePort`` that aggregates citation rows into per-source summaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from agent_runtime.persistence.records import CitationRecord, SourceAggregate


class _CitationReader(Protocol):
    """Narrow read surface of :class:`InMemoryCitationStore`.

    Using a Protocol avoids a hard import-time dependency on the citation store
    module, keeping this file decoupled from the store's full interface.
    """

    async def list_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> Sequence[CitationRecord]:
        """Return all citations for a conversation."""
        ...


class _SourceAggregator:
    """Pure aggregator: many citations → one aggregate per unique source doc."""

    @classmethod
    def aggregate(
        cls,
        *,
        rows: Sequence[CitationRecord],
        run_id: str | None,
        limit: int,
    ) -> tuple[SourceAggregate, ...]:
        """Group citation rows by source doc, sort by citation count desc, and return top ``limit``."""
        if run_id is not None:
            rows = tuple(row for row in rows if row.run_id == run_id)
        buckets: dict[tuple[str, str], list[CitationRecord]] = {}
        for row in rows:
            key = (row.source_connector, row.source_doc_id)
            buckets.setdefault(key, []).append(row)
        aggregates = tuple(cls._fold(bucket) for bucket in buckets.values())
        ordered = sorted(
            aggregates,
            key=lambda agg: (agg.citation_count, agg.last_cited_at),
            reverse=True,
        )
        return tuple(ordered[:limit])

    @staticmethod
    def _fold(rows: list[CitationRecord]) -> SourceAggregate:
        """Collapse a list of same-doc citations into one aggregate, keeping the latest row's metadata."""
        rows.sort(key=lambda row: row.created_at)
        latest = rows[-1]
        return SourceAggregate(
            citation_id=latest.citation_id,
            conversation_id=latest.conversation_id,
            org_id=latest.org_id,
            source_connector=latest.source_connector,
            source_doc_id=latest.source_doc_id,
            source_url=latest.source_url,
            title=latest.title,
            snippet=latest.snippet,
            freshness_at=_SourceAggregator._latest_freshness(rows),
            citation_count=len(rows),
            last_cited_at=latest.created_at,
        )

    @staticmethod
    def _latest_freshness(rows: list[CitationRecord]) -> datetime | None:
        """Return the most recent ``freshness_at`` across a bucket, or ``None`` if all are absent."""
        candidates = [row.freshness_at for row in rows if row.freshness_at is not None]
        return max(candidates) if candidates else None


class InMemorySourceStore:
    """Aggregate citations into per-source rows for the Workspace pane."""

    def __init__(self, citations: _CitationReader) -> None:
        self._citations = citations

    async def aggregate_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str | None,
        limit: int,
    ) -> Sequence[SourceAggregate]:
        """Aggregate citations for a conversation into per-source summaries."""
        rows = tuple(
            await self._citations.list_for_conversation(
                org_id=org_id, conversation_id=conversation_id
            )
        )
        return _SourceAggregator.aggregate(rows=rows, run_id=run_id, limit=limit)


__all__ = ("InMemorySourceStore",)
