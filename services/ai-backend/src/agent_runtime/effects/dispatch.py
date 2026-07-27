"""The one claim-before-effect dispatcher shared by approved effect families.

Historical stage formats may differ in how they prove approval, but they must
all converge here before a transport executor can run.  This coordinator owns
the non-negotiable order: prepare, durable claim, cancellation/reauthorization,
then exactly one apply.  Callers supply only server-derived dispatch facts and
an executor selected by worker composition.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.effects.claims import EffectClaim, EffectClaimState, EffectClaimStore
from agent_runtime.effects.contracts import EffectDispatchRequest
from agent_runtime.effects.executor import (
    EffectExecutionAuthorization,
    EffectExecutionScope,
    EffectExecutor,
    PreparedEffect,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.entities import EffectExecutionResult
from agent_runtime.surfaces_v2.ledger_models import EffectOutcome


class EffectDispatchStatus(StrEnum):
    """Outcome of one shared dispatch attempt."""

    APPLIED = "applied"
    REPLAYED = "replayed"
    REFUSED = "refused"
    PRECONDITION_DRIFT = "precondition_drift"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"
    CLAIMED = "claimed"


class EffectDispatchResult(RuntimeContract):
    """Safe result of the canonical claim/authorize/apply protocol."""

    status: EffectDispatchStatus
    stage_id: str
    revision: int = Field(ge=1)
    safe_code: str
    claim: EffectClaim | None = None
    outcome: EffectOutcome | None = None


@runtime_checkable
class EffectDispatchObserver(Protocol):
    """Allows a caller to durably publish a claim before apply begins."""

    async def claimed(self, *, scope: EffectExecutionScope, claim: EffectClaim) -> None:
        """Publish the durable claim; exceptions must prevent apply."""


class _NoopObserver:
    async def claimed(self, *, scope: EffectExecutionScope, claim: EffectClaim) -> None:
        del scope, claim


CancellationCheck = Callable[
    [EffectExecutionScope, EffectDispatchRequest], Awaitable[bool]
]


class EffectDispatchCoordinator:
    """Run the only executor-facing claim-before-effect protocol."""

    def __init__(self, *, claims: EffectClaimStore) -> None:
        self._claims = claims

    async def dispatch(
        self,
        *,
        scope: EffectExecutionScope,
        request: EffectDispatchRequest,
        executor: EffectExecutor,
        expected_precondition_digest: str | None = None,
        cancellation: CancellationCheck | None = None,
        observer: EffectDispatchObserver | None = None,
    ) -> EffectDispatchResult:
        """Prepare, claim, reauthorize, and apply once for an approved request."""

        if executor.kind is not request.executor:
            return _refused(request, "executor_mismatch")
        existing = await self._claims.get(
            org_id=scope.org_id,
            executor=request.executor,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            if not _claim_matches(existing, scope=scope, request=request):
                return _refused(request, "effect_claim_conflict")
            return _existing(existing)

        prepared = await self._prepare(executor=executor, request=request)
        if prepared is None:
            return _refused(request, "prepare_failed")
        if prepared.request != request:
            await _abort_safely(executor, prepared)
            return _refused(request, "prepare_request_mismatch")
        if (
            expected_precondition_digest is not None
            and prepared.observed_precondition_digest != expected_precondition_digest
        ):
            await _abort_safely(executor, prepared)
            return EffectDispatchResult(
                status=EffectDispatchStatus.PRECONDITION_DRIFT,
                stage_id=request.stage_id,
                revision=request.revision,
                safe_code="precondition_drift",
                outcome=EffectOutcome.PRECONDITION_DRIFT,
            )

        proposed = _claim_from(scope=scope, request=request, prepared=prepared)
        acquisition = await self._claims.claim(claim=proposed)
        if not acquisition.created:
            if acquisition.claim.prepared_ref != prepared.prepared_ref:
                await _abort_safely(executor, prepared)
            if not _claim_matches(acquisition.claim, scope=scope, request=request):
                return _refused(request, "effect_claim_conflict")
            return _existing(acquisition.claim)

        claim = acquisition.claim
        await (observer or _NoopObserver()).claimed(scope=scope, claim=claim)

        if cancellation is not None and await cancellation(scope, request):
            await _abort_safely(executor, prepared)
            stored = await self._claims.update(
                claim=_completed(
                    claim,
                    EffectExecutionResult(
                        outcome=EffectOutcome.CANCELLED,
                        retryable=False,
                        safe_message="The effect was cancelled before it was applied.",
                    ),
                    state=EffectClaimState.CANCELLED,
                )
            )
            return _from_claim(stored, status=EffectDispatchStatus.CANCELLED)

        authorization = await self._authorize(executor=executor, prepared=prepared)
        if not authorization.allowed:
            await _abort_safely(executor, prepared)
            stored = await self._claims.update(
                claim=_completed(
                    claim,
                    EffectExecutionResult(
                        outcome=EffectOutcome.FAILED,
                        retryable=False,
                        safe_message=(
                            "Authorization is no longer available; no external "
                            "change was made."
                        ),
                    ),
                    state=EffectClaimState.COMPLETED,
                )
            )
            return EffectDispatchResult(
                status=EffectDispatchStatus.REFUSED,
                stage_id=request.stage_id,
                revision=request.revision,
                safe_code=authorization.safe_code,
                claim=stored,
                outcome=stored.outcome,
            )

        try:
            result = await executor.apply(prepared)
        except (asyncio.TimeoutError, TimeoutError):
            return await self._indeterminate(claim=claim, request=request)
        except Exception:  # noqa: BLE001 - a claim means a send may have escaped.
            return await self._indeterminate(claim=claim, request=request)

        if result.outcome is EffectOutcome.INDETERMINATE:
            return await self._indeterminate(
                claim=claim, request=request, result=result
            )
        stored = await self._claims.update(
            claim=_completed(claim, result, state=EffectClaimState.COMPLETED)
        )
        return _from_claim(stored, status=EffectDispatchStatus.APPLIED)

    async def _indeterminate(
        self,
        *,
        claim: EffectClaim,
        request: EffectDispatchRequest,
        result: EffectExecutionResult | None = None,
    ) -> EffectDispatchResult:
        stored = await self._claims.update(
            claim=_completed(
                claim,
                result
                or EffectExecutionResult(
                    outcome=EffectOutcome.INDETERMINATE,
                    retryable=False,
                    safe_message="The effect outcome could not be confirmed.",
                ),
                state=EffectClaimState.INDETERMINATE,
            )
        )
        return _from_claim(stored, status=EffectDispatchStatus.INDETERMINATE)

    @staticmethod
    async def _prepare(
        *, executor: EffectExecutor, request: EffectDispatchRequest
    ) -> PreparedEffect | None:
        if not executor.capabilities.supports_prepare:
            return PreparedEffect(request=request)
        try:
            return await executor.prepare(request)
        except Exception:  # noqa: BLE001 - no claim means no external effect.
            return None

    @staticmethod
    async def _authorize(
        *, executor: EffectExecutor, prepared: PreparedEffect
    ) -> EffectExecutionAuthorization:
        try:
            decision = await executor.authorize(prepared)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - authorization failures deny by default.
            return EffectExecutionAuthorization(
                allowed=False, safe_code="authorization_unavailable"
            )
        if not isinstance(decision, EffectExecutionAuthorization):
            return EffectExecutionAuthorization(
                allowed=False, safe_code="authorization_invalid"
            )
        return decision


def _claim_from(
    *,
    scope: EffectExecutionScope,
    request: EffectDispatchRequest,
    prepared: PreparedEffect,
) -> EffectClaim:
    return EffectClaim(
        org_id=scope.org_id,
        run_id=scope.run_id,
        stage_id=request.stage_id,
        revision=request.revision,
        idempotency_key=request.idempotency_key,
        executor=request.executor,
        proposal_digest=request.proposal_digest,
        target_digest=request.target_digest,
        prepared_ref=prepared.prepared_ref,
        target_ref=request.target_ref,
        proposal_ref=request.proposal_ref,
        proposal_content_ref=request.proposal_content_ref,
        actor=request.actor,
        decision_ledger_id=request.decision_ledger_id,
        row_keys=request.row_keys,
    )


def _claim_matches(
    claim: EffectClaim,
    *,
    scope: EffectExecutionScope,
    request: EffectDispatchRequest,
) -> bool:
    return (
        claim.org_id == scope.org_id
        and claim.run_id == scope.run_id
        and claim.stage_id == request.stage_id
        and claim.revision == request.revision
        and claim.idempotency_key == request.idempotency_key
        and claim.executor is request.executor
        and claim.proposal_digest == request.proposal_digest
        and claim.target_digest == request.target_digest
        and claim.target_ref == request.target_ref
        and claim.proposal_ref == request.proposal_ref
        and claim.proposal_content_ref == request.proposal_content_ref
        and claim.actor is request.actor
        and claim.decision_ledger_id == request.decision_ledger_id
        and claim.row_keys == request.row_keys
    )


def _existing(claim: EffectClaim) -> EffectDispatchResult:
    if claim.state is EffectClaimState.CLAIMED:
        return _from_claim(claim, status=EffectDispatchStatus.CLAIMED)
    return _from_claim(
        claim,
        status=(
            EffectDispatchStatus.INDETERMINATE
            if claim.state is EffectClaimState.INDETERMINATE
            else EffectDispatchStatus.REPLAYED
        ),
    )


def _refused(request: EffectDispatchRequest, code: str) -> EffectDispatchResult:
    return EffectDispatchResult(
        status=EffectDispatchStatus.REFUSED,
        stage_id=request.stage_id,
        revision=request.revision,
        safe_code=code,
    )


def _from_claim(
    claim: EffectClaim, *, status: EffectDispatchStatus
) -> EffectDispatchResult:
    return EffectDispatchResult(
        status=status,
        stage_id=claim.stage_id,
        revision=claim.revision,
        safe_code=(
            claim.outcome.value if claim.outcome is not None else claim.state.value
        ),
        claim=claim,
        outcome=claim.outcome,
    )


def _completed(
    claim: EffectClaim,
    result: EffectExecutionResult,
    *,
    state: EffectClaimState,
) -> EffectClaim:
    return EffectClaim.model_validate(
        {
            **claim.model_dump(mode="json"),
            "state": state.value,
            "outcome": result.outcome.value,
            "receipt_ref": result.receipt_ref,
            "result_digest": result.result_digest,
            "safe_message": _safe_message(result.safe_message),
            "row_results": (
                [item.model_dump(mode="json") for item in result.row_results]
                if result.row_results is not None
                else None
            ),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )


async def _abort_safely(executor: EffectExecutor, prepared: PreparedEffect) -> None:
    try:
        await executor.abort(prepared)
    except Exception:  # noqa: BLE001 - abort is a best-effort reservation release.
        return


def _safe_message(value: str | None) -> str | None:
    if (
        isinstance(value, str)
        and value.strip()
        and "\n" not in value
        and "\r" not in value
        and len(value) <= 512
    ):
        return value
    return None


__all__ = (
    "CancellationCheck",
    "EffectDispatchCoordinator",
    "EffectDispatchObserver",
    "EffectDispatchResult",
    "EffectDispatchStatus",
)
