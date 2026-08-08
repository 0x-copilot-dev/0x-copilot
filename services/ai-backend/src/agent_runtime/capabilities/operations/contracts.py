"""Stable contracts for the Universal Operation Gateway.

The A1 wire entities remain the source of truth.  This module adds only the
runtime ports and bounded intermediate values needed to orchestrate them.
There is deliberately no ``apply`` method on :class:`OperationAdapter`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.surfaces.write_ops_capture import WriteOpCandidate
from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.entities import (
    ArtifactIntent,
    OperationDescriptor,
    OperationDisposition,
    OperationRequest,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    GateKind,
    LedgerEventType,
    OperationClassificationBasis,
    PresentationDecision,
)


class OperationGatewayMode(StrEnum):
    """Runtime authority mode for operation normalization."""

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class OperationClassification(RuntimeContract):
    """Pure, content-safe effect classification."""

    effect_class: EffectClass
    basis: OperationClassificationBasis
    confidence: float = Field(ge=0.0, le=1.0)
    descriptor_version: str = Field(min_length=1, max_length=64)
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=16)

    @field_validator("reasons")
    @classmethod
    def _reason_codes_only(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value or len(value) > 96:
                raise ValueError("classification reasons must be bounded codes")
            if not all(
                char.islower() or char.isdigit() or char in "._-" for char in value
            ):
                raise ValueError("classification reasons must be bounded codes")
        return values


class OperationPresentationOutcome(RuntimeContract):
    """Gateway-owned facts required to present one completed operation.

    The operation gateway derives this value only *after* an adapter's durable
    result write succeeds. An adapter returns neutral result data and never
    constructs a presentation contract, imports a ledger emitter/renderer, or
    receives a UI-facing service.
    """

    operation_id: str = Field(min_length=1, max_length=255)
    capability: str = Field(min_length=1, max_length=255)
    op: str = Field(min_length=1, max_length=255)
    result_ref: str = Field(min_length=1, max_length=2048)
    output: dict[str, object]
    latency_ms: int = Field(ge=0)
    #: The sibling WRITE ops of ``capability``, as its descriptors declared them
    #: at the instant this read ran. Carried here — not looked up later —
    #: because this is the only moment the loaded session is in hand, and a save
    #: arrives in another process with no way to ask. Stays generic in the same
    #: register ``capability`` / ``op`` are: a write op is a fact about a
    #: capability, not about MCP. Empty means nothing was captured, which the
    #: write-back lane treats as "no write is proposable" and refuses.
    write_ops: tuple[WriteOpCandidate, ...] = ()

    @field_validator("result_ref")
    @classmethod
    def _presentation_result_ref_is_logical(cls, value: str) -> str:
        if value.startswith("/") or ":\\" in value:
            raise ValueError("operation presentation result reference must be logical")
        return value


class OperationRawResult(RuntimeContract):
    """Neutral read/internal result; large bytes remain behind ``result_ref``.

    ``result_payload`` is transport output that has already been durably
    stored. It is deliberately not a surface or ledger contract: the gateway
    alone combines it with operation identity to create an outcome presenter
    input.
    """

    result_ref: str | None = Field(default=None, max_length=2048)
    safe_summary: str = Field(min_length=1, max_length=512)
    activity_ref: str | None = Field(default=None, max_length=2048)
    result_payload: dict[str, object] | None = None
    latency_ms: int | None = Field(default=None, ge=0)

    @field_validator("result_ref", "activity_ref")
    @classmethod
    def _logical_refs(cls, value: str | None) -> str | None:
        if value is not None and (value.startswith("/") or ":\\" in value):
            raise ValueError("operation result references must be logical")
        return value


class ProposedEffect(RuntimeContract):
    """Prepared external effect.

    ``stage_id`` and ``proposal_ref`` are produced by an adapter backed by the
    future generic stage service.  A3 never constructs this value at a legacy
    shadow seam and never exposes an effect-application handle.
    """

    stage_id: str = Field(min_length=1, max_length=128)
    proposal_ref: str = Field(min_length=1, max_length=2048)
    safe_summary: str = Field(min_length=1, max_length=512)
    activity_ref: str | None = Field(default=None, max_length=2048)
    artifact_source_ref: str | None = Field(default=None, max_length=2048)

    @field_validator("proposal_ref", "activity_ref", "artifact_source_ref")
    @classmethod
    def _logical_refs(cls, value: str | None) -> str | None:
        if value is not None and (value.startswith("/") or ":\\" in value):
            raise ValueError("proposal references must be logical")
        return value


class OperationResultSummary(RuntimeContract):
    """Bounded facts consumed by deterministic presentation policy."""

    result_ref: str | None = None
    safe_summary: str
    has_artifact_source: bool = False
    is_proposal: bool = False


class PresentationPlan(RuntimeContract):
    """Presentation decision plus a stable, non-user-text reason code."""

    decision: PresentationDecision
    basis: str = Field(min_length=1, max_length=96)


class GateResolution(RuntimeContract):
    """Result of checking the descriptor's required gates."""

    allowed: bool
    gate_kind: GateKind | None = None
    safe_summary: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _blocked_requires_gate(self) -> GateResolution:
        if not self.allowed and self.gate_kind is None:
            raise ValueError("blocked gate resolution requires gate_kind")
        return self


