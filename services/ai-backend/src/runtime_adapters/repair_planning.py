"""Adapter composition for the D12 planning-only repair runner.

The helpers centralize backend selection so the worker's effect coordinator and
the D12 planner observe the same durable claim source.  They deliberately do
not compose a queue, cleanup executor, deletion port, or effect executor.
"""

from __future__ import annotations

from agent_runtime.effects.claims import EffectClaimStore
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.repair_planning import (
    RepairLegalHoldLookup,
    RepairPlanningSnapshotStore,
)
from agent_runtime.surfaces_v2.repair_reconciliation import RepairLegalHoldState


class UnknownRepairLegalHoldLookup:
    """Safe fallback when a backend has no durable legal-hold authority."""

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RepairLegalHoldState:
        del org_id, user_id, conversation_id
        return RepairLegalHoldState.UNKNOWN


class FileRepairLegalHoldLookup:
    """Read the desktop file backend's canonical conversation hold fact."""

    def __init__(self, *, persistence: object) -> None:
        self._persistence = persistence

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RepairLegalHoldState:
        try:
            from runtime_adapters.file._deletion import LegalHoldPolicy

            conversation = await self._persistence.get_conversation_for_org(  # noqa: SLF001
                org_id=org_id,
                conversation_id=conversation_id,
            )
            if conversation is None or conversation.user_id != user_id:
                return RepairLegalHoldState.UNKNOWN
            return (
                RepairLegalHoldState.ACTIVE
                if LegalHoldPolicy.is_on_hold(conversation)
                else RepairLegalHoldState.NONE
            )
        except Exception:
            return RepairLegalHoldState.UNKNOWN


class PostgresRepairLegalHoldLookup:
    """Use the established transaction-safe Postgres legal-hold predicate."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RepairLegalHoldState:
        try:
            from runtime_adapters.postgres.artifact_hold_fence import (
                has_active_hold_for_scope,
            )

            async with self._store._role_connection("worker") as conn:  # noqa: SLF001
                held = await has_active_hold_for_scope(
                    conn,
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
            return RepairLegalHoldState.ACTIVE if held else RepairLegalHoldState.NONE
        except Exception:
            return RepairLegalHoldState.UNKNOWN


def build_effect_claim_store(
    *, settings: RuntimeSettings, persistence: object
) -> EffectClaimStore:
    """Select one backend-correct claim store for worker-owned consumers."""

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for repair planning.",
                retryable=False,
            )
        from runtime_adapters.file.effect_claim_store import FileEffectClaimStore

        return FileEffectClaimStore(root=root)
    if backend == "postgres":
        if not hasattr(persistence, "_role_connection"):
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Postgres repair planning requires the runtime worker store.",
                retryable=False,
            )
        from runtime_adapters.postgres.effect_claim_store import (
            PostgresEffectClaimStore,
        )

        return PostgresEffectClaimStore(store=persistence)
    from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore

    return InMemoryEffectClaimStore()


def build_repair_planning_snapshot_store(
    *, settings: RuntimeSettings, persistence: object
) -> RepairPlanningSnapshotStore:
    """Select the real state adapter for the configured runtime backend."""

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for repair planning.",
                retryable=False,
            )
        from runtime_adapters.file.repair_planning_store import (
            FileRepairPlanningSnapshotStore,
        )

        return FileRepairPlanningSnapshotStore(root=root)
    if backend == "postgres":
        if not hasattr(persistence, "_role_connection"):
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Postgres repair planning requires the runtime worker store.",
                retryable=False,
            )
        from runtime_adapters.postgres.repair_planning_store import (
            PostgresRepairPlanningSnapshotStore,
        )

        return PostgresRepairPlanningSnapshotStore(store=persistence)
    from runtime_adapters.in_memory.repair_planning_store import (
        InMemoryRepairPlanningSnapshotStore,
    )

    return InMemoryRepairPlanningSnapshotStore()


def build_repair_legal_hold_lookup(
    *, settings: RuntimeSettings, persistence: object
) -> RepairLegalHoldLookup:
    """Return a real hold authority where one is available, else UNKNOWN."""

    if settings.store.backend == "file":
        return FileRepairLegalHoldLookup(persistence=persistence)
    if settings.store.backend == "postgres" and hasattr(
        persistence, "_role_connection"
    ):
        return PostgresRepairLegalHoldLookup(store=persistence)
    return UnknownRepairLegalHoldLookup()


__all__ = (
    "FileRepairLegalHoldLookup",
    "PostgresRepairLegalHoldLookup",
    "UnknownRepairLegalHoldLookup",
    "build_effect_claim_store",
    "build_repair_legal_hold_lookup",
    "build_repair_planning_snapshot_store",
)
