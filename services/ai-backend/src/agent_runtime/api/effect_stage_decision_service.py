"""Owner-scoped decisions for canonical A4 effect stages.

The A4 ``EffectStager`` owns the decision state machine and the A5 command
enqueue.  Product-specific approval routes use this service only to establish
the authenticated run scope and select the executor kinds they are allowed to
act on.  The service intentionally has no executor registry, connector, or
workspace dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import PersistencePort, RuntimeQueuePort
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectCommitCommand,
    EffectStageScope,
    EffectStageState,
)
from agent_runtime.effects.errors import (
    EffectStageForbidden,
    EffectStageInvalidTransition,
    EffectStageNotFound,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.effects.rollout import effect_execution_capabilities
from agent_runtime.effects.staging import EffectStager
from agent_runtime.rollout_admission import (
    E2GovernedLane,
    E2RolloutAdmission,
    E2RolloutAdmissionDenied,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectExecutorKind,
    LedgerEventType,
)


@dataclass(frozen=True)
class EffectStageDecisionService:
    """Apply one user decision through the existing A4/A5 semantics.

    ``allowed_executors`` is supplied by the calling route.  It is an
    authorization boundary, not a presentation hint: an otherwise valid stage
    with another executor is deliberately indistinguishable from not found.
    This lets workspace preserve its receipt-only path while an MCP effect can
    use the normal external-effect decision path.
    """

    persistence: PersistencePort
    event_producer: RuntimeEventProducer
    queue: RuntimeQueuePort
    rollout_admission: E2RolloutAdmission

    async def current_stage(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        allowed_executors: frozenset[EffectExecutorKind],
    ) -> EffectStageState:
        """Return one owned stage only when its executor is route-authorised."""

        _run, _scope, stager, current = await self._owned_stage(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=allowed_executors,
        )
        del _run, _scope, stager
        return current

    async def stage_history(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        allowed_executors: frozenset[EffectExecutorKind],
    ) -> tuple[EffectStageState, tuple[StructuralEvent, ...]]:
        """Return the owner-scoped stage and its append-only event history.

        Review projections use this read seam instead of reaching into the
        decision service's private composition.  It preserves the same
        owner/executor indistinguishability boundary as ``current_stage``.
        """

        _run, scope, stager, current = await self._owned_stage(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=allowed_executors,
        )
        del _run
        events = await stager.ledger.list_stage_events(
            scope=scope,
            stage_id=stage_id,
        )
        return current, tuple(events)

    async def record_decision(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...] | None = None,
        allowed_executors: frozenset[EffectExecutorKind],
        idempotency_namespace: str = "effect-decision",
    ) -> EffectStageState:
        """Record an exact user decision and enqueue only a fresh approval.

        The stager re-folds and validates the current revision/digests before
        it appends anything.  Its own idempotency behaviour is retained: an
        identical retry returns the recorded state and does not enqueue a
        second A5 command.
        """

        run, scope, stager, _current = await self._owned_stage(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=allowed_executors,
        )
        governed_lane = self._admit_rollout_lane(run=run, executor=_current.executor)
        owner_ref = f"principal://users/{run.user_id}"
        return await stager.decide(
            scope=scope,
            stage_id=stage_id,
            revision=revision,
            decision=decision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=owner_ref,
            ),
            idempotency_key=self._decision_key(
                namespace=idempotency_namespace,
                run_id=run.run_id,
                stage_id=stage_id,
                revision=revision,
                decision=decision,
                proposal_digest=proposal_digest,
                target_digest=target_digest,
                row_keys=row_keys,
            ),
            row_keys=row_keys,
            governed_capabilities=(
                governed_lane.capabilities if governed_lane is not None else None
            ),
        )

    async def record_row_decisions(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        decisions: dict[str, str],
        proposal_digest: str,
        target_digest: str,
        allowed_executors: frozenset[EffectExecutorKind],
        idempotency_key: str,
    ) -> EffectStageState:
        """Persist user row posture through the canonical effect ledger."""

        run, scope, stager, _current = await self._owned_stage(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=allowed_executors,
        )
        return await stager.record_row_decisions(
            scope=scope,
            stage_id=stage_id,
            revision=revision,
            decisions=decisions,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=f"principal://users/{run.user_id}",
            ),
            idempotency_key=idempotency_key,
        )

    async def enqueue_rowset_retry(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...],
        basis_ledger_id: str,
        allowed_executors: frozenset[EffectExecutorKind],
    ) -> EffectStageState:
        """Enqueue only the exact failed scope from the latest durable result."""

        run, scope, stager, current = await self._owned_stage(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=allowed_executors,
        )
        decision = current.decision
        if (
            decision is None
            or decision.decision is not EffectDecisionKind.APPROVE
            or decision.row_keys is None
            or current.current_revision.revision != revision
            or current.current_revision.proposal_digest != proposal_digest
            or current.target_digest != target_digest
        ):
            raise EffectStageInvalidTransition()
        events = await stager.ledger.list_stage_events(
            scope=scope,
            stage_id=stage_id,
        )
        failed = _latest_failed_scope(events=events, basis_ledger_id=basis_ledger_id)
        if (
            failed is None
            or set(row_keys) != set(failed)
            or not set(row_keys).issubset(decision.row_keys)
        ):
            raise EffectStageInvalidTransition()
        governed_lane = self._admit_rollout_lane(
            run=run,
            executor=current.executor,
        )
        idempotency_key = self._retry_key(
            run_id=run.run_id,
            stage_id=stage_id,
            revision=revision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            row_keys=row_keys,
            basis_ledger_id=basis_ledger_id,
        )
        await stager.outbox.enqueue_after_decision(
            EffectCommitCommand(
                run_id=run.run_id,
                stage_id=stage_id,
                revision=revision,
                decision_ledger_id=decision.ledger_id,
                proposal_digest=proposal_digest,
                target_digest=target_digest,
                idempotency_key=idempotency_key,
                row_keys=row_keys,
                retry_basis_ledger_id=basis_ledger_id,
                governed_capabilities=(
                    governed_lane.capabilities if governed_lane is not None else None
                ),
            )
        )
        return current

    async def _owned_stage(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        allowed_executors: frozenset[EffectExecutorKind],
    ) -> tuple[object, EffectStageScope, EffectStager, EffectStageState]:
        if not allowed_executors:
            raise EffectStageNotFound()
        try:
            EffectStageIdCodec.parse(stage_id)
        except ValueError as error:
            raise EffectStageNotFound() from error

        run = await self.persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            # Do not disclose a foreign run or an unowned effect-stage id.
            raise EffectStageNotFound()

        owner_ref = f"principal://users/{run.user_id}"
        scope = EffectStageScope(run_id=run.run_id, owner_ref=owner_ref)
        stager = EffectStager(
            ledger=RuntimeEffectLedger(
                event_producer=self.event_producer,
                run=run,
                owner_ref=owner_ref,
            ),
            outbox=RuntimeEffectCommitOutbox(
                queue=self.queue,
                scope=EffectExecutionScope(
                    org_id=run.org_id,
                    user_id=run.user_id,
                    conversation_id=run.conversation_id,
                    run_id=run.run_id,
                    owner_ref=owner_ref,
                ),
            ),
        )
        state = await stager.get_state(scope=scope, stage_id=stage_id)
        if state.executor not in allowed_executors:
            # Executor-kind is part of the product boundary.  In particular,
            # the generic MCP route must never mint a workspace decision.
            raise EffectStageNotFound()
        return run, scope, stager, state

    def _admit_rollout_lane(
        self,
        *,
        run,
        executor: EffectExecutorKind,  # noqa: ANN001
    ) -> E2GovernedLane | None:
        """Deny the decision/enqueue path before it mutates the effect ledger.

        Construction requires this immutable admission snapshot, including for
        direct domain harnesses.  There is no unchecked decision/enqueue
        variant to fall back to when an E2 lane becomes explicit.
        """

        try:
            return self.rollout_admission.begin_governed_lane(
                capabilities=effect_execution_capabilities(executor),
                facts_provider=PersistedRunCohortFactsProvider(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
            )
        except (E2RolloutAdmissionDenied, ValueError) as exc:
            raise EffectStageForbidden() from exc

    @staticmethod
    def _decision_key(
        *,
        namespace: str,
        run_id: str,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...] | None = None,
    ) -> str:
        """Derive one stable retry key from the exact reviewed snapshot."""

        material = json.dumps(
            {
                "run_id": run_id,
                "stage_id": stage_id,
                "revision": revision,
                "decision": decision.value,
                "proposal_digest": proposal_digest,
                "target_digest": target_digest,
                "row_keys": row_keys,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return f"{namespace}-{hashlib.sha256(material).hexdigest()}"

    @staticmethod
    def _retry_key(
        *,
        run_id: str,
        stage_id: str,
        revision: int,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...],
        basis_ledger_id: str,
    ) -> str:
        material = json.dumps(
            {
                "run_id": run_id,
                "stage_id": stage_id,
                "revision": revision,
                "proposal_digest": proposal_digest,
                "target_digest": target_digest,
                "row_keys": sorted(row_keys),
                "basis_ledger_id": basis_ledger_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return f"effect-rowset-retry-{hashlib.sha256(material).hexdigest()}"


def _latest_failed_scope(
    *,
    events: Sequence[StructuralEvent],
    basis_ledger_id: str,
) -> tuple[str, ...] | None:
    applied = [
        event
        for event in events
        if event.event_type == LedgerEventType.EFFECT_APPLIED.value
        and isinstance(event.payload.get("row_results"), list | tuple)
    ]
    if not applied:
        return None
    latest = max(applied, key=lambda event: (event.sequence_no, event.ledger_id))
    if latest.ledger_id != basis_ledger_id:
        return None
    failed: list[str] = []
    seen: set[str] = set()
    for item in latest.payload.get("row_results", ()):
        if not isinstance(item, dict):
            return None
        row_key = item.get("row_key")
        outcome = item.get("outcome")
        if (
            not isinstance(row_key, str)
            or not row_key
            or row_key in seen
            or outcome not in {"applied", "failed"}
        ):
            return None
        seen.add(row_key)
        if outcome == "failed":
            failed.append(row_key)
    return tuple(failed) if failed else None


__all__ = ["EffectStageDecisionService"]
