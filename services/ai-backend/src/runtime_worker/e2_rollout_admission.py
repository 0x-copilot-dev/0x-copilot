"""Worker adapters that enforce E2 admission before any effect dispatches.

The pure E2 controller deliberately cannot see queues or executors.  This
module is its single worker-side adapter: it reconstructs the already-approved
stage from the append-only ledger, maps its closed executor kind to the exact
E2 dependency set, and delegates only after the full set is admitted.

It wraps the existing A5 handler rather than altering the effect engine.  That
keeps E2 a request-path concern and leaves the effect protocol's immutable
revision/claim-before-apply invariants intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.effects.fold import EffectStageFold
from agent_runtime.effects.rollout import effect_execution_capabilities
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    E2RolloutAdmissionDenied,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind, LedgerEventType
from runtime_api.schemas import RunRecord, RuntimeEffectCommitCommand


_EFFECT_STAGE_EVENT_TYPES = frozenset(
    {
        LedgerEventType.EFFECT_STAGED.value,
        LedgerEventType.EFFECT_REVISED.value,
        LedgerEventType.EFFECT_DECISION_RECORDED.value,
        LedgerEventType.EFFECT_CLAIMED.value,
        LedgerEventType.EFFECT_APPLIED.value,
        LedgerEventType.EFFECT_INDETERMINATE.value,
        LedgerEventType.EFFECT_RECONCILED.value,
    }
)


@runtime_checkable
class EffectCommitHandlerPort(Protocol):
    """Narrow downstream handler surface; it owns the actual effect protocol."""

    async def handle(self, command: RuntimeEffectCommitCommand) -> None:
        """Handle one transport command after E2 admission."""


@runtime_checkable
class EffectStageExecutorResolver(Protocol):
    """Read only the closed executor kind from an immutable effect stage."""

    async def resolve_executor(
        self, *, run: RunRecord, stage_id: str
    ) -> EffectExecutorKind:
        """Return the stage's authoritative executor or fail closed."""


@dataclass(frozen=True)
class EventStoreEffectStageExecutorResolver:
    """Fold one stage from the canonical runtime event stream."""

    event_store: EventStorePort

    async def resolve_executor(
        self, *, run: RunRecord, stage_id: str
    ) -> EffectExecutorKind:
        envelopes = await self.event_store.list_events_after(
            org_id=run.org_id,
            run_id=run.run_id,
            after_sequence=0,
        )
        events: list[StructuralEvent] = []
        for envelope in envelopes:
            event_type = envelope.event_type.value
            if (
                event_type not in _EFFECT_STAGE_EVENT_TYPES
                or envelope.payload.get("stage_id") != stage_id
            ):
                continue
            events.append(
                StructuralEvent(
                    run_id=envelope.run_id,
                    ledger_id=LedgerIdCodec.format(
                        envelope.run_id, envelope.sequence_no
                    ),
                    sequence_no=envelope.sequence_no,
                    event_type=event_type,
                    payload=dict(envelope.payload),
                    created_at=envelope.created_at.isoformat(),
                )
            )
        try:
            return EffectStageFold.fold(events).executor
        except Exception as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Effect command stage is unavailable.",
                retryable=False,
            ) from exc


@dataclass(frozen=True)
class E2RolloutEffectCommitHandler:
    """Enforce E2 cohorts and rollback before delegating to any effect handler."""

    delegate: EffectCommitHandlerPort
    persistence: PersistencePort
    executor_resolver: EffectStageExecutorResolver
    admission: E2RolloutAdmission

    async def handle(self, command: RuntimeEffectCommitCommand) -> None:
        """Fail closed before a claim, prepare, or executor can be reached."""

        run = await self._verified_run(command)
        executor = await self.executor_resolver.resolve_executor(
            run=run,
            stage_id=command.stage_id,
        )
        try:
            self.admission.require_all(
                capabilities=effect_execution_capabilities(executor),
                facts_provider=PersistedRunCohortFactsProvider(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
            )
        except (E2RolloutAdmissionDenied, ValueError) as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Effect commit is unavailable for this rollout cohort.",
                retryable=False,
            ) from exc
        await self.delegate.handle(command)

    async def _verified_run(self, command: RuntimeEffectCommitCommand) -> RunRecord:
        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if (
            run is None
            or run.org_id != command.org_id
            or run.run_id != command.run_id
            or run.user_id != command.user_id
            or run.conversation_id != command.conversation_id
        ):
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Effect command scope does not match its run.",
                retryable=False,
            )
        return run


__all__ = (
    "E2RolloutEffectCommitHandler",
    "EffectCommitHandlerPort",
    "EffectStageExecutorResolver",
    "EventStoreEffectStageExecutorResolver",
    "effect_execution_capabilities",
)
