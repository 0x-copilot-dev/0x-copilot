"""Pure domain contracts for deterministic, conservative batch planning.

Every concurrency-relevant vocabulary in this module is a :class:`NarrowableEnum`
whose members are declared **narrowest first**. Declaration order *is* the
authority rank, so the conservative member is the structural default rather than
a documented convention, and the only composition operator exposed anywhere in
F6 is :meth:`NarrowableEnum.narrowest` — a minimum over that rank. There is no
operator that can widen a resolved policy, which is what makes the precedence
chain in :mod:`agent_runtime.capabilities.concurrency.descriptor_policy`
structurally safe rather than merely careful.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
import hashlib
import hmac
import re
from typing import ClassVar, Self

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes


class NarrowableEnum(StrEnum):
    """Closed vocabulary ordered from most conservative to least conservative.

    Members must be declared narrowest first. A member added at the top of the
    declaration becomes the new conservative floor automatically; a member added
    at the bottom can never silently become the default.
    """

    @property
    def rank(self) -> int:
        """Return the authority rank, where ``0`` is the narrowest member."""

        return list(type(self)).index(self)

    @classmethod
    def conservative(cls) -> Self:
        """Return the narrowest member, used for unknown and absent metadata."""

        return next(iter(cls))

    @classmethod
    def narrowest(cls, *values: Self) -> Self:
        """Return the narrowest supplied member.

        This is the only composition operator for concurrency vocabularies. It
        cannot return a value wider than any of its inputs.
        """

        if not values:
            raise ValueError("at least one value is required")
        return min(values, key=lambda value: value.rank)


class ConcurrencyMode(NarrowableEnum):
    """Concurrency posture declared by trusted capability metadata."""

    SERIAL = "serial"
    SAME_SUBJECT_SERIAL = "same_subject_serial"
    PARALLEL_SAFE = "parallel_safe"


class SideEffectKind(NarrowableEnum):
    """Side-effect class relevant to concurrent admission.

    ``UNKNOWN`` is narrower than an irreversible write: an undeclared effect
    class cannot be reasoned about at all, so it forbids every overlap.
    """

    UNKNOWN = "unknown"
    IRREVERSIBLE_WRITE = "irreversible_write"
    REVERSIBLE_WRITE = "reversible_write"
    READ = "read"
    NONE = "none"


class IdempotencyKind(NarrowableEnum):
    """Idempotency posture declared by the capability owner."""

    NONE = "none"
    KEYED = "keyed"
    NATURAL = "natural"


class RateLimitScope(NarrowableEnum):
    """Scope at which an executor must acquire a permit.

    A broader scope shares one permit pool across more work, so it admits less
    concurrency and is therefore narrower. ``UNKNOWN`` is narrower still: an
    undeclared scope must acquire at the broadest available pool.
    """

    UNKNOWN = "unknown"
    GLOBAL = "global"
    INSTALLATION = "installation"
    USER = "user"
    CONNECTOR = "connector"
    CAPABILITY = "capability"


class OrderingRequirement(NarrowableEnum):
    """Result ordering required by a capability."""

    INPUT_ORDER = "input_order"
    COMPLETION_ORDER = "completion_order"
    NONE = "none"


class ProviderSessionConstraint(NarrowableEnum):
    """Serialization the provider's own session or transport state demands.

    A connector or MCP server with non-thread-safe session state cannot be
    driven concurrently even when every other dimension permits it.
    ``UNKNOWN`` forbids overlap entirely because the session scope itself is
    undeclared.
    """

    UNKNOWN = "unknown"
    INSTALLATION_SERIAL = "installation_serial"
    SESSION_SERIAL = "session_serial"
    SESSION_PARALLEL_SAFE = "session_parallel_safe"


class PolicySource(NarrowableEnum):
    """Trust source for concurrency metadata.

    Rank doubles as precedence. The most authoritative source
    (``PRODUCT_CATALOG``) establishes the policy; every less authoritative
    source is applied afterwards in descending rank order and may only narrow.
    """

    CONSERVATIVE_DEFAULT = "conservative_default"
    TRUSTED_PROVIDER = "trusted_provider"
    USER_APPROVED_OVERRIDE = "user_approved_override"
    PRODUCT_CATALOG = "product_catalog"


class ConcurrencyPolicyField(StrEnum):
    """Closed field vocabulary for content-free policy decision records.

    Values match :class:`ConcurrencyPolicy` attribute names so a record is
    self-describing without carrying any declared value.
    """

    MODE = "mode"
    SIDE_EFFECT = "side_effect"
    IDEMPOTENCY = "idempotency"
    RESOURCE_KEY_TEMPLATE = "resource_key_template"
    MAX_PARALLELISM = "max_parallelism"
    RATE_LIMIT_SCOPE = "rate_limit_scope"
    ORDERING_REQUIREMENT = "ordering_requirement"
    PROVIDER_SESSION_CONSTRAINT = "provider_session_constraint"


class ConcurrencyRejectionReason(StrEnum):
    """Stable, content-free reason for refusing declared concurrency metadata."""

    WIDER_THAN_ESTABLISHED = "wider_than_established"
    UNPARSEABLE_DEFAULTED_SAFE = "unparseable_defaulted_safe"
    TEMPLATE_NOT_NARROWER = "template_not_narrower"
    DUPLICATE_SOURCE = "duplicate_source"
    UNSUPPORTED_SOURCE = "unsupported_source"
    CAPABILITY_MISMATCH = "capability_mismatch"
    MALFORMED_TEMPLATE = "malformed_template"
    MISSING_DIMENSION_VALUE = "missing_dimension_value"
    UNEXPECTED_DIMENSION_VALUE = "unexpected_dimension_value"
    OVERSIZED_DIMENSION_VALUE = "oversized_dimension_value"
    WEAK_DIGEST_SECRET = "weak_digest_secret"


class ConcurrencyPolicyError(Exception):
    """Base concurrency-policy failure with an already-safe public message.

    ``safe_summary`` is authored at the class level and never interpolates
    declared metadata, dimension values, or connector payloads. The
    low-cardinality ``reason`` carries the detail instead.

    This family deliberately does not derive from ``ValueError``: Pydantic
    converts ``ValueError`` raised inside a validator into a generic
    ``ValidationError``, which would erase the typed class and the reason code
    at exactly the boundaries that need them most.
    """

    _SUMMARY: ClassVar[str] = "capability concurrency metadata was rejected"

    def __init__(self, reason: ConcurrencyRejectionReason) -> None:
        super().__init__(self._SUMMARY)
        self.reason = reason
        self.safe_summary = self._SUMMARY


class ResourceKeyTemplateRejected(ConcurrencyPolicyError):
    """A resource-key template is not a closed, well-formed dimension list."""

    _SUMMARY: ClassVar[str] = "resource key template is not a supported template"


class ResourceKeyRenderRejected(ConcurrencyPolicyError):
    """Resource-key material is missing, unexpected, oversized, or unkeyed."""

    _SUMMARY: ClassVar[str] = "resource key could not be rendered from key material"


class ConcurrencyDeclarationRejected(ConcurrencyPolicyError):
    """A declaration cannot participate in precedence resolution at all."""

    _SUMMARY: ClassVar[str] = "capability concurrency declaration was not admitted"


class ConcurrencyPolicyWideningRejected(ConcurrencyPolicyError):
    """A less authoritative source attempted to widen an established policy."""

    _SUMMARY: ClassVar[str] = (
        "capability concurrency metadata may only narrow an established policy"
    )


class ResourceKeyDimension(StrEnum):
    """Closed subject dimensions a resource-key template may address."""

    CONNECTOR = "connector"
    INSTALLATION = "installation"
    SESSION = "session"
    ACCOUNT = "account"
    SUBJECT = "subject"
    CONTAINER = "container"
    OBJECT = "object"
    REGION = "region"


class ResourceKeyTemplate(RuntimeContract):
    """Closed template that renders one keyed, digested resource scope key.

    The template stores dimension *names* only. Raw connector paths, object
    identifiers, account identifiers, and user content are supplied at render
    time and leave this module solely as an ``hmac-sha256:<64 hex>`` digest, in
    the exact shape :class:`BatchOperation` accepts.

    Templates form their own narrowing lattice. A template addressing a strict
    subset of another's dimensions produces coarser keys, collides more often,
    and therefore serializes more; it is narrower. Absence is the bottom
    element, because without a key the planner cannot establish independence at
    all and falls back to a serial segment.
    """

    class Limits:
        MAX_DIMENSIONS = 8
        MAX_TEMPLATE_LENGTH = 256
        MAX_VALUE_LENGTH = 512
        MIN_SECRET_BYTES = 32

    class Digest:
        ALGORITHM = "hmac-sha256"
        PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")

    class Keys:
        VERSION = "version"
        TEMPLATE = "template"
        VALUES = "values"

    SCHEMA_VERSION: ClassVar[int] = 1
    _PLACEHOLDER: ClassVar[re.Pattern[str]] = re.compile(r"^\{([a-z_]+)\}$")
    _SEPARATOR: ClassVar[str] = "/"

    dimensions: tuple[ResourceKeyDimension, ...] = Field(
        min_length=1,
        max_length=Limits.MAX_DIMENSIONS,
    )

    @model_validator(mode="after")
    def _dimensions_are_unique(self) -> Self:
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        return self

    @classmethod
    def from_template(cls, template: str) -> Self:
        """Parse ``{connector}/{object}`` into a closed dimension list.

        Raises :class:`ResourceKeyTemplateRejected` for anything else. Use
        :meth:`parse` on the untrusted path, where failure must mean "no key"
        rather than an exception.
        """

        if not isinstance(template, str):
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        normalized = template.strip()
        if not normalized or len(normalized) > cls.Limits.MAX_TEMPLATE_LENGTH:
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        dimensions: list[ResourceKeyDimension] = []
        for part in normalized.split(cls._SEPARATOR):
            match = cls._PLACEHOLDER.fullmatch(part)
            if match is None:
                raise ResourceKeyTemplateRejected(
                    ConcurrencyRejectionReason.MALFORMED_TEMPLATE
                )
            try:
                dimension = ResourceKeyDimension(match.group(1))
            except ValueError as exc:
                raise ResourceKeyTemplateRejected(
                    ConcurrencyRejectionReason.MALFORMED_TEMPLATE
                ) from exc
            if dimension in dimensions:
                raise ResourceKeyTemplateRejected(
                    ConcurrencyRejectionReason.MALFORMED_TEMPLATE
                )
            dimensions.append(dimension)
        if len(dimensions) > cls.Limits.MAX_DIMENSIONS:
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        return cls(dimensions=tuple(dimensions))

    @classmethod
    def parse(cls, value: object) -> Self | None:
        """Return a template for trusted-shaped input, or ``None`` for anything else."""

        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return None
        try:
            return cls.from_template(value)
        except ResourceKeyTemplateRejected:
            return None

    @classmethod
    def narrowest(cls, *templates: Self | None) -> Self | None:
        """Return the narrowest template, or ``None`` when none can be proven.

        Absence wins over any template, a strict dimension subset wins over its
        superset, and two templates that cannot be ordered fall to ``None``
        rather than to an invented merge.
        """

        if not templates:
            raise ValueError("at least one template is required")
        narrowed = templates[0]
        for candidate in templates[1:]:
            narrowed = cls._narrow_pair(narrowed, candidate)
        return narrowed

    @classmethod
    def _narrow_pair(cls, left: Self | None, right: Self | None) -> Self | None:
        if left is None or right is None:
            return None
        if left == right:
            return left
        left_dimensions = frozenset(left.dimensions)
        right_dimensions = frozenset(right.dimensions)
        if right_dimensions < left_dimensions:
            return right
        if left_dimensions < right_dimensions:
            return left
        return None

    @property
    def canonical_template(self) -> str:
        """Return the stable ``{a}/{b}`` form used as digest domain separation."""

        return self._SEPARATOR.join(
            f"{{{dimension.value}}}" for dimension in self.dimensions
        )

    def render(
        self,
        *,
        secret: bytes,
        values: Mapping[ResourceKeyDimension, str],
    ) -> str:
        """Return one keyed digest for this template's dimension values.

        The returned digest is the only thing derived from ``values`` that
        leaves this module. Raises :class:`ResourceKeyRenderRejected` when the
        key material is incomplete, unexpected, oversized, or unkeyed.
        """

        if not isinstance(secret, (bytes, bytearray)) or (
            len(secret) < self.Limits.MIN_SECRET_BYTES
        ):
            raise ResourceKeyRenderRejected(
                ConcurrencyRejectionReason.WEAK_DIGEST_SECRET
            )
        supplied = set(values)
        expected = set(self.dimensions)
        if supplied - expected:
            raise ResourceKeyRenderRejected(
                ConcurrencyRejectionReason.UNEXPECTED_DIMENSION_VALUE
            )
        material: dict[str, str] = {}
        for dimension in self.dimensions:
            raw = values.get(dimension)
            if not isinstance(raw, str) or not raw.strip():
                raise ResourceKeyRenderRejected(
                    ConcurrencyRejectionReason.MISSING_DIMENSION_VALUE
                )
            normalized = raw.strip()
            if len(normalized) > self.Limits.MAX_VALUE_LENGTH:
                raise ResourceKeyRenderRejected(
                    ConcurrencyRejectionReason.OVERSIZED_DIMENSION_VALUE
                )
            material[dimension.value] = normalized
        payload = canonical_json_bytes(
            {
                self.Keys.VERSION: self.SCHEMA_VERSION,
                self.Keys.TEMPLATE: self.canonical_template,
                self.Keys.VALUES: material,
            }
        )
        digest = hmac.new(bytes(secret), payload, hashlib.sha256).hexdigest()
        return f"{self.Digest.ALGORITHM}:{digest}"

    def render_or_none(
        self,
        *,
        secret: bytes,
        values: Mapping[ResourceKeyDimension, str],
    ) -> str | None:
        """Return :meth:`render`, or ``None`` when the key cannot be established.

        Schedulers use this variant: an absent key is reported to the planner as
        unknown resources, which produces a serial segment.
        """

        try:
            return self.render(secret=secret, values=values)
        except ResourceKeyRenderRejected:
            return None


class ConcurrencyPolicy(RuntimeContract):
    """Resolved policy for one operation.

    Every default is its vocabulary's conservative floor, so an instance built
    with no arguments encodes no knowledge and therefore cannot authorize
    parallel execution.

    ``max_parallelism`` is the one deliberately non-safety field: ``None`` means
    the capability declares no bound of its own and the enclosing batch or
    permit ceiling applies. Safety is carried by the closed vocabularies, never
    by a scheduling bound.
    """

    mode: ConcurrencyMode = ConcurrencyMode.conservative()
    side_effect: SideEffectKind = SideEffectKind.conservative()
    idempotency: IdempotencyKind = IdempotencyKind.conservative()
    resource_key_template: ResourceKeyTemplate | None = None
    max_parallelism: int | None = Field(default=None, ge=1, le=16)
    rate_limit_scope: RateLimitScope = RateLimitScope.conservative()
    ordering_requirement: OrderingRequirement = OrderingRequirement.conservative()
    provider_session_constraint: ProviderSessionConstraint = (
        ProviderSessionConstraint.conservative()
    )
    policy_source: PolicySource = PolicySource.conservative()

    @field_validator("resource_key_template", mode="before")
    @classmethod
    def _coerce_resource_key_template(cls, value: object) -> object:
        if value is None or isinstance(value, (ResourceKeyTemplate, Mapping)):
            return value
        if isinstance(value, str):
            return ResourceKeyTemplate.from_template(value)
        raise ResourceKeyTemplateRejected(ConcurrencyRejectionReason.MALFORMED_TEMPLATE)

    def value_for(self, policy_field: ConcurrencyPolicyField) -> object:
        """Return the resolved value for one closed policy field."""

        return getattr(self, policy_field.value)


class BatchFailurePolicy(StrEnum):
    """Admission behavior after a child operation fails."""

    FAIL_FAST = "fail_fast"
    COLLECT_ALL = "collect_all"
    STOP_NEW = "stop_new"


class BatchOperation(RuntimeContract):
    """Planning facts for one already-authorized operation.

    ``dependency_ids=None`` and ``resource_fingerprints=None`` mean unknown,
    not empty. Callers must supply empty tuples to explicitly attest that an
    operation has no dependencies or resource subjects.

    Resource fingerprints are keyed/opaque values produced outside this
    domain — normally by :meth:`ResourceKeyTemplate.render`. Raw connector
    arguments and object identifiers do not belong in a batch plan.
    """

    operation_id: str = Field(min_length=1, max_length=255)
    authorization_epoch: str = Field(min_length=1, max_length=255)
    dependency_ids: tuple[str, ...] | None = None
    resource_fingerprints: tuple[str, ...] | None = None

    @staticmethod
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
        return cls._canonical_identifiers(value, field_name="dependency_ids")

    @field_validator("resource_fingerprints", mode="before")
    @classmethod
    def _canonical_resources(cls, value: object) -> tuple[str, ...] | None:
        normalized = cls._canonical_identifiers(
            value,
            field_name="resource_fingerprints",
        )
        if normalized is not None and any(
            ResourceKeyTemplate.Digest.PATTERN.fullmatch(item) is None
            for item in normalized
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
    "ConcurrencyDeclarationRejected",
    "ConcurrencyMode",
    "ConcurrencyPolicy",
    "ConcurrencyPolicyError",
    "ConcurrencyPolicyField",
    "ConcurrencyPolicyWideningRejected",
    "ConcurrencyRejectionReason",
    "IdempotencyKind",
    "NarrowableEnum",
    "OperationBatch",
    "OrderingRequirement",
    "PolicySource",
    "ProviderSessionConstraint",
    "RateLimitScope",
    "ResourceKeyDimension",
    "ResourceKeyRenderRejected",
    "ResourceKeyTemplate",
    "ResourceKeyTemplateRejected",
    "SideEffectKind",
)
