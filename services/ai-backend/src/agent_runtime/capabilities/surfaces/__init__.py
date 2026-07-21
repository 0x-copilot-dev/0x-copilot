"""Generative-UI surface capability package.

PRD-01 seeded this package with the SurfaceSpec pydantic mirror + validator
(:mod:`spec_models`). PRD-02 added backend **emission**: a builtin curated spec
library (:mod:`builtin`), the pure-domain :class:`~.projector.SurfaceProjector`
that turns tool output into a ``SurfaceEnvelope``, and the
``RUNTIME_SURFACE_EMISSION`` flag (:mod:`config`). PRD-07 adds the **generation
subsystem**: the store adapters (:mod:`store`), the structural output-shape hash
(:mod:`shape_hash`), and the cheap-model :class:`~.generator.SurfaceSpecGenerator`
plus its run-scoped :class:`~.generator.SurfaceGenerationScheduler`, steered by
the packaged ``spec-authoring`` skill.
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
from agent_runtime.capabilities.surfaces.config import SurfaceEmissionFlag
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
    "PersistenceCommitAuditSink",
    "RemoteState",
    "SpecAuthoringSkill",
    "SpecKey",
    "StoredSpec",
    "SurfaceCommitConnector",
    "SurfaceCommitExecutor",
    "SurfaceEditMerger",
    "SurfaceEdits",
    "SurfaceEmissionFlag",
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
