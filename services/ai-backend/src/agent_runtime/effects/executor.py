"""Closed executor protocol for the universal effect coordinator.

Executors own transport mechanics only.  They receive an exact,
server-constructed request and a prepared handle; they never receive mutable
stage state, approval policy, or model input.  The coordinator owns every
decision about whether an external mutation may happen.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator

from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.contracts import EffectDispatchRequest
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.entities import (
    EffectExecutionRequest,
    EffectExecutionResult,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind, EffectOutcome

_REF_MAX_LENGTH = 2048


class EffectExecutorCapabilities(RuntimeContract):
    """Static executor declaration used by the coordinator and registry.

    ``prepare_performs_mutation`` must remain false for every normal executor.
    A legacy transport that cannot split prepare/apply declares
    ``supports_prepare=False``; the coordinator still performs all of its own
    validation before it durably claims and invokes that executor's one apply.
    """

    supports_prepare: bool = True
    supports_reconcile: bool = False
    native_idempotency: bool = False
    prepare_performs_mutation: bool = False


EffectPreparedRequest = EffectDispatchRequest | EffectExecutionRequest


class PreparedEffect(RuntimeContract):
    """Opaque prepare result that can safely be persisted as a claim reference."""

    # ``EffectExecutionRequest`` remains accepted for direct executor contract
    # tests and persisted A4 compatibility. Worker dispatch always supplies the
    # narrower shared ``EffectDispatchRequest``.
    request: EffectPreparedRequest
    prepared_ref: str | None = Field(default=None, max_length=_REF_MAX_LENGTH)
    observed_precondition_digest: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    expires_at: str | None = None

    @field_validator("prepared_ref")
    @classmethod
    def _prepared_ref_is_opaque(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or value != value.strip()
            or "://" not in value
            or value.startswith(("/", "~", "\\"))
            or value.lower().startswith(("file://", "filesystem://", "data:"))
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise ValueError("prepared_ref must be an opaque safe URI reference")
        return value


class EffectExecutionAuthorization(RuntimeContract):
    """Live authorization verdict immediately preceding one effect dispatch.

    The coordinator has already verified the immutable approved stage.  This
    separate verdict is intentionally transport-neutral: it allows an executor
    to re-check a revocable connector grant, native host permit, or browser
    session without receiving mutable policy or model arguments.
    """

    allowed: bool
    safe_code: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[a-z0-9._-]+$",
    )


class EffectExecutionScope(RuntimeContract):
    """Verified run facts supplied to executor factories, never command payloads."""

    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    conversation_id: str | None = Field(default=None, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    owner_ref: str = Field(min_length=1, max_length=_REF_MAX_LENGTH)


@runtime_checkable
class EffectExecutor(Protocol):
    """Transport-only executor protocol consumed exclusively by A5 workers."""

    kind: EffectExecutorKind
    capabilities: EffectExecutorCapabilities

    async def prepare(self, request: EffectDispatchRequest) -> PreparedEffect:
        """Validate/reserve/read state without a user-visible mutation."""

    async def authorize(self, prepared: PreparedEffect) -> EffectExecutionAuthorization:
        """Re-check revocable authority immediately before ``apply``."""

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        """Apply the exact approved request after the coordinator's durable claim."""

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        """Resolve a prior claim without replaying a potentially sent mutation."""

    async def abort(self, prepared: PreparedEffect) -> None:
        """Release an un-applied prepared reservation best-effort."""


class RecordingEffectExecutor:
    """Deterministic protocol fake used to prove coordinator ordering.

    It is intentionally a real implementation of the protocol rather than a
    mock: tests can inspect ``calls`` while controlling each outcome.  It has no
    network/client dependency and never synthesizes approval or policy.
    """

    def __init__(
        self,
        *,
        kind: EffectExecutorKind = EffectExecutorKind.BUILTIN,
        capabilities: EffectExecutorCapabilities | None = None,
        prepare_result: PreparedEffect | None = None,
        apply_result: EffectExecutionResult | None = None,
        reconcile_result: EffectExecutionResult | None = None,
        on_prepare: Callable[[EffectDispatchRequest], Awaitable[PreparedEffect]]
        | None = None,
        on_apply: Callable[[PreparedEffect], Awaitable[EffectExecutionResult]]
        | None = None,
        on_reconcile: Callable[[EffectClaim], Awaitable[EffectExecutionResult]]
        | None = None,
        on_authorize: Callable[
            [PreparedEffect], Awaitable[EffectExecutionAuthorization]
        ]
        | None = None,
    ) -> None:
        self.kind = kind
        self.capabilities = capabilities or EffectExecutorCapabilities(
            supports_prepare=True,
            supports_reconcile=True,
            native_idempotency=True,
        )
        self._prepare_result = prepare_result
        self._apply_result = apply_result or EffectExecutionResult(
            outcome=EffectOutcome.APPLIED,
            retryable=False,
        )
        self._reconcile_result = reconcile_result or EffectExecutionResult(
            outcome=EffectOutcome.INDETERMINATE,
            retryable=False,
        )
        self._on_prepare = on_prepare
        self._on_apply = on_apply
        self._on_reconcile = on_reconcile
        self._on_authorize = on_authorize
        self.calls: list[str] = []
        self.prepared_requests: list[EffectDispatchRequest] = []
        self.applied_prepared: list[PreparedEffect] = []
        self.reconciled_claims: list[EffectClaim] = []
        self.aborted_prepared: list[PreparedEffect] = []
        self.authorized_prepared: list[PreparedEffect] = []

    async def prepare(self, request: EffectDispatchRequest) -> PreparedEffect:
        self.calls.append("prepare")
        self.prepared_requests.append(request)
        if self._on_prepare is not None:
            return await self._on_prepare(request)
        if self._prepare_result is not None:
            return self._prepare_result
        return PreparedEffect(
            request=request,
            prepared_ref=(
                f"prepared://effects/{request.stage_id}/{request.idempotency_key}"
            ),
        )

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        self.calls.append("apply")
        self.applied_prepared.append(prepared)
        if self._on_apply is not None:
            return await self._on_apply(prepared)
        return self._apply_result

    async def authorize(self, prepared: PreparedEffect) -> EffectExecutionAuthorization:
        self.calls.append("authorize")
        self.authorized_prepared.append(prepared)
        if self._on_authorize is not None:
            return await self._on_authorize(prepared)
        return EffectExecutionAuthorization(allowed=True, safe_code="authorized")

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        self.calls.append("reconcile")
        self.reconciled_claims.append(claim)
        if self._on_reconcile is not None:
            return await self._on_reconcile(claim)
        return self._reconcile_result

    async def abort(self, prepared: PreparedEffect) -> None:
        self.calls.append("abort")
        self.aborted_prepared.append(prepared)


def utc_now() -> str:
    """Return an injected-test-friendly default timestamp for executor metadata."""

    return datetime.now(UTC).isoformat()


__all__ = [
    "EffectExecutionScope",
    "EffectExecutionAuthorization",
    "EffectPreparedRequest",
    "EffectExecutor",
    "EffectExecutorCapabilities",
    "PreparedEffect",
    "RecordingEffectExecutor",
    "utc_now",
]
