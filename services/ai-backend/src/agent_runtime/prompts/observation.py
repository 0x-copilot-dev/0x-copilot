"""Body-free F2 prompt assembly and provider-cache observations.

The model-facing prompt and provider response stay outside these contracts.
Only immutable run bindings, canonical digests, bounded counts, and closed
outcome/reason vocabularies may enter the durable run event journal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import Field, NonNegativeInt, field_validator, model_validator

from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_PROVIDER_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"


class PromptCacheOwner(StrEnum):
    """The sole layer that owned cache decoration for one model call."""

    NONE = "none"
    FRAMEWORK = "framework"
    PRODUCT = "product"


class PromptAssemblyOutcome(StrEnum):
    """Closed assembly rollout state."""

    ENFORCED = "enforced"
    SHADOW = "shadow"
    LEGACY_COMPARISON = "legacy_comparison"
    FEATURE_OFF = "feature_off"


class PromptAssemblyReasonCode(StrEnum):
    """Content-free explanation for the recorded assembly state."""

    TYPED_PLAN_ENFORCED = "typed_plan_enforced"
    SHADOW_PLAN_ASSEMBLED = "shadow_plan_assembled"
    LEGACY_RENDERER_COMPARED = "legacy_renderer_compared"
    PROMPT_ASSEMBLY_DISABLED = "prompt_assembly_disabled"


class PromptCacheOutcome(StrEnum):
    """Provider-reported cache result, or an explicit unsupported state."""

    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"
    MISS = "miss"
    UNSUPPORTED = "unsupported"


class PromptCacheReasonCode(StrEnum):
    """Closed cache observation reasons safe for metrics and F1."""

    PROVIDER_REPORTED_READ = "provider_reported_read"
    PROVIDER_REPORTED_WRITE = "provider_reported_write"
    PROVIDER_REPORTED_READ_WRITE = "provider_reported_read_write"
    PROVIDER_REPORTED_MISS = "provider_reported_miss"
    PROVIDER_METADATA_NOT_REPORTED = "provider_metadata_not_reported"
    ADAPTER_UNSUPPORTED = "adapter_unsupported"
    DECORATION_DISABLED = "decoration_disabled"


class PromptFragmentTokenTotals(RuntimeContract):
    """Bounded estimated token totals by canonical fragment tier."""

    system_policy: NonNegativeInt = 0
    stable: NonNegativeInt = 0
    contextual: NonNegativeInt = 0
    volatile: NonNegativeInt = 0
    current_turn: NonNegativeInt = 0

    @property
    def total(self) -> int:
        return (
            self.system_policy
            + self.stable
            + self.contextual
            + self.volatile
            + self.current_turn
        )


class PromptAssemblyObservationInput(RuntimeContract):
    """Narrow bridge from an assembled plan into the observation lane."""

    model_call_id: Annotated[str, Field(min_length=1, max_length=160)]
    plan_id: Annotated[str, Field(min_length=1, max_length=160)]
    plan_revision: Annotated[str, Field(min_length=1, max_length=160)]
    plan_digest: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(pattern=_PROVIDER_PATTERN, max_length=80)
    model_family: Annotated[str, Field(min_length=1, max_length=160)]
    complete_system_digest: str = Field(pattern=_SHA256_PATTERN)
    stable_prefix_digest: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    fragment_count: NonNegativeInt
    stable_prefix_fragment_count: NonNegativeInt
    system_bytes: NonNegativeInt
    estimated_input_tokens: NonNegativeInt
    fragment_tokens: PromptFragmentTokenTotals = Field(
        default_factory=PromptFragmentTokenTotals
    )
    cache_owner: PromptCacheOwner
    outcome: PromptAssemblyOutcome
    reason_code: PromptAssemblyReasonCode

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("model_family")
    @classmethod
    def _normalize_model_family(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model_family must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _stable_prefix_is_consistent(self) -> "PromptAssemblyObservationInput":
        if self.stable_prefix_fragment_count > self.fragment_count:
            raise ValueError("stable prefix count exceeds total fragment count")
        has_prefix = self.stable_prefix_fragment_count > 0
        if has_prefix != (self.stable_prefix_digest is not None):
            raise ValueError(
                "stable prefix digest and fragment count must be present together"
            )
        if self.fragment_tokens.total > self.estimated_input_tokens:
            raise ValueError("fragment token totals exceed estimated input tokens")
        expected_reason = {
            PromptAssemblyOutcome.ENFORCED: (
                PromptAssemblyReasonCode.TYPED_PLAN_ENFORCED
            ),
            PromptAssemblyOutcome.SHADOW: (
                PromptAssemblyReasonCode.SHADOW_PLAN_ASSEMBLED
            ),
            PromptAssemblyOutcome.LEGACY_COMPARISON: (
                PromptAssemblyReasonCode.LEGACY_RENDERER_COMPARED
            ),
            PromptAssemblyOutcome.FEATURE_OFF: (
                PromptAssemblyReasonCode.PROMPT_ASSEMBLY_DISABLED
            ),
        }[self.outcome]
        if self.reason_code is not expected_reason:
            raise ValueError("assembly outcome and reason code do not reconcile")
        return self


class PromptCacheObservationInput(RuntimeContract):
    """Actual provider usage bound to one prior assembly observation."""

    model_call_id: Annotated[str, Field(min_length=1, max_length=160)]
    assembly_record_id: Annotated[str, Field(min_length=1, max_length=160)]
    assembly_record_digest: str = Field(pattern=_SHA256_PATTERN)
    plan_id: Annotated[str, Field(min_length=1, max_length=160)]
    plan_digest: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(pattern=_PROVIDER_PATTERN, max_length=80)
    model_family: Annotated[str, Field(min_length=1, max_length=160)]
    cache_owner: PromptCacheOwner
    outcome: PromptCacheOutcome
    reason_code: PromptCacheReasonCode
    provider_reported: bool
    input_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def _usage_reconciles(self) -> "PromptCacheObservationInput":
        if (
            self.cached_input_tokens + self.cache_creation_input_tokens
            > self.input_tokens
        ):
            raise ValueError("cache token subsets exceed provider input tokens")
        if self.outcome is PromptCacheOutcome.UNSUPPORTED:
            if self.provider_reported:
                raise ValueError(
                    "unsupported cache outcome cannot be provider-reported"
                )
            if self.cached_input_tokens or self.cache_creation_input_tokens:
                raise ValueError("unsupported cache outcome cannot carry cache tokens")
            if self.reason_code not in {
                PromptCacheReasonCode.PROVIDER_METADATA_NOT_REPORTED,
                PromptCacheReasonCode.ADAPTER_UNSUPPORTED,
                PromptCacheReasonCode.DECORATION_DISABLED,
            }:
                raise ValueError("unsupported cache outcome has an invalid reason")
            return self
        if not self.provider_reported:
            raise ValueError("cache read/write/miss requires provider metadata")
        expected = _reported_cache_outcome(
            cached_input_tokens=self.cached_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
        )
        if self.outcome is not expected:
            raise ValueError(
                "cache outcome does not reconcile with provider token usage"
            )
        expected_reason = {
            PromptCacheOutcome.READ: PromptCacheReasonCode.PROVIDER_REPORTED_READ,
            PromptCacheOutcome.WRITE: PromptCacheReasonCode.PROVIDER_REPORTED_WRITE,
            PromptCacheOutcome.READ_WRITE: (
                PromptCacheReasonCode.PROVIDER_REPORTED_READ_WRITE
            ),
            PromptCacheOutcome.MISS: PromptCacheReasonCode.PROVIDER_REPORTED_MISS,
        }[self.outcome]
        if self.reason_code is not expected_reason:
            raise ValueError("cache outcome and reason code do not reconcile")
        return self

    @classmethod
    def from_usage(
        cls,
        *,
        assembly: "PromptAssembledRecord",
        usage: NormalizedTokenUsage,
        reason_code: PromptCacheReasonCode | None = None,
    ) -> "PromptCacheObservationInput":
        """Normalize only usage metadata the selected provider actually sent."""

        if usage.provider_cache_metadata_observed:
            outcome = _reported_cache_outcome(
                cached_input_tokens=usage.cached_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
            )
            resolved_reason = (
                reason_code
                or {
                    PromptCacheOutcome.READ: PromptCacheReasonCode.PROVIDER_REPORTED_READ,
                    PromptCacheOutcome.WRITE: PromptCacheReasonCode.PROVIDER_REPORTED_WRITE,
                    PromptCacheOutcome.READ_WRITE: (
                        PromptCacheReasonCode.PROVIDER_REPORTED_READ_WRITE
                    ),
                    PromptCacheOutcome.MISS: PromptCacheReasonCode.PROVIDER_REPORTED_MISS,
                }[outcome]
            )
            provider_reported = True
        else:
            outcome = PromptCacheOutcome.UNSUPPORTED
            resolved_reason = (
                reason_code or PromptCacheReasonCode.PROVIDER_METADATA_NOT_REPORTED
            )
            provider_reported = False
        return cls(
            model_call_id=assembly.model_call_id,
            assembly_record_id=assembly.record_id,
            assembly_record_digest=assembly.record_digest,
            plan_id=assembly.plan_id,
            plan_digest=assembly.plan_digest,
            provider=assembly.provider,
            model_family=assembly.model_family,
            cache_owner=assembly.cache_owner,
            outcome=outcome,
            reason_code=resolved_reason,
            provider_reported=provider_reported,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
        )


class _PromptObservationRecord(RuntimeContract):
    schema_version: Literal[1] = 1
    record_id: Annotated[str, Field(min_length=1, max_length=160)]
    run_id: Annotated[str, Field(min_length=1, max_length=160)]
    snapshot_id: Annotated[str, Field(min_length=1, max_length=160)]
    snapshot_digest: str = Field(pattern=_SHA256_PATTERN)
    model_call_id: Annotated[str, Field(min_length=1, max_length=160)]
    created_at: datetime
    record_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _digest_matches(self) -> "_PromptObservationRecord":
        if self.record_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError(
                "prompt observation digest does not match canonical record"
            )
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"created_at", "record_digest"},
        )


class PromptAssembledRecord(_PromptObservationRecord):
    """Strict body-free ``prompt.assembled.v1`` record."""

    record_kind: Literal["assembled"] = "assembled"
    plan_id: str
    plan_revision: str
    plan_digest: str = Field(pattern=_SHA256_PATTERN)
    provider: str
    model_family: str
    complete_system_digest: str = Field(pattern=_SHA256_PATTERN)
    stable_prefix_digest: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    fragment_count: NonNegativeInt
    stable_prefix_fragment_count: NonNegativeInt
    system_bytes: NonNegativeInt
    estimated_input_tokens: NonNegativeInt
    fragment_tokens: PromptFragmentTokenTotals
    cache_owner: PromptCacheOwner
    outcome: PromptAssemblyOutcome
    reason_code: PromptAssemblyReasonCode

    @model_validator(mode="after")
    def _assembly_reconciles(self) -> "PromptAssembledRecord":
        PromptAssemblyObservationInput.model_validate(
            self.model_dump(
                mode="python",
                include=set(PromptAssemblyObservationInput.model_fields),
            )
        )
        return self

    @classmethod
    def create(
        cls,
        *,
        binding: RunControlBinding,
        observation: PromptAssemblyObservationInput,
        created_at: datetime | None = None,
    ) -> "PromptAssembledRecord":
        snapshot = binding.snapshot
        record_id = _record_id(
            run_id=snapshot.run_id,
            record_kind="assembled",
            model_call_id=observation.model_call_id,
        )
        values = {
            "record_id": record_id,
            "run_id": snapshot.run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_digest": snapshot.snapshot_digest,
            **observation.model_dump(mode="python"),
            "fragment_tokens": observation.fragment_tokens,
            "created_at": created_at or datetime.now(timezone.utc),
        }
        provisional = cls.model_construct(**values, record_digest="0" * 64)
        return cls(
            **values,
            record_digest=canonical_json_sha256(provisional.digest_payload()),
        )


class PromptCacheObservedRecord(_PromptObservationRecord):
    """Strict body-free ``prompt.cache.observed.v1`` record."""

    record_kind: Literal["cache_observed"] = "cache_observed"
    assembly_record_id: str
    assembly_record_digest: str = Field(pattern=_SHA256_PATTERN)
    plan_id: str
    plan_digest: str = Field(pattern=_SHA256_PATTERN)
    provider: str
    model_family: str
    cache_owner: PromptCacheOwner
    outcome: PromptCacheOutcome
    reason_code: PromptCacheReasonCode
    provider_reported: bool
    input_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0

    @model_validator(mode="after")
    def _usage_reconciles(self) -> "PromptCacheObservedRecord":
        PromptCacheObservationInput.model_validate(
            self.model_dump(
                mode="python",
                include=set(PromptCacheObservationInput.model_fields),
            )
        )
        return self

    @classmethod
    def create(
        cls,
        *,
        binding: RunControlBinding,
        observation: PromptCacheObservationInput,
        created_at: datetime | None = None,
    ) -> "PromptCacheObservedRecord":
        snapshot = binding.snapshot
        record_id = _record_id(
            run_id=snapshot.run_id,
            record_kind="cache_observed",
            model_call_id=observation.model_call_id,
        )
        values = {
            "record_id": record_id,
            "run_id": snapshot.run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_digest": snapshot.snapshot_digest,
            **observation.model_dump(mode="python"),
            "created_at": created_at or datetime.now(timezone.utc),
        }
        provisional = cls.model_construct(**values, record_digest="0" * 64)
        return cls(
            **values,
            record_digest=canonical_json_sha256(provisional.digest_payload()),
        )


PromptObservationRecord: TypeAlias = PromptAssembledRecord | PromptCacheObservedRecord


class SequencedPromptObservationRecord(RuntimeContract):
    sequence_no: Annotated[int, Field(ge=1)]
    record: PromptObservationRecord = Field(discriminator="record_kind")


class PromptObservationWrite(RuntimeContract):
    """Verified tenant/subject transport facts for one observation append."""

    org_id: Annotated[str, Field(min_length=1, max_length=160)]
    subject_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    trace_id: Annotated[str, Field(min_length=1, max_length=160)]
    record: PromptObservationRecord = Field(discriminator="record_kind")


class PromptObservationStorePort(Protocol):
    async def append(
        self,
        write: PromptObservationWrite,
    ) -> SequencedPromptObservationRecord: ...

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedPromptObservationRecord, ...]: ...


class PromptAssemblyObserver:
    """Run-bound service used by the model seam to publish F2 observations."""

    def __init__(
        self,
        *,
        store: PromptObservationStorePort,
        binding: RunControlBinding,
        org_id: str,
        subject_fingerprint: str,
        trace_id: str,
    ) -> None:
        if subject_fingerprint != binding.snapshot.subject_fingerprint:
            raise PromptObservationScopeConflict(run_id=binding.snapshot.run_id)
        self._store = store
        self._binding = binding
        self._org_id = org_id
        self._subject_fingerprint = subject_fingerprint
        self._trace_id = trace_id

    async def record_assembled(
        self,
        observation: PromptAssemblyObservationInput,
        *,
        created_at: datetime | None = None,
    ) -> SequencedPromptObservationRecord:
        record = PromptAssembledRecord.create(
            binding=self._binding,
            observation=observation,
            created_at=created_at,
        )
        return await self._store.append(self._write(record))

    async def record_cache(
        self,
        *,
        assembly: PromptAssembledRecord,
        usage: NormalizedTokenUsage,
        reason_code: PromptCacheReasonCode | None = None,
        created_at: datetime | None = None,
    ) -> SequencedPromptObservationRecord:
        if (
            assembly.run_id != self._binding.snapshot.run_id
            or assembly.snapshot_id != self._binding.snapshot.snapshot_id
            or assembly.snapshot_digest != self._binding.snapshot.snapshot_digest
        ):
            raise PromptObservationSnapshotConflict(run_id=assembly.run_id)
        observation = PromptCacheObservationInput.from_usage(
            assembly=assembly,
            usage=usage,
            reason_code=reason_code,
        )
        record = PromptCacheObservedRecord.create(
            binding=self._binding,
            observation=observation,
            created_at=created_at,
        )
        return await self._store.append(self._write(record))

    def _write(self, record: PromptObservationRecord) -> PromptObservationWrite:
        return PromptObservationWrite(
            org_id=self._org_id,
            subject_fingerprint=self._subject_fingerprint,
            trace_id=self._trace_id,
            record=record,
        )


class PromptObservationError(RuntimeError):
    """Base error for fail-closed prompt observation persistence."""


class PromptObservationConflict(PromptObservationError):
    def __init__(self, *, run_id: str, record_id: str) -> None:
        self.run_id = run_id
        self.record_id = record_id
        super().__init__(f"prompt observation {record_id} conflicts for run {run_id}")


class PromptObservationCorruption(PromptObservationError):
    def __init__(self, *, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"prompt observation journal for run {run_id}: {reason}")


class PromptObservationScopeConflict(PromptObservationError):
    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"prompt observation scope conflict for run {run_id}")


class PromptObservationSnapshotConflict(PromptObservationError):
    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"prompt observation snapshot conflict for run {run_id}")


def _record_id(*, run_id: str, record_kind: str, model_call_id: str) -> str:
    digest = hashlib.sha256(
        f"{run_id}\x00{record_kind}\x00{model_call_id}".encode("utf-8")
    ).hexdigest()
    return f"prompt_{record_kind}:{digest}"


def _reported_cache_outcome(
    *,
    cached_input_tokens: int,
    cache_creation_input_tokens: int,
) -> PromptCacheOutcome:
    if cached_input_tokens and cache_creation_input_tokens:
        return PromptCacheOutcome.READ_WRITE
    if cached_input_tokens:
        return PromptCacheOutcome.READ
    if cache_creation_input_tokens:
        return PromptCacheOutcome.WRITE
    return PromptCacheOutcome.MISS


__all__ = [
    "PromptAssembledRecord",
    "PromptAssemblyObservationInput",
    "PromptAssemblyObserver",
    "PromptAssemblyOutcome",
    "PromptAssemblyReasonCode",
    "PromptCacheObservationInput",
    "PromptCacheObservedRecord",
    "PromptCacheOutcome",
    "PromptCacheOwner",
    "PromptCacheReasonCode",
    "PromptFragmentTokenTotals",
    "PromptObservationConflict",
    "PromptObservationCorruption",
    "PromptObservationRecord",
    "PromptObservationScopeConflict",
    "PromptObservationSnapshotConflict",
    "PromptObservationStorePort",
    "PromptObservationWrite",
    "SequencedPromptObservationRecord",
]
