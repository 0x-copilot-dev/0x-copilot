"""Thin worker adapter for body-free A5 reconciliation commands."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.coordinator import EffectReconcileCommand
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from runtime_api.schemas import RuntimeEffectReconcileCommand
from runtime_api.schemas import RunRecord
from runtime_worker.handlers.effect_commit import (
    EffectCoordinatorFactory,
    RuntimeRunLookupPort,
    load_verified_runtime_run,
)


@runtime_checkable
class EffectClaimLookupPort(Protocol):
    """Read the durable A5 claim that recovery work must be bound to."""

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        """Return a tenant-scoped effect claim, if it still exists."""


@runtime_checkable
class EffectReconcileAuditPort(Protocol):
    """Append safe reconciliation evidence without carrying effect bodies."""

    async def emit_effect_reconciled(
        self,
        *,
        run: RunRecord,
        claim: EffectClaim,
        status: str,
        safe_code: str,
    ) -> None:
        """Record the final body-free reconciliation disposition."""


class RuntimeEffectReconcileHandler:
    """Revalidate recovery scope before asking A5 to reconcile a prior claim.

    Recovery commands intentionally carry no proposal or target body.  This
    adapter verifies both the durable claim and run before converting to the
    even narrower domain command; the coordinator then remains solely
    responsible for reconciliation and its ledger outcome.
    """

    def __init__(
        self,
        *,
        persistence: RuntimeRunLookupPort,
        claims: EffectClaimLookupPort,
        coordinator_factory: EffectCoordinatorFactory,
        audit_emitter: EffectReconcileAuditPort | None = None,
    ) -> None:
        self._persistence = persistence
        self._claims = claims
        self._coordinator_factory = coordinator_factory
        self._audit_emitter = audit_emitter

    async def handle(self, command: RuntimeEffectReconcileCommand) -> None:
        """Delegate only a claim that is durably bound to this tenant and run."""

        claim = await self._claims.get_by_claim_id(
            org_id=command.org_id,
            claim_id=command.claim_id,
        )
        if claim is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Effect-reconcile claim is unavailable.",
                retryable=False,
            )
        if claim.org_id != command.org_id or claim.run_id != command.run_id:
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Effect-reconcile command scope does not match its claim.",
                retryable=False,
            )

        run = await load_verified_runtime_run(
            persistence=self._persistence,
            org_id=command.org_id,
            run_id=command.run_id,
        )
        pure_command = EffectReconcileCommand(
            org_id=command.org_id,
            claim_id=command.claim_id,
        )
        coordinator = self._coordinator_factory.for_run(run=run)
        result = await coordinator.reconcile(pure_command)
        audit_emitter = self._audit_emitter
        if audit_emitter is not None:
            try:
                await audit_emitter.emit_effect_reconciled(
                    run=run,
                    claim=claim,
                    status=_safe_result_field(result, "status"),
                    safe_code=_safe_result_field(result, "safe_code"),
                )
            except Exception:
                # A chain writer outage must not replay or re-run a safe
                # reconciliation result.  The queue's normal completion path
                # remains authoritative for retry/recovery.
                pass


def _safe_result_field(result: object, name: str) -> str:
    """Extract a bounded enum-like result field without trusting a fake port."""

    value = getattr(result, name, "unknown")
    value = getattr(value, "value", value)
    if not isinstance(value, str) or len(value) > 64:
        return "unknown"
    return value


__all__ = (
    "EffectClaimLookupPort",
    "EffectReconcileAuditPort",
    "RuntimeEffectReconcileHandler",
)
