"""Adversarial coverage for the filesystem-only D3 worker composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.sandbox.contracts import SandboxProviderId
from agent_runtime.capabilities.sandbox.cleanup_store import SandboxCleanupSchedule
from agent_runtime.capabilities.sandbox.ports import (
    SandboxPatchCollection,
    SandboxPatchCollectorPort,
)
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
    content_ref_for_blob,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from runtime_worker.capability_tool_wiring import CapabilityToolWiring
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker
from runtime_api.schemas import RuntimeRunCommand
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from runtime_worker.sandbox_composition import (
    FileSandboxRecoveryReaper,
    SandboxWorkerBundle,
)
from tests.unit.agent_runtime.capabilities.sandbox.fakes import (
    FakeSandboxProvider,
    make_request,
)


_ENV = {
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
    "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
    "RUNTIME_SANDBOX_PROVIDER": "langsmith",
    "RUNTIME_SANDBOX_REGION": "test-region",
}


def _context(
    *, run_id: str = "run_a", org_id: str = "org_a", user_id: str = "user_a"
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id,
        org_id=org_id,
        roles={"member"},
        model_profile=ModelConfig(
            provider="fake",
            model_name="fake-model",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id=run_id,
    )


@dataclass
class _FileStore:
    layout: FileStoreLayout
    object_store: object


@dataclass
class _RunStore:
    run: object

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        if (
            getattr(self.run, "org_id", None) == org_id
            and getattr(self.run, "run_id", None) == run_id
        ):
            return self.run
        return None


def _persisted_run(context: AgentRuntimeContext) -> object:
    return SimpleNamespace(
        run_id=context.run_id,
        org_id=context.org_id,
        user_id=context.user_id,
        conversation_id="conv_a",
        runtime_context=context,
    )


class _ArtifactService:
    """Minimal A2-shaped publisher fake; records no source bytes externally."""

    def __init__(self) -> None:
        self.published: list[bytes] = []

    async def publish_from_stream(self, *, request, chunks, **_kwargs):
        body = b"".join([chunk async for chunk in chunks])
        assert hashlib.sha256(body).hexdigest() == request.expected_digest
        self.published.append(body)
        revision = SimpleNamespace(
            artifact_id="art_550e8400-e29b-41d4-a716-446655440000",
            content_digest=request.expected_digest,
            byte_size=len(body),
            content_ref=(
                "artifact://art_550e8400-e29b-41d4-a716-446655440000/"
                f"revisions/{len(self.published)}"
            ),
        )
        return SimpleNamespace(
            record=SimpleNamespace(current_revision=SimpleNamespace(revision=revision))
        )


class _Dependencies:
    def __init__(self) -> None:
        self.update: dict[str, object] | None = None

    def model_copy(self, *, update: dict[str, object]):
        self.update = update
        return self


class _CompletePatchCollector(SandboxPatchCollectorPort):
    async def collect(self, *, active, request) -> SandboxPatchCollection:
        del active, request
        return SandboxPatchCollection(result_entries={}, complete=True)


class _RecordingProvider(FakeSandboxProvider):
    """An online provider fake that records every byte its backend receives."""

    def __init__(self) -> None:
        super().__init__()
        self.received_bytes = 0
        self.received_paths: list[str] = []

    async def create(self, request):
        handle = await super().create(request)
        original_upload = handle.backend.upload_files

        def _record_upload(files):
            self.received_bytes += sum(len(content) for _path, content in files)
            self.received_paths.extend(path for path, _content in files)
            return original_upload(files)

        handle.backend.upload_files = _record_upload
        return handle


class _UnattestedProvider(_RecordingProvider):
    @property
    def isolation_ready(self) -> bool:
        return False


class _TransientTeardownProvider(_RecordingProvider):
    """Fails one recovery pass, then permits the restarted worker to drain it."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_teardown = True

    async def terminate(self, provider_session_ref: str) -> None:
        if self.fail_teardown:
            raise OSError("simulated provider outage")
        await super().terminate(provider_session_ref)


@dataclass
class _HistoryGapOverlayStore:
    manifest: OverlayManifest

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        assert run_id == self.manifest.run_id
        return self.manifest

    async def get_manifest_version(self, *, run_id: str, version: int):
        assert run_id == self.manifest.run_id
        assert version == self.manifest.version
        return None


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _seed_overlay(
    *,
    overlays: InMemoryWorkspaceOverlayStore,
    blobs: InMemoryArtifactBlobStore,
    run_id: str,
    content: bytes,
) -> None:
    stored = await blobs.put_stream(
        expected_digest=None,
        chunks=_chunks(content),
        byte_limit=1024 * 1024,
    )
    await overlays.append_revision(
        run_id=run_id,
        expected_version=0,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path="/workspace/project/report.csv",
                entry=OverlayEntry(
                    virtual_path="/workspace/project/report.csv",
                    entry_kind=WorkspaceEntryKind.FILE,
                    operation=WorkspaceOperation.CREATE,
                    content_ref=content_ref_for_blob(stored.blob_key),
                    content_digest=stored.content_digest,
                    byte_size=stored.byte_size,
                    baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
                    author="agent",
                ),
            ),
        ),
    )


