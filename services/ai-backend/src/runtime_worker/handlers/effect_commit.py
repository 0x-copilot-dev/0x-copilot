"""Thin worker adapter for durable A5 effect-commit commands.

The adapter deliberately has only two capabilities: read the authoritative run
record and ask a run-scoped coordinator to handle a body-free domain command.
It does not construct an executor, append ledger events, or own a claim store.
Those consequential capabilities remain behind the coordinator factory.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runtime.effects.contracts import EffectCommitCommand
from agent_runtime.effects.coordinator import EffectReconcileCommand
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from runtime_api.schemas import (
    RunRecord,
    RuntimeEffectCommitCommand,
)


@runtime_checkable
class RuntimeRunLookupPort(Protocol):
    """Read only the trusted run record needed to bind a worker command."""

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by its durable organization identity."""


@runtime_checkable
class EffectCoordinatorPort(Protocol):
    """The narrow coordinator surface that worker adapters may invoke."""

    async def handle(self, command: EffectCommitCommand) -> object:
        """Coordinate one already-approved effect commit."""

    async def reconcile(self, command: EffectReconcileCommand) -> object:
        """Reconcile one already-claimed effect without replaying apply."""


@runtime_checkable
class EffectCoordinatorFactory(Protocol):
    """Bind a coordinator to verified server-side run facts only."""

    def for_run(self, *, run: RunRecord) -> EffectCoordinatorPort:
        """Return a coordinator whose dependencies are scoped to ``run``."""


async def load_verified_runtime_run(
    *,
    persistence: RuntimeRunLookupPort,
    org_id: str,
    run_id: str,
) -> RunRecord:
    """Load and verify the authoritative tenant/run binding.

    A command is never trusted merely because it made it onto the durable queue.
    The run record is the source of truth and mismatches are terminal: retrying a
    foreign or stale scope must not reach a coordinator.
    """

    run = await persistence.get_run(org_id=org_id, run_id=run_id)
    if run is None:
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Effect command run is unavailable.",
            retryable=False,
        )
    if run.org_id != org_id or run.run_id != run_id:
        raise AgentRuntimeError(
            RuntimeErrorCode.PERMISSION_DENIED,
            "Effect command scope does not match its run.",
            retryable=False,
        )
    return run


class RuntimeEffectCommitHandler:
    """Revalidate a durable A5 commit envelope before delegating to A5."""

    def __init__(
        self,
        *,
        persistence: RuntimeRunLookupPort,
        coordinator_factory: EffectCoordinatorFactory,
    ) -> None:
        self._persistence = persistence
        self._coordinator_factory = coordinator_factory

    async def handle(self, command: RuntimeEffectCommitCommand) -> None:
        """Map exactly one verified transport command into the pure command."""

        run = await load_verified_runtime_run(
            persistence=self._persistence,
            org_id=command.org_id,
            run_id=command.run_id,
        )
        if (
            run.user_id != command.user_id
            or run.conversation_id != command.conversation_id
        ):
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Effect-commit command scope does not match its run.",
                retryable=False,
            )

        pure_command = EffectCommitCommand(
            run_id=command.run_id,
            stage_id=command.stage_id,
            revision=command.revision,
            decision_ledger_id=command.decision_ledger_id,
            proposal_digest=command.proposal_digest,
            target_digest=command.target_digest,
            idempotency_key=command.idempotency_key,
            row_keys=command.row_keys,
            retry_basis_ledger_id=command.retry_basis_ledger_id,
        )
        coordinator = self._coordinator_factory.for_run(run=run)
        await coordinator.handle(pure_command)


__all__ = (
    "EffectCoordinatorFactory",
    "EffectCoordinatorPort",
    "RuntimeEffectCommitHandler",
    "RuntimeRunLookupPort",
    "load_verified_runtime_run",
)
