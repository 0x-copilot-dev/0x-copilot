"""Safe recovery scheduling for uncertain effect claims.

Recovery is intentionally split from execution.  The scheduler observes durable
claims only after a minimum quiet period and emits a body-free reconcile command;
the coordinator's reconciliation path can ask an executor about a prior provider
idempotency token, but it cannot invoke ``apply`` again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from agent_runtime.effects.claims import EffectClaim, EffectClaimStore
from agent_runtime.effects.coordinator import EffectReconcileCommand


@runtime_checkable
class EffectReconcileQueuePort(Protocol):
    """Queue only recovery work; it has no execute/apply method."""

    async def enqueue_reconcile(self, command: EffectReconcileCommand) -> None:
        """Persist one reconciliation task for a previously claimed attempt."""


@dataclass(frozen=True)
class EffectReconciliationScheduler:
    """Queue stale incomplete claims without ever re-sending their mutation."""

    claims: EffectClaimStore
    queue: EffectReconcileQueuePort
    minimum_age: timedelta = timedelta(minutes=2)

    async def schedule_incomplete(
        self,
        *,
        now: datetime | None = None,
        org_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EffectReconcileCommand, ...]:
        """Enqueue only claims old enough to be safely considered abandoned.

        A currently-running worker may have claimed an attempt just before this
        sweep.  Deferring claims newer than ``minimum_age`` prevents a recovery
        loop from racing the primary execution path.  Idempotency belongs to the
        runtime queue adapter; duplicate recovery commands remain harmless because
        :meth:`EffectCoordinator.reconcile` never invokes ``apply``.
        """

        if limit < 1:
            return ()
        reference_now = _as_utc(now or datetime.now(UTC))
        cutoff = reference_now - self.minimum_age
        candidates = await self.claims.list_incomplete(org_id=org_id, limit=limit)
        commands = tuple(
            EffectReconcileCommand(org_id=claim.org_id, claim_id=claim.claim_id)
            for claim in candidates
            if _updated_at(claim) <= cutoff
        )
        for command in commands:
            await self.queue.enqueue_reconcile(command)
        return commands


def stale_claims(
    *,
    claims: Sequence[EffectClaim],
    now: datetime,
    minimum_age: timedelta,
) -> tuple[EffectClaim, ...]:
    """Pure stale-claim selection used by scheduler tests and future job adapters."""

    cutoff = _as_utc(now) - minimum_age
    return tuple(claim for claim in claims if _updated_at(claim) <= cutoff)


def _updated_at(claim: EffectClaim) -> datetime:
    try:
        return _as_utc(datetime.fromisoformat(claim.updated_at))
    except ValueError:
        # A malformed persisted timestamp is never eligible for automated recovery.
        return datetime.max.replace(tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = [
    "EffectReconcileQueuePort",
    "EffectReconciliationScheduler",
    "stale_claims",
]