class ArtifactContentPart(RuntimeContract):
    """Provider-neutral, explicit artifact content part.

    It carries the same intent as ``publish_artifact`` and exactly one content
    transport: bounded inline UTF-8 text or a server-resolvable logical ref.
    Plain strings and Markdown/code fences cannot validate as this contract.
    """

    type: str = Field(pattern=r"^artifact$")
    intent: ArtifactIntent
    content: str | None = Field(default=None, max_length=1024 * 1024)
    content_ref: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("content_ref")
    @classmethod
    def _content_ref_is_logical(cls, value: str | None) -> str | None:
        if value is not None and (value.startswith("/") or ":\\" in value):
            raise ValueError("artifact content_ref must be logical")
        return value

    @model_validator(mode="after")
    def _one_content_transport(self) -> ArtifactContentPart:
        if (self.content is None) == (self.content_ref is None):
            raise ValueError(
                "artifact content part requires exactly one content transport"
            )
        return self


class ModelArtifactContentPart(ArtifactContentPart):
    """A3-compatible name for the normalized B1 content-part contract."""


@dataclass(frozen=True)
class ArtifactPublicationSource:
    """Ephemeral bytes/ref handed from a trusted adapter to the A2 repository.

    Inline bytes are deliberately excluded from ``repr`` so failure telemetry
    cannot accidentally include authored content.
    """

    content: bytes | None = field(default=None, repr=False)
    content_ref: str | None = None
    # An adapter may preserve its opaque upstream provenance without exposing
    # the bytes or an implementation path.  This is distinct from
    # ``content_ref``: inline bytes have no server-resolvable source transport
    # but still need an auditable origin such as ``payload://browser/...``.
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if (self.content is None) == (self.content_ref is None):
            raise ValueError(
                "artifact publication source requires exactly one transport"
            )
        if self.content_ref is not None and (
            self.content_ref.startswith("/") or ":\\" in self.content_ref
        ):
            raise ValueError("artifact publication source reference must be logical")
        if self.source_ref is not None and (
            self.source_ref.startswith("/") or ":\\" in self.source_ref
        ):
            raise ValueError("artifact publication provenance must be logical")


@dataclass(frozen=True)
class ArtifactRevisionSource:
    """One agent-authored revision of an artifact that already exists.

    Distinct from :class:`ArtifactPublicationSource` because the target is an
    existing durable object, so the contract must carry the compare-and-append
    fields. ``parent_revision`` is required: a model working from stale content
    must lose the CAS rather than silently clobber a revision the user wrote.

    Deliberately carries no kind/title/media type — those are immutable
    properties of the artifact and are read from the stored record, so revising
    cannot change what an artifact is.
    """

    artifact_id: str
    parent_revision: int
    content: bytes | None = field(default=None, repr=False)
    content_ref: str | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if (self.content is None) == (self.content_ref is None):
            raise ValueError("artifact revision source requires exactly one transport")
        if self.parent_revision < 1:
            raise ValueError("artifact revision source requires a positive parent")
        if self.content_ref is not None and (
            self.content_ref.startswith("/") or ":\\" in self.content_ref
        ):
            raise ValueError("artifact revision source reference must be logical")
        if self.source_ref is not None and (
            self.source_ref.startswith("/") or ":\\" in self.source_ref
        ):
            raise ValueError("artifact revision provenance must be logical")


