"""E2 D2 bounded, non-authoritative shadow comparison primitives.

The comparator is intentionally incapable of becoming an execution path.  It
has no gateway, artifact, stage, queue, persistence, event-emitter, or model
return-value dependency.  Its only outputs are a closed-vocabulary metric and
an occasionally sampled diagnostic containing process-keyed fingerprints.

``E2RolloutResolution`` is the sole rollout authority.  A comparison records
only when its mapped capability is exactly ``shadow``.  ``off`` and
``enforce`` are both inert here, so this module cannot accidentally activate
or alter enforce behavior before the owning cohort implementation lands.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from agent_runtime.observability.shadow_comparison_metrics import (
    FailSoftShadowComparisonMetrics,
    ProtectedShadowDiagnosticLogSink,
    ShadowComparisonDiagnosticSink,
    ShadowComparisonMetrics,
    ShadowComparisonMetricsPort,
)
from agent_runtime.rollout import (
    E2RolloutResolution,
    RolloutCapability,
    RolloutMode,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes


class ShadowComparisonKind(StrEnum):
    """The closed D2 comparison surface; never accept caller-defined kinds."""

    CLASSIFICATION = "classification"
    DISPOSITION = "disposition"
    SURFACE_TAB_PROJECTION = "surface_tab_projection"
    RECEIPT_FOLD = "receipt_fold"
    PENDING_FOLD = "pending_fold"
    USAGE_FOLD = "usage_fold"
    ARTIFACT_DRAFT_METADATA = "artifact_draft_metadata"
    PROPOSAL_CANONICALIZATION = "proposal_canonicalization"


class ShadowComparisonOutcome(StrEnum):
    """Closed metric outcomes; inputs never become label values."""

    DISABLED = "disabled"
    MATCH = "match"
    MISMATCH = "mismatch"
    UNCOMPARABLE = "uncomparable"
    ERROR = "error"


_CAPABILITY_BY_KIND: Final[dict[ShadowComparisonKind, RolloutCapability]] = {
    ShadowComparisonKind.CLASSIFICATION: RolloutCapability.OPERATION_GATEWAY,
    ShadowComparisonKind.DISPOSITION: RolloutCapability.OPERATION_GATEWAY,
    ShadowComparisonKind.PROPOSAL_CANONICALIZATION: RolloutCapability.OPERATION_GATEWAY,
    ShadowComparisonKind.SURFACE_TAB_PROJECTION: RolloutCapability.PRESENTATION_V2_1,
    ShadowComparisonKind.RECEIPT_FOLD: RolloutCapability.PRESENTATION_V2_1,
    ShadowComparisonKind.PENDING_FOLD: RolloutCapability.PRESENTATION_V2_1,
    ShadowComparisonKind.USAGE_FOLD: RolloutCapability.PRESENTATION_V2_1,
    ShadowComparisonKind.ARTIFACT_DRAFT_METADATA: RolloutCapability.ARTIFACT_REPOSITORY,
}


class _Bounds:
    """Hard CPU/memory bounds for values entering a diagnostic fingerprint."""

    MAX_DEPTH = 6
    MAX_FIELDS = 256
    MAX_MAPPING_ITEMS = 64
    MAX_SEQUENCE_ITEMS = 64
    MAX_STRING_BYTES = 1024
    DIAGNOSTIC_SAMPLE_DENOMINATOR = 64
    DIGEST_HEX_LENGTH = 32


# Process-local salting means a diagnostic hash cannot be dictionary-attacked
# outside the process.  It intentionally changes on process restart; the hash
# is a protected debugging correlation token, not a durable identifier.
_PROCESS_FINGERPRINT_KEY: Final[bytes] = secrets.token_bytes(32)


@dataclass(frozen=True)
class BoundedShadowFingerprint:
    """A private, bounded fingerprint with no replayable source value."""

    digest: str
    field_count: int
    truncated: bool


@dataclass(frozen=True)
class ShadowComparisonResult:
    """Safe return value for tests and read-only operational observers."""

    kind: ShadowComparisonKind
    capability: RolloutCapability
    outcome: ShadowComparisonOutcome
    legacy_digest: str | None = None
    canonical_digest: str | None = None


class ProtectedShadowDiagnostic(BaseModel):
    """The sole diagnostic schema accepted by the production log sink.

    Fingerprints are keyed and truncated.  No raw field name, identifier,
    proposal, path, title, content, exception text, or tenant value is allowed
    on this model.
    """

    kind: ShadowComparisonKind
    capability: RolloutCapability
    legacy_digest: str = Field(
        min_length=_Bounds.DIGEST_HEX_LENGTH, max_length=_Bounds.DIGEST_HEX_LENGTH
    )
    canonical_digest: str = Field(
        min_length=_Bounds.DIGEST_HEX_LENGTH, max_length=_Bounds.DIGEST_HEX_LENGTH
    )
    legacy_field_count: int = Field(ge=0, le=_Bounds.MAX_FIELDS)
    canonical_field_count: int = Field(ge=0, le=_Bounds.MAX_FIELDS)
    legacy_truncated: bool
    canonical_truncated: bool
    sample_key_digest: str = Field(
        min_length=_Bounds.DIGEST_HEX_LENGTH, max_length=_Bounds.DIGEST_HEX_LENGTH
    )


class _UncomparableValue(ValueError):
    """Input cannot be represented without exceeding D2's safety bounds."""


