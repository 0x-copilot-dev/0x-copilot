"""Adversarial D3 lifecycle/coordinator verification."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import hashlib

import pytest

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfiles
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxDeliverable,
    SandboxError,
    SandboxErrorCode,
    SandboxLifecycleState,
    SandboxRunRequest,
)
from agent_runtime.capabilities.sandbox.coordinator import SandboxLifecycleCoordinator
from agent_runtime.capabilities.sandbox.lifecycle import InMemorySandboxLifecycleStore
from agent_runtime.capabilities.sandbox.ports import (
    SandboxArtifactPublisherPort,
    SandboxDownloadedFile,
    SandboxPatchCollection,
    SandboxPatchCollectorPort,
    SandboxPatchImportPort,
    SandboxProcessOutput,
    SandboxRuntimePort,
    SandboxSnapshotContentPort,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    ActiveSandbox,
    RemoteExecutionService,
)
from agent_runtime.capabilities.sandbox.usage_meter import InMemorySandboxUsageMeter
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    RawSnapshotEntry,
    WorkspaceManifestBuilder,
)
from tests.unit.agent_runtime.capabilities.sandbox.contracts_helpers import (
    active_config,
)
from tests.unit.agent_runtime.capabilities.sandbox.fakes import (
    FailingTerminateProvider,
    FakeSandboxProvider,
    make_request,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _ref(content: bytes, *, artifact_id: str = "artifact-input") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        sha256=_digest(content),
        size_bytes=len(content),
    )


class _SnapshotSource(SandboxSnapshotContentPort):
    def __init__(self, content: Mapping[str, bytes]) -> None:
        self._content = dict(content)

    async def open(self, ref: ArtifactRef) -> AsyncIterator[bytes]:
        content = self._content[ref.sha256]

        async def stream() -> AsyncIterator[bytes]:
            yield content[:2]
            yield content[2:]

        return stream()


class _Publisher(SandboxArtifactPublisherPort):
    def __init__(self, *, mismatch: bool = False) -> None:
        self.calls: list[tuple[object, bytes]] = []
        self._mismatch = mismatch

    async def publish(
        self, *, publication, chunks: AsyncIterator[bytes]
    ) -> ArtifactRef:
        content = b"".join([chunk async for chunk in chunks])
        self.calls.append((publication, content))
        if self._mismatch:
            return _ref(b"not-the-sandbox-file", artifact_id="artifact-wrong")
        return _ref(content, artifact_id="artifact-output")


class _Runtime(SandboxRuntimePort):
    def __init__(
        self,
        *,
        files: Mapping[str, bytes] | None = None,
        output: SandboxProcessOutput | None = None,
        execute_error: BaseException | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.output = output or SandboxProcessOutput(
            stdout="ok", stderr="", exit_code=0, duration_ms=7
        )
        self.execute_error = execute_error
        self.uploaded: dict[str, bytes] = {}
        self.execute_calls = 0

    async def upload(self, *, active, request, source) -> int:
        total = 0
        for entry in request.create_request.snapshot.entries:
            payload = b"".join(
                [chunk async for chunk in await source.open(entry.payload_ref)]
            )
            assert _digest(payload) == entry.sha256
            self.uploaded[entry.path] = payload
            total += len(payload)
        return total

    async def execute(
        self, *, active: ActiveSandbox, command: str
    ) -> SandboxProcessOutput:
        del active, command
        self.execute_calls += 1
        if self.execute_error is not None:
            raise self.execute_error
        return self.output

    async def download(self, *, active, paths):
        del active
        results: list[SandboxDownloadedFile] = []
        for path in paths:
            content = self.files[path]

            async def stream(value: bytes = content) -> AsyncIterator[bytes]:
                yield value[:1]
                yield value[1:]

            results.append(SandboxDownloadedFile(path=path, chunks=stream()))
        return tuple(results)


class _PatchCollector(SandboxPatchCollectorPort):
    def __init__(self, collection: SandboxPatchCollection) -> None:
        self.collection = collection

    async def collect(self, *, active, request) -> SandboxPatchCollection:
        del active, request
        return self.collection


class _PatchImporter(SandboxPatchImportPort):
    def __init__(self) -> None:
        self.requests = []

    async def import_patch(self, request) -> str:
        self.requests.append(request)
        return "overlay-revision://sandbox-1"


def _service(provider: FakeSandboxProvider | None = None) -> RemoteExecutionService:
    config = active_config()
    selected = provider or FakeSandboxProvider()
    registry = SandboxProviderRegistry.from_config(
        config,
        overrides={config.provider: selected},  # type: ignore[dict-item]
    )
    return RemoteExecutionService(
        registry=registry,
        config=config,
        session_store=InMemorySandboxSessionStore(),
    )


def _request(
    *,
    snapshot_content: bytes = b"input",
    deliverables: tuple[SandboxDeliverable, ...] = (),
    collect_patch: bool = False,
    redaction_terms: tuple[str, ...] = (),
) -> SandboxRunRequest:
    source_ref = _ref(snapshot_content)
    manifest = WorkspaceManifestBuilder.build(
        workspace_id="private-workspace-id",
        root_grant_id="private-grant-id",
        raw_entries=[
            RawSnapshotEntry(
                path="input.txt",
                sha256=source_ref.sha256,
                size_bytes=source_ref.size_bytes,
                payload_ref=source_ref,
            )
        ],
        limits=SandboxLimitProfiles.get("desktop_v1"),
    )
    create = make_request().model_copy(
        update={
            "operation_id": "sandbox-operation-1",
            "snapshot": WorkspaceManifestBuilder.to_sandbox_snapshot(
                manifest, snapshot_id="snapshot:operation-1"
            ),
        }
    )
    return SandboxRunRequest(
        create_request=create,
        command="echo safe-command",
        deliverables=deliverables,
        collect_patch=collect_patch,
        redaction_terms=redaction_terms,
    )


def _coordinator(
    *,
    runtime: _Runtime,
    source: _SnapshotSource,
    publisher: _Publisher | None = None,
    provider: FakeSandboxProvider | None = None,
    patch_collector: _PatchCollector | None = None,
    patch_importer: _PatchImporter | None = None,
) -> tuple[
    SandboxLifecycleCoordinator,
    InMemorySandboxLifecycleStore,
    InMemorySandboxUsageMeter,
]:
    lifecycle = InMemorySandboxLifecycleStore()
    meter = InMemorySandboxUsageMeter()
    return (
        SandboxLifecycleCoordinator(
            service=_service(provider),
            lifecycle_store=lifecycle,
            runtime=runtime,
            usage_meter=meter,
            snapshot_source=source,
            artifact_publisher=publisher,
            patch_collector=patch_collector,
            patch_importer=patch_importer,
            limits=SandboxLimitProfiles.get("desktop_v1"),
        ),
        lifecycle,
        meter,
    )


@pytest.mark.asyncio
class TestSandboxLifecycleCoordinator:
    async def test_empty_selected_snapshot_never_reaches_provider_upload_or_execute(
        self,
    ) -> None:
        """The final execution boundary rejects zero C1/A2 inputs pre-provider."""

        provider = FakeSandboxProvider()
        runtime = _Runtime()
        coordinator, lifecycle, _meter = _coordinator(
            runtime=runtime,
            source=_SnapshotSource({}),
            provider=provider,
        )
        request = SandboxRunRequest(
            create_request=make_request(),
            command="echo must-not-run",
            deliverables=(),
            collect_patch=False,
            redaction_terms=(),
        )

        with pytest.raises(SandboxError) as excinfo:
            await coordinator.run(request)

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED
        assert provider.create_calls == 0
        assert runtime.uploaded == {}
        assert runtime.execute_calls == 0
        assert (
            await lifecycle.get(idempotency_key=request.create_request.idempotency_key)
            is None
        )

    async def test_exact_snapshot_artifact_redaction_usage_and_cleanup(self) -> None:
        source_content = b"immutable-input"
        output_content = b"a,b\n1,2\n"
        runtime = _Runtime(
            files={"/workspace/result.csv": output_content},
            output=SandboxProcessOutput(
                stdout="secret-value visible",
                stderr="stderr secret-value",
                exit_code=0,
                duration_ms=19,
            ),
        )
        publisher = _Publisher()
        coordinator, lifecycle, meter = _coordinator(
            runtime=runtime,
            source=_SnapshotSource({_digest(source_content): source_content}),
            publisher=publisher,
        )
        request = _request(
            snapshot_content=source_content,
            deliverables=(
                SandboxDeliverable(
                    path="/workspace/result.csv",
                    media_type="text/csv",
                    suggested_filename="result.csv",
                    title="Sandbox result",
                ),
            ),
            redaction_terms=("secret-value",),
        )

        result = await coordinator.run(request)

        assert runtime.uploaded == {"/workspace/input.txt": source_content}
        assert runtime.execute_calls == 1
        assert result.state is SandboxLifecycleState.CLEANED
        assert "secret-value" not in result.stdout + result.stderr
        assert result.stdout == "[REDACTED] visible"
        assert publisher.calls[0][1] == output_content
        assert result.artifacts[0].artifact_ref.sha256 == _digest(output_content)
        assert meter.get(request.create_request.operation_id) is not None
        record = await lifecycle.get(
            idempotency_key=request.create_request.idempotency_key
        )
        assert record is not None and record.state is SandboxLifecycleState.CLEANED
        assert "safe-command" not in record.model_dump_json()
        assert (
            "private-workspace-id"
            not in request.create_request.snapshot.model_dump_json()
        )

    async def test_duplicate_or_crash_reentry_never_reexecutes_command(self) -> None:
        content = b"input"
        runtime = _Runtime()
        coordinator, lifecycle, _ = _coordinator(
            runtime=runtime,
            source=_SnapshotSource({_digest(content): content}),
        )
        request = _request(snapshot_content=content)
        await coordinator.run(request)

        with pytest.raises(SandboxError) as excinfo:
            await coordinator.run(request)
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE
        assert runtime.execute_calls == 1
        persisted = await lifecycle.get(
            idempotency_key=request.create_request.idempotency_key
        )
        assert persisted is not None and persisted.execution_started is True

    async def test_unknown_execution_is_not_replayed_after_cleanup(self) -> None:
        content = b"input"
        runtime = _Runtime(
            execute_error=SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "provider disconnected",
            )
        )
        coordinator, lifecycle, _ = _coordinator(
            runtime=runtime,
            source=_SnapshotSource({_digest(content): content}),
        )
        request = _request(snapshot_content=content)

        with pytest.raises(SandboxError) as excinfo:
            await coordinator.run(request)
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE
        assert runtime.execute_calls == 1
        record = await lifecycle.get(
            idempotency_key=request.create_request.idempotency_key
        )
        assert record is not None and record.execution_started is True
        with pytest.raises(SandboxError):
            await coordinator.run(request)
        assert runtime.execute_calls == 1

    async def test_cancellation_marks_unknown_execution_and_never_replays(self) -> None:
        content = b"input"
        runtime = _Runtime(execute_error=asyncio.CancelledError())
        coordinator, lifecycle, _ = _coordinator(
            runtime=runtime,
            source=_SnapshotSource({_digest(content): content}),
        )
        request = _request(snapshot_content=content)

        with pytest.raises(asyncio.CancelledError):
            await coordinator.run(request)
        record = await lifecycle.get(
            idempotency_key=request.create_request.idempotency_key
        )
        assert record is not None and record.execution_started is True
        assert runtime.execute_calls == 1
        with pytest.raises(SandboxError):
            await coordinator.run(request)

    async def test_cleanup_pending_is_visible_and_janitor_retries(self) -> None:
        content = b"input"
        provider = FailingTerminateProvider()
        coordinator, lifecycle, _ = _coordinator(
            runtime=_Runtime(),
            source=_SnapshotSource({_digest(content): content}),
            provider=provider,
        )
        request = _request(snapshot_content=content)

        result = await coordinator.run(request)
        assert result.state is SandboxLifecycleState.CLEANUP_PENDING
        records = await coordinator.reconcile()
        assert len(records) == 1
        assert records[0].state is SandboxLifecycleState.CLEANUP_PENDING
        assert records[0].cleanup_attempts >= 2
        assert (
            await lifecycle.get(idempotency_key=request.create_request.idempotency_key)
        ) is not None

    async def test_mismatched_artifact_ref_fails_closed(self) -> None:
        content = b"input"
        runtime = _Runtime(files={"/workspace/output.txt": b"exact"})
        coordinator, _, _ = _coordinator(
            runtime=runtime,
            source=_SnapshotSource({_digest(content): content}),
            publisher=_Publisher(mismatch=True),
        )
        request = _request(
            snapshot_content=content,
            deliverables=(
                SandboxDeliverable(
                    path="/workspace/output.txt",
                    media_type="text/plain",
                    suggested_filename="output.txt",
                    title="Output",
                ),
            ),
        )

        with pytest.raises(SandboxError) as excinfo:
            await coordinator.run(request)
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH

    async def test_complete_patch_has_move_mkdir_and_typed_overlay_handoff(
        self,
    ) -> None:
        content = b"input"
        ref = _ref(content, artifact_id="artifact-patch")
        collector = _PatchCollector(
            SandboxPatchCollection(
                result_entries={
                    "docs/input.txt": RawSnapshotEntry(
                        path="docs/input.txt",
                        sha256=ref.sha256,
                        size_bytes=ref.size_bytes,
                        payload_ref=ref,
                    )
                },
                directories=("docs",),
                moves={"input.txt": "docs/input.txt"},
                complete=True,
            )
        )
        importer = _PatchImporter()
        coordinator, _, _ = _coordinator(
            runtime=_Runtime(),
            source=_SnapshotSource({_digest(content): content}),
            patch_collector=collector,
            patch_importer=importer,
        )
        request = _request(snapshot_content=content, collect_patch=True)

        result = await coordinator.run(request)
        assert result.patch is not None
        assert {(entry.operation, entry.path) for entry in result.patch.entries} == {
            ("mkdir", "/workspace/docs"),
            ("move", "/workspace/docs/input.txt"),
        }
        overlay_ref = await coordinator.import_patch(result)
        assert overlay_ref == "overlay-revision://sandbox-1"
        assert importer.requests[0].run_id == request.create_request.run_id
        assert importer.requests[0].patch.complete is True

    async def test_incomplete_patch_never_reaches_import_port(self) -> None:
        content = b"input"
        collector = _PatchCollector(
            SandboxPatchCollection(result_entries={}, complete=False)
        )
        importer = _PatchImporter()
        coordinator, _, _ = _coordinator(
            runtime=_Runtime(),
            source=_SnapshotSource({_digest(content): content}),
            patch_collector=collector,
            patch_importer=importer,
        )

        with pytest.raises(SandboxError) as excinfo:
            await coordinator.run(
                _request(snapshot_content=content, collect_patch=True)
            )
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE
        assert importer.requests == []