def _bundle(
    *,
    tmp_path,
    context: AgentRuntimeContext,
    blobs=None,
    overlays=None,
    artifacts=None,
    collector: SandboxPatchCollectorPort | None = None,
    provider: FakeSandboxProvider | None = None,
    run_store: _RunStore | None = None,
    env=None,
):
    return SandboxWorkerBundle.compose(
        runtime_context=context,
        file_store=FileRuntimeApiStore(tmp_path / "agent-data"),
        artifact_service=artifacts or _ArtifactService(),  # type: ignore[arg-type]
        artifact_blob_store=blobs or InMemoryArtifactBlobStore(),
        workspace_overlay_store=overlays or InMemoryWorkspaceOverlayStore(),
        run_store=run_store or _RunStore(_persisted_run(context)),
        patch_collector=collector,
        env=env or _ENV,
        provider_overrides={
            SandboxProviderId.LANGSMITH: provider or _RecordingProvider()
        },
    )


class TestSandboxWorkerBundle:
    def test_partial_file_native_foundation_keeps_the_model_tool_absent(
        self, tmp_path
    ) -> None:
        """No fake provider/A2 surface can bypass the three authority gates."""

        context = _context()
        legacy = CapabilityToolWiring(runtime_context=context, env=_ENV)
        assert legacy.sandbox_execute_tool() is None

        bundle = _bundle(tmp_path=tmp_path, context=context)
        assert bundle is None
        construction = inspect.getsource(CapabilityToolWiring.sandbox_execute_tool)
        assert "build_sandbox_backend" not in construction
        assert "RemoteExecutionService" not in construction

    async def test_fakes_cannot_make_partial_d3_authority_model_visible(
        self, tmp_path
    ) -> None:
        """A test provider/A2-shaped service is never a production authority."""

        context = _context()
        blobs = InMemoryArtifactBlobStore()
        overlays = InMemoryWorkspaceOverlayStore()
        await _seed_overlay(
            overlays=overlays, blobs=blobs, run_id="run_a", content=b"allowed"
        )
        provider = _RecordingProvider()
        artifacts = _ArtifactService()

        assert (
            _bundle(
                tmp_path=tmp_path,
                context=context,
                blobs=blobs,
                overlays=overlays,
                artifacts=artifacts,
                provider=provider,
                collector=_CompletePatchCollector(),
            )
            is None
        )
        assert provider.create_calls == 0
        assert provider.received_bytes == 0
        assert artifacts.published == []

    async def test_retained_history_loss_remains_a_separate_authority_failure(
        self, tmp_path
    ) -> None:
        """Partial composition remains dark; C1 pointer loss is still not empty."""

        context = _context()
        provider = _RecordingProvider()
        assert (
            _bundle(
                tmp_path=tmp_path,
                context=context,
                overlays=_HistoryGapOverlayStore(
                    manifest=OverlayManifest(run_id="run_a", version=1)
                ),
                provider=provider,
            )
            is None
        )
        assert provider.create_calls == 0

    @pytest.mark.parametrize(
        "missing",
        ("file_store", "artifacts", "blobs", "overlays", "run_store"),
    )
    def test_missing_required_authority_omits_the_tool(self, tmp_path, missing) -> None:
        context = _context()
        values = {
            "file_store": FileRuntimeApiStore(tmp_path / "agent-data"),
            "artifact_service": _ArtifactService(),
            "artifact_blob_store": InMemoryArtifactBlobStore(),
            "workspace_overlay_store": InMemoryWorkspaceOverlayStore(),
            "run_store": _RunStore(_persisted_run(context)),
        }
        parameter = {
            "file_store": "file_store",
            "artifacts": "artifact_service",
            "blobs": "artifact_blob_store",
            "overlays": "workspace_overlay_store",
            "run_store": "run_store",
        }[missing]
        values[parameter] = None

        assert (
            SandboxWorkerBundle.compose(
                runtime_context=context,
                env=_ENV,
                provider_overrides={SandboxProviderId.LANGSMITH: _RecordingProvider()},
                **values,
            )
            is None
        )

    def test_non_file_postgres_and_unattested_provider_omit_the_tool(
        self, tmp_path
    ) -> None:
        context = _context()
        # ``file_store=None`` represents the hosted/Postgres path. D3 makes no
        # attempt to construct an alternate lifecycle/history implementation.
        assert (
            SandboxWorkerBundle.compose(
                runtime_context=context,
                file_store=None,
                artifact_service=_ArtifactService(),  # type: ignore[arg-type]
                artifact_blob_store=InMemoryArtifactBlobStore(),
                workspace_overlay_store=InMemoryWorkspaceOverlayStore(),
                run_store=_RunStore(_persisted_run(context)),
                env=_ENV,
                provider_overrides={SandboxProviderId.LANGSMITH: _RecordingProvider()},
            )
            is None
        )
        # A lookalike that exposes ``layout``/``object_store`` is not a trusted
        # desktop runtime store. D3 rejects it by concrete adapter identity.
        assert (
            SandboxWorkerBundle.compose(
                runtime_context=context,
                file_store=_FileStore(
                    layout=FileStoreLayout(tmp_path / "lookalike"),
                    object_store=object(),
                ),
                artifact_service=_ArtifactService(),  # type: ignore[arg-type]
                artifact_blob_store=InMemoryArtifactBlobStore(),
                workspace_overlay_store=InMemoryWorkspaceOverlayStore(),
                run_store=_RunStore(_persisted_run(context)),
                env=_ENV,
                provider_overrides={SandboxProviderId.LANGSMITH: _RecordingProvider()},
            )
            is None
        )
        assert (
            _bundle(
                tmp_path=tmp_path,
                context=context,
                provider=_UnattestedProvider(),
            )
            is None
        )

    def test_capability_wiring_omits_non_file_bundle_without_a_fallback(self) -> None:
        assert (
            CapabilityToolWiring(
                runtime_context=_context(),
                env=_ENV,
                sandbox_tool_factory=None,
            ).sandbox_execute_tool()
            is None
        )

    async def test_worker_restart_reaps_durable_cleanup_duty(self, tmp_path) -> None:
        """A new worker process drains a duty written before an earlier crash."""

        store = FileRuntimeApiStore(tmp_path / "agent-data")
        provider = _TransientTeardownProvider()
        # The provider receives a normal immutable request; only its opaque
        # session ref is copied into the durable duty.
        handle = await provider.create(
            make_request(run_id="run_cleanup", idempotency_key="cleanup-idem")
        )
        first = FileSandboxRecoveryReaper.compose(
            file_store=store,
            env=_ENV,
            provider_overrides={SandboxProviderId.LANGSMITH: provider},
        )
        assert first is not None
        await first.runtime.cleanup_store.schedule(
            SandboxCleanupSchedule(
                operation_id="sandbox:run_cleanup",
                run_id="run_cleanup",
                provider_session_ref=handle.session.provider_session_ref,
                snapshot_digest="a" * 64,
            )
        )

        # Reconstruct from the same filesystem root: no in-memory schedule
        # shares state across this restart boundary.
        restarted_store = FileRuntimeApiStore(tmp_path / "agent-data")
        failed_worker = RuntimeWorker(
            persistence=restarted_store,
            event_store=restarted_store,
            queue=restarted_store,
            settings=RuntimeSettings.load(environ={"SURFACES_V2": "false"}),
            artifact_service=_ArtifactService(),
            artifact_blob_store=InMemoryArtifactBlobStore(),
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),
            sandbox_provider_overrides={SandboxProviderId.LANGSMITH: provider},
            capability_env=_ENV,
        )
        # The worker performs the durable cleanup pass before polling the
        # queue, including when there is no user command to claim. The first
        # provider failure stays durable; it is not converted into a false
        # success or an in-memory retry.
        assert await failed_worker.run_once() is False
        pending = await first.runtime.cleanup_store.get("sandbox:run_cleanup")
        assert pending is not None and pending.state == "cleanup_pending"
        await first.runtime.cleanup_store.transition(
            record=pending.model_copy(
                update={
                    "transition_no": pending.transition_no + 1,
                    "updated_at": datetime.now(UTC),
                    "retry_not_before": datetime.now(UTC) - timedelta(seconds=1),
                }
            ),
            expected_transition_no=pending.transition_no,
        )
        provider.fail_teardown = False
        recovered_store = FileRuntimeApiStore(tmp_path / "agent-data")
        recovered_worker = RuntimeWorker(
            persistence=recovered_store,
            event_store=recovered_store,
            queue=recovered_store,
            settings=RuntimeSettings.load(environ={"SURFACES_V2": "false"}),
            artifact_service=_ArtifactService(),
            artifact_blob_store=InMemoryArtifactBlobStore(),
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),
            sandbox_provider_overrides={SandboxProviderId.LANGSMITH: provider},
            capability_env=_ENV,
        )
        assert await recovered_worker.run_once() is False
        duty = await first.runtime.cleanup_store.get("sandbox:run_cleanup")
        assert duty is not None and duty.state == "cleaned"
        assert provider.terminated_refs == [handle.session.provider_session_ref]

    async def test_queued_context_mismatch_stops_before_snapshot_or_provider(
        self, tmp_path
    ) -> None:
        context = _context()
        provider = _RecordingProvider()
        handler = RuntimeRunHandler(
            persistence=_RunStore(_persisted_run(context)),  # type: ignore[arg-type]
            event_store=FileRuntimeApiStore(tmp_path / "agent-data"),  # type: ignore[arg-type]
            artifact_service=_ArtifactService(),  # type: ignore[arg-type]
            artifact_blob_store=InMemoryArtifactBlobStore(),  # type: ignore[arg-type]
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),  # type: ignore[arg-type]
            sandbox_provider_overrides={SandboxProviderId.LANGSMITH: provider},
            capability_env=_ENV,
        )
        command = RuntimeRunCommand(
            run_id=context.run_id,
            conversation_id="conv_a",
            org_id=context.org_id,
            user_id=context.user_id,
            trace_id="trace_a",
            runtime_context=context.model_copy(update={"user_id": "user_other"}),
        )

        with pytest.raises(AgentRuntimeError, match="runtime context"):
            await handler.handle(command)

        assert provider.create_calls == 0

    def test_default_file_worker_stays_dark_with_test_attested_provider(
        self, tmp_path
    ) -> None:
        """Provider attestation cannot substitute the missing C1/A2 authorities."""

        store = FileRuntimeApiStore(tmp_path / "agent-data")
        settings = RuntimeSettings.load(environ={"SURFACES_V2": "false"})
        common = {
            "persistence": store,
            "event_store": store,
            "queue": store,
            "settings": settings,
            "artifact_service": _ArtifactService(),
            "artifact_blob_store": InMemoryArtifactBlobStore(),
            "workspace_overlay_store": InMemoryWorkspaceOverlayStore(),
            "capability_env": _ENV,
        }
        unavailable_worker = RuntimeWorker(**common)  # type: ignore[arg-type]
        assert unavailable_worker.run_handler._sandbox_worker_bundle(_context()) is None  # noqa: SLF001

        worker = RuntimeWorker(
            **common,
            sandbox_provider_overrides={
                SandboxProviderId.LANGSMITH: _RecordingProvider()
            },
        )  # type: ignore[arg-type]
        assert worker.run_handler._sandbox_worker_bundle(_context()) is None  # noqa: SLF001

    def test_runtime_handler_passes_only_the_composed_bundle_to_wiring(
        self, tmp_path, monkeypatch
    ) -> None:
        """The handler is a caller of the bundle, never a direct provider seam."""

        context = _context()
        file_store = FileRuntimeApiStore(tmp_path / "agent-data")
        captured: dict[str, object] = {}

        class _Wiring:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

            def code_mode_tool(self):
                return None

            def sandbox_execute_tool(self):
                return None

        handler = RuntimeRunHandler(
            persistence=_RunStore(_persisted_run(context)),  # type: ignore[arg-type]
            event_store=file_store,  # type: ignore[arg-type]
            dependencies_factory=lambda _context: _Dependencies(),  # type: ignore[arg-type]
            artifact_service=_ArtifactService(),  # type: ignore[arg-type]
            artifact_blob_store=InMemoryArtifactBlobStore(),  # type: ignore[arg-type]
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),  # type: ignore[arg-type]
            sandbox_provider_overrides={
                SandboxProviderId.LANGSMITH: _RecordingProvider()
            },
            capability_env=_ENV,
        )
        monkeypatch.setattr(
            "runtime_worker.capability_tool_wiring.CapabilityToolWiring", _Wiring
        )
        monkeypatch.setattr(
            handler, "_subagent_artifacts_backend", lambda _command: None
        )
        monkeypatch.setattr(handler, "_drafts_backend", lambda **_kwargs: None)
        monkeypatch.setattr(
            handler, "_stage_rowset_write_tool", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(handler, "_publish_artifact_tool", lambda: None)
        command = SimpleNamespace(
            runtime_context=context,
            org_id=context.org_id,
            user_id=context.user_id,
            run_id=context.run_id,
            conversation_id="conv_a",
        )

        handler._dependencies_for_run(
            command,  # type: ignore[arg-type]
            SimpleNamespace(has_observations=False),  # type: ignore[arg-type]
        )

        assert captured["sandbox_tool_factory"] is None