class OperationAdapter(Protocol):
    """Provider-specific operation port with no effect-apply capability."""

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        """Execute a no-effect or internal-reversible operation."""

    async def build_proposal(self, request: OperationRequest) -> ProposedEffect:
        """Prepare and stage an external effect without applying it."""


class OperationOutcomePresenter(Protocol):
    """Project a completed operation through the configured presentation policy.

    This is deliberately an operation port rather than an MCP port.  Browser,
    builtin, and future providers may return the same
    :class:`OperationPresentationOutcome` without acquiring a second ledger or
    surface-emission seam.
    """

    async def present(self, outcome: OperationPresentationOutcome) -> None:
        """Best-effort presentation after an operation result is durable."""


class ArtifactPublicationAdapter(Protocol):
    """Optional adapter capability for explicit authored artifact bytes/refs."""

    async def artifact_publication(
        self, request: OperationRequest
    ) -> ArtifactPublicationSource | None:
        """Return the exact content transport to persist for this operation."""


class OperationGateResolver(Protocol):
    """Resolve descriptor-declared access/auth/grant gates."""

    async def resolve(
        self,
        *,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        classification: OperationClassification,
    ) -> GateResolution:
        """Return whether execution/proposal construction may proceed."""


class OperationEventEmitter(Protocol):
    """Append canonical operation events to the bound run ledger."""

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        """Append one already-safe payload."""


class ArtifactServicePort(Protocol):
    """Narrow structural subset of A2's Artifact Service used by the gateway."""

    def promote_source(self, **kwargs: object) -> Awaitable[object]:
        """Stream one authorized immutable source into bounded artifact storage."""

    def publish_from_bytes(self, **kwargs: object) -> Awaitable[object]:
        """Persist trusted inline bytes through the A2 repository."""

    def publish_from_source(self, **kwargs: object) -> Awaitable[object]:
        """Persist one exact scoped source with trusted attribution."""


class OperationMetricsPort(Protocol):
    """Low-cardinality metrics sink."""

    def requested(self, *, capability: str, op: str, effect_class: str) -> None: ...

    def completed(
        self,
        *,
        capability: str,
        op: str,
        effect_class: str,
        outcome: str,
        latency_ms: int,
    ) -> None: ...

    def failed(
        self, *, capability: str, op: str, effect_class: str, failure_code: str
    ) -> None: ...

    def classification_mismatch(
        self,
        *,
        capability: str,
        op: str,
        legacy_class: str,
        gateway_class: str,
    ) -> None: ...

    def disposition_mismatch(
        self,
        *,
        capability: str,
        op: str,
        legacy_disposition: str,
        gateway_disposition: str,
    ) -> None: ...

    def stage_required(
        self, *, capability: str, op: str, effect_class: str
    ) -> None: ...

    def artifact_intent(self, *, capability: str, op: str) -> None: ...


class OperationArgumentResolver(Protocol):
    """Canonical argument persistence/resolution seam.

    A3's in-memory implementation is shadow-only.  Authoritative mode requires
    a durable implementation that survives worker restart.
    """

    def put(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None: ...

    def get(self, ref: str) -> tuple[str, bytes] | None: ...


class OperationArgumentStore:
    """Run-local canonical argument bytes for shadow comparison only."""

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, bytes]] = {}

    def put(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None:
        existing = self._entries.get(ref)
        candidate = (digest, canonical_bytes)
        if existing is not None and existing != candidate:
            raise ValueError("operation argument reference already has different bytes")
        self._entries[ref] = candidate

    def get(self, ref: str) -> tuple[str, bytes] | None:
        return self._entries.get(ref)


JsonArguments = JsonObject

__all__ = (
    "ArtifactIntent",
    "ArtifactContentPart",
    "ArtifactPublicationAdapter",
    "ArtifactPublicationSource",
    "ArtifactServicePort",
    "GateResolution",
    "JsonArguments",
    "ModelArtifactContentPart",
    "OperationAdapter",
    "OperationArgumentResolver",
    "OperationArgumentStore",
    "OperationClassification",
    "OperationOutcomePresenter",
    "OperationPresentationOutcome",
    "OperationDescriptor",
    "OperationDisposition",
    "OperationEventEmitter",
    "OperationGatewayMode",
    "OperationGateResolver",
    "OperationMetricsPort",
    "OperationRawResult",
    "OperationRequest",
    "OperationResultSummary",
    "PresentationPlan",
    "ProposedEffect",
)
