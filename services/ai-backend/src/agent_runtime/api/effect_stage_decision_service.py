"""Owner-scoped decisions for canonical A4 effect stages.

The A4 ``EffectStager`` owns the decision state machine and the A5 command
enqueue.  Product-specific approval routes use this service only to establish
the authenticated run scope and select the executor kinds they are allowed to
act on.  The service intentionally has no executor registry, connector, or
workspace dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import PersistencePort, RuntimeQueuePort
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectStageScope,
    EffectStageState,
)
from agent_runtime.effects.errors import EffectStageNotFound
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectExecutorKind,
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
            ),
        )

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
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return f"{namespace}-{hashlib.sha256(material).hexdigest()}"


__all__ = ["EffectStageDecisionService"]
