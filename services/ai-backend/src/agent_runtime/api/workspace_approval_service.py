"""C3 workspace-effect decision service and canonical receipt source.

The desktop host may submit an untrusted review snapshot, but it must receive
its approval facts from the canonical A4 ledger fold.  This service scopes the
run to the authenticated user, restricts the route to workspace effects, and
delegates the actual digest-pinned decision to :class:`EffectStager`.  It has
no executor or local-workspace dependency; A5 remains the sole execution path.
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
class WorkspaceApprovalDecisionService:
    """Record a desktop workspace decision against one authenticated run.

    The service deliberately accepts the snapshot digests only as values for
    A4 to verify.  Its caller must project the response from the returned
    folded state, never by echoing those inputs.
    """

    persistence: PersistencePort
    event_producer: RuntimeEventProducer
    queue: RuntimeQueuePort

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
    ) -> EffectStageState:
        """Persist one exact workspace approval/rejection and return its fold.

        Unknown, foreign, malformed, and non-workspace stages all fail as
        not-found.  This makes the workspace-only endpoint non-enumerable and
        prevents a desktop host from obtaining a local-write receipt for an
        unrelated executor kind.
        """

        try:
            EffectStageIdCodec.parse(stage_id)
        except ValueError as error:
            raise EffectStageNotFound() from error

        run = await self._owned_run(org_id=org_id, user_id=user_id, run_id=run_id)
        owner_ref = f"principal://users/{run.user_id}"
        stage_scope = EffectStageScope(run_id=run.run_id, owner_ref=owner_ref)
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
        current = await stager.get_state(scope=stage_scope, stage_id=stage_id)
        if current.executor is not EffectExecutorKind.WORKSPACE:
            raise EffectStageNotFound()

        return await stager.decide(
            scope=stage_scope,
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
                run_id=run.run_id,
                stage_id=stage_id,
                revision=revision,
                decision=decision,
                proposal_digest=proposal_digest,
                target_digest=target_digest,
            ),
        )

    async def _owned_run(self, *, org_id: str, user_id: str, run_id: str):
        """Resolve a run without disclosing whether a foreign run exists."""

        run = await self.persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise EffectStageNotFound()
        return run

    @staticmethod
    def _decision_key(
        *,
        run_id: str,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
    ) -> str:
        """Derive a stable retry key from the exact reviewed snapshot only."""

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
        return f"workspace-decision-{hashlib.sha256(material).hexdigest()}"


__all__ = ["WorkspaceApprovalDecisionService"]
