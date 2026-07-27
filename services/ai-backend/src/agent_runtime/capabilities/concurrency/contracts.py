"""Pure domain contracts for deterministic, conservative batch planning."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
from typing import Self

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract

_RESOURCE_FINGERPRINT_PATTERN = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")


class ConcurrencyMode(StrEnum):
    """Concurrency posture declared by trusted capability metadata."""

    SERIAL = "serial"
    PARALLEL_SAFE = "parallel_safe"
    SAME_SUBJECT_SERIAL = "same_subject_serial"


class SideEffectKind(StrEnum):
    """Side-effect class relevant to concurrent admission."""

    NONE = "none"
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"
    UNKNOWN = "unknown"


class IdempotencyKind(StrEnum):
    """Idempotency posture declared by the capability owner."""

    NONE = "none"
    KEYED = "keyed"
    NATURAL = "natural"


class RateLimitScope(StrEnum):
    """Scope at which a future executor must acquire a permit."""

    CAPABILITY = "capability"
    CONNECTOR = "connector"
    USER = "user"
    INSTALLATION = "installation"
    GLOBAL = "global"


class OrderingRequirement(StrEnum):
    """Result ordering required by a capability."""

    NONE = "none"
    INPUT_ORDER = "input_order"
    COMPLETION_ORDER = "completion_order"


class PolicySource(StrEnum):
    """Trust source for concurrency metadata."""

    PRODUCT_CATALOG = "product_catalog"
    TRUSTED_PROVIDER = "trusted_provider"
    CONSERVATIVE_DEFAULT = "conservative_default"


class ConcurrencyPolicy(RuntimeContract):
    """Resolved policy for one operation.

    Defaults deliberately encode no knowledge and therefore cannot authorize
    parallel execution.
    """

    mode: ConcurrencyMode = ConcurrencyMode.SERIAL
    side_effect: SideEffectKind = SideEffectKind.UNKNOWN
    idempotency: IdempotencyKind = IdempotencyKind.NONE
    resource_key_template: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    max_parallelism: int | None = Field(default=None, ge=1, le=16)
    rate_limit_scope: RateLimitScope = RateLimitScope.CAPABILITY
    ordering_requirement: OrderingRequirement = OrderingRequirement.INPUT_ORDER
    policy_source: PolicySource = PolicySource.CONSERVATIVE_DEFAULT


class BatchFailurePolicy(StrEnum):
    """Admission behavior after a child operation fails."""

    FAIL_FAST = "fail_fast"
    COLLECT_ALL = "collect_all"
    STOP_NEW = "stop_new"


def _canonical_identifiers(
    value: object,
    *,
    field_name: str,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be an iterable of identifiers")
    normalized: list[str] = []
    for item in value:  # type: ignore[union-attr]
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} must contain non-empty strings")
        identifier = item.strip()
        if identifier in normalized:
            raise ValueError(f"{field_name} must not contain duplicates")
        normalized.append(identifier)
    return tuple(normalized)


class BatchOperation(RuntimeContract):
    """Planning facts for one already-authorized operation.

    ``dependency_ids=None`` and ``resource_fingerprints=None`` mean unknown,
    not empty. Callers must supply empty tuples to explicitly attest that an
    operation has no dependencies or resource subjects.

    Resource fingerprints are keyed/opaque values produced outside this
    domain. Raw connector arguments and object identifiers do not belong in a
    batch plan.
    """

    operation_id: str = Field(min_length=1, max_length=255)
    authorization_epoch: str = Field(min_length=1, max_length=255)
    dependency_ids: tuple[str, ...] | None = None
    resource_fingerprints: tuple[str, ...] | None = None

    @field_validator("operation_id", "authorization_epoch")
    @classmethod
    def _strip_required_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("operation identifiers must be non-empty")
        return normalized

    @field_validator("dependency_ids", mode="before")
    @classmethod
    def _canonical_dependencies(cls, value: object) -> tuple[str, ...] | None:
        return _canonical_identifiers(value, field_name="dependency_ids")

    @field_validator("resource_fingerprints", mode="before")
    @classmethod
    def _canonical_resources(cls, value: object) -> tuple[str, ...] | None:
        normalized = _canonical_identifiers(
            value,
            field_name="resource_fingerprints",
        )
        if normalized is not None and any(
            _RESOURCE_FINGERPRINT_PATTERN.fullmatch(item) is None for item in normalized
        ):
            raise ValueError(
                "resource_fingerprints must contain keyed HMAC-SHA256 digests"
            )
        return normalized

    @model_validator(mode="after")
    def _cannot_depend_on_self(self) -> Self:
        if self.dependency_ids and self.operation_id in self.dependency_ids:
            raise ValueError("an operation cannot depend on itself")
        return self


class OperationBatch(RuntimeContract):
    """Ordered operations and the batch-level concurrency ceiling."""

    batch_id: str = Field(min_length=1, max_length=255)
    parent_operation_id: str | None = Field(default=None, min_length=1, max_length=255)
    operations: tuple[BatchOperation, ...] = Field(min_length=1, max_length=100)
    deadline: datetime | None = None
    max_parallelism: int = Field(default=1, ge=1, le=16)
    failure_policy: BatchFailurePolicy = BatchFailurePolicy.STOP_NEW

    @field_validator("batch_id")
    @classmethod
    def _strip_batch_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("batch_id must be non-empty")
        return normalized

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _operation_graph_is_well_formed(self) -> Self:
        operation_ids = tuple(operation.operation_id for operation in self.operations)
        known_ids = set(operation_ids)
        if len(operation_ids) != len(known_ids):
            raise ValueError("batch operation_id values must be unique")
        preceding_ids: set[str] = set()
        for operation in self.operations:
            unknown_dependencies = set(operation.dependency_ids or ()) - known_ids
            if unknown_dependencies:
                raise ValueError(
                    "dependency_ids must reference operations in the batch"
                )
            forward_dependencies = set(operation.dependency_ids or ()) - preceding_ids
            if forward_dependencies:
                raise ValueError(
                    "dependency_ids must reference earlier operations in the batch"
                )
            preceding_ids.add(operation.operation_id)
        return self


class BatchSegmentMode(StrEnum):
    """Execution mode assigned to one contiguous batch segment."""

    SERIAL = "serial"
    PARALLEL = "parallel"


class BatchSegmentReason(StrEnum):
    """Stable, content-free reason explaining a planner decision."""

    INDEPENDENT_READS = "independent_reads"
    BATCH_SERIAL_DEFAULT = "batch_serial_default"
    CONSERVATIVE_POLICY_DEFAULT = "conservative_policy_default"
    POLICY_REQUIRES_SERIAL = "policy_requires_serial"
    POLICY_PARALLELISM_DISABLED = "policy_parallelism_disabled"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"
    EFFECTFUL_OPERATION = "effectful_operation"
    UNKNOWN_DEPENDENCIES = "unknown_dependencies"
    EXPLICIT_DEPENDENCIES = "explicit_dependencies"
    UNKNOWN_RESOURCES = "unknown_resources"
    SAME_SUBJECT_REQUIRES_RESOURCE = "same_subject_requires_resource"
    RESOURCE_CONFLICT = "resource_conflict"
    AUTHORIZATION_EPOCH_BARRIER = "authorization_epoch_barrier"
    INSUFFICIENT_PARALLEL_MEMBERS = "insufficient_parallel_members"


class BatchSegment(RuntimeContract):
    """One deterministic serial or parallel section of a plan."""

    segment_index: int = Field(ge=0)
    mode: BatchSegmentMode
    operation_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    reason: BatchSegmentReason
    max_parallelism: int = Field(ge=1, le=16)

    @model_validator(mode="after")
    def _mode_matches_width(self) -> Self:
        if self.mode is BatchSegmentMode.PARALLEL and len(self.operation_ids) < 2:
            raise ValueError("parallel segments require at least two operations")
        if self.mode is BatchSegmentMode.SERIAL and self.max_parallelism != 1:
            raise ValueError("serial segments require max_parallelism=1")
        if len(self.operation_ids) > self.max_parallelism:
            raise ValueError("segment width exceeds max_parallelism")
        return self


class BatchPlan(RuntimeContract):
    """Stable planner output that covers every input operation exactly once."""

    batch_id: str = Field(min_length=1, max_length=255)
    operation_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    segments: tuple[BatchSegment, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _segments_cover_ordered_operations(self) -> Self:
        expected_indices = tuple(range(len(self.segments)))
        actual_indices = tuple(segment.segment_index for segment in self.segments)
        if actual_indices != expected_indices:
            raise ValueError("segment indices must be contiguous and zero-based")
        planned_ids = tuple(
            operation_id
            for segment in self.segments
            for operation_id in segment.operation_ids
        )
        if planned_ids != self.operation_ids:
            raise ValueError("segments must preserve and exactly cover operation order")
        return self


__all__ = (
    "BatchFailurePolicy",
    "BatchOperation",
    "BatchPlan",
    "BatchSegment",
    "BatchSegmentMode",
    "BatchSegmentReason",
    "ConcurrencyMode",
    "ConcurrencyPolicy",
    "IdempotencyKind",
    "OperationBatch",
    "OrderingRequirement",
    "PolicySource",
    "RateLimitScope",
    "SideEffectKind",
)
