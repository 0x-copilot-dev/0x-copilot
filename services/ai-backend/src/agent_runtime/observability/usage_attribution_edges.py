"""Usage-attribution domain helpers and a legacy in-memory test double.

The durable asynchronous store contract is
:class:`agent_runtime.api.ports.UsageAttributionEdgeStorePort`.  This module
keeps the original synchronous helper for pure operation-tree tests while
re-exporting the canonical telemetry record used by every production adapter.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.persistence.records import (
    UsageAttributionEdge,
    UsageAttributionRelationship,
)


class UsageAttributionEdgeStore(Protocol):
    """Append-only port. Implementations must never update an existing edge."""

    def append(self, edge: UsageAttributionEdge) -> bool:
        """Persist one edge; return ``True`` only when it was newly appended."""

    def list_for_operation(self, operation_id: str) -> tuple[UsageAttributionEdge, ...]:
        """Return immutable edges in deterministic append order."""


class InMemoryUsageAttributionEdgeStore:
    """Deterministic test/dev implementation of the append-only edge port."""

    __slots__ = ("_by_key", "_edges")

    def __init__(self) -> None:
        self._by_key: dict[tuple[object, ...], UsageAttributionEdge] = {}
        self._edges: list[UsageAttributionEdge] = []

    def append(self, edge: UsageAttributionEdge) -> bool:
        if edge.idempotency_key in self._by_key:
            return False
        self._by_key[edge.idempotency_key] = edge
        self._edges.append(edge)
        return True

    def list_for_operation(self, operation_id: str) -> tuple[UsageAttributionEdge, ...]:
        return tuple(edge for edge in self._edges if edge.operation_id == operation_id)


__all__ = (
    "InMemoryUsageAttributionEdgeStore",
    "UsageAttributionEdge",
    "UsageAttributionEdgeStore",
    "UsageAttributionRelationship",
)
