"""Typed, content-safe failures for the F6 concurrency domain.

Two families live here, and they are deliberately different shapes because they
answer different questions.

:class:`ConcurrencyPolicyError` describes *declared metadata that was refused*.
Its text is authored at the class level and never interpolates a declared value,
because that value arrived from an untrusted connector or MCP server. The
low-cardinality :class:`ConcurrencyRejectionReason` carries the detail instead.
The family notably does **not** derive from ``ValueError``: Pydantic converts a
``ValueError`` raised inside a validator into a generic ``ValidationError``,
which would erase the typed class and the reason code at exactly the boundaries
that need them most.

:class:`PermitError` describes a *genuine programming fault* in the permit
table — a double release, a release of something never admitted, or a
cross-event-loop reuse. Permit saturation is never in this family: it is a typed
``PermitOutcome`` on a lease, so a caller can never mistake a full pool for a
tool failure.

Kill-switch errors deliberately stay in
:mod:`agent_runtime.capabilities.concurrency.kill_switches`, because they are
parameterized by the kill-switch scope vocabulary that module owns.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar


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


class PermitErrorCode(StrEnum):
    """Stable, content-free permit failure codes."""

    DOUBLE_RELEASE = "permit_double_release"
    RELEASE_NOT_ADMITTED = "permit_release_not_admitted"
    EVENT_LOOP_MISMATCH = "permit_event_loop_mismatch"


class PermitError(RuntimeError):
    """Base permit fault carrying only a stable code and safe public text."""

    def __init__(self, code: PermitErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class PermitDoubleReleaseError(PermitError):
    """Raised when an admitted permit is released more than once."""

    def __init__(self) -> None:
        super().__init__(
            PermitErrorCode.DOUBLE_RELEASE,
            "Concurrency permit was already released.",
        )


class PermitNotAdmittedError(PermitError):
    """Raised when a refused permit is released as if it were held."""

    def __init__(self) -> None:
        super().__init__(
            PermitErrorCode.RELEASE_NOT_ADMITTED,
            "Concurrency permit was never admitted and cannot be released.",
        )


class PermitEventLoopMismatchError(PermitError):
    """Raised when one run's permit table is reused from another event loop."""

    def __init__(self) -> None:
        super().__init__(
            PermitErrorCode.EVENT_LOOP_MISMATCH,
            "Concurrency permits are bound to a single run event loop.",
        )


__all__ = (
    "ConcurrencyDeclarationRejected",
    "ConcurrencyPolicyError",
    "ConcurrencyPolicyWideningRejected",
    "ConcurrencyRejectionReason",
    "PermitDoubleReleaseError",
    "PermitError",
    "PermitErrorCode",
    "PermitEventLoopMismatchError",
    "PermitNotAdmittedError",
    "ResourceKeyRenderRejected",
    "ResourceKeyTemplateRejected",
)
