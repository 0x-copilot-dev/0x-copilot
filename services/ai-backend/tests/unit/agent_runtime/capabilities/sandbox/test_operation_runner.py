"""Adversarial tests for the coordinator-only v2.1 sandbox operation runner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import hashlib
import inspect
import json
from types import SimpleNamespace
from typing import cast

import pytest

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfiles
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxError,
    SandboxErrorCode,
    SandboxLifecycleState,
    SandboxRunResult,
    WorkspacePatchManifest,
)
from agent_runtime.capabilities.sandbox.operation_adapter import (
    SandboxOperationAvailability,
    SandboxOperationLaunch,
)
from agent_runtime.capabilities.sandbox.operation_runner import (
    SandboxLifecycleOperationRunner,
    SandboxSnapshotStoreContentSource,
)
from agent_runtime.capabilities.sandbox.result_publisher import (
    ArtifactServiceSandboxResultPublisher,
    SandboxResultPublication,
    SandboxResultPublisherPort,
)
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxSnapshotEntry,
    SandboxSnapshotFileStorePort,
    SandboxSnapshotManifest,
    SandboxSnapshotSourceKind,
)

_INPUT_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440000/revisions/1"
_RESULT_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440001/revisions/1"
_DIGEST = "a" * 64


class _SnapshotStore(SandboxSnapshotFileStorePort):
    def __init__(self) -> None:
        self.opened: list[str] = []

    async def resolve(self, *, source):  # pragma: no cover - runner receives manifest
        del source
        return None

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        self.opened.append(content_ref)

        async def stream() -> AsyncIterator[bytes]:
            yield b"abc"

        return stream()


class _ArtifactService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], bytes]] = []

    async def publish_from_stream(self, **kwargs: object) -> object:
        chunks = cast(AsyncIterator[bytes], kwargs["chunks"])
        content = b"".join([chunk async for chunk in chunks])
        self.calls.append((kwargs, content))
        return SimpleNamespace(
            record=SimpleNamespace(
                current_revision=SimpleNamespace(
                    revision=SimpleNamespace(
                        content_ref=_RESULT_ARTIFACT,
                        content_digest=hashlib.sha256(content).hexdigest(),
                        byte_size=len(content),
                    )
                )
            )
        )


@dataclass
class _ResultPublisher(SandboxResultPublisherPort):
    calls: list[tuple[SandboxResultPublication, bytes]] = field(default_factory=list)
    returned_ref: str = _RESULT_ARTIFACT

    async def publish_result(
        self, *, publication: SandboxResultPublication, chunks: AsyncIterator[bytes]
    ) -> str:
        content = b"".join([chunk async for chunk in chunks])
        self.calls.append((publication, content))
        return self.returned_ref


@dataclass
class _Coordinator:
    result: SandboxRunResult
    calls: list[object] = field(default_factory=list)
    import_calls: list[SandboxRunResult] = field(default_factory=list)
    fail_after_first: bool = False

    async def run(self, request):
        self.calls.append(request)
        if self.fail_after_first and len(self.calls) > 1:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "This sandbox operation has already started and will not be replayed.",
            )
        return self.result

    async def import_patch(self, result: SandboxRunResult) -> str:
        self.import_calls.append(result)
        return "workspace-overlay://runs/run_1/versions/1"


def _result(*, patch: WorkspacePatchManifest | None = None) -> SandboxRunResult:
    return SandboxRunResult(
        run_id="run_1",
        operation_id="operation_1",
        state=SandboxLifecycleState.CLEANED,
        stdout="calculation complete",
        stderr="",
        exit_code=0,
        duration_ms=17,
        patch=patch,
    )


def _launch() -> SandboxOperationLaunch:
    entry = SandboxSnapshotEntry(
        virtual_path="/workspace/report.csv",
        source_kind=SandboxSnapshotSourceKind.ARTIFACT,
        source_ref=_INPUT_ARTIFACT,
        content_ref=_INPUT_ARTIFACT,
        content_digest=_DIGEST,
        size_bytes=3,
    )
    snapshot = SandboxSnapshotManifest.from_entries((entry,))
    return SandboxOperationLaunch(
        run_id="run_1",
        operation_id="operation_1",
        idempotency_key="sandbox:" + "b" * 64,
        command="python report.py",
        snapshot=snapshot,
    )


def _runner(
    coordinator: _Coordinator,
    publisher: _ResultPublisher | None = None,
) -> SandboxLifecycleOperationRunner:
    return SandboxLifecycleOperationRunner(
        coordinator=coordinator,
        result_publisher=publisher or _ResultPublisher(),
        limits=SandboxLimitProfiles.get("desktop_v1"),
        availability=SandboxOperationAvailability(available=True),
    )


@pytest.mark.asyncio
class TestSandboxLifecycleOperationRunner:
    async def test_converts_exact_virtual_manifest_then_only_calls_coordinator(
        self,
    ) -> None:
        coordinator = _Coordinator(result=_result())
        publisher = _ResultPublisher()
        runner = _runner(coordinator, publisher)

        outcome = await runner.run(request=_launch())

        assert len(coordinator.calls) == 1
        request = coordinator.calls[0]
        assert request.command == "python report.py"
        assert request.create_request.run_id == "run_1"
        assert request.create_request.operation_id == "operation_1"
        assert request.create_request.idempotency_key == "sandbox:" + "b" * 64
        assert request.create_request.egress.mode == "deny_all"
        assert request.create_request.secret_refs == ()
        assert request.deliverables == ()
        assert request.collect_patch is False
        entry = request.create_request.snapshot.entries[0]
        assert entry.path == "/workspace/report.csv"
        assert entry.payload_ref.artifact_id == _INPUT_ARTIFACT
        assert entry.payload_ref.sha256 == _DIGEST
        assert entry.payload_ref.size_bytes == 3
        assert outcome.result_ref == _RESULT_ARTIFACT
        assert outcome.safe_summary == "Sandbox command completed."
        assert len(publisher.calls) == 1

    async def test_result_is_only_an_immutable_artifact_ref_and_safe_summary(
        self,
    ) -> None:
        coordinator = _Coordinator(result=_result())
        publisher = _ResultPublisher()

        outcome = await _runner(coordinator, publisher).run(request=_launch())

        assert outcome.model_dump() == {
            "run_id": "run_1",
            "operation_id": "operation_1",
            "result_ref": _RESULT_ARTIFACT,
            "safe_summary": "Sandbox command completed.",
            "activity_ref": None,
            "artifacts": (),
            "patch": None,
        }
        publication, content = publisher.calls[0]
        assert publication.content_digest == hashlib.sha256(content).hexdigest()
        assert publication.byte_size == len(content)
        assert publication.idempotency_key.startswith("sandbox-result:")
        assert json.loads(content) == {
            "duration_ms": 17,
            "exit_code": 0,
            "output_truncated": False,
            "state": "cleaned",
            "stderr": "",
            "stdout": "calculation complete",
            "v": 1,
        }
        assert "calculation complete" not in outcome.safe_summary

    async def test_duplicate_behavior_is_delegated_to_the_coordinator(self) -> None:
        coordinator = _Coordinator(result=_result(), fail_after_first=True)
        publisher = _ResultPublisher()
        runner = _runner(coordinator, publisher)

        await runner.run(request=_launch())
        with pytest.raises(SandboxError) as excinfo:
            await runner.run(request=_launch())

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE
        assert len(coordinator.calls) == 2
        assert len(publisher.calls) == 1

    async def test_complete_patch_is_imported_only_via_coordinator_port(self) -> None:
        patch = WorkspacePatchManifest(
            session_id="sandbox-session-1",
            baseline_manifest_sha256="c" * 64,
            entries=(),
            complete=True,
            manifest_sha256="d" * 64,
        )
        coordinator = _Coordinator(result=_result(patch=patch))

        outcome = await _runner(coordinator).run(request=_launch())

        assert coordinator.import_calls == [coordinator.result]
        assert outcome.activity_ref == "workspace-overlay://runs/run_1/versions/1"
        assert (
            "workspace"
            not in inspect.getsource(
                SandboxLifecycleOperationRunner._publish_result
            ).lower()
        )

    async def test_hostile_file_reference_is_rejected_before_coordinator(self) -> None:
        launch = _launch()
        unsafe_entry = SandboxSnapshotEntry.model_construct(
            virtual_path="/workspace/report.csv",
            source_kind=SandboxSnapshotSourceKind.ARTIFACT,
            source_ref=_INPUT_ARTIFACT,
            content_ref="file:///Users/alice/report.csv",
            content_digest=_DIGEST,
            size_bytes=3,
            executable=False,
        )
        unsafe_snapshot = launch.snapshot.model_copy(
            update={"entries": (unsafe_entry,)}
        )
        unsafe_launch = launch.model_copy(update={"snapshot": unsafe_snapshot})
        coordinator = _Coordinator(result=_result())

        with pytest.raises(SandboxError) as excinfo:
            await _runner(coordinator).run(request=unsafe_launch)

        assert excinfo.value.code is SandboxErrorCode.SNAPSHOT_INVALID
        assert coordinator.calls == []

    async def test_excluded_snapshot_path_is_rejected_not_silently_omitted(
        self,
    ) -> None:
        launch = _launch()
        excluded_entry = SandboxSnapshotEntry(
            virtual_path="/workspace/.env",
            source_kind=SandboxSnapshotSourceKind.ARTIFACT,
            source_ref=_INPUT_ARTIFACT,
            content_ref=_INPUT_ARTIFACT,
            content_digest=_DIGEST,
            size_bytes=3,
        )
        excluded_snapshot = SandboxSnapshotManifest.from_entries((excluded_entry,))
        coordinator = _Coordinator(result=_result())

        with pytest.raises(SandboxError) as excinfo:
            await _runner(coordinator).run(
                request=launch.model_copy(update={"snapshot": excluded_snapshot})
            )

        assert excinfo.value.code is SandboxErrorCode.SNAPSHOT_INVALID
        assert coordinator.calls == []

    async def test_snapshot_content_uses_only_injected_store_and_refuses_file_tokens(
        self,
    ) -> None:
        store = _SnapshotStore()
        source = SandboxSnapshotStoreContentSource(store=store)
        stream = await source.open(
            ArtifactRef(artifact_id=_INPUT_ARTIFACT, sha256=_DIGEST, size_bytes=3)
        )
        assert b"".join([chunk async for chunk in stream]) == b"abc"
        assert store.opened == [_INPUT_ARTIFACT]
        with pytest.raises(SandboxError) as excinfo:
            await source.open(
                ArtifactRef(
                    artifact_id="file:///Users/alice/report.csv",
                    sha256=_DIGEST,
                    size_bytes=3,
                )
            )
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH

    async def test_combined_result_output_is_bounded_before_artifact_publication(
        self,
    ) -> None:
        coordinator = _Coordinator(
            result=SandboxRunResult(
                run_id="run_1",
                operation_id="operation_1",
                state=SandboxLifecycleState.CLEANED,
                stdout="a" * (96 * 1024),
                stderr="b" * (96 * 1024),
                exit_code=0,
                duration_ms=1,
            )
        )
        publisher = _ResultPublisher()

        await _runner(coordinator, publisher).run(request=_launch())

        _, content = publisher.calls[0]
        decoded = json.loads(content)
        assert len(content) <= 128 * 1024
        assert decoded["output_truncated"] is True
        assert "[sandbox: output truncated to result ceiling]" in decoded["stdout"]
        assert "[sandbox: output truncated to result ceiling]" in decoded["stderr"]

    async def test_result_publisher_uses_a2_and_returns_its_immutable_revision(
        self,
    ) -> None:
        service = _ArtifactService()
        publisher = ArtifactServiceSandboxResultPublisher(
            service=cast(object, service),  # structural test double for A2 port
            org_id="org_1",
            user_id="user_1",
        )
        content = b'{"v":1}'
        publication = SandboxResultPublication(
            run_id="run_1",
            operation_id="operation_1",
            content_digest=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            idempotency_key="sandbox-result:" + "e" * 64,
        )

        async def stream() -> AsyncIterator[bytes]:
            yield content

        ref = await publisher.publish_result(publication=publication, chunks=stream())

        assert ref == _RESULT_ARTIFACT
        kwargs, published = service.calls[0]
        assert published == content
        assert kwargs["org_id"] == "org_1"
        assert kwargs["user_id"] == "user_1"
        assert kwargs["request"].idempotency_key == publication.idempotency_key
        assert kwargs["request"].expected_digest == publication.content_digest
        assert kwargs["provenance"].source_ref == "payload://sandbox/operation_1/result"

    async def test_runner_has_no_direct_provider_or_session_execution_path(
        self,
    ) -> None:
        source = inspect.getsource(SandboxLifecycleOperationRunner)
        assert "RemoteExecutionService" not in source
        assert ".aexecute(" not in source
        assert ".session_scope(" not in source
