"""Provider-neutral, artifact-backed collection of a sandbox workspace patch.

The collector is deliberately a read/publish boundary.  It enumerates only the
virtual ``/workspace`` tree, publishes changed bytes through A2, and returns a
complete patch input to D3's coordinator.  It has no C1 importer or host-file
authority: applying the resulting patch remains an explicit later operation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import hashlib
from pathlib import PurePosixPath

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfile
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxArtifactPublication,
    SandboxError,
    SandboxErrorCode,
    SandboxRunRequest,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxArtifactPublisherPort,
    SandboxPatchCollection,
    SandboxPatchCollectorPort,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import ActiveSandbox
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    RawSnapshotEntry,
    WorkspacePathValidator,
)


class DeepAgentArtifactPatchCollector(SandboxPatchCollectorPort):
    """Collect one complete virtual-tree view and publish changed bytes to A2."""

    def __init__(
        self, *, publisher: SandboxArtifactPublisherPort, limits: SandboxLimitProfile
    ) -> None:
        self._publisher = publisher
        self._limits = limits

    async def collect(
        self, *, active: ActiveSandbox, request: SandboxRunRequest
    ) -> SandboxPatchCollection:
        baseline = {
            entry.path: entry for entry in request.create_request.snapshot.entries
        }
        baseline_directories = _parent_directories(baseline)
        result_entries: dict[str, RawSnapshotEntry] = {}
        discovered_directories: set[str] = set()
        pending = ["/workspace"]
        visited_directories: set[str] = set()
        total_scanned_bytes = 0

        while pending:
            directory = pending.pop()
            if directory in visited_directories:
                raise _incomplete()
            visited_directories.add(directory)
            listing = await active.backend.als(directory)
            if listing.error is not None or listing.entries is None:
                raise _incomplete()
            for item in listing.entries:
                raw_path = item.get("path")
                if not isinstance(raw_path, str):
                    raise _incomplete()
                path = _workspace_path(raw_path)
                if WorkspacePathValidator.is_excluded(path):
                    continue
                is_directory = bool(item.get("is_dir", False))
                if is_directory:
                    discovered_directories.add(path)
                    pending.append(path)
                    continue
                if path in result_entries:
                    raise _incomplete()
                content = await _download_one(active=active, path=path)
                total_scanned_bytes += len(content)
                if (
                    len(content) > self._limits.max_upload_file_bytes
                    or total_scanned_bytes > self._limits.max_upload_total_bytes
                    or len(result_entries) >= self._limits.max_upload_files
                ):
                    raise SandboxError(
                        SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                        "Sandbox patch collection exceeded the file snapshot ceiling.",
                    )
                digest = hashlib.sha256(content).hexdigest()
                original = baseline.get(path)
                if (
                    original is not None
                    and original.sha256 == digest
                    and original.size_bytes == len(content)
                ):
                    reference = original.payload_ref
                else:
                    reference = await self._publish_changed(
                        request=request,
                        path=path,
                        content=content,
                        digest=digest,
                    )
                result_entries[path] = RawSnapshotEntry(
                    path=path,
                    sha256=digest,
                    size_bytes=len(content),
                    payload_ref=reference,
                )

        return SandboxPatchCollection(
            result_entries=result_entries,
            directories=tuple(sorted(discovered_directories - baseline_directories)),
            complete=True,
        )

    async def _publish_changed(
        self,
        *,
        request: SandboxRunRequest,
        path: str,
        content: bytes,
        digest: str,
    ) -> ArtifactRef:
        publication = SandboxArtifactPublication(
            run_id=request.create_request.run_id,
            operation_id=request.create_request.operation_id,
            source_path=path,
            media_type="application/octet-stream",
            suggested_filename=PurePosixPath(path).name,
            title="Sandbox patch file",
            content_digest=digest,
            byte_size=len(content),
            idempotency_key=(
                "sandbox-patch-file:"
                + hashlib.sha256(
                    f"{request.create_request.operation_id}\0{path}".encode("utf-8")
                ).hexdigest()
            ),
        )
        reference = await self._publisher.publish(
            publication=publication, chunks=_single_chunk(content)
        )
        if reference.sha256 != digest or reference.size_bytes != len(content):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox patch bytes did not preserve their immutable artifact identity.",
            )
        return reference


async def _download_one(*, active: ActiveSandbox, path: str) -> bytes:
    try:
        # Native Deep Agents download is synchronous.  Keep it behind the
        # policy-wrapped backend and move only the provider call off-loop.
        responses = await asyncio.to_thread(active.backend.download_files, [path])
    except Exception as exc:  # noqa: BLE001 - provider details are unsafe here
        raise _incomplete() from exc
    if (
        len(responses) != 1
        or responses[0].error is not None
        or responses[0].content is None
        or responses[0].path != path
    ):
        raise _incomplete()
    return responses[0].content


def _workspace_path(raw_path: str) -> str:
    try:
        return WorkspacePathValidator.normalize(raw_path)
    except SandboxError as exc:
        raise _incomplete() from exc


def _parent_directories(entries: dict[str, object]) -> set[str]:
    directories: set[str] = set()
    for path in entries:
        parent = PurePosixPath(path).parent
        while str(parent) != "/workspace":
            directories.add(str(parent))
            parent = parent.parent
    return directories


async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _incomplete() -> SandboxError:
    return SandboxError(
        SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE,
        "The sandbox workspace could not be collected as a complete patch.",
    )


__all__ = ("DeepAgentArtifactPatchCollector",)