class _FingerprintBuilder:
    """Build a safely comparable structural shape without retaining raw values."""

    def __init__(self) -> None:
        self.field_count = 0
        self.truncated = False

    def fingerprint(self, value: object) -> BoundedShadowFingerprint:
        normalized = self._normalize(value, depth=0)
        digest = _protected_digest(canonical_json_bytes(normalized))
        return BoundedShadowFingerprint(
            digest=digest,
            field_count=self.field_count,
            truncated=self.truncated,
        )

    def _normalize(self, value: object, *, depth: int) -> object:
        self._field()
        if depth > _Bounds.MAX_DEPTH:
            self.truncated = True
            return {"type": "max_depth"}
        if isinstance(value, BaseModel):
            try:
                return self._normalize(value.model_dump(mode="json"), depth=depth + 1)
            except Exception as exc:  # no raw exception escapes this boundary
                raise _UncomparableValue from exc
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "bool", "value": value}
        if isinstance(value, int) and not isinstance(value, bool):
            return {"type": "int", "value": value}
        if isinstance(value, float):
            if not math.isfinite(value):
                raise _UncomparableValue
            return {"type": "float", "value": value}
        if isinstance(value, str):
            return self._text(value.encode("utf-8", errors="surrogatepass"), "str")
        if isinstance(value, bytes):
            return self._text(value, "bytes")
        if isinstance(value, Mapping):
            return self._mapping(value, depth=depth)
        if isinstance(value, (list, tuple)):
            return self._sequence(value, depth=depth)
        raise _UncomparableValue

    def _mapping(self, value: Mapping[object, object], *, depth: int) -> object:
        entries: list[tuple[str, object]] = []
        try:
            iterator = iter(value.items())
            for index, (key, item) in enumerate(iterator):
                if index >= _Bounds.MAX_MAPPING_ITEMS:
                    self.truncated = True
                    break
                if not isinstance(key, str):
                    raise _UncomparableValue
                entries.append((_protected_digest(key.encode("utf-8")), item))
        except _UncomparableValue:
            raise
        except Exception as exc:
            raise _UncomparableValue from exc
        return {
            "type": "mapping",
            "entries": [
                {"key": digest, "value": self._normalize(item, depth=depth + 1)}
                for digest, item in sorted(entries, key=lambda entry: entry[0])
            ],
            "truncated": self.truncated,
        }

    def _sequence(
        self, value: list[object] | tuple[object, ...], *, depth: int
    ) -> object:
        if len(value) > _Bounds.MAX_SEQUENCE_ITEMS:
            self.truncated = True
        return {
            "type": "sequence",
            "items": [
                self._normalize(item, depth=depth + 1)
                for item in value[: _Bounds.MAX_SEQUENCE_ITEMS]
            ],
            "truncated": self.truncated,
        }

    def _text(self, value: bytes, value_type: str) -> object:
        if len(value) > _Bounds.MAX_STRING_BYTES:
            self.truncated = True
        prefix = value[: _Bounds.MAX_STRING_BYTES]
        return {
            "type": value_type,
            "bytes": len(value),
            "digest": _protected_digest(prefix),
            "truncated": len(value) > _Bounds.MAX_STRING_BYTES,
        }

    def _field(self) -> None:
        self.field_count += 1
        if self.field_count > _Bounds.MAX_FIELDS:
            self.truncated = True
            raise _UncomparableValue


