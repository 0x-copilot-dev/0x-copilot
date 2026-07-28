"""Runtime adapter composition from env-backed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)

from agent_runtime.api.artifact_repository import (
    ArtifactSourceLookupPort,
    RuntimeArtifactSourceLookup,
)
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.api.prompt_observation_store import (
    EventJournalPromptObservationStore,
)
from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
    RuntimeStoreLifecyclePort,
)
from agent_runtime.artifacts.ports import (
    ArtifactBlobStorePort,
    ArtifactGarbageCollectorPort,
    ArtifactMetadataStorePort,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.harness_quality.ports import (
    EvaluationObjectDeletionPolicy,
    EvaluationRepositoryPort,
)
from agent_runtime.control_plane.ports import (
    RunControlDecisionStorePort,
    RunControlSnapshotStorePort,
)
from agent_runtime.prompts.observation import PromptObservationStorePort
from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.persistence.ports import (
    CitationStorePort,
    ConversationToolOrdinalStorePort,
    DraftStorePort,
    ShareStorePort,
    SourceStorePort,
    SubagentStorePort,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.artifact_blob_store import (
    InMemoryArtifactBlobStore,
)
from runtime_adapters.in_memory.artifact_gc import (
    InMemoryArtifactGarbageCollector,
)
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters._artifact_repository import (
    ArtifactQuarantineReaper,
    ArtifactRepositoryBundle,
    ArtifactRetentionPurger,
)
from runtime_adapters.artifact_queue import ArtifactAwareRuntimeQueue
from runtime_adapters.artifact_lifecycle import ArtifactLifecycleJobs
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from runtime_adapters.in_memory.share_store import InMemoryShareStore
from runtime_adapters.in_memory.source_store import InMemorySourceStore
from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.artifact_store import (
    PostgresArtifactMetadataStore,
)
from runtime_adapters.artifact_references import (
    ArtifactReferenceRepositoryPort,
    FileArtifactReferenceStore,
    InMemoryArtifactReferenceStore,
    PostgresArtifactReferenceStore,
)
from runtime_adapters.postgres.artifact_gc import PostgresArtifactGarbageCollector
from runtime_adapters.postgres.conversation_tool_ordinal_store import (
    PostgresConversationToolOrdinalStore,
)
from runtime_adapters.postgres.draft_store import PostgresDraftStore
from runtime_adapters.postgres.share_store import PostgresShareStore
from runtime_adapters.postgres.source_store import PostgresSourceStore
from runtime_adapters.postgres.subagent_store import PostgresSubagentStore


def _build_file_ports(settings: RuntimeSettings) -> "RuntimePorts":
    """Construct the file-native ``RuntimePorts`` for the desktop profile.

    Fails closed (``CONFIGURATION_ERROR``) unless every precondition holds:
    ``RUNTIME_STORE_BACKEND=file`` **and**
    ``ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`` **and**
    ``RUNTIME_FILE_STORE_ROOT`` is set. The web/Postgres/in-memory paths are
    never reached from here, so their behavior is untouched.
    """

    profile = os.environ.get(ENV_DEPLOYMENT_PROFILE, "").strip().lower()
    if profile != PROFILE_SINGLE_USER_DESKTOP:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "RUNTIME_STORE_BACKEND=file requires "
            f"{ENV_DEPLOYMENT_PROFILE}={PROFILE_SINGLE_USER_DESKTOP} "
            f"(got {profile or 'unset'!r}).",
            retryable=False,
        )
    root = settings.store.file_store_root
    if not root:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "RUNTIME_FILE_STORE_ROOT is required when RUNTIME_STORE_BACKEND=file.",
            retryable=False,
        )

    # Imported lazily so the file backend (and its sqlite dependency) is only
    # pulled in for the desktop path — the web image never imports it.
    from runtime_adapters.file import (
        FileArtifactBlobStore,
        FileArtifactGarbageCollector,
        FileArtifactMetadataStore,
        FileConversationToolOrdinalStore,
        FileDraftStore,
        FileRuntimeApiStore,
        FileShareStore,
    )
    from runtime_adapters.file.artifact_publication import (
        FileArtifactPublicationCoordinator,
    )
    from runtime_adapters.file.citation_store import FileCitationStore
    from runtime_adapters.in_memory.source_store import InMemorySourceStore
    from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore

    # Boot-time state-ledger compaction is ON by default; RUNTIME_FILE_STORE_
    # COMPACTION=0/false/off is the kill switch for the persistence path.
    compaction_enabled = os.environ.get(
        "RUNTIME_FILE_STORE_COMPACTION", "true"
    ).strip().lower() not in {"0", "false", "no", "off", "disabled"}
    file_store = FileRuntimeApiStore(
        root,
        max_bytes=settings.store.file_store_max_bytes,
        retention_days=settings.store.file_store_retention_days,
        compaction_enabled=compaction_enabled,
    )
    layout = file_store.layout
    from runtime_adapters.file.evaluation_repository import FileEvaluationRepository

    evaluation_repository = FileEvaluationRepository(
        layout,
        object_store=file_store.object_store,
        object_deletion_policy=EvaluationObjectDeletionPolicy.SHARED_STORE_METADATA_ONLY,
    )
    file_store.configure_external_object_references(evaluation_repository)
    file_store.configure_source_run_deletion_observer(evaluation_repository)
    run_control_store = EventJournalRunControlStore(file_store)
    citation_store = FileCitationStore(layout)
    bundle = None
    queue: RuntimeQueuePort = file_store
    if settings.execution.artifact_effects_v2:
        artifact_coordinator = FileArtifactPublicationCoordinator(layout)
        artifact_blob_store = FileArtifactBlobStore(layout, artifact_coordinator)
        artifact_reference_store = FileArtifactReferenceStore(
            layout, artifact_coordinator
        )
        artifact_metadata_store = FileArtifactMetadataStore(
            layout, artifact_coordinator, artifact_reference_store
        )
        artifact_gc = FileArtifactGarbageCollector(
            layout, artifact_coordinator, artifact_reference_store
        )
        artifact_lifecycle = ArtifactLifecycleJobs(
            store=artifact_metadata_store,
            retention_purger=artifact_metadata_store,
            garbage_collector=artifact_gc,
            quarantine_reaper=artifact_gc,
        )
        bundle = ArtifactRepositoryBundle(
            coordinator=artifact_coordinator,
            metadata_store=artifact_metadata_store,
            blob_store=artifact_blob_store,
            reference_provider=artifact_reference_store,
            garbage_collector=artifact_gc,
            retention_purger=artifact_metadata_store,
            quarantine_reaper=artifact_gc,
            canonical_outbox=artifact_metadata_store,
            lifecycle_jobs=artifact_lifecycle,
        )
        file_store.configure_artifact_lifecycle(artifact_lifecycle)
        queue = ArtifactAwareRuntimeQueue(file_store, artifact_metadata_store)
    return RuntimePorts(
        persistence=file_store,
        event_store=file_store,
        queue=queue,
        backend="file",
        lifecycle=file_store,
        draft_store=FileDraftStore(layout),
        share_store=FileShareStore(layout),
        conversation_tool_ordinal_store=FileConversationToolOrdinalStore(layout),
        run_control_snapshot_store=run_control_store,
        run_control_decision_store=run_control_store,
        prompt_observation_store=EventJournalPromptObservationStore(
            events=file_store,
            snapshots=run_control_store,
        ),
        model_invocation_store=EventJournalModelInvocationStore(
            events=file_store,
            snapshots=run_control_store,
        ),
        # Pure projectors over the file store's file-backed materialized view.
        subagent_store=InMemorySubagentStore(file_store),
        source_store=InMemorySourceStore(citation_store),
        evaluation_repository=evaluation_repository,
        # The DURABLE FileCitationStore — the SAME instance source_store reads —
        # is now the write-side port too, so run citations persist to disk
        # instead of an ephemeral in-memory sibling.
        citation_store=citation_store,
        artifact_source_lookup=RuntimeArtifactSourceLookup(file_store),
        artifact_effects_v2=settings.execution.artifact_effects_v2,
        artifact_repository=bundle,
        artifact_metadata_store=bundle.metadata_store if bundle else None,
        artifact_blob_store=bundle.blob_store if bundle else None,
        artifact_reference_provider=bundle.reference_provider if bundle else None,
        artifact_garbage_collector=bundle.garbage_collector if bundle else None,
        artifact_retention_purger=bundle.retention_purger if bundle else None,
        artifact_quarantine_reaper=bundle.quarantine_reaper if bundle else None,
        artifact_lifecycle_jobs=bundle.lifecycle_jobs if bundle else None,
        artifact_event_publication=queue if bundle else None,
    )


@dataclass(frozen=True)
class ArtifactServiceStorageDependencies:
    """Storage-owned inputs required before the API may build ArtifactService."""

    metadata_store: ArtifactMetadataStorePort
    blob_store: ArtifactBlobStorePort
    canonical_outbox: object
    event_publication: RuntimeQueuePort


@dataclass(frozen=True)
class RuntimePorts:
    """Composed runtime ports (async-native).

    Every consumer-facing dependency is typed against a Protocol — no
    concrete class names leak. The lifespan owner drives the store via
    :attr:`lifecycle`; the satellite stores are pre-built so consumers
    never need to know which backend is wired in.
    """

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str
    lifecycle: RuntimeStoreLifecyclePort
    draft_store: DraftStorePort
    share_store: ShareStorePort
    conversation_tool_ordinal_store: ConversationToolOrdinalStorePort
    run_control_snapshot_store: RunControlSnapshotStorePort
    run_control_decision_store: RunControlDecisionStorePort
    prompt_observation_store: PromptObservationStorePort
    model_invocation_store: ModelInvocationStorePort
    subagent_store: SubagentStorePort
    source_store: SourceStorePort
    # F1 local evaluation/release spine. In-memory is test/dev only; desktop
    # and explicitly configured shared deployments use the bounded file/CAS
    # adapter.
    evaluation_repository: EvaluationRepositoryPort | None = None
    # Postgres-only escape hatch. Populated only when ``backend == "postgres"``
    # so the opt-in ``DbStatementMetricsCollector`` can reach the pool via
    # ``_role_connection``. Every other consumer should use the typed ports
    # above and stay backend-agnostic.
    postgres_store: PostgresRuntimeApiStore | None = None
    # Backend-correct citation store for the run WRITE path. Every construction
    # site sets it: the Postgres store itself (it satisfies CitationStorePort),
    # the durable ``FileCitationStore`` for the file backend (the SAME instance
    # ``source_store`` reads), or a shared ``InMemoryCitationStore``. Defaults
    # None so any legacy constructor still works — the worker then falls back to
    # its historical isinstance resolution.
    citation_store: CitationStorePort | None = None
    # Exact logical-source lookup is composed for every runtime backend. It is
    # independent of artifact storage and never scans message/event history.
    artifact_source_lookup: ArtifactSourceLookupPort | None = None
    # SDR-19 rollout seam. Every field remains ``None`` when the single gate is
    # off, preserving legacy constructors and startup behavior.
    artifact_effects_v2: bool = False
    artifact_repository: ArtifactRepositoryBundle | None = None
    artifact_metadata_store: ArtifactMetadataStorePort | None = None
    artifact_blob_store: ArtifactBlobStorePort | None = None
    artifact_reference_provider: ArtifactReferenceRepositoryPort | None = None
    artifact_garbage_collector: ArtifactGarbageCollectorPort | None = None
    artifact_retention_purger: ArtifactRetentionPurger | None = None
    artifact_quarantine_reaper: ArtifactQuarantineReaper | None = None
    artifact_lifecycle_jobs: ArtifactLifecycleJobs | None = None
    artifact_event_publication: RuntimeQueuePort | None = None

    def __post_init__(self) -> None:
        """Fail during composition instead of serving an enabled 503."""

        self.require_artifact_service_storage()

    def require_artifact_repository(self) -> ArtifactRepositoryBundle | None:
        """Return the cohesive enabled bundle, or ``None`` for a clean OFF state."""

        named = {
            "repository": self.artifact_repository,
            "metadata": self.artifact_metadata_store,
            "blob": self.artifact_blob_store,
            "reference": self.artifact_reference_provider,
            "collector": self.artifact_garbage_collector,
            "retention": self.artifact_retention_purger,
            "reaper": self.artifact_quarantine_reaper,
            "lifecycle": self.artifact_lifecycle_jobs,
            "event_publication": self.artifact_event_publication,
        }
        if not self.artifact_effects_v2:
            if any(value is not None for value in named.values()):
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "Artifact repository dependencies must be absent when "
                    "ARTIFACT_EFFECTS_V2 is disabled.",
                    retryable=False,
                )
            return None
        missing = tuple(name for name, value in named.items() if value is None)
        if missing:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "ARTIFACT_EFFECTS_V2 requires a complete artifact repository "
                f"composition; missing {', '.join(missing)}.",
                retryable=False,
            )
        bundle = self.artifact_repository
        assert bundle is not None
        identities = (
            bundle.metadata_store is self.artifact_metadata_store,
            bundle.blob_store is self.artifact_blob_store,
            bundle.reference_provider is self.artifact_reference_provider,
            bundle.garbage_collector is self.artifact_garbage_collector,
            bundle.retention_purger is self.artifact_retention_purger,
            bundle.quarantine_reaper is self.artifact_quarantine_reaper,
            bundle.lifecycle_jobs is self.artifact_lifecycle_jobs,
            bundle.canonical_outbox is self.artifact_metadata_store,
            self.artifact_event_publication is self.queue,
        )
        if not all(identities):
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "ARTIFACT_EFFECTS_V2 requires one cohesive artifact repository "
                "and event-publication composition.",
                retryable=False,
            )
        return bundle

    def require_artifact_service_storage(
        self,
    ) -> ArtifactServiceStorageDependencies | None:
        """Minimal fail-fast invariant consumed by the API composition lane."""

        bundle = self.require_artifact_repository()
        if bundle is None:
            return None
        assert self.artifact_metadata_store is not None
        assert self.artifact_blob_store is not None
        assert self.artifact_event_publication is not None
        return ArtifactServiceStorageDependencies(
            metadata_store=self.artifact_metadata_store,
            blob_store=self.artifact_blob_store,
            canonical_outbox=bundle.canonical_outbox,
            event_publication=self.artifact_event_publication,
        )


class RuntimeAdapterFactory:
    """Build runtime adapters for API and worker processes."""

    @classmethod
    def from_store(
        cls,
        store: InMemoryRuntimeApiStore,
        *,
        artifact_effects_v2: bool = False,
    ) -> RuntimePorts:
        """Build a minimal in-memory :class:`RuntimePorts` from an existing store.

        Tests use this helper to construct ports without coupling to
        coordinator internals.  Every satellite store is freshly
        constructed so they share no state with other test instances.
        """
        in_memory_citation = InMemoryCitationStore()
        run_control_store = EventJournalRunControlStore(store)
        bundle = cls._in_memory_artifact_bundle() if artifact_effects_v2 else None
        queue: RuntimeQueuePort = store
        if bundle is not None:
            store.configure_artifact_lifecycle(bundle.lifecycle_jobs)
            queue = ArtifactAwareRuntimeQueue(store, bundle.metadata_store)
        return RuntimePorts(
            persistence=store,
            event_store=store,
            queue=queue,
            backend="in_memory",
            lifecycle=store,
            draft_store=InMemoryDraftStore(),
            share_store=InMemoryShareStore(),
            conversation_tool_ordinal_store=InMemoryConversationToolOrdinalStore(),
            run_control_snapshot_store=run_control_store,
            run_control_decision_store=run_control_store,
            prompt_observation_store=EventJournalPromptObservationStore(
                events=store,
                snapshots=run_control_store,
            ),
            model_invocation_store=EventJournalModelInvocationStore(
                events=store,
                snapshots=run_control_store,
            ),
            subagent_store=InMemorySubagentStore(store),
            # One citation store shared by the read-side projector and the
            # write-side port, so a run's citations are visible to Sources.
            source_store=InMemorySourceStore(in_memory_citation),
            evaluation_repository=InMemoryEvaluationRepository(),
            citation_store=in_memory_citation,
            artifact_source_lookup=RuntimeArtifactSourceLookup(store),
            artifact_effects_v2=artifact_effects_v2,
            artifact_repository=bundle,
            artifact_metadata_store=bundle.metadata_store if bundle else None,
            artifact_blob_store=bundle.blob_store if bundle else None,
            artifact_reference_provider=bundle.reference_provider if bundle else None,
            artifact_garbage_collector=bundle.garbage_collector if bundle else None,
            artifact_retention_purger=bundle.retention_purger if bundle else None,
            artifact_quarantine_reaper=bundle.quarantine_reaper if bundle else None,
            artifact_lifecycle_jobs=bundle.lifecycle_jobs if bundle else None,
            artifact_event_publication=queue if bundle else None,
        )

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, *, role: str = "api"
    ) -> RuntimePorts:
        """Construct and return all runtime ports from application settings.

        ``role`` is stamped on the pool's ``application_name`` so connections
        are identifiable in ``pg_stat_activity``. The caller must open and
        close the pool via ``ports.lifecycle``.
        """

        backend = settings.store.backend
        # When the SSE bus uses Postgres LISTEN/NOTIFY, the adapter must fire a
        # NOTIFY after every event append so the API process's listener wakes
        # the SSE handler cross-process.  The in-memory bus uses asyncio.Condition
        # and does not need an explicit notification.
        notify_after_append = settings.resolved_event_bus_backend() == "postgres"
        # ``in_memory`` is the legacy alias for ``in_memory_async`` — both
        # route to the async-native InMemoryRuntimeApiStore.
        if backend in {"in_memory_async", "in_memory"}:
            in_memory_store = InMemoryRuntimeApiStore()
            run_control_store = EventJournalRunControlStore(in_memory_store)
            in_memory_citation = InMemoryCitationStore()
            bundle = (
                cls._in_memory_artifact_bundle()
                if settings.execution.artifact_effects_v2
                else None
            )
            queue: RuntimeQueuePort = in_memory_store
            if bundle is not None:
                in_memory_store.configure_artifact_lifecycle(bundle.lifecycle_jobs)
                queue = ArtifactAwareRuntimeQueue(
                    in_memory_store, bundle.metadata_store
                )
            return RuntimePorts(
                persistence=in_memory_store,
                event_store=in_memory_store,
                queue=queue,
                backend=backend,
                lifecycle=in_memory_store,
                draft_store=InMemoryDraftStore(),
                share_store=InMemoryShareStore(),
                conversation_tool_ordinal_store=InMemoryConversationToolOrdinalStore(),
                run_control_snapshot_store=run_control_store,
                run_control_decision_store=run_control_store,
                prompt_observation_store=EventJournalPromptObservationStore(
                    events=in_memory_store,
                    snapshots=run_control_store,
                ),
                model_invocation_store=EventJournalModelInvocationStore(
                    events=in_memory_store,
                    snapshots=run_control_store,
                ),
                subagent_store=InMemorySubagentStore(in_memory_store),
                # Shared instance: read-side projector and write-side port agree.
                source_store=InMemorySourceStore(in_memory_citation),
                evaluation_repository=InMemoryEvaluationRepository(),
                citation_store=in_memory_citation,
                artifact_source_lookup=RuntimeArtifactSourceLookup(in_memory_store),
                artifact_effects_v2=settings.execution.artifact_effects_v2,
                artifact_repository=bundle,
                artifact_metadata_store=bundle.metadata_store if bundle else None,
                artifact_blob_store=bundle.blob_store if bundle else None,
                artifact_reference_provider=(
                    bundle.reference_provider if bundle else None
                ),
                artifact_garbage_collector=(
                    bundle.garbage_collector if bundle else None
                ),
                artifact_retention_purger=(bundle.retention_purger if bundle else None),
                artifact_quarantine_reaper=(
                    bundle.quarantine_reaper if bundle else None
                ),
                artifact_lifecycle_jobs=bundle.lifecycle_jobs if bundle else None,
                artifact_event_publication=queue if bundle else None,
            )
        if backend == "postgres":
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
            postgres_store = PostgresRuntimeApiStore(
                settings.store.database_url,
                role=role,
                notify_after_append=notify_after_append,
            )
            run_control_store = EventJournalRunControlStore(postgres_store)
            bundle = (
                cls._postgres_artifact_bundle(postgres_store, artifact_blob_root or "")
                if settings.execution.artifact_effects_v2
                else None
            )
            if bundle is not None:
                postgres_store.configure_artifact_lifecycle(bundle.lifecycle_jobs)
            return RuntimePorts(
                persistence=postgres_store,
                event_store=postgres_store,
                queue=postgres_store,
                backend=backend,
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
                evaluation_repository=cls._shared_evaluation_repository(settings),
                postgres_store=postgres_store,
                # The Postgres store IS a CitationStorePort — same instance the
                # worker resolved historically, so write behavior is unchanged.
                citation_store=postgres_store,
                artifact_source_lookup=RuntimeArtifactSourceLookup(postgres_store),
                artifact_effects_v2=settings.execution.artifact_effects_v2,
                artifact_repository=bundle,
                artifact_metadata_store=bundle.metadata_store if bundle else None,
                artifact_blob_store=bundle.blob_store if bundle else None,
                artifact_reference_provider=(
                    bundle.reference_provider if bundle else None
                ),
                artifact_garbage_collector=(
                    bundle.garbage_collector if bundle else None
                ),
                artifact_retention_purger=(bundle.retention_purger if bundle else None),
                artifact_quarantine_reaper=(
                    bundle.quarantine_reaper if bundle else None
                ),
                artifact_lifecycle_jobs=bundle.lifecycle_jobs if bundle else None,
                artifact_event_publication=postgres_store if bundle else None,
            )
        if backend == "file":
            return _build_file_ports(settings)
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported async runtime store backend '{backend}'. "
            "Use 'in_memory_async', 'postgres', or 'file'.",
            retryable=False,
        )

    @staticmethod
    def _shared_evaluation_repository(
        settings: RuntimeSettings,
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
        from runtime_adapters.file._paths import FileStoreLayout
        from runtime_adapters.file._capacity import QuotaGuard
        from runtime_adapters.file.evaluation_repository import (
            FileEvaluationRepository,
        )
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

    @staticmethod
    def _postgres_artifact_bundle(parent, root: str) -> ArtifactRepositoryBundle:
        """Construct one shared coordinator/blob instance for Postgres."""

        from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
        from runtime_adapters.file._paths import FileStoreLayout
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

    @staticmethod
    def _in_memory_artifact_bundle() -> ArtifactRepositoryBundle:
        coordinator = InMemoryArtifactPublicationCoordinator()
        blob_store = InMemoryArtifactBlobStore(coordinator)
        reference_store = InMemoryArtifactReferenceStore(coordinator)
        metadata_store = InMemoryArtifactMetadataStore(coordinator, reference_store)
        gc = InMemoryArtifactGarbageCollector(
            coordinator, metadata_store, reference_store
        )
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
