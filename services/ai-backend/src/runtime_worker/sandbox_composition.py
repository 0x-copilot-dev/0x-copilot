"""Filesystem-first composition for the model-visible D3 sandbox tool.

This is the one worker boundary permitted to join the C1 retained-overlay
authority, A2 artifact bytes, D3 file records, a provider-attested lifecycle
coordinator, and the operation-gateway adapter.  It deliberately fails closed:
there is no hosted/Postgres branch, no in-memory lifecycle fallback, no direct
``session_scope``/``aexecute`` model tool, and no automatic C1 patch import.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.artifacts.service import ArtifactService
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.sandbox.config import (
    RemoteSandboxConfig,
    SandboxLimitProfile,
)
from agent_runtime.capabilities.sandbox.coordinator import SandboxLifecycleCoordinator
from agent_runtime.capabilities.sandbox.execute_tool import (
    SandboxExecuteToolFactory,
    SandboxRunIdentityProvider,
)
from agent_runtime.capabilities.sandbox.lifecycle import FileSandboxLifecycleStore
from agent_runtime.capabilities.sandbox.operation_adapter import (
    SandboxOperationAdapter,
    SandboxOperationAvailability,
    sandbox_operation_descriptor,
)
from agent_runtime.capabilities.sandbox.operation_runner import (
    SandboxLifecycleOperationRunner,
    SandboxSnapshotStoreContentSource,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxPatchCollectorPort,
    SandboxProviderPort,
)
from agent_runtime.capabilities.sandbox.providers.openai_hosted import (
    OpenAIHostedContainerClient,
    OpenAIHostedContainerProvider,
)
from agent_runtime.capabilities.sandbox.result_publisher import (
    ArtifactServiceSandboxResultPublisher,
)
from agent_runtime.capabilities.sandbox.artifact_publisher import (
    ArtifactServiceSandboxPublisher,
)
from agent_runtime.capabilities.sandbox.patch_collector import (
    DeepAgentArtifactPatchCollector,
)
from agent_runtime.capabilities.sandbox.runtime_adapter import DeepAgentSandboxRuntime
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
)
from agent_runtime.capabilities.sandbox.seam import build_sandbox_backend
from agent_runtime.capabilities.sandbox.session_store import FileSandboxSessionStore
from agent_runtime.capabilities.sandbox.snapshot_file_store import (
    SandboxSnapshotIdentity,
    SealedSandboxSnapshotFileStore,
    TrustedSandboxSnapshotPlanProvider,
    WorkspaceOverlaySandboxSnapshotFileResolver,
)
from agent_runtime.capabilities.sandbox.snapshot import SandboxSnapshotPlanProvider
from agent_runtime.capabilities.sandbox.usage_meter import FileSandboxUsageMeter
from agent_runtime.capabilities.sandbox.cleanup_store import FileSandboxCleanupStore
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
)
from agent_runtime.capabilities.workspace.ports import WorkspaceOverlayStorePort
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_worker.sandbox_snapshot_authority import (
    RuntimeWorkerOverlaySnapshotPlanAuthority,
    SandboxVerifiedRunStorePort,
)


_DESKTOP_PROFILE = "single_user_desktop"
_DEPLOYMENT_PROFILE_ENV = "ENTERPRISE_DEPLOYMENT_PROFILE"


@dataclass(frozen=True)
class FileSandboxAuthorityPrerequisites:
    """The three independently-owned prerequisites for model-visible D3.

    This object is intentionally resolved only by the file-native composition
    root.  It is not an injectable test/config switch: a provider double or an
    A2-shaped object must never turn on a model capability before the actual
    C1/A2 implementations exist.  The current runtime has none of these
    complete authorities, so ``resolve`` returns ``None`` and D3 remains dark.

    Future work must replace the ``None`` with concrete, verified adapters
    owned by C1 and A2—not a protocol-shaped mock—and bind all three together:

    * a retained full C1 base-plus-overlay snapshot exporter;
    * a durable A2 result-and-deliverable publisher; and
    * an explicit user-triggered C1 patch importer.
    """

    full_c1_snapshot_exporter: object
    durable_a2_deliverable_publisher: object
    user_triggered_c1_patch_importer: object

    @classmethod
    def resolve(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        layout: FileStoreLayout,
    ) -> "FileSandboxAuthorityPrerequisites | None":
        """Resolve only complete concrete production authority.

        The present C1 authority exports retained overlay entries only, A2 is
        not bound to requested deliverables, and no user-triggered C1 importer
        consumes a sandbox patch.  Deliberately do not synthesize partial
        objects here: returning ``None`` is the sole honest model posture.
        ``runtime_context`` and ``layout`` stay in this private resolution
        boundary so the future implementation derives scope from verified
        worker facts rather than model arguments.
        """

        del runtime_context, layout
        return None


@dataclass(frozen=True)
class SandboxWorkerBundle:
    """One complete, file-native factory for ``run_in_sandbox``.

    Its fields are intentionally private: callers may ask only for a
    gateway-routed model tool.  They cannot reach a provider service, a file
    layout, a snapshot resolver, or a patch importer through this object.
    ``cleanup_store`` is retained as part of the D3 file-native bundle for the
    separate janitor/recovery loop; command completion itself never imports a
    patch or writes a host workspace.
    """

    _gateway: OperationGateway
    _adapter: SandboxOperationAdapter
    _snapshot_provider: SandboxSnapshotPlanProvider
    _cleanup_store: FileSandboxCleanupStore

    @classmethod
    def compose(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        file_store: object | None,
        artifact_service: ArtifactService | None,
        artifact_blob_store: ArtifactBlobStorePort | None,
        workspace_overlay_store: WorkspaceOverlayStorePort | None,
        run_store: SandboxVerifiedRunStorePort | None,
        patch_collector: SandboxPatchCollectorPort | None = None,
        env: Mapping[str, str] | None = None,
        provider_overrides: (
            Mapping[SandboxProviderId, SandboxProviderPort] | None
        ) = None,
        openai_hosted_container_client: OpenAIHostedContainerClient | None = None,
    ) -> "SandboxWorkerBundle | None":
        """Return the complete factory or ``None`` when any authority is absent.

        This makes the only production posture explicit: file-native desktop
        plus C1 retained versions, A2 blob/result authority, a complete patch
        collector, and an isolation-ready provider.  A non-file runtime never
        substitutes Postgres or memory persistence.
        """

        values = dict(env) if env is not None else _environment()
        if values.get(_DEPLOYMENT_PROFILE_ENV, "") != _DESKTOP_PROFILE:
            return None
        layout = _file_layout(file_store)
        if layout is None:
            return None
        prerequisites = FileSandboxAuthorityPrerequisites.resolve(
            runtime_context=runtime_context,
            layout=layout,
        )
        if prerequisites is None:
            # Keep the descriptor/tool absent until C1 and A2 supply the full
            # authority bundle above.  Provider overrides and artifact-service
            # doubles are intentionally unable to change this answer.
            return None
        if (
            artifact_service is None
            or artifact_blob_store is None
            or workspace_overlay_store is None
            or run_store is None
            or not _has_overlay_history_authority(workspace_overlay_store)
            or not _has_blob_authority(artifact_blob_store)
            or not _has_verified_run_authority(run_store)
        ):
            return None
        runtime = FileSandboxWorkerRuntime.compose(
            file_store=file_store,
            env=values,
            provider_overrides=provider_overrides,
            openai_hosted_container_client=openai_hosted_container_client,
        )
        if runtime is None:
            return None
        identity = SandboxSnapshotIdentity(
            run_id=runtime_context.run_id,
            org_id=runtime_context.org_id,
            user_id=runtime_context.user_id,
        )

        # C1's authority returns only exact retained overlay versions for this
        # verified run.  The resolver requires the same bound identity again;
        # neither model input nor a current/latest manifest reaches this path.
        authority = RuntimeWorkerOverlaySnapshotPlanAuthority(
            overlay_store=workspace_overlay_store,
            run_store=run_store,
        )
        snapshot_provider = _BoundSnapshotPlanProvider(
            identity=identity,
            delegate=TrustedSandboxSnapshotPlanProvider(authority=authority),
        )
        overlay_resolver = WorkspaceOverlaySandboxSnapshotFileResolver(
            identity=identity,
            overlay_store=workspace_overlay_store,
            blob_store=artifact_blob_store,
        )
        # The plan authority currently emits C1 overlays only.  This adapter
        # narrows the generic C1/A2 source port to that factual contract rather
        # than fabricating an artifact-metadata or latest-view fallback.
        source_store = _OverlaySnapshotFileStore(resolver=overlay_resolver)
        snapshot_store = SealedSandboxSnapshotFileStore(
            source=source_store,
            root=_sealed_snapshot_root(layout=layout, run_id=identity.run_id),
            max_entry_bytes=runtime.limits.max_upload_file_bytes,
        )
        resolved_patch_collector = patch_collector or DeepAgentArtifactPatchCollector(
            publisher=ArtifactServiceSandboxPublisher(
                service=artifact_service,
                org_id=identity.org_id,
                user_id=identity.user_id,
            ),
            limits=runtime.limits,
        )
        coordinator = SandboxLifecycleCoordinator(
            service=runtime.service,
            lifecycle_store=runtime.lifecycle_store,
            runtime=DeepAgentSandboxRuntime(),
            usage_meter=FileSandboxUsageMeter(root=layout.root / "sandbox" / "usage"),
            snapshot_source=SandboxSnapshotStoreContentSource(store=snapshot_store),
            # Overlay snapshots require a complete provider-specific listing.
            # The file-native collector is constructed above (or explicitly
            # injected by a trusted extension), so a command cannot mutate an
            # overlay snapshot without returning a reviewable artifact patch.
            patch_collector=resolved_patch_collector,
            limits=runtime.limits,
        )
        runner = SandboxLifecycleOperationRunner(
            coordinator=coordinator,
            result_publisher=ArtifactServiceSandboxResultPublisher(
                service=artifact_service,
                org_id=identity.org_id,
                user_id=identity.user_id,
            ),
            limits=runtime.limits,
            availability=SandboxOperationAvailability(available=True),
        )
        adapter = SandboxOperationAdapter(
            runner=runner,
            snapshot_store=snapshot_store,
        )
        return cls(
            _gateway=OperationGateway(
                descriptors=OperationDescriptorRegistry(
                    entries=(sandbox_operation_descriptor(),)
                )
            ),
            _adapter=adapter,
            _snapshot_provider=snapshot_provider,
            _cleanup_store=runtime.cleanup_store,
        )

    def build_tool(self, *, identity_provider: Callable[[], object]) -> object | None:
        """Create only the descriptor/gateway-routed model tool."""

        return SandboxExecuteToolFactory.build(
            gateway=self._gateway,
            adapter=self._adapter,
            identity_provider=cast(SandboxRunIdentityProvider, identity_provider),
            snapshot_provider=self._snapshot_provider,
        )


@dataclass(frozen=True)
class FileSandboxWorkerRuntime:
    """Concrete desktop-file lifecycle ownership shared by tool + reaper.

    ``FileRuntimeApiStore`` is intentionally checked by concrete type.  A
    protocol-compatible in-memory/Postgres object never becomes a D3 authority.
    """

    limits: SandboxLimitProfile
    service: RemoteExecutionService
    lifecycle_store: FileSandboxLifecycleStore
    cleanup_store: FileSandboxCleanupStore

    @classmethod
    def compose(
        cls,
        *,
        file_store: object | None,
        env: Mapping[str, str] | None = None,
        provider_overrides: (
            Mapping[SandboxProviderId, SandboxProviderPort] | None
        ) = None,
        openai_hosted_container_client: OpenAIHostedContainerClient | None = None,
    ) -> "FileSandboxWorkerRuntime | None":
        values = dict(env) if env is not None else _environment()
        if values.get(_DEPLOYMENT_PROFILE_ENV, "") != _DESKTOP_PROFILE:
            return None
        layout = _file_layout(file_store)
        if layout is None:
            return None
        try:
            config = RemoteSandboxConfig.from_env(values)
            limits = config.resolve_limits()
            cleanup_store = FileSandboxCleanupStore(layout=layout)
            resolved_overrides = dict(provider_overrides or {})
            if (
                config.provider is SandboxProviderId.OPENAI_HOSTED_CONTAINER
                and SandboxProviderId.OPENAI_HOSTED_CONTAINER not in resolved_overrides
            ):
                if (
                    openai_hosted_container_client is None
                    or config.openai_hosted_container is None
                ):
                    return None
                resolved_overrides[SandboxProviderId.OPENAI_HOSTED_CONTAINER] = (
                    OpenAIHostedContainerProvider(
                        config=config.openai_hosted_container,
                        client=openai_hosted_container_client,
                    )
                )
            service = build_sandbox_backend(
                config,
                provider_overrides=resolved_overrides,
                session_store=FileSandboxSessionStore(layout=layout),
                cleanup_store=cleanup_store,
            )
        except Exception:  # noqa: BLE001 - deployment configuration fails closed
            return None
        if service is None:
            return None
        return cls(
            limits=limits,
            service=service,
            lifecycle_store=FileSandboxLifecycleStore(
                root=layout.root / "sandbox" / "lifecycle"
            ),
            cleanup_store=cleanup_store,
        )


@dataclass(frozen=True)
class FileSandboxRecoveryReaper:
    """Drain durable provider teardown duties on worker start and each loop."""

    runtime: FileSandboxWorkerRuntime

    @classmethod
    def compose(
        cls,
        *,
        file_store: object | None,
        env: Mapping[str, str] | None = None,
        provider_overrides: (
            Mapping[SandboxProviderId, SandboxProviderPort] | None
        ) = None,
        openai_hosted_container_client: OpenAIHostedContainerClient | None = None,
    ) -> "FileSandboxRecoveryReaper | None":
        runtime = FileSandboxWorkerRuntime.compose(
            file_store=file_store,
            env=env,
            provider_overrides=provider_overrides,
            openai_hosted_container_client=openai_hosted_container_client,
        )
        return cls(runtime=runtime) if runtime is not None else None

    async def run_once(self, *, limit: int = 100) -> tuple[str, ...]:
        cleaned: list[str] = []
        now = datetime.now(UTC)
        for duty in await self.runtime.cleanup_store.list_pending(limit=limit):
            if duty.retry_not_before > now:
                continue
            if duty.state == "provisioning":
                was_cleaned = (
                    await self.runtime.service.cleanup_provisioning_reservation(
                        run_id=duty.run_id,
                        owner_marker=duty.owner_marker or "",
                        operation_id=duty.operation_id,
                    )
                )
            else:
                if (
                    duty.provider_session_ref is None
                ):  # pragma: no cover - contract invariant
                    continue
                was_cleaned = await self.runtime.service.cleanup_provider_ref(
                    run_id=duty.run_id,
                    provider_session_ref=duty.provider_session_ref,
                    operation_id=duty.operation_id,
                )
            if was_cleaned:
                cleaned.append(duty.operation_id)
                continue
            await self.runtime.cleanup_store.transition(
                record=duty.model_copy(
                    update={
                        "attempts": duty.attempts + 1,
                        "transition_no": duty.transition_no + 1,
                        "retry_not_before": now + timedelta(seconds=1),
                        "updated_at": now,
                        "error_summary": "provider teardown pending",
                    }
                ),
                expected_transition_no=duty.transition_no,
            )
        return tuple(cleaned)


@dataclass(frozen=True)
class _OverlaySnapshotFileStore:
    """C1-only source store used before the sealed-byte boundary.

    It deliberately has no artifact-revision branch: C1's actual worker
    authority emits only retained overlay refs today.  Adding an unscoped A2
    metadata lookup here would weaken, not complete, the stated authority.
    """

    resolver: WorkspaceOverlaySandboxSnapshotFileResolver

    async def resolve(self, *, source, virtual_path):  # noqa: ANN001
        from agent_runtime.capabilities.sandbox.snapshot import (  # noqa: PLC0415
            SandboxSnapshotSourceKind,
        )

        if source.kind is not SandboxSnapshotSourceKind.OVERLAY:
            return None
        return await self.resolver.resolve_overlay_file(
            overlay_ref=source.source_ref,
            virtual_path=virtual_path,
        )

    async def open(self, *, content_ref: str):
        return await self.resolver.open(content_ref=content_ref)


@dataclass(frozen=True)
class _BoundSnapshotPlanProvider(SandboxSnapshotPlanProvider):
    """Pin C1 selection to the handler's verified run identity.

    ``SandboxExecuteToolFactory`` accepts an identity provider solely to create
    the canonical operation request.  That value must never be able to select
    a different C1 overlay.  The worker-owned bundle therefore checks it
    against the verified identity captured at composition, then delegates using
    only the captured values.
    """

    identity: SandboxSnapshotIdentity
    delegate: TrustedSandboxSnapshotPlanProvider

    async def snapshot_for(self, *, run_id, org_id, user_id):  # noqa: ANN001
        if (
            run_id != self.identity.run_id
            or org_id != self.identity.org_id
            or user_id != self.identity.user_id
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            )
        return await self.delegate.snapshot_for(
            run_id=self.identity.run_id,
            org_id=self.identity.org_id,
            user_id=self.identity.user_id,
        )


def _file_layout(file_store: object | None) -> FileStoreLayout | None:
    """Return only the concrete file-runtime layout trusted by desktop D3."""

    if not isinstance(file_store, FileRuntimeApiStore):
        return None
    return file_store.layout


def _has_overlay_history_authority(store: object) -> bool:
    return callable(getattr(store, "get_manifest", None)) and callable(
        getattr(store, "get_manifest_version", None)
    )


def _has_blob_authority(store: object) -> bool:
    return callable(getattr(store, "stat", None)) and callable(
        getattr(store, "open_stream", None)
    )


def _has_verified_run_authority(store: object) -> bool:
    return callable(getattr(store, "get_run", None))


def _sealed_snapshot_root(*, layout: FileStoreLayout, run_id: str) -> Path:
    return (
        layout.root / "sandbox" / "sealed-snapshots" / FileStoreLayout.safe_key(run_id)
    )


def _environment() -> dict[str, str]:
    import os  # noqa: PLC0415

    return dict(os.environ)


__all__ = (
    "FileSandboxAuthorityPrerequisites",
    "FileSandboxRecoveryReaper",
    "FileSandboxWorkerRuntime",
    "SandboxWorkerBundle",
)
