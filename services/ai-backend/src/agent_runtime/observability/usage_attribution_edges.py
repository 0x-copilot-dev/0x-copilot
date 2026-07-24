"""Immutable links from metered model calls to operations and their outputs.

Usage rows are append-only historical facts.  When an operation later produces
an artifact or stage, D2 records a separate edge instead of rewriting the
usage row.  This module is intentionally storage-neutral: later adapters may
persist the exact same contract without changing attribution semantics.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract


class UsageAttributionRelationship(StrEnum):
    PRODUCED = "produced"
    REVISED = "revised"
    PROPOSED = "proposed"
    SHAPED = "shaped"


class UsageAttributionEdge(RuntimeContract):
    """One immutable relationship between a usage record and an operation.

    ``produced``/``revised``/``shaped`` require an artifact. ``proposed``
    requires a stage.  An edge may name both when a proposal is derived from a
    newly produced artifact; that remains one historical relation, never an
    update to either source row.
    """

    usage_record_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    stage_id: str | None = Field(default=None, min_length=1, max_length=128)
    relationship: UsageAttributionRelationship

    @model_validator(mode="after")
    def _relationship_target_is_present(self) -> "UsageAttributionEdge":
        if self.relationship is UsageAttributionRelationship.PROPOSED:
            if self.stage_id is None:
                raise ValueError("proposed usage attribution requires stage_id")
            return self
        if self.artifact_id is None:
            raise ValueError(
                f"{self.relationship.value} usage attribution requires artifact_id"
            )
        return self

    @property
    def idempotency_key(self) -> tuple[str, str, str | None, str | None, str]:
        """Stable natural key used by durable stores for retry-safe append."""

        return (
            self.usage_record_id,
            self.operation_id,
            self.artifact_id,
            self.stage_id,
            self.relationship.value,
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
        self._by_key: dict[
            tuple[str, str, str | None, str | None, str], UsageAttributionEdge
        ] = {}
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
