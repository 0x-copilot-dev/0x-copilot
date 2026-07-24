"""Generative-UI surface capability package.

PRD-01 seeded this package with the SurfaceSpec pydantic mirror + validator
(:mod:`spec_models`). PRD-02 added backend **emission**: a builtin curated spec
library (:mod:`builtin`) and the pure-domain :class:`~.projector.SurfaceProjector`
that turns tool output into a ``SurfaceEnvelope``. PRD-07 adds the **generation
subsystem**: the store adapters (:mod:`store`), the structural output-shape hash
(:mod:`shape_hash`), and the cheap-model :class:`~.generator.SurfaceSpecGenerator`
plus its run-scoped :class:`~.generator.SurfaceGenerationScheduler`, steered by
the packaged ``spec-authoring`` skill.

PRD-E3 retired the v1 ``result["surface"]`` appendage: the ``SurfaceProjector``
survives as the **envelope-computation ladder** consumed by the Generative
Surfaces v2 Work Ledger emitter (``surfaces_v2.emitter``), but the standalone
``RUNTIME_SURFACE_EMISSION`` gate and its ``config`` module were deleted — v2's
``SURFACES_V2`` flag is now the only switch.
"""

from agent_runtime.capabilities.surfaces.backend_store import (
    BackendHttpSurfaceSpecStore,
    build_surface_spec_store,
)
from agent_runtime.capabilities.surfaces.commit import (
    CommitAuditSink,
    CommitEventSink,
    CommitKind,
    CommitLedgerEntry,
    CommitLedgerPort,
    CommitOutcome,
    CommitProposal,
    CommitRequest,
    CommitStatus,
    ConnectorCommitResult,
    InMemoryCommitLedger,
    PersistenceCommitAuditSink,
    RemoteState,
    SurfaceCommitConnector,
    SurfaceCommitExecutor,
    SurfaceEditMerger,
    SurfaceEdits,
)
from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    GenToolDescriptor,
    SpecAuthoringSkill,
    SurfaceGenerationScheduler,
    SurfaceSpecGenerator,
    build_surface_generation_scheduler,
)
from agent_runtime.capabilities.surfaces.projector import (
    SurfaceGenerationSchedulerPort,
    SurfaceProjector,
)
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.shape_request import (
    InvitedShapeAttempt,
    ShapeRequestError,
    ShapeRequestOutcome,
    ShapeRequestRunner,
)
from agent_runtime.capabilities.surfaces.store import (
    FileSurfaceSpecStore,
    InMemorySurfaceSpecStore,
    SpecKey,
    StoredSpec,
    SurfaceSpecReadPort,
    SurfaceSpecStorePort,
)

__all__ = [
    "BackendHttpSurfaceSpecStore",
    "CommitAuditSink",
    "CommitEventSink",
    "CommitKind",
    "CommitLedgerEntry",
    "CommitLedgerPort",
    "CommitOutcome",
    "CommitProposal",
    "CommitRequest",
    "CommitStatus",
    "ConnectorCommitResult",
    "FileSurfaceSpecStore",
    "GenFailure",
    "GenToolDescriptor",
    "InMemoryCommitLedger",
    "InMemorySurfaceSpecStore",
    "InvitedShapeAttempt",
    "PersistenceCommitAuditSink",
    "RemoteState",
    "ShapeRequestError",
    "ShapeRequestOutcome",
    "ShapeRequestRunner",
    "SpecAuthoringSkill",
    "SpecKey",
    "StoredSpec",
    "SurfaceCommitConnector",
    "SurfaceCommitExecutor",
    "SurfaceEditMerger",
    "SurfaceEdits",
    "SurfaceGenerationScheduler",
    "SurfaceGenerationSchedulerPort",
    "SurfaceProjector",
    "SurfaceSpecGenerator",
    "SurfaceSpecReadPort",
    "SurfaceSpecStorePort",
    "build_surface_generation_scheduler",
    "build_surface_spec_store",
    "output_shape_hash",
]
