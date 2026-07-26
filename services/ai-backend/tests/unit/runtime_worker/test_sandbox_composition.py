"""Adversarial coverage for the filesystem-only D3 worker composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import hashlib
import inspect
import json
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.sandbox.contracts import SandboxProviderId
from agent_runtime.capabilities.sandbox.ports import (
    SandboxPatchCollection,
    SandboxPatchCollectorPort,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
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
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from runtime_worker.capability_tool_wiring import CapabilityToolWiring
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.sandbox_composition import SandboxWorkerBundle
from tests.unit.agent_runtime.capabilities.sandbox.fakes import FakeSandboxProvider


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


class _ArtifactService:
    """Minimal A2-shaped publisher fake; records no source bytes externally."""

    def __init__(self) -> None:
        self.published: list[bytes] = []

    async def publish_from_stream(self, *, request, chunks, **_kwargs):
        body = b"".join([chunk async for chunk in chunks])
        assert hashlib.sha256(body).hexdigest() == request.expected_digest
        self.published.append(body)
        revision = SimpleNamespace(
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


class _ChangedAfterStatBlobStore:
    """Reports the approved blob metadata but streams altered source bytes."""

    def __init__(self, delegate: InMemoryArtifactBlobStore) -> None:
        self._delegate = delegate

    async def stat(self, blob_key: str):
        return await self._delegate.stat(blob_key)

    async def open_stream(self, blob_key: str) -> AsyncIterator[bytes]:
        del blob_key

        async def _changed() -> AsyncIterator[bytes]:
            # Same byte length as the approved test blob: this reaches the
            # sealed store's final digest verification rather than only a
            # streaming size ceiling.
            yield b"changed--original-bytes"

        return _changed()


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
    env=None,
):
    return SandboxWorkerBundle.compose(
        runtime_context=context,
        file_store=_FileStore(
            layout=FileStoreLayout(tmp_path / "agent-data"), object_store=object()
        ),
        artifact_service=artifacts or _ArtifactService(),  # type: ignore[arg-type]
        artifact_blob_store=blobs or InMemoryArtifactBlobStore(),
        workspace_overlay_store=overlays or InMemoryWorkspaceOverlayStore(),
        patch_collector=collector
        if collector is not None
        else _CompletePatchCollector(),
        env=env or _ENV,
        provider_overrides={
            SandboxProviderId.LANGSMITH: provider or _RecordingProvider()
        },
    )


def _bind_context(context: AgentRuntimeContext):
    return OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id="conv_a",
            run_id=context.run_id,
        ),
        policy_snapshot=ToolUsePolicySnapshot.from_response(workspace=None, user=None),
        ledger_emitter=None,
        artifact_service=None,
        mode=OperationGatewayMode.ENFORCE,
        canonical_arguments_durable=True,
    )


class TestSandboxWorkerBundle:
    def test_legacy_default_is_absent_but_file_native_composition_is_exposed(
        self, tmp_path
    ) -> None:
        """There is one production route, not a dormant direct-tool fallback."""

        context = _context()
        legacy = CapabilityToolWiring(runtime_context=context, env=_ENV)
        assert legacy.sandbox_execute_tool() is None

        bundle = _bundle(tmp_path=tmp_path, context=context)
        assert bundle is not None
        composed = CapabilityToolWiring(
            runtime_context=context,
            env=_ENV,
            sandbox_tool_factory=bundle,
        )
        assert composed.sandbox_execute_tool() is not None
        construction = inspect.getsource(CapabilityToolWiring.sandbox_execute_tool)
        assert "build_sandbox_backend" not in construction
        assert "RemoteExecutionService" not in construction

    async def test_file_first_bundle_builds_gateway_tool_and_binds_only_verified_run(
        self, tmp_path
    ) -> None:
        context = _context()
        blobs = InMemoryArtifactBlobStore()
        overlays = InMemoryWorkspaceOverlayStore()
        await _seed_overlay(
            overlays=overlays, blobs=blobs, run_id="run_a", content=b"allowed"
        )
        # A different org/user/run's C1 state is present but cannot be selected:
        # C1's actual storage key is the verified run id, not a model argument.
        await _seed_overlay(
            overlays=overlays,
            blobs=blobs,
            run_id="run_b",
            content=b"other-principal-data",
        )
        provider = _RecordingProvider()
        bundle = _bundle(
            tmp_path=tmp_path,
            context=context,
            blobs=blobs,
            overlays=overlays,
            provider=provider,
        )

        assert bundle is not None
        tool = bundle.build_tool(
            identity_provider=lambda: SimpleNamespace(
                run_id="run_a", org_id="org_a", user_id="user_a"
            )
        )
        assert tool is not None
        token = _bind_context(context)
        try:
            payload = json.loads(await tool.ainvoke({"command": "echo:ok"}))
        finally:
            OperationContext.unbind(token)

        assert payload["status"] == "completed"
        assert provider.received_bytes == len(b"allowed")
        assert provider.received_paths == ["/workspace/project/report.csv"]
        assert provider.create_requests[0].run_id == "run_a"

    async def test_changed_blob_is_sealed_and_rejected_before_provider_receives_bytes(
        self, tmp_path
    ) -> None:
        context = _context()
        canonical_blobs = InMemoryArtifactBlobStore()
        overlays = InMemoryWorkspaceOverlayStore()
        await _seed_overlay(
            overlays=overlays,
            blobs=canonical_blobs,
            run_id="run_a",
            content=b"approved-original-bytes",
        )
        provider = _RecordingProvider()
        bundle = _bundle(
            tmp_path=tmp_path,
            context=context,
            blobs=_ChangedAfterStatBlobStore(canonical_blobs),
            overlays=overlays,
            provider=provider,
        )

        assert bundle is not None
        tool = bundle.build_tool(
            identity_provider=lambda: SimpleNamespace(
                run_id="run_a", org_id="org_a", user_id="user_a"
            )
        )
        assert tool is not None
        token = _bind_context(context)
        try:
            payload = json.loads(await tool.ainvoke({"command": "echo:never"}))
        finally:
            OperationContext.unbind(token)

        assert payload["status"] == "failed"
        assert provider.create_calls == 0
        assert provider.received_bytes == 0

    async def test_bundle_refuses_cross_principal_or_run_snapshot_selection(
        self, tmp_path
    ) -> None:
        context = _context()
        blobs = InMemoryArtifactBlobStore()
        overlays = InMemoryWorkspaceOverlayStore()
        await _seed_overlay(
            overlays=overlays, blobs=blobs, run_id="run_a", content=b"principal-a"
        )
        await _seed_overlay(
            overlays=overlays,
            blobs=blobs,
            run_id="run_b",
            content=b"principal-b",
        )
        provider = _RecordingProvider()
        bundle = _bundle(
            tmp_path=tmp_path,
            context=context,
            blobs=blobs,
            overlays=overlays,
            provider=provider,
        )

        assert bundle is not None
        # This port is worker-owned in production. The bundle still defends
        # against accidental cross-user/run wiring instead of allowing the C1
        # run-keyed overlay authority to select another principal's run.
        tool = bundle.build_tool(
            identity_provider=lambda: SimpleNamespace(
                run_id="run_b", org_id="org_b", user_id="user_b"
            )
        )
        assert tool is not None
        token = _bind_context(context)
        try:
            payload = json.loads(await tool.ainvoke({"command": "echo:never"}))
        finally:
            OperationContext.unbind(token)

        assert payload == {
            "status": "failed",
            "summary": (
                "An authorized immutable sandbox snapshot is unavailable; "
                "no command was run."
            ),
        }
        assert provider.create_calls == 0
        assert provider.received_bytes == 0

    async def test_retained_history_gap_fails_before_provider_provisioning(
        self, tmp_path
    ) -> None:
        context = _context()
        provider = _RecordingProvider()
        bundle = _bundle(
            tmp_path=tmp_path,
            context=context,
            overlays=_HistoryGapOverlayStore(
                manifest=OverlayManifest(run_id="run_a", version=1)
            ),
            provider=provider,
        )

        assert bundle is not None
        tool = bundle.build_tool(
            identity_provider=lambda: SimpleNamespace(
                run_id="run_a", org_id="org_a", user_id="user_a"
            )
        )
        assert tool is not None
        token = _bind_context(context)
        try:
            payload = json.loads(await tool.ainvoke({"command": "echo:never"}))
        finally:
            OperationContext.unbind(token)

        assert payload["status"] == "failed"
        assert provider.create_calls == 0
        assert provider.received_bytes == 0

    @pytest.mark.parametrize(
        "missing",
        ("file_store", "artifacts", "blobs", "overlays", "collector"),
    )
    def test_missing_required_authority_omits_the_tool(self, tmp_path, missing) -> None:
        context = _context()
        values = {
            "file_store": _FileStore(
                layout=FileStoreLayout(tmp_path / "agent-data"), object_store=object()
            ),
            "artifact_service": _ArtifactService(),
            "artifact_blob_store": InMemoryArtifactBlobStore(),
            "workspace_overlay_store": InMemoryWorkspaceOverlayStore(),
            "patch_collector": _CompletePatchCollector(),
        }
        parameter = {
            "file_store": "file_store",
            "artifacts": "artifact_service",
            "blobs": "artifact_blob_store",
            "overlays": "workspace_overlay_store",
            "collector": "patch_collector",
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
                patch_collector=_CompletePatchCollector(),
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

    def test_runtime_handler_passes_only_the_composed_bundle_to_wiring(
        self, tmp_path, monkeypatch
    ) -> None:
        """The handler is a caller of the bundle, never a direct provider seam."""

        context = _context()
        file_store = _FileStore(
            layout=FileStoreLayout(tmp_path / "agent-data"), object_store=object()
        )
        captured: dict[str, object] = {}

        class _Wiring:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

            def code_mode_tool(self):
                return None

            def sandbox_execute_tool(self):
                return None

        handler = RuntimeRunHandler(
            persistence=object(),  # type: ignore[arg-type]
            event_store=file_store,  # type: ignore[arg-type]
            dependencies_factory=lambda _context: _Dependencies(),  # type: ignore[arg-type]
            artifact_service=_ArtifactService(),  # type: ignore[arg-type]
            artifact_blob_store=InMemoryArtifactBlobStore(),  # type: ignore[arg-type]
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),  # type: ignore[arg-type]
            sandbox_patch_collector=_CompletePatchCollector(),
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

        assert isinstance(captured["sandbox_tool_factory"], SandboxWorkerBundle)
