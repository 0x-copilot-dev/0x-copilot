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
from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportVerificationStore,
)
from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationCheckpointStore,
    LegacyStageMigrationStore,
)


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
    return UnknownRepairLegalHoldLookup()


def build_audit_export_verification_store(
    *, settings: RuntimeSettings, persistence: object
) -> AuditExportVerificationStore:
    """Select the backend-correct safe D7/D12 sampling state adapter.

    The resulting store owns only signed safe manifest metadata, cursors,
    leases, and verification outcomes.  It has no queue or executor handle.
    """

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for audit export verification.",
                retryable=False,
            )
        from runtime_adapters.file.audit_export_verification_store import (
            FileAuditExportVerificationStore,
        )

        return FileAuditExportVerificationStore(root=root)
    from runtime_adapters.in_memory.audit_export_verification_store import (
        InMemoryAuditExportVerificationStore,
    )

    return InMemoryAuditExportVerificationStore()


def build_legacy_migration_checkpoint_store(
    *, settings: RuntimeSettings, persistence: object
) -> LegacyMigrationCheckpointStore:
    """Select the durable E2 prerequisite checkpoint adapter for this backend."""

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for legacy migration.",
                retryable=False,
            )
        from runtime_adapters.file.legacy_migration_store import (
            FileLegacyMigrationCheckpointStore,
        )

        return FileLegacyMigrationCheckpointStore(root=root)
    from runtime_adapters.in_memory.legacy_migration_store import (
        InMemoryLegacyMigrationCheckpointStore,
    )

    return InMemoryLegacyMigrationCheckpointStore()


def build_legacy_stage_migration_store(
    *, settings: RuntimeSettings, persistence: object
) -> LegacyStageMigrationStore:
    """Select the durable E2 D5 mapping adapter for this runtime backend."""

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for legacy stage migration.",
                retryable=False,
            )
        from runtime_adapters.file.legacy_stage_migration_store import (
            FileLegacyStageMigrationStore,
        )

        return FileLegacyStageMigrationStore(root=root)
    from runtime_adapters.in_memory.legacy_stage_migration_store import (
        InMemoryLegacyStageMigrationStore,
    )

    return InMemoryLegacyStageMigrationStore()


def build_legacy_stage_migration_service(
    *, settings: RuntimeSettings, persistence: object, event_store: object
) -> object:
    """Compose real E2 D5 inventory/control ports for the selected adapter.

    This is intentionally a control-plane-only assembly.  The writer receives
    a no-commit outbox, while the queue port can only neutralize an old command
    with a typed CAS result.  It cannot claim or dispatch either legacy or
    canonical work.
    """

    from agent_runtime.api.events import RuntimeEventProducer
    from agent_runtime.api.legacy_stage_migration_runtime import (
        DurableLegacyStageCandidateResolver,
        RuntimeCanonicalHeldStageWriter,
        RuntimeLegacyFrozenReconciler,
        RuntimeLegacyPendingStageInventory,
        RuntimeLegacyQueueNeutralizer,
        RuntimeLegacyStageMigrationAudit,
        RuntimeLegacyStageSourceFence,
    )
    from agent_runtime.api.legacy_stage_migration_service import (
        LegacyStageMigrationService,
    )

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for legacy stage migration.",
                retryable=False,
            )
        from runtime_adapters.file.legacy_stage_migration_control import (
            FileLegacyStageQueueControl,
            FileLegacyStageReservationStore,
        )

        queue_control = FileLegacyStageQueueControl(store=persistence)
        reservations = FileLegacyStageReservationStore(store=persistence, root=root)
    else:
        from runtime_adapters.in_memory.legacy_stage_migration_control import (
            InMemoryLegacyStageQueueControl,
            InMemoryLegacyStageReservationStore,
        )

        queue_control = InMemoryLegacyStageQueueControl(store=persistence)
        reservations = InMemoryLegacyStageReservationStore(store=persistence)

    fence = RuntimeLegacyStageSourceFence(
        reservations=reservations,
    )
    return LegacyStageMigrationService(
        inventory=RuntimeLegacyPendingStageInventory(
            persistence=persistence,
            event_store=event_store,
            queue=queue_control,
            candidates=DurableLegacyStageCandidateResolver(evidence=reservations),
        ),
        mappings=build_legacy_stage_migration_store(
            settings=settings, persistence=persistence
        ),
        writer=RuntimeCanonicalHeldStageWriter(
            persistence=persistence,
            event_producer=RuntimeEventProducer(
                persistence=persistence, event_store=event_store
            ),
            fence=fence,
        ),
        queue=RuntimeLegacyQueueNeutralizer(
            cancel_cas=queue_control.cancel_unclaimed,
        ),
        reconciler=RuntimeLegacyFrozenReconciler(
            audit=persistence,
            checkpoints=reservations,
        ),
        audit=RuntimeLegacyStageMigrationAudit(audit=persistence),
    )


__all__ = (
    "FileRepairLegalHoldLookup",
    "UnknownRepairLegalHoldLookup",
    "build_effect_claim_store",
    "build_audit_export_verification_store",
    "build_legacy_migration_checkpoint_store",
    "build_legacy_stage_migration_service",
    "build_legacy_stage_migration_store",
    "build_repair_legal_hold_lookup",
    "build_repair_planning_snapshot_store",
)
