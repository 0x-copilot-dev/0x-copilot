"""The fail-closed, claim-before-effect commit coordinator.

This is the only domain path from a folded ``APPROVED`` effect stage to an
executor's ``apply`` method.  It intentionally owns no transport or mutable
proposal bytes: trusted adapters resolve run scope and stream immutable refs,
while closed executor factories provide the one transport implementation for a
declared executor kind.

The ordering in :meth:`EffectCoordinator.handle` is a safety invariant:

``fold -> revalidate -> prepare -> durable claim -> apply -> durable result``.

Any redelivery which finds a claim exits without calling ``apply``.  A timeout
or process error after a claim is honest ``indeterminate`` state; it is never a
reason to replay a possibly-sent mutation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
from typing import Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimState,
    EffectClaimStore,
)
from agent_runtime.effects.contracts import (
    EffectCommitCommand,
    EffectStageScope,
    EffectStageState,
    EffectStageStatus,
)
from agent_runtime.effects.executor import (
    EffectExecutionAuthorization,
    EffectExecutionScope,
    EffectExecutor,
    PreparedEffect,
)
from agent_runtime.effects.executor_registry import EffectExecutorRegistry
from agent_runtime.effects.fold import EffectStageFold
from agent_runtime.effects.ports import EffectStageLedgerPort
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.entities import (
    EffectExecutionRequest,
    EffectExecutionResult,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectOutcome,
    LedgerEventType,
)

_EVENT_CLAIMED = LedgerEventType.EFFECT_CLAIMED.value
_EVENT_APPLIED = LedgerEventType.EFFECT_APPLIED.value
_EVENT_INDETERMINATE = LedgerEventType.EFFECT_INDETERMINATE.value
_EVENT_RECONCILED = LedgerEventType.EFFECT_RECONCILED.value
_PAYLOAD_VERSION = 1
_PUBLIC_UNKNOWN_OUTCOME = "The effect outcome could not be confirmed."
_PUBLIC_PRECONDITION_DRIFT = "The target changed before the effect was applied."
_PUBLIC_CANCELLED = "The effect was cancelled before it was applied."
_PUBLIC_AUTHORIZATION_REVOKED = (
    "Authorization is no longer available; no external change was made."
)


class EffectCoordinatorStatus(StrEnum):
    """Public-safe disposition of one command delivery."""

    APPLIED = "applied"
    REPLAYED = "replayed"
    REFUSED = "refused"
    PRECONDITION_DRIFT = "precondition_drift"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"
    CLAIMED = "claimed"


class EffectCoordinatorResult(RuntimeContract):
    """Body-free result of handle/reconcile; safe to log and expose internally."""

    status: EffectCoordinatorStatus
    stage_id: str
    revision: int = Field(ge=1)
    claim_id: str | None = None
    outcome: EffectOutcome | None = None
    safe_code: str


class EffectReconcileCommand(RuntimeContract):
    """A durable recovery task names a tenant-scoped claim, never a new effect."""

    org_id: str = Field(min_length=1, max_length=255)
    claim_id: str = Field(min_length=1, max_length=255)


@runtime_checkable
class EffectExecutionScopeResolver(Protocol):
    """Resolve verified runtime identity for a worker command."""

    async def resolve(self, *, run_id: str) -> EffectExecutionScope | None:
        """Return ``None`` for a deleted, missing, or unauthorised run."""


@runtime_checkable
class EffectImmutableReferenceResolver(Protocol):
    """Stream only server-authorised immutable bytes for digest revalidation."""

    def open(
        self,
        *,
        scope: EffectExecutionScope,
        reference: str,
    ) -> AsyncIterator[bytes]:
        """Yield the exact bytes named by one server-held opaque reference."""


@runtime_checkable
class EffectCancellationPort(Protocol):
    """Read a trusted cancellation bit immediately before ``apply``."""

    async def is_cancelled(
        self,
        *,
        scope: EffectExecutionScope,
        command: EffectCommitCommand,
    ) -> bool:
        """Return whether this queued execution was cancelled before dispatch."""


@runtime_checkable
class EffectAuditPort(Protocol):
    """Append safe facts; implementations must never accept proposal bodies."""

    async def record(
        self,
        *,
        action: str,
        facts: Mapping[str, str | int | None],
    ) -> None:
        """Best-effort audit sink supplied by worker composition."""


class _NoopAudit:
    async def record(
        self,
        *,
        action: str,
        facts: Mapping[str, str | int | None],
    ) -> None:
        del action, facts


class EffectResultRecorder:
    """Restricted canonical producer for effect claim/result ledger facts.

    Only :mod:`agent_runtime.effects.coordinator` constructs this helper.  Worker
    handlers call the coordinator, never append ``effect.applied`` directly.
    Deterministic phase keys make retry/recovery event publication idempotent
    after a durable claim/result has already been persisted.
    """

    def __init__(self, *, ledger: EffectStageLedgerPort) -> None:
        self._ledger = ledger

    async def record_claimed(
        self, *, scope: EffectStageScope, claim: EffectClaim
    ) -> None:
        await self._append(
            scope=scope,
            phase="claimed",
            claim=claim,
            event_type=_EVENT_CLAIMED,
            payload={
                "v": _PAYLOAD_VERSION,
                "stage_id": claim.stage_id,
                "revision": claim.revision,
                "claim_id": claim.claim_id,
                "executor": claim.executor.value,
                "attempt": claim.attempt,
            },
        )

    async def record_completion(
        self,
        *,
        scope: EffectStageScope,
        claim: EffectClaim,
    ) -> None:
        """Publish the canonical terminal fact after a durable claim transition."""

        if claim.state is EffectClaimState.INDETERMINATE:
            await self._append(
                scope=scope,
                phase="indeterminate",
                claim=claim,
                event_type=_EVENT_INDETERMINATE,
                payload={
                    "v": _PAYLOAD_VERSION,
                    "stage_id": claim.stage_id,
                    "revision": claim.revision,
                    "claim_id": claim.claim_id,
                    "reason": _safe_message(
                        claim.safe_message, _PUBLIC_UNKNOWN_OUTCOME
                    ),
                },
            )
            return
        if claim.outcome is None:
            return
        await self._append(
            scope=scope,
            phase="applied",
            claim=claim,
            event_type=_EVENT_APPLIED,
            payload=_applied_payload(claim),
        )

    async def record_preclaim_drift(
        self,
        *,
        scope: EffectStageScope,
        command: EffectCommitCommand,
    ) -> None:
        """Record a prepare-time drift outcome without creating a claim."""

        await self._ledger.append_stage_event(
            scope=scope,
            event_type=_EVENT_APPLIED,
            payload={
                "v": _PAYLOAD_VERSION,
                "stage_id": command.stage_id,
                "revision": command.revision,
                "outcome": EffectOutcome.PRECONDITION_DRIFT.value,
            },
            idempotency_key=_phase_key(command.idempotency_key, "preclaim-drift"),
            request_fingerprint=_fingerprint(
                "preclaim-drift",
                {
                    "stage_id": command.stage_id,
                    "revision": command.revision,
                    "proposal_digest": command.proposal_digest,
                    "target_digest": command.target_digest,
                },
            ),
        )

    async def record_reconciled(
        self,
        *,
        scope: EffectStageScope,
        claim: EffectClaim,
    ) -> None:
        if claim.outcome is None:
            return
        await self._append(
            scope=scope,
            phase="reconciled",
            claim=claim,
            event_type=_EVENT_RECONCILED,
            payload={
                "v": _PAYLOAD_VERSION,
                "stage_id": claim.stage_id,
                "revision": claim.revision,
                "claim_id": claim.claim_id,
                "outcome": claim.outcome.value,
                **({"receipt_ref": claim.receipt_ref} if claim.receipt_ref else {}),
            },
        )

    async def _append(
        self,
        *,
        scope: EffectStageScope,
        phase: str,
        claim: EffectClaim,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        await self._ledger.append_stage_event(
            scope=scope,
            event_type=event_type,
            payload=payload,
            idempotency_key=_phase_key(claim.claim_id, phase),
            request_fingerprint=_fingerprint(phase, dict(payload)),
        )


class EffectCoordinator:
    """Run the only claim-before-effect protocol for approved stage commands."""

    def __init__(
        self,
        *,
        ledger: EffectStageLedgerPort,
        claims: EffectClaimStore,
        scopes: EffectExecutionScopeResolver,
        references: EffectImmutableReferenceResolver,
        executors: EffectExecutorRegistry,
        cancellation: EffectCancellationPort | None = None,
        audit: EffectAuditPort | None = None,
    ) -> None:
        self._ledger = ledger
        self._claims = claims
        self._scopes = scopes
        self._references = references
        self._executors = executors
        self._cancellation = cancellation
        self._audit = audit or _NoopAudit()
        self._recorder = EffectResultRecorder(ledger=ledger)

    async def handle(self, command: EffectCommitCommand) -> EffectCoordinatorResult:
        """Validate, prepare, claim, apply and complete exactly one approved effect."""

        execution_scope = await self._scopes.resolve(run_id=command.run_id)
        if execution_scope is None or execution_scope.run_id != command.run_id:
            return await self._refuse(command=command, code="run_unavailable")
        stage_scope = EffectStageScope(
            run_id=execution_scope.run_id,
            owner_ref=execution_scope.owner_ref,
        )
        state = await self._load_authorised_stage(scope=stage_scope, command=command)
        if state is None:
            return await self._refuse(command=command, code="approval_mismatch")
        if not await self._references_match(scope=execution_scope, state=state):
            return await self._refuse(command=command, code="immutable_ref_mismatch")

        request = _execution_request(state=state, command=command)
        existing = await self._claims.get(
            org_id=execution_scope.org_id,
            executor=state.executor,
            idempotency_key=command.idempotency_key,
        )
        if existing is not None:
            candidate = _claim_from(
                scope=execution_scope,
                state=state,
                command=command,
                prepared=PreparedEffect(
                    request=request,
                    prepared_ref=existing.prepared_ref,
                ),
            )
            if not existing.same_request_as(candidate):
                return await self._refuse(
                    command=command,
                    code="effect_claim_conflict",
                )
            return await self._existing_claim_result(
                scope=stage_scope,
                claim=existing,
            )
        executor = self._executors.resolve(kind=state.executor, scope=execution_scope)
        prepared = await self._prepare(executor=executor, request=request)
        if prepared is None:
            return await self._refuse(command=command, code="prepare_failed")
        if prepared.request != request:
            await _abort_safely(executor, prepared)
            return await self._refuse(command=command, code="prepare_request_mismatch")
        if _precondition_drift(state=state, prepared=prepared):
            await _abort_safely(executor, prepared)
            await self._recorder.record_preclaim_drift(
                scope=stage_scope, command=command
            )
            await self._audit.record(
                action="effect.commit.precondition_drift",
                facts=_audit_facts(command=command, state=state, claim_id=None),
            )
            return EffectCoordinatorResult(
                status=EffectCoordinatorStatus.PRECONDITION_DRIFT,
                stage_id=command.stage_id,
                revision=command.revision,
                outcome=EffectOutcome.PRECONDITION_DRIFT,
                safe_code="precondition_drift",
            )

        proposed_claim = _claim_from(
            scope=execution_scope,
            state=state,
            command=command,
            prepared=prepared,
        )
        acquisition = await self._claims.claim(claim=proposed_claim)
        if not acquisition.created:
            if acquisition.claim.prepared_ref != prepared.prepared_ref:
                await _abort_safely(executor, prepared)
            return await self._existing_claim_result(
                scope=stage_scope, claim=acquisition.claim
            )
        claim = acquisition.claim
        # A failed ledger append leaves a durable claim but never reaches apply.
        await self._recorder.record_claimed(scope=stage_scope, claim=claim)
        await self._audit.record(
            action="effect.commit.claimed",
            facts=_audit_facts(command=command, state=state, claim_id=claim.claim_id),
        )

        if self._cancellation is not None and await self._cancellation.is_cancelled(
            scope=execution_scope, command=command
        ):
            await _abort_safely(executor, prepared)
            cancelled = _completed_claim(
                claim=claim,
                result=EffectExecutionResult(
                    outcome=EffectOutcome.CANCELLED,
                    retryable=False,
                    safe_message=_PUBLIC_CANCELLED,
                ),
                state=EffectClaimState.CANCELLED,
            )
            stored = await self._claims.update(claim=cancelled)
            await self._recorder.record_completion(scope=stage_scope, claim=stored)
            return _result_from_claim(stored, status=EffectCoordinatorStatus.CANCELLED)

        authorization = await self._authorize(executor=executor, prepared=prepared)
        if not authorization.allowed:
            await _abort_safely(executor, prepared)
            denied = _completed_claim(
                claim=claim,
                result=EffectExecutionResult(
                    outcome=EffectOutcome.FAILED,
                    retryable=False,
                    safe_message=_PUBLIC_AUTHORIZATION_REVOKED,
                ),
                state=EffectClaimState.COMPLETED,
            )
            stored = await self._claims.update(claim=denied)
            await self._recorder.record_completion(scope=stage_scope, claim=stored)
            await self._audit.record(
                action="effect.commit.authorization_denied",
                facts={
                    **_audit_facts(
                        command=command, state=state, claim_id=stored.claim_id
                    ),
                    "authorization_code": authorization.safe_code,
                },
            )
            return EffectCoordinatorResult(
                status=EffectCoordinatorStatus.REFUSED,
                stage_id=command.stage_id,
                revision=command.revision,
                claim_id=stored.claim_id,
                outcome=EffectOutcome.FAILED,
                safe_code=authorization.safe_code,
            )

        try:
            result = await executor.apply(prepared)
        except (asyncio.TimeoutError, TimeoutError):
            return await self._mark_indeterminate(
                scope=stage_scope,
                claim=claim,
                state=state,
                command=command,
                safe_message=_PUBLIC_UNKNOWN_OUTCOME,
            )
        except Exception:  # noqa: BLE001 - after a durable claim, fail honestly.
            return await self._mark_indeterminate(
                scope=stage_scope,
                claim=claim,
                state=state,
                command=command,
                safe_message=_PUBLIC_UNKNOWN_OUTCOME,
            )

        if result.outcome is EffectOutcome.INDETERMINATE:
            return await self._mark_indeterminate(
                scope=stage_scope,
                claim=claim,
                state=state,
                command=command,
                safe_message=_safe_message(
                    result.safe_message, _PUBLIC_UNKNOWN_OUTCOME
                ),
                result=result,
            )
        completed = _completed_claim(
            claim=claim,
            result=result,
            state=EffectClaimState.COMPLETED,
        )
        stored = await self._claims.update(claim=completed)
        await self._recorder.record_completion(scope=stage_scope, claim=stored)
        await self._audit.record(
            action="effect.commit.completed",
            facts=_audit_facts(command=command, state=state, claim_id=stored.claim_id),
        )
        return _result_from_claim(stored, status=EffectCoordinatorStatus.APPLIED)

    async def reconcile(
        self, command: EffectReconcileCommand
    ) -> EffectCoordinatorResult:
        """Resolve one prior claim without ever calling ``apply`` again."""

        claim = await self._claims.get_by_claim_id(
            org_id=command.org_id, claim_id=command.claim_id
        )
        if claim is None:
            return EffectCoordinatorResult(
                status=EffectCoordinatorStatus.REFUSED,
                stage_id="unknown",
                revision=1,
                safe_code="claim_not_found",
            )
        scope = await self._scopes.resolve(run_id=claim.run_id)
        if scope is None or scope.org_id != claim.org_id:
            return _result_from_claim(claim, status=EffectCoordinatorStatus.REFUSED)
        stage_scope = EffectStageScope(run_id=claim.run_id, owner_ref=scope.owner_ref)
        if claim.state in {EffectClaimState.COMPLETED, EffectClaimState.CANCELLED}:
            await self._recorder.record_completion(scope=stage_scope, claim=claim)
            return _result_from_claim(claim, status=EffectCoordinatorStatus.REPLAYED)

        executor = self._executors.resolve(kind=claim.executor, scope=scope)
        if not executor.capabilities.supports_reconcile:
            return await self._mark_reconcile_indeterminate(
                scope=stage_scope, claim=claim
            )
        try:
            result = await executor.reconcile(claim)
        except Exception:  # noqa: BLE001 - reconciliation must never replay apply.
            return await self._mark_reconcile_indeterminate(
                scope=stage_scope, claim=claim
            )
        if result.outcome is EffectOutcome.INDETERMINATE:
            return await self._mark_reconcile_indeterminate(
                scope=stage_scope,
                claim=claim,
                safe_message=result.safe_message,
            )
        completed = _completed_claim(
            claim=claim,
            result=result,
            state=EffectClaimState.COMPLETED,
        )
        stored = await self._claims.update(claim=completed)
        await self._recorder.record_reconciled(scope=stage_scope, claim=stored)
        await self._recorder.record_completion(scope=stage_scope, claim=stored)
        return _result_from_claim(stored, status=EffectCoordinatorStatus.APPLIED)

    async def _load_authorised_stage(
        self, *, scope: EffectStageScope, command: EffectCommitCommand
    ) -> EffectStageState | None:
        try:
            events = await self._ledger.list_stage_events(
                scope=scope, stage_id=command.stage_id
            )
            state = EffectStageFold.fold(events)
        except Exception:  # noqa: BLE001 - malformed history cannot execute.
            return None
        decision = state.decision
        if (
            state.scope != scope
            or state.status is not EffectStageStatus.APPROVED
            or decision is None
            or decision.decision is not EffectDecisionKind.APPROVE
            or decision.revision != command.revision
            or decision.ledger_id != command.decision_ledger_id
            or decision.proposal_digest != command.proposal_digest
            or decision.target_digest != command.target_digest
            or state.current_revision.revision != command.revision
            or state.current_revision.proposal_digest != command.proposal_digest
            or state.target_digest != command.target_digest
        ):
            return None
        return state

    async def _references_match(
        self, *, scope: EffectExecutionScope, state: EffectStageState
    ) -> bool:
        proposal_content_ref = state.current_revision.proposal_content_ref
        if proposal_content_ref is None:
            # Canonical proposal identity without immutable bytes is replayable
            # history, not executable authority.
            return False
        proposal_digest = await _digest_reference(
            resolver=self._references,
            scope=scope,
            reference=proposal_content_ref,
        )
        target_digest = await _digest_reference(
            resolver=self._references,
            scope=scope,
            reference=state.target.target_ref,
        )
        return (
            proposal_digest == state.current_revision.proposal_digest
            and target_digest == state.target_digest
        )

    @staticmethod
    async def _prepare(
        *, executor: EffectExecutor, request: EffectExecutionRequest
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
        """Contain a live authorization check before the sole ``apply`` call.

        The stage fold proves the historical approval.  This second contract
        lets a transport observe revocation that happened after approval but
        before worker dispatch, without creating another dispatcher path.
        Exceptions deny by default so a broken authority lookup cannot become
        permission to send.
        """

        try:
            decision = await executor.authorize(prepared)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - authorization is fail closed.
            return EffectExecutionAuthorization(
                allowed=False,
                safe_code="authorization_unavailable",
            )
        if not isinstance(decision, EffectExecutionAuthorization):
            return EffectExecutionAuthorization(
                allowed=False,
                safe_code="authorization_invalid",
            )
        return decision

    async def _existing_claim_result(
        self, *, scope: EffectStageScope, claim: EffectClaim
    ) -> EffectCoordinatorResult:
        """Repair idempotent ledger publication but never invoke ``apply``."""

        if claim.state is EffectClaimState.CLAIMED:
            await self._recorder.record_claimed(scope=scope, claim=claim)
            return _result_from_claim(claim, status=EffectCoordinatorStatus.CLAIMED)
        await self._recorder.record_completion(scope=scope, claim=claim)
        status = (
            EffectCoordinatorStatus.INDETERMINATE
            if claim.state is EffectClaimState.INDETERMINATE
            else EffectCoordinatorStatus.REPLAYED
        )
        return _result_from_claim(claim, status=status)

    async def _mark_indeterminate(
        self,
        *,
        scope: EffectStageScope,
        claim: EffectClaim,
        state: EffectStageState,
        command: EffectCommitCommand,
        safe_message: str,
        result: EffectExecutionResult | None = None,
    ) -> EffectCoordinatorResult:
        indeterminate = _indeterminate_claim(
            claim=claim, safe_message=safe_message, result=result
        )
        stored = await self._claims.update(claim=indeterminate)
        await self._recorder.record_completion(scope=scope, claim=stored)
        await self._audit.record(
            action="effect.commit.indeterminate",
            facts=_audit_facts(command=command, state=state, claim_id=stored.claim_id),
        )
        return _result_from_claim(stored, status=EffectCoordinatorStatus.INDETERMINATE)

    async def _mark_reconcile_indeterminate(
        self,
        *,
        scope: EffectStageScope,
        claim: EffectClaim,
        safe_message: str | None = None,
    ) -> EffectCoordinatorResult:
        # Reconciliation is observational.  If the claim is already durable
        # indeterminate and the executor still cannot prove an outcome, there
        # is no legal state transition to persist.  Re-publish the idempotent
        # completion fact and retain the original claim byte-for-byte.
        if claim.state is EffectClaimState.INDETERMINATE:
            await self._recorder.record_completion(scope=scope, claim=claim)
            return _result_from_claim(
                claim, status=EffectCoordinatorStatus.INDETERMINATE
            )
        indeterminate = _indeterminate_claim(
            claim=claim,
            safe_message=_safe_message(safe_message, _PUBLIC_UNKNOWN_OUTCOME),
        )
        stored = await self._claims.update(claim=indeterminate)
        await self._recorder.record_completion(scope=scope, claim=stored)
        return _result_from_claim(stored, status=EffectCoordinatorStatus.INDETERMINATE)

    async def _refuse(
        self, *, command: EffectCommitCommand, code: str
    ) -> EffectCoordinatorResult:
        await self._audit.record(
            action="effect.commit.refused",
            facts={
                "run_id": command.run_id,
                "stage_id": command.stage_id,
                "revision": command.revision,
                "code": code,
            },
        )
        return EffectCoordinatorResult(
            status=EffectCoordinatorStatus.REFUSED,
            stage_id=command.stage_id,
            revision=command.revision,
            safe_code=code,
        )


async def _digest_reference(
    *,
    resolver: EffectImmutableReferenceResolver,
    scope: EffectExecutionScope,
    reference: str,
) -> str | None:
    """Hash an immutable reference without retaining proposal/target bodies."""

    digest = hashlib.sha256()
    try:
        async for chunk in resolver.open(scope=scope, reference=reference):
            if not isinstance(chunk, bytes):
                return None
            digest.update(chunk)
    except Exception:  # noqa: BLE001 - unavailable refs are a safe refusal.
        return None
    return digest.hexdigest()


async def _abort_safely(executor: EffectExecutor, prepared: PreparedEffect) -> None:
    try:
        await executor.abort(prepared)
    except Exception:  # noqa: BLE001 - abort is a best-effort reservation release.
        return


def _execution_request(
    *, state: EffectStageState, command: EffectCommitCommand
) -> EffectExecutionRequest:
    decision = state.decision
    if decision is None:  # defensive; caller passed the approval gate.
        raise ValueError("an approved effect stage requires an approval decision")
    proposal_content_ref = state.current_revision.proposal_content_ref
    if proposal_content_ref is None:
        raise ValueError("an executable effect stage requires proposal content")
    return EffectExecutionRequest(
        stage_id=state.stage_id,
        revision=command.revision,
        idempotency_key=command.idempotency_key,
        target_ref=state.target.target_ref,
        target_digest=state.target_digest,
        proposal_ref=state.current_revision.proposal_ref,
        proposal_content_ref=proposal_content_ref,
        proposal_digest=state.current_revision.proposal_digest,
        actor=decision.actor.actor,
        decision_ledger_id=decision.ledger_id,
    )


def _claim_from(
    *,
    scope: EffectExecutionScope,
    state: EffectStageState,
    command: EffectCommitCommand,
    prepared: PreparedEffect,
) -> EffectClaim:
    decision = state.decision
    if decision is None:  # defensive; approval is checked before this call.
        raise ValueError("an approved effect stage requires an approval decision")
    proposal_content_ref = state.current_revision.proposal_content_ref
    if proposal_content_ref is None:
        raise ValueError("an executable effect stage requires proposal content")
    return EffectClaim(
        org_id=scope.org_id,
        run_id=scope.run_id,
        stage_id=state.stage_id,
        revision=command.revision,
        idempotency_key=command.idempotency_key,
        executor=state.executor,
        proposal_digest=state.current_revision.proposal_digest,
        target_digest=state.target_digest,
        prepared_ref=prepared.prepared_ref,
        target_ref=state.target.target_ref,
        proposal_ref=state.current_revision.proposal_ref,
        proposal_content_ref=proposal_content_ref,
        actor=decision.actor.actor,
        decision_ledger_id=decision.ledger_id,
    )


def _precondition_drift(*, state: EffectStageState, prepared: PreparedEffect) -> bool:
    expected = state.current_revision.precondition_digest
    return expected is not None and prepared.observed_precondition_digest != expected


def _completed_claim(
    *,
    claim: EffectClaim,
    result: EffectExecutionResult,
    state: EffectClaimState,
) -> EffectClaim:
    return EffectClaim.model_validate(
        {
            **claim.model_dump(mode="json"),
            "state": state.value,
            "outcome": result.outcome.value,
            "receipt_ref": result.receipt_ref,
            "result_digest": result.result_digest,
            "safe_message": _safe_message(result.safe_message, None),
            "updated_at": _now(),
        }
    )


def _indeterminate_claim(
    *,
    claim: EffectClaim,
    safe_message: str,
    result: EffectExecutionResult | None = None,
) -> EffectClaim:
    return EffectClaim.model_validate(
        {
            **claim.model_dump(mode="json"),
            "state": EffectClaimState.INDETERMINATE.value,
            "outcome": EffectOutcome.INDETERMINATE.value,
            "receipt_ref": result.receipt_ref if result else None,
            "result_digest": result.result_digest if result else None,
            "safe_message": _safe_message(safe_message, _PUBLIC_UNKNOWN_OUTCOME),
            "updated_at": _now(),
        }
    )


def _result_from_claim(
    claim: EffectClaim, *, status: EffectCoordinatorStatus
) -> EffectCoordinatorResult:
    return EffectCoordinatorResult(
        status=status,
        stage_id=claim.stage_id,
        revision=claim.revision,
        claim_id=claim.claim_id,
        outcome=claim.outcome,
        safe_code=(
            claim.outcome.value if claim.outcome is not None else claim.state.value
        ),
    )


def _applied_payload(claim: EffectClaim) -> dict[str, object]:
    if claim.outcome is None:
        raise ValueError("a canonical effect result requires an outcome")
    payload: dict[str, object] = {
        "v": _PAYLOAD_VERSION,
        "stage_id": claim.stage_id,
        "revision": claim.revision,
        "outcome": claim.outcome.value,
    }
    if claim.receipt_ref is not None:
        payload["receipt_ref"] = claim.receipt_ref
    if claim.result_digest is not None:
        payload["result_digest"] = claim.result_digest
    return payload


def _phase_key(identity: str, phase: str) -> str:
    return f"effect:{phase}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _fingerprint(phase: str, value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        {"phase": phase, "value": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_message(value: str | None, fallback: str | None) -> str | None:
    if (
        isinstance(value, str)
        and value.strip()
        and len(value) <= 512
        and "\n" not in value
        and "\r" not in value
    ):
        return value
    return fallback


def _audit_facts(
    *, command: EffectCommitCommand, state: EffectStageState, claim_id: str | None
) -> dict[str, str | int | None]:
    return {
        "run_id": command.run_id,
        "stage_id": state.stage_id,
        "revision": command.revision,
        "decision_ledger_id": command.decision_ledger_id,
        "executor": state.executor.value,
        "operation_id": state.operation_id,
        "claim_id": claim_id,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "EffectAuditPort",
    "EffectCancellationPort",
    "EffectCoordinator",
    "EffectCoordinatorResult",
    "EffectCoordinatorStatus",
    "EffectExecutionScopeResolver",
    "EffectImmutableReferenceResolver",
    "EffectReconcileCommand",
    "EffectResultRecorder",
]
