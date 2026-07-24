"""Pure replay projection for the D2 operation tree.

The projection has no runtime, persistence, or UI dependency.  It consumes the
append-only work ledger plus immutable usage-attribution edges and is therefore
safe to rebuild after a stream reconnect or worker retry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.usage_attribution_edges import (
    UsageAttributionEdge,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectStagedPayload,
    LedgerEventType,
    OperationCompletedPayload,
    OperationFailedPayload,
    OperationOutcome,
    OperationRequestedPayload,
    Producer,
)


class OperationNodeStatus(StrEnum):
    REQUESTED = "requested"
    CLASSIFIED = "classified"
    SUCCEEDED = "succeeded"
    STAGED = "staged"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OperationUsageTotals(RuntimeContract):
    """Deduplicated token/cost total for one projected operation."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_micro_usd: int | None = None


class OperationUsageRecord(RuntimeContract):
    """The bounded usage subset needed by the tree projection."""

    usage_record_id: str = Field(min_length=1, max_length=128)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_micro_usd: int | None = None


class OperationTreeEvent(RuntimeContract):
    """Normalized ledger event supplied by a stream/replay adapter."""

    sequence_no: int = Field(ge=1)
    event_type: LedgerEventType
    payload: dict[str, object]
    occurred_at: datetime


class OperationNode(RuntimeContract):
    operation_id: str = Field(min_length=1, max_length=128)
    parent_operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    producer: Producer
    capability: str = Field(min_length=1, max_length=128)
    op: str = Field(min_length=1, max_length=128)
    status: OperationNodeStatus
    started_at: datetime
    completed_at: datetime | None = None
    artifact_ids: tuple[str, ...] = ()
    stage_ids: tuple[str, ...] = ()
    usage_totals: OperationUsageTotals = Field(default_factory=OperationUsageTotals)


class OperationTree(RuntimeContract):
    """Deterministic replay result, ordered by request sequence then id."""

    nodes: tuple[OperationNode, ...]

    def node(self, operation_id: str) -> OperationNode | None:
        return next(
            (node for node in self.nodes if node.operation_id == operation_id), None
        )


class OperationTreeProjection:
    """Fold canonical operation/effect events without inventing missing state."""

    @classmethod
    def fold(
        cls,
        events: Iterable[OperationTreeEvent],
        *,
        attribution_edges: Iterable[UsageAttributionEdge] = (),
        usage_records: Iterable[OperationUsageRecord] = (),
    ) -> OperationTree:
        mutable: dict[str, _MutableNode] = {}
        for event in sorted(events, key=lambda item: item.sequence_no):
            if event.event_type is LedgerEventType.OPERATION_REQUESTED:
                cls._fold_requested(mutable, event)
            elif event.event_type is LedgerEventType.OPERATION_CLASSIFIED:
                cls._fold_classified(mutable, event)
            elif event.event_type is LedgerEventType.OPERATION_COMPLETED:
                cls._fold_completed(mutable, event)
            elif event.event_type is LedgerEventType.OPERATION_FAILED:
                cls._fold_failed(mutable, event)
            elif event.event_type is LedgerEventType.EFFECT_STAGED:
                cls._fold_effect_staged(mutable, event)

        records = {record.usage_record_id: record for record in usage_records}
        cls._fold_attribution(mutable, attribution_edges, records)
        return OperationTree(
            nodes=tuple(
                node.freeze()
                for node in sorted(
                    mutable.values(),
                    key=lambda node: (node.sequence_no, node.operation_id),
                )
            )
        )

    @staticmethod
    def _fold_requested(
        nodes: dict[str, "_MutableNode"], event: OperationTreeEvent
    ) -> None:
        try:
            payload = OperationRequestedPayload.model_validate(event.payload)
        except ValidationError:
            return
        if payload.operation_id in nodes:
            return
        nodes[payload.operation_id] = _MutableNode(
            operation_id=payload.operation_id,
            parent_operation_id=payload.parent_operation_id,
            producer=payload.producer,
            capability=payload.capability,
            op=payload.op,
            status=OperationNodeStatus.REQUESTED,
            started_at=event.occurred_at,
            sequence_no=event.sequence_no,
        )

    @staticmethod
    def _fold_classified(
        nodes: dict[str, "_MutableNode"], event: OperationTreeEvent
    ) -> None:
        operation_id = _operation_id(event.payload)
        node = nodes.get(operation_id)
        if node is not None and node.status is OperationNodeStatus.REQUESTED:
            node.status = OperationNodeStatus.CLASSIFIED

    @staticmethod
    def _fold_completed(
        nodes: dict[str, "_MutableNode"], event: OperationTreeEvent
    ) -> None:
        try:
            payload = OperationCompletedPayload.model_validate(event.payload)
        except ValidationError:
            return
        node = nodes.get(payload.operation_id)
        if node is None:
            return
        node.status = _status_for_outcome(payload.outcome)
        node.completed_at = event.occurred_at

    @staticmethod
    def _fold_failed(
        nodes: dict[str, "_MutableNode"], event: OperationTreeEvent
    ) -> None:
        try:
            payload = OperationFailedPayload.model_validate(event.payload)
        except ValidationError:
            return
        node = nodes.get(payload.operation_id)
        if node is None:
            return
        node.status = OperationNodeStatus.FAILED
        node.completed_at = event.occurred_at

    @staticmethod
    def _fold_effect_staged(
        nodes: dict[str, "_MutableNode"], event: OperationTreeEvent
    ) -> None:
        try:
            payload = EffectStagedPayload.model_validate(event.payload)
        except ValidationError:
            return
        node = nodes.get(payload.operation_id)
        if node is not None:
            node.stage_ids.add(payload.stage_id)

    @staticmethod
    def _fold_attribution(
        nodes: dict[str, "_MutableNode"],
        edges: Iterable[UsageAttributionEdge],
        records: Mapping[str, OperationUsageRecord],
    ) -> None:
        seen: set[tuple[str, str, str | None, str | None, str]] = set()
        for edge in edges:
            if edge.idempotency_key in seen:
                continue
            seen.add(edge.idempotency_key)
            node = nodes.get(edge.operation_id)
            if node is None:
                continue
            if edge.artifact_id is not None:
                node.artifact_ids.add(edge.artifact_id)
            if edge.stage_id is not None:
                node.stage_ids.add(edge.stage_id)
            if edge.usage_record_id in node.usage_record_ids:
                continue
            node.usage_record_ids.add(edge.usage_record_id)
            record = records.get(edge.usage_record_id)
            if record is not None:
                node.usage_records.append(record)


