"""Postgres storage backend — the multi-process server deployment.

Everything Postgres-specific in the composition path lives here, imported only
when ``RUNTIME_STORE_BACKEND=postgres`` selects it. That is what keeps
``psycopg`` off the desktop's import path: ``runtime_adapters.factory`` no
longer names this module, the registry resolves it by dotted path at call time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_runtime.api.artifact_repository import RuntimeArtifactSourceLookup
from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.api.prompt_observation_store import (
    EventJournalPromptObservationStore,
)
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from runtime_adapters._artifact_repository import ArtifactRepositoryBundle
from runtime_adapters.artifact_lifecycle import ArtifactLifecycleJobs
from runtime_adapters.artifact_references import PostgresArtifactReferenceStore
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.artifact_gc import PostgresArtifactGarbageCollector
from runtime_adapters.postgres.artifact_store import PostgresArtifactMetadataStore
from runtime_adapters.postgres.conversation_tool_ordinal_store import (
    PostgresConversationToolOrdinalStore,
)
from runtime_adapters.postgres.draft_store import PostgresDraftStore
from runtime_adapters.postgres.share_store import PostgresShareStore
from runtime_adapters.postgres.source_store import PostgresSourceStore
from runtime_adapters.postgres.subagent_store import PostgresSubagentStore

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids an import cycle
    from agent_runtime.settings import RuntimeSettings
    from runtime_adapters.factory import RuntimePorts


def build_ports(settings: "RuntimeSettings", *, role: str = "api") -> "RuntimePorts":
    """Compose the Postgres port surface.

    ``role`` is stamped on the pool's ``application_name`` so connections are
    identifiable in ``pg_stat_activity``. The caller must open and close the
    pool via ``ports.lifecycle``.
    """

    from runtime_adapters.factory import RuntimePorts

    if settings.store.database_url is None:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "DATABASE_URL is required when RUNTIME_STORE_BACKEND=postgres.",
            retryable=False,
        )
    artifact_blob_root: str | None = None
    if settings.execution.artifact_effects_v2:
        artifact_blob_root = settings.store.artifact_blob_root
        if (
            not artifact_blob_root
            or not Path(artifact_blob_root).is_absolute()
            or Path(artifact_blob_root) == Path("/")
        ):
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_ARTIFACT_BLOB_ROOT must be an explicit absolute "
                "durable shared root when RUNTIME_STORE_BACKEND=postgres.",
                retryable=False,
            )
    # When the SSE bus uses Postgres LISTEN/NOTIFY, the adapter must fire a
    # NOTIFY after every event append so the API process's listener wakes the
    # SSE handler cross-process. The in-memory bus uses asyncio.Condition and
    # does not need an explicit notification.
    notify_after_append = settings.resolved_event_bus_backend() == "postgres"
    postgres_store = PostgresRuntimeApiStore(
        settings.store.database_url,
        role=role,
        notify_after_append=notify_after_append,
    )
    run_control_store = EventJournalRunControlStore(postgres_store)
    bundle = (
        _artifact_bundle(postgres_store, artifact_blob_root or "")
        if settings.execution.artifact_effects_v2
        else None
    )
    if bundle is not None:
        postgres_store.configure_artifact_lifecycle(bundle.lifecycle_jobs)
    return RuntimePorts(
        persistence=postgres_store,
        event_store=postgres_store,
        queue=postgres_store,
        backend="postgres",
        lifecycle=postgres_store,
        draft_store=PostgresDraftStore(postgres_store),
        share_store=PostgresShareStore(postgres_store),
        conversation_tool_ordinal_store=PostgresConversationToolOrdinalStore(
            postgres_store
        ),
        run_control_snapshot_store=run_control_store,
        run_control_decision_store=run_control_store,
        prompt_observation_store=EventJournalPromptObservationStore(
            events=postgres_store,
            snapshots=run_control_store,
        ),
        model_invocation_store=EventJournalModelInvocationStore(
            events=postgres_store,
            snapshots=run_control_store,
        ),
        subagent_store=PostgresSubagentStore(postgres_store),
        source_store=PostgresSourceStore(postgres_store),
        evaluation_repository=_shared_evaluation_repository(settings),
        postgres_store=postgres_store,
        # The Postgres store IS a CitationStorePort — same instance the worker
        # resolved historically, so write behavior is unchanged.
        citation_store=postgres_store,
        artifact_source_lookup=RuntimeArtifactSourceLookup(postgres_store),
        artifact_effects_v2=settings.execution.artifact_effects_v2,
        artifact_repository=bundle,
        artifact_metadata_store=bundle.metadata_store if bundle else None,
        artifact_blob_store=bundle.blob_store if bundle else None,
        artifact_reference_provider=(bundle.reference_provider if bundle else None),
        artifact_garbage_collector=(bundle.garbage_collector if bundle else None),
        artifact_retention_purger=(bundle.retention_purger if bundle else None),
        artifact_quarantine_reaper=(bundle.quarantine_reaper if bundle else None),
        artifact_lifecycle_jobs=bundle.lifecycle_jobs if bundle else None,
        artifact_event_publication=postgres_store if bundle else None,
    )


def _shared_evaluation_repository(
    settings: "RuntimeSettings",
) -> EvaluationRepositoryPort | None:
    """Compose a multi-process-safe file/CAS repository only on an explicit root."""

    root = settings.store.evaluation_store_root
    if root is None:
        if settings.evaluation.projection_enabled:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_EVALUATION_STORE_ROOT is required when evaluation "
                "projection is enabled with the Postgres runtime store.",
                retryable=False,
            )
        return None
    path = Path(root)
    if not path.is_absolute() or path == Path("/"):
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "RUNTIME_EVALUATION_STORE_ROOT must be an explicit absolute root.",
            retryable=False,
        )
    from runtime_adapters.file._capacity import QuotaGuard
    from runtime_adapters.file._paths import FileStoreLayout
    from runtime_adapters.file.evaluation_repository import FileEvaluationRepository
    from runtime_adapters.file.object_store import FileObjectStore

    layout = FileStoreLayout(path)
    object_store = FileObjectStore(
        layout,
        quota=QuotaGuard(
            layout,
            max_bytes=settings.store.evaluation_store_max_bytes,
        ),
    )
    return FileEvaluationRepository(layout, object_store=object_store)


def _artifact_bundle(parent: object, root: str) -> ArtifactRepositoryBundle:
    """Construct one shared coordinator/blob instance for Postgres."""

    from runtime_adapters.file._paths import FileStoreLayout
    from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
    from runtime_adapters.file.artifact_publication import (
        FileArtifactPublicationCoordinator,
    )

    layout = FileStoreLayout(Path(root))
    coordinator = FileArtifactPublicationCoordinator(layout)
    blob_store = FileArtifactBlobStore(layout, coordinator)
    reference_store = PostgresArtifactReferenceStore(parent, blob_store)
    metadata_store = PostgresArtifactMetadataStore(parent, blob_store)
    gc = PostgresArtifactGarbageCollector(parent, blob_store)
    lifecycle = ArtifactLifecycleJobs(
        store=metadata_store,
        retention_purger=metadata_store,
        garbage_collector=gc,
        quarantine_reaper=gc,
    )
    return ArtifactRepositoryBundle(
        coordinator=coordinator,
        metadata_store=metadata_store,
        blob_store=blob_store,
        reference_provider=reference_store,
        garbage_collector=gc,
        retention_purger=metadata_store,
        quarantine_reaper=gc,
        canonical_outbox=metadata_store,
        lifecycle_jobs=lifecycle,
    )
