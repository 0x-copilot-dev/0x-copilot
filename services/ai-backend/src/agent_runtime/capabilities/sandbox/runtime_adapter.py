"""Deep Agents transfer/execution adapter for the D3 lifecycle coordinator.

This adapter intentionally translates only provider-safe virtual paths and
content-addressed streams.  It does not inspect a host filesystem, own an
overlay, or expose the provider client outside the active sandbox session.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import hashlib
import time

from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxError,
    SandboxErrorCode,
    SandboxRunRequest,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxDownloadedFile,
    SandboxProcessOutput,
    SandboxRuntimePort,
    SandboxSnapshotContentPort,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import ActiveSandbox
from agent_runtime.capabilities.sandbox.workspace_transfer import WorkspacePathValidator


class DeepAgentSandboxRuntime(SandboxRuntimePort):
    """Adapter over the pinned Deep Agents sandbox protocol.

    Native protocol uploads/downloads are whole-file APIs.  D3 therefore reads
    each bounded input through a streaming, digest-verifying reader before the
    single provider call, and exposes downloads again as streams to A2.  The
    configured per-file snapshot ceiling bounds that temporary materialization.
    """

    async def upload(
        self,
        *,
        active: ActiveSandbox,
        request: SandboxRunRequest,
        source: SandboxSnapshotContentPort,
    ) -> int:
        uploaded = 0
        files: list[tuple[str, bytes]] = []
        for entry in request.create_request.snapshot.entries:
            content = await self._read_exact(source=source, ref=entry.payload_ref)
            if len(content) != entry.size_bytes:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content size verification failed.",
                )
            files.append((entry.path, content))
            uploaded += len(content)
        if not files:
            return 0
        try:
            responses = await asyncio.to_thread(active.backend.upload_files, files)
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider boundary
            raise SandboxError(
                SandboxErrorCode.SANDBOX_UPLOAD_FAILED,
                "The sandbox provider could not upload the verified snapshot.",
            ) from exc
        if len(responses) != len(files) or any(
            response.error for response in responses
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_UPLOAD_FAILED,
                "The sandbox provider did not confirm every snapshot file.",
            )
        return uploaded

    async def execute(
        self, *, active: ActiveSandbox, command: str
    ) -> SandboxProcessOutput:
        started = time.monotonic()
        try:
            response = await active.backend.aexecute(command)
        except TimeoutError as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_COMMAND_TIMEOUT,
                "The sandbox command exceeded its time limit.",
            ) from exc
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider boundary
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "The sandbox provider did not confirm command completion.",
            ) from exc
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        # The pinned Deep Agents response has one combined output field.  The
        # coordinator keeps separate fields so a provider with native stderr
        # can fill it without changing any durable or UI contract.
        return SandboxProcessOutput(
            stdout=response.output or "",
            stderr="",
            exit_code=response.exit_code,
            duration_ms=duration_ms,
            truncated=bool(response.truncated),
        )

    async def download(
        self,
        *,
        active: ActiveSandbox,
        paths: tuple[str, ...],
    ) -> tuple[SandboxDownloadedFile, ...]:
        if not paths:
            return ()
        normalized = tuple(WorkspacePathValidator.normalize(path) for path in paths)
        try:
            responses = await asyncio.to_thread(
                active.backend.download_files, list(normalized)
            )
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider boundary
            raise SandboxError(
                SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                "The sandbox provider could not download requested output.",
            ) from exc
        if len(responses) != len(normalized):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                "The sandbox provider did not return every requested output.",
            )
        downloaded: list[SandboxDownloadedFile] = []
        for expected_path, response in zip(normalized, responses, strict=True):
            if (
                response.error is not None
                or response.content is None
                or response.path != expected_path
            ):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                    "The sandbox provider could not verify requested output.",
                )
            downloaded.append(
                SandboxDownloadedFile(
                    path=expected_path,
                    chunks=self._single_chunk(response.content),
                )
            )
        return tuple(downloaded)

    @staticmethod
    async def _read_exact(
        *, source: SandboxSnapshotContentPort, ref: ArtifactRef
    ) -> bytes:
        hasher = hashlib.sha256()
        size = 0
        chunks: list[bytes] = []
        stream = await source.open(ref)
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content verification failed.",
                )
            size += len(chunk)
            if size > ref.size_bytes:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content exceeded its declared size.",
                )
            hasher.update(chunk)
            chunks.append(chunk)
        if size != ref.size_bytes or hasher.hexdigest() != ref.sha256:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content digest verification failed.",
            )
        return b"".join(chunks)

    @staticmethod
    async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
        yield content


__all__ = ("DeepAgentSandboxRuntime",)