class _MutableNode:
    __slots__ = (
        "artifact_ids",
        "capability",
        "completed_at",
        "op",
        "operation_id",
        "parent_operation_id",
        "producer",
        "sequence_no",
        "stage_ids",
        "started_at",
        "status",
        "usage_record_ids",
        "usage_records",
    )

    def __init__(
        self,
        *,
        operation_id: str,
        parent_operation_id: str | None,
        producer: Producer,
        capability: str,
        op: str,
        status: OperationNodeStatus,
        started_at: datetime,
        sequence_no: int,
    ) -> None:
        self.operation_id = operation_id
        self.parent_operation_id = parent_operation_id
        self.producer = producer
        self.capability = capability
        self.op = op
        self.status = status
        self.started_at = started_at
        self.completed_at: datetime | None = None
        self.sequence_no = sequence_no
        self.artifact_ids: set[str] = set()
        self.stage_ids: set[str] = set()
        self.usage_record_ids: set[str] = set()
        self.usage_records: list[OperationUsageRecord] = []

    def freeze(self) -> OperationNode:
        costs = [record.cost_micro_usd for record in self.usage_records]
        return OperationNode(
            operation_id=self.operation_id,
            parent_operation_id=self.parent_operation_id,
            producer=self.producer,
            capability=self.capability,
            op=self.op,
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            artifact_ids=tuple(sorted(self.artifact_ids)),
            stage_ids=tuple(sorted(self.stage_ids)),
            usage_totals=OperationUsageTotals(
                input_tokens=sum(record.input_tokens for record in self.usage_records),
                output_tokens=sum(
                    record.output_tokens for record in self.usage_records
                ),
                total_tokens=sum(record.total_tokens for record in self.usage_records),
                cost_micro_usd=(
                    sum(cost for cost in costs if cost is not None)
                    if any(cost is not None for cost in costs)
                    else None
                ),
            ),
        )


def _operation_id(payload: Mapping[str, object]) -> str:
    value = payload.get("operation_id")
    return value if isinstance(value, str) else ""


def _status_for_outcome(outcome: OperationOutcome) -> OperationNodeStatus:
    return {
        OperationOutcome.SUCCEEDED: OperationNodeStatus.SUCCEEDED,
        OperationOutcome.STAGED: OperationNodeStatus.STAGED,
        OperationOutcome.BLOCKED: OperationNodeStatus.BLOCKED,
        OperationOutcome.CANCELLED: OperationNodeStatus.CANCELLED,
        OperationOutcome.FAILED: OperationNodeStatus.FAILED,
    }[outcome]


__all__ = (
    "OperationNode",
    "OperationNodeStatus",
    "OperationTree",
    "OperationTreeEvent",
    "OperationTreeProjection",
    "OperationUsageRecord",
    "OperationUsageTotals",
)
