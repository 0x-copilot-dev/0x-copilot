"""Structural ports for the pure effect-staging domain.

No implementation lives in this package.  In particular, the outbox port can store a
body-free command but deliberately has no claim, dispatch, or executor method.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.effects.contracts import EffectCommitCommand, EffectStageScope
from agent_runtime.execution.contracts import RuntimeContract


class StructuralEvent(RuntimeContract):
    """Transport-neutral event envelope the fold consumes.

    ``event_type`` remains a string on purpose.  The A1 ledger enum/payload mirrors are
    extended only by the integration phase, so this foundation never creates a shadow
    event enum.
    """

    run_id: str
    ledger_id: str
    sequence_no: int = Field(ge=1)
    event_type: str
    payload: dict[str, object]
    created_at: str


@runtime_checkable
class EffectStageLedgerPort(Protocol):
    """Durably list/append structural stage events without importing transport code."""

    async def list_stage_events(
        self,
        *,
        scope: EffectStageScope,
        stage_id: str,
    ) -> Sequence[StructuralEvent]:
        """Return the stage history in stable append order."""

    async def append_stage_event(
        self,
        *,
        scope: EffectStageScope,
        event_type: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_fingerprint: str,
    ) -> StructuralEvent:
        """Append or replay one semantic mutation.

        Concrete adapters later bind ``(scope, idempotency_key)`` to
        ``request_fingerprint``.  Matching retries return the original event; a changed
        fingerprint raises the domain's idempotency conflict.  This foundation makes no
        claim about cross-store atomicity.
        """


@runtime_checkable
class EffectCommitOutboxPort(Protocol):
    """Store an approved command only; it exposes no effectful operation."""

    async def enqueue_after_decision(self, command: EffectCommitCommand) -> None:
        """Persist a body-free command after an approval event."""


@runtime_checkable
class EffectClockPort(Protocol):
    """Provide an ISO timestamp; injecting it keeps folds and tests deterministic."""

    def now(self) -> str:
        """Return one safe timestamp string."""


@runtime_checkable
class EffectStageIdGeneratorPort(Protocol):
    """Allocate a valid ``stg_…`` identifier without accepting a model-supplied id."""

    def new_stage_id(self) -> str:
        """Return a fresh, validated stage identifier."""


__all__ = [
    "EffectClockPort",
    "EffectCommitOutboxPort",
    "EffectStageIdGeneratorPort",
    "EffectStageLedgerPort",
    "StructuralEvent",
]
