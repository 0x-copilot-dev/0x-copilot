"""Transport outbox adapter for approved universal effects.

The effect staging domain emits a body-free :class:`EffectCommitCommand` only
after it has durably recorded a digest-pinned approval.  This adapter binds
that command to the runtime worker queue using identity resolved from the
trusted run record at composition time.  It deliberately has no executor,
claim, or event-emission capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from agent_runtime.api.ports import RuntimeQueuePort
from agent_runtime.effects.contracts import EffectCommitCommand
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from runtime_api.schemas import RuntimeEffectCommitCommand


@dataclass(frozen=True)
class RuntimeEffectCommitOutbox:
    """Translate one approved pure-domain command into a runtime command.

    ``scope`` is constructed from the authoritative run record, never from an
    effect proposal or an HTTP body.  A stable command identifier makes
    retries observable as one semantic delivery; the worker's durable claim is
    still the final exactly-once boundary before any external mutation.
    """

    queue: RuntimeQueuePort
    scope: EffectExecutionScope

    async def enqueue_after_decision(self, command: EffectCommitCommand) -> None:
        """Persist a body-free command for a matching approved run only."""

        if command.run_id != self.scope.run_id:
            raise ValueError(
                "effect command run does not match the trusted outbox scope"
            )
        conversation_id = self.scope.conversation_id
        if not conversation_id:
            raise ValueError("trusted effect outbox scope requires a conversation")
        await self.queue.enqueue_effect_commit(
            RuntimeEffectCommitCommand(
                command_id=self._command_id(command),
                org_id=self.scope.org_id,
                user_id=self.scope.user_id,
                conversation_id=conversation_id,
                run_id=command.run_id,
                stage_id=command.stage_id,
                revision=command.revision,
                decision_ledger_id=command.decision_ledger_id,
                proposal_digest=command.proposal_digest,
                target_digest=command.target_digest,
                idempotency_key=command.idempotency_key,
                row_keys=command.row_keys,
                retry_basis_ledger_id=command.retry_basis_ledger_id,
                governed_capabilities=command.governed_capabilities,
                trace_propagation=QueueTracePropagator.inject(),
            )
        )

    def _command_id(self, command: EffectCommitCommand) -> str:
        """Derive a deterministic opaque id from immutable approval facts."""

        body = {
            "org_id": self.scope.org_id,
            "run_id": command.run_id,
            "stage_id": command.stage_id,
            "revision": command.revision,
            "decision_ledger_id": command.decision_ledger_id,
            "proposal_digest": command.proposal_digest,
            "target_digest": command.target_digest,
            "idempotency_key": command.idempotency_key,
            "row_keys": command.row_keys,
            "retry_basis_ledger_id": command.retry_basis_ledger_id,
        }
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return f"effcmd_{hashlib.sha256(encoded).hexdigest()}"


__all__ = ["RuntimeEffectCommitOutbox"]