def _protected_digest(value: bytes) -> str:
    """Return a process-keyed fixed-length digest; never a raw identifier."""

    return hashlib.blake2b(
        value,
        key=_PROCESS_FINGERPRINT_KEY,
        digest_size=_Bounds.DIGEST_HEX_LENGTH // 2,
    ).hexdigest()


class ShadowComparisonService:
    """Compare pure values in E2 shadow mode without gaining execution authority."""

    def __init__(
        self,
        *,
        resolution: E2RolloutResolution,
        metrics_port: ShadowComparisonMetricsPort | None = None,
        diagnostic_sink: ShadowComparisonDiagnosticSink | None = None,
        diagnostic_sampler: Callable[[str], bool] | None = None,
    ) -> None:
        self._resolution = resolution
        self._telemetry = FailSoftShadowComparisonMetrics(
            metrics_port=metrics_port or ShadowComparisonMetrics(),
            diagnostic_sink=diagnostic_sink or ProtectedShadowDiagnosticLogSink(),
        )
        self._diagnostic_sampler = diagnostic_sampler or self._sample_diagnostic

    @staticmethod
    def capability_for(kind: ShadowComparisonKind) -> RolloutCapability:
        """Return D1's one typed rollout lane for a comparison kind."""

        return _CAPABILITY_BY_KIND[kind]

    def is_shadow_enabled(self, kind: ShadowComparisonKind) -> bool:
        """Only exactly-shadow lanes record; enforce is deliberately inert."""

        return (
            self._resolution.modes.mode_for(self.capability_for(kind))
            is RolloutMode.SHADOW
        )

    def compare(
        self,
        *,
        kind: ShadowComparisonKind,
        legacy: object,
        canonical: object,
        sample_key: str | None,
    ) -> ShadowComparisonResult:
        """Fingerprint two already-computed values; never invoke a producer.

        The method is synchronous by design.  A caller cannot pass a callback,
        coroutine, gateway, queue command, or model-visible continuation for
        this service to invoke.
        """

        capability = self.capability_for(kind)
        if not self.is_shadow_enabled(kind):
            return ShadowComparisonResult(
                kind=kind,
                capability=capability,
                outcome=ShadowComparisonOutcome.DISABLED,
            )
        try:
            legacy_fingerprint = _FingerprintBuilder().fingerprint(legacy)
            canonical_fingerprint = _FingerprintBuilder().fingerprint(canonical)
        except _UncomparableValue:
            return self._record(
                kind=kind,
                capability=capability,
                outcome=ShadowComparisonOutcome.UNCOMPARABLE,
            )
        except Exception:
            return self._record(
                kind=kind,
                capability=capability,
                outcome=ShadowComparisonOutcome.ERROR,
            )

        outcome = (
            ShadowComparisonOutcome.MATCH
            if legacy_fingerprint.digest == canonical_fingerprint.digest
            else ShadowComparisonOutcome.MISMATCH
        )
        result = self._record(
            kind=kind,
            capability=capability,
            outcome=outcome,
            legacy_digest=legacy_fingerprint.digest,
            canonical_digest=canonical_fingerprint.digest,
        )
        if outcome is ShadowComparisonOutcome.MISMATCH and sample_key is not None:
            self._record_sampled_diagnostic(
                kind=kind,
                capability=capability,
                legacy=legacy_fingerprint,
                canonical=canonical_fingerprint,
                sample_key=sample_key,
            )
        return result

    def uncomparable(self, *, kind: ShadowComparisonKind) -> ShadowComparisonResult:
        """Record a bounded-input skip without pretending the values matched."""

        capability = self.capability_for(kind)
        if not self.is_shadow_enabled(kind):
            return ShadowComparisonResult(
                kind=kind,
                capability=capability,
                outcome=ShadowComparisonOutcome.DISABLED,
            )
        return self._record(
            kind=kind,
            capability=capability,
            outcome=ShadowComparisonOutcome.UNCOMPARABLE,
        )

    def _record(
        self,
        *,
        kind: ShadowComparisonKind,
        capability: RolloutCapability,
        outcome: ShadowComparisonOutcome,
        legacy_digest: str | None = None,
        canonical_digest: str | None = None,
    ) -> ShadowComparisonResult:
        self._telemetry.comparison(
            kind=kind,
            capability=capability,
            outcome=outcome,
        )
        return ShadowComparisonResult(
            kind=kind,
            capability=capability,
            outcome=outcome,
            legacy_digest=legacy_digest,
            canonical_digest=canonical_digest,
        )

    def _record_sampled_diagnostic(
        self,
        *,
        kind: ShadowComparisonKind,
        capability: RolloutCapability,
        legacy: BoundedShadowFingerprint,
        canonical: BoundedShadowFingerprint,
        sample_key: str,
    ) -> None:
        sample_key_digest = _protected_digest(sample_key.encode("utf-8"))
        if not self._diagnostic_sampler(sample_key_digest):
            return
        diagnostic = ProtectedShadowDiagnostic(
            kind=kind,
            capability=capability,
            legacy_digest=legacy.digest,
            canonical_digest=canonical.digest,
            legacy_field_count=legacy.field_count,
            canonical_field_count=canonical.field_count,
            legacy_truncated=legacy.truncated,
            canonical_truncated=canonical.truncated,
            sample_key_digest=sample_key_digest,
        )
        self._telemetry.diagnostic_sampled(
            kind=kind,
            capability=capability,
            diagnostic=diagnostic,
        )

    @staticmethod
    def _sample_diagnostic(sample_key_digest: str) -> bool:
        """Deterministically retain one in 64 protected mismatch samples."""

        return (
            int(sample_key_digest[-4:], 16) % _Bounds.DIAGNOSTIC_SAMPLE_DENOMINATOR == 0
        )


_CONTEXT: ContextVar[ShadowComparisonService | None] = ContextVar(
    "e2_shadow_comparison_context", default=None
)


class ShadowComparisonContext:
    """Run-scoped access to the D2 observer, following existing ContextVar seams."""

    @classmethod
    def bind_for_run(
        cls,
        *,
        resolution: E2RolloutResolution,
        metrics_port: ShadowComparisonMetricsPort | None = None,
        diagnostic_sink: ShadowComparisonDiagnosticSink | None = None,
    ) -> Token[ShadowComparisonService | None]:
        return _CONTEXT.set(
            ShadowComparisonService(
                resolution=resolution,
                metrics_port=metrics_port,
                diagnostic_sink=diagnostic_sink,
            )
        )

    @staticmethod
    def active() -> ShadowComparisonService | None:
        return _CONTEXT.get()

    @staticmethod
    def unbind(token: Token[ShadowComparisonService | None]) -> None:
        _CONTEXT.reset(token)


__all__ = (
    "BoundedShadowFingerprint",
    "ProtectedShadowDiagnostic",
    "ShadowComparisonContext",
    "ShadowComparisonKind",
    "ShadowComparisonOutcome",
    "ShadowComparisonResult",
    "ShadowComparisonService",
)
