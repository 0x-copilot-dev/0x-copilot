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
    E2GovernedLane,
    E2RolloutAdmission,
    E2RolloutAdmissionDenied,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind, LedgerEventType
from agent_runtime.surfaces_v2.stage_rollout import StagedWriteRolloutGate
from agent_runtime.surfaces_v2.staging import StagedWriteFold
from runtime_api.schemas import (
    RunRecord,
    RuntimeEffectCommitCommand,
    RuntimeStageCommitCommand,
)


_EFFECT_STAGE_EVENT_TYPES = frozenset(
    {
        LedgerEventType.EFFECT_STAGED.value,
        LedgerEventType.EFFECT_PROJECTION_BOUND.value,
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
class StageCommitHandlerPort(Protocol):
    """Narrow downstream surface for the D1/D3 connector-commit consumer."""

    async def handle(self, command: RuntimeStageCommitCommand) -> None:
        """Handle one admitted stage command."""


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
        facts = PersistedRunCohortFactsProvider(
            org_id=run.org_id,
            user_id=run.user_id,
        )
        expected_capabilities = effect_execution_capabilities(executor)
        try:
            if command.governed_capabilities is None:
                # Compatibility commands keep the existing current-config
                # admission behavior. Newly governed commands below never fall
                # back here after an off/restart transition.
                self.admission.require_all(
                    capabilities=expected_capabilities,
                    facts_provider=facts,
                )
            else:
                lane = E2GovernedLane(capabilities=command.governed_capabilities)
                if lane.capabilities != expected_capabilities:
                    raise ValueError("effect command rollout mark mismatches executor")
                self.admission.require_governed_lane(
                    lane=lane,
                    facts_provider=facts,
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


@dataclass(frozen=True)
class E2RolloutStageCommitHandler:
    """Protect the legacy staged-MCP commit lane before claim or dispatch.

    The wrapper is installed around the *only* worker stage-command dispatch
    reference, including injected handlers. It folds the authoritative ledger
    mark, checks the redundant body-free command copy for tampering, then asks
    the same E2 gate that admitted the stage whether that governed lane may
    continue under the current process configuration.
    """

    delegate: StageCommitHandlerPort
    persistence: PersistencePort
    event_store: EventStorePort
    admission: E2RolloutAdmission

    async def handle(self, command: RuntimeStageCommitCommand) -> None:
        """Deny before the downstream handler can claim, prepare, or apply."""

        run = await self._verified_run(command)
        state = await self._stage_state(run=run, stage_id=command.stage_id)
        if state is None:
            # Preserve the D2 handler's existing stale/unknown stage no-op.
            await self.delegate.handle(command)
            return
        lane = state.governed_lane
        command_mark = command.governed_capabilities
        if (
            state.governed_lane_invalid
            or (lane is None and command_mark is not None)
            or (lane is not None and command_mark != lane.capabilities)
        ):
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Staged write commit is unavailable for this rollout cohort.",
                retryable=False,
            )
        try:
            StagedWriteRolloutGate(admission=self.admission).require_continuation(
                lane=lane,
                malformed_mark=state.governed_lane_invalid,
                facts_provider=PersistedRunCohortFactsProvider(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
            )
        except (E2RolloutAdmissionDenied, ValueError) as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Staged write commit is unavailable for this rollout cohort.",
                retryable=False,
            ) from exc
        await self.delegate.handle(command)

    async def _verified_run(self, command: RuntimeStageCommitCommand) -> RunRecord:
        run = await self.persistence.get_run(
            org_id=command.org_id,
            run_id=command.run_id,
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
                "Staged write command scope does not match its run.",
                retryable=False,
            )
        return run

    async def _stage_state(self, *, run: RunRecord, stage_id: str):  # noqa: ANN202
        envelopes = await self.event_store.list_events_after(
            org_id=run.org_id,
            run_id=run.run_id,
            after_sequence=0,
        )
        return StagedWriteFold.fold(envelopes).get(stage_id)


__all__ = (
    "E2RolloutEffectCommitHandler",
    "E2RolloutStageCommitHandler",
    "EffectCommitHandlerPort",
    "EffectStageExecutorResolver",
    "EventStoreEffectStageExecutorResolver",
    "StageCommitHandlerPort",
    "effect_execution_capabilities",
)
