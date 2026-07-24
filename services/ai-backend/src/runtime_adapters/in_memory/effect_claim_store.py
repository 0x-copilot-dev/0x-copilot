"""Atomic in-memory implementation of the A5 effect-claim port."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimAcquisition,
    EffectClaimConflict,
    EffectClaimNotFound,
    EffectClaimState,
    validate_claim_transition,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


class InMemoryEffectClaimStore:
    """One-process parity adapter with atomic idempotency acquisition."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_key: dict[tuple[str, EffectExecutorKind, str], EffectClaim] = {}
        self._by_claim_id: dict[tuple[str, str], EffectClaim] = {}

    async def claim(self, *, claim: EffectClaim) -> EffectClaimAcquisition:
        key = (claim.org_id, claim.executor, claim.idempotency_key)
        with self._lock:
            existing = self._by_key.get(key)
            if existing is None:
                self._by_key[key] = claim
                self._by_claim_id[(claim.org_id, claim.claim_id)] = claim
                return EffectClaimAcquisition(created=True, claim=claim)
            if not existing.same_request_as(claim):
                raise EffectClaimConflict()
            return EffectClaimAcquisition(created=False, claim=existing)

    async def get(
        self,
        *,
        org_id: str,
        executor: EffectExecutorKind,
        idempotency_key: str,
    ) -> EffectClaim | None:
        with self._lock:
            return self._by_key.get((org_id, executor, idempotency_key))

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        with self._lock:
            return self._by_claim_id.get((org_id, claim_id))

    async def update(self, *, claim: EffectClaim) -> EffectClaim:
        key = (claim.org_id, claim.executor, claim.idempotency_key)
        with self._lock:
            previous = self._by_key.get(key)
            if previous is None or previous.claim_id != claim.claim_id:
                raise EffectClaimNotFound()
            validate_claim_transition(previous=previous, replacement=claim)
            self._by_key[key] = claim
            self._by_claim_id[(claim.org_id, claim.claim_id)] = claim
            return claim

    async def list_incomplete(
        self, *, org_id: str | None = None, limit: int = 100
    ) -> Sequence[EffectClaim]:
        if limit < 1:
            return ()
        unresolved = {EffectClaimState.CLAIMED, EffectClaimState.INDETERMINATE}
        with self._lock:
            rows = [
                claim
                for claim in self._by_key.values()
                if claim.state in unresolved
                and (org_id is None or claim.org_id == org_id)
            ]
        return tuple(
            sorted(rows, key=lambda claim: (claim.created_at, claim.claim_id))[:limit]
        )


__all__ = ["InMemoryEffectClaimStore"]
