"""Durable, no-blind-replay orchestration for one sandbox operation.

The coordinator is deliberately the only D3 layer that combines a provider
session, a lifecycle record, exact snapshot transfer, output disposition, and
usage attribution.  It has no Electron, local-workspace, or broker authority.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import hashlib

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfile
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxArtifactPublication,
    SandboxError,
    SandboxErrorCode,
    SandboxLifecycleRecord,
    SandboxLifecycleState,
    SandboxPatchImportRequest,
    SandboxPublishedArtifact,
    SandboxRunRequest,
    SandboxRunResult,
    SandboxUsageAttribution,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxArtifactPublisherPort,
    SandboxLifecycleStore,
    SandboxPatchCollectorPort,
    SandboxPatchImportPort,
    SandboxRuntimePort,
    SandboxSnapshotContentPort,
    SandboxUsageMeterPort,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    ActiveSandbox,
    RemoteExecutionService,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    WorkspaceManifestBuilder,
    WorkspacePatchBuilder,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class SandboxLifecycleCoordinator:
    """Execute one immutable sandbox request exactly once at the provider edge.

    A process crash after ``RUNNING`` may lose the in-memory backend handle, but
    not the durable ``execution_started`` fact.  Re-entry therefore never calls
    execute again: it returns an indeterminate error and reconciliation only
    terminates the provider resource.
    """

    _TRUNCATION_NOTE = "\n[sandbox: output truncated to the preview ceiling]"
    _REDACTION = "[REDACTED]"

    def __init__(
        self,
        *,
        service: RemoteExecutionService,
        lifecycle_store: SandboxLifecycleStore,
        runtime: SandboxRuntimePort,
        usage_meter: SandboxUsageMeterPort,
        snapshot_source: SandboxSnapshotContentPort | None = None,
        artifact_publisher: SandboxArtifactPublisherPort | None = None,
        patch_collector: SandboxPatchCollectorPort | None = None,
        patch_importer: SandboxPatchImportPort | None = None,
        limits: SandboxLimitProfile,
    ) -> None:
        self._service = service
        self._lifecycle = lifecycle_store
        self._runtime = runtime
        self._usage = usage_meter
        self._snapshot_source = snapshot_source
        self._artifacts = artifact_publisher
        self._patch_collector = patch_collector
        self._patch_importer = patch_importer
        self._limits = limits

    async def run(self, request: SandboxRunRequest) -> SandboxRunResult:
        """Run an immutable request, or fail closed instead of replaying it."""

        # This is the final D3 execution boundary.  Do not create a provider
        # session for an empty snapshot even if a malformed/internal caller
        # bypasses the operation adapter and plan materializer.
        if not request.create_request.snapshot.entries:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            )
        WorkspaceManifestBuilder.verify_manifest(
            request.create_request.snapshot, limits=self._limits
        )
        lifecycle = SandboxLifecycleRecord(
            operation_id=request.create_request.operation_id,
            run_id=request.create_request.run_id,
            idempotency_key=request.create_request.idempotency_key,
            request_digest=self._request_digest(request),
        )
        acquired = await self._lifecycle.acquire(record=lifecycle)
        lifecycle = acquired.record
        if (
            not acquired.created
            and lifecycle.state is not SandboxLifecycleState.REQUESTED
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "This sandbox operation has already started and will not be replayed.",
            )

        active: ActiveSandbox | None = None
        output = None
        uploaded_bytes = 0
        downloaded_bytes = 0
        artifacts: tuple[SandboxPublishedArtifact, ...] = ()
        patch = None
        try:
            active = await self._service.create(request.create_request)
            lifecycle = await self._transition(
                lifecycle,
                SandboxLifecycleState.PROVISIONED,
                provider_session_ref=active.session.provider_session_ref,
            )
            lifecycle = await self._transition(
                lifecycle, SandboxLifecycleState.UPLOADING
            )
            uploaded_bytes = await self._upload(active=active, request=request)

            # This durable fact is written before the external execute call.
            # Any crash after it is an indeterminate/reconcile path, never retry.
            lifecycle = await self._transition(
                lifecycle,
                SandboxLifecycleState.RUNNING,
                execution_started=True,
            )
            output = await self._runtime.execute(active=active, request=request)
            lifecycle = await self._transition(
                lifecycle, SandboxLifecycleState.COLLECTING
            )

            artifacts, downloaded_bytes = await self._collect_artifacts(
                active=active,
                request=request,
            )
            patch = await self._collect_patch(active=active, request=request)
            await self._usage.record_once(
                SandboxUsageAttribution(
                    operation_id=request.create_request.operation_id,
                    run_id=request.create_request.run_id,
                    duration_ms=output.duration_ms,
                    commands=1,
                    uploaded_bytes=uploaded_bytes,
                    downloaded_bytes=downloaded_bytes,
                )
            )
            lifecycle = await self._transition(
                lifecycle, SandboxLifecycleState.COMPLETED
            )
            result = SandboxRunResult(
                run_id=request.create_request.run_id,
                operation_id=request.create_request.operation_id,
                state=SandboxLifecycleState.COMPLETED,
                stdout=self._safe_preview(output.stdout, request.redaction_terms),
                stderr=self._safe_preview(output.stderr, request.redaction_terms),
                output_truncated=(
                    output.truncated
                    or self._preview_would_truncate(
                        output.stdout, request.redaction_terms
                    )
                    or self._preview_would_truncate(
                        output.stderr, request.redaction_terms
                    )
                ),
                exit_code=output.exit_code,
                duration_ms=output.duration_ms,
                artifacts=artifacts,
                patch=patch,
            )
        except asyncio.CancelledError:
            lifecycle = await self._record_cancelled(lifecycle)
            raise
        except SandboxError as error:
            lifecycle = await self._record_failure(lifecycle, error)
            raise
        except Exception as exc:  # noqa: BLE001 - keep provider failures safe
            lifecycle = await self._record_failure(
                lifecycle,
                SandboxError(
                    SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                    "The sandbox operation could not be confirmed.",
                ),
            )
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "The sandbox operation could not be confirmed.",
            ) from exc
        finally:
            if active is not None:
                lifecycle = await self._cleanup_active(
                    active=active, lifecycle=lifecycle
                )

        if lifecycle.state is SandboxLifecycleState.CLEANUP_PENDING:
            # Do not make a successful completion claim while teardown is
            # uncertain. The immutable outputs stay retrievable by their refs.
            return result.model_copy(
                update={"state": SandboxLifecycleState.CLEANUP_PENDING}
            )
        return result.model_copy(update={"state": lifecycle.state})

    async def import_patch(self, result: SandboxRunResult) -> str:
        """Hand a complete patch to C3's overlay-only import port.

        The port is deliberately optional while C3 ships.  Its contract is the
        sole D3→C3 seam; no provider or sandbox code can mutate a host directly.
        """

        if self._patch_importer is None or result.patch is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE,
                "No complete sandbox patch is available to import.",
            )
        WorkspacePatchBuilder.verify_patch(result.patch, require_complete=True)
        return await self._patch_importer.import_patch(
            SandboxPatchImportRequest(
                run_id=result.run_id,
                operation_id=result.operation_id,
                patch=result.patch,
            )
        )

    async def reconcile(
        self, *, limit: int = 100
    ) -> tuple[SandboxLifecycleRecord, ...]:
        """Recover safely after worker restart without repeating execution.

        ``requested`` is the one retry-safe state because no provider session
        exists. All later states either become indeterminate first or continue
        cleanup. A janitor can call this repeatedly; successful cleanup is
        idempotent and failed cleanup remains visibly pending.
        """

        reconciled: list[SandboxLifecycleRecord] = []
        for record in await self._lifecycle.list_recoverable(limit=limit):
            current = record
            if current.state is SandboxLifecycleState.REQUESTED:
                reconciled.append(current)
                continue
            if current.execution_started and current.state in {
                SandboxLifecycleState.RUNNING,
                SandboxLifecycleState.COLLECTING,
            }:
                current = await self._transition(
                    current, SandboxLifecycleState.INDETERMINATE
                )
            if current.provider_session_ref is None:
                reconciled.append(current)
                continue
            current = await self._transition(
                current,
                SandboxLifecycleState.CLEANUP_PENDING,
                cleanup_attempts=current.cleanup_attempts + 1,
            )
            cleaned = await self._service.cleanup_provider_ref(
                run_id=current.run_id,
                provider_session_ref=current.provider_session_ref,
                operation_id=current.operation_id,
            )
            if cleaned:
                current = await self._transition(current, SandboxLifecycleState.CLEANED)
            reconciled.append(current)
        return tuple(reconciled)

    async def _upload(
        self, *, active: ActiveSandbox, request: SandboxRunRequest
    ) -> int:
        if request.create_request.snapshot.entries and self._snapshot_source is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "Verified snapshot content is required for this sandbox operation.",
            )
        if self._snapshot_source is None:
            return 0
        return await self._runtime.upload(
            active=active,
            request=request,
            source=self._snapshot_source,
        )

    async def _collect_artifacts(
        self,
        *,
        active: ActiveSandbox,
        request: SandboxRunRequest,
    ) -> tuple[tuple[SandboxPublishedArtifact, ...], int]:
        if not request.deliverables:
            return (), 0
        if self._artifacts is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                "Sandbox artifact publication is not configured.",
            )
        files = await self._runtime.download(
            active=active,
            paths=tuple(item.path for item in request.deliverables),
        )
        by_path = {item.path: item for item in files}
        published: list[SandboxPublishedArtifact] = []
        total = 0
        for deliverable in request.deliverables:
            from agent_runtime.capabilities.sandbox.workspace_transfer import (  # noqa: PLC0415
                WorkspacePathValidator,
            )

            path = WorkspacePathValidator.normalize(deliverable.path)
            downloaded = by_path.get(path)
            if downloaded is None:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                    "The sandbox provider omitted a requested deliverable.",
                )
            state: dict[str, int | str] = {"size": 0, "digest": ""}
            publication = SandboxArtifactPublication(
                run_id=request.create_request.run_id,
                operation_id=request.create_request.operation_id,
                source_path=path,
                media_type=deliverable.media_type,
                suggested_filename=deliverable.suggested_filename,
                title=deliverable.title,
                idempotency_key=self._artifact_idempotency(
                    request.create_request.operation_id, path
                ),
            )
            ref = await self._artifacts.publish(
                publication=publication,
                chunks=self._verified_download_stream(downloaded.chunks, state),
            )
            digest = str(state["digest"])
            size = int(state["size"])
            if not digest or ref.sha256 != digest or ref.size_bytes != size:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox deliverable bytes did not match the published artifact.",
                )
            total += size
            if total > self._limits.download_changed_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox deliverable bytes exceed the download ceiling.",
                )
            published.append(
                SandboxPublishedArtifact(
                    source_path=path,
                    media_type=deliverable.media_type,
                    suggested_filename=deliverable.suggested_filename,
                    artifact_ref=ref,
                )
            )
        return tuple(published), total

    async def _collect_patch(
        self, *, active: ActiveSandbox, request: SandboxRunRequest
    ):
        if not request.collect_patch:
            return None
        if self._patch_collector is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE,
                "Sandbox patch collection is not configured.",
            )
        collection = await self._patch_collector.collect(active=active, request=request)
        patch = WorkspacePatchBuilder.build(
            baseline=request.create_request.snapshot,
            result_entries=collection.result_entries,
            directories=collection.directories,
            moves=collection.moves,
            complete=collection.complete,
            limits=self._limits,
        )
        WorkspacePatchBuilder.verify_patch(patch, require_complete=True)
        return patch

    async def _cleanup_active(
        self, *, active: ActiveSandbox, lifecycle: SandboxLifecycleRecord
    ) -> SandboxLifecycleRecord:
        result = await self._service.teardown(
            active.session.session_id, operation_id=lifecycle.operation_id
        )
        if result is not None and result.cleanup_state == "deleted":
            if lifecycle.state is SandboxLifecycleState.CLEANED:
                return lifecycle
            if lifecycle.state is SandboxLifecycleState.REQUESTED:
                return lifecycle
            return await self._transition(lifecycle, SandboxLifecycleState.CLEANED)
        if lifecycle.state is SandboxLifecycleState.CLEANED:
            return lifecycle
        return await self._transition(
            lifecycle,
            SandboxLifecycleState.CLEANUP_PENDING,
            cleanup_attempts=lifecycle.cleanup_attempts + 1,
        )

    async def _record_cancelled(
        self, lifecycle: SandboxLifecycleRecord
    ) -> SandboxLifecycleRecord:
        if lifecycle.execution_started:
            return await self._transition(
                lifecycle, SandboxLifecycleState.INDETERMINATE
            )
        return await self._transition(lifecycle, SandboxLifecycleState.CANCELLED)

    async def _record_failure(
        self, lifecycle: SandboxLifecycleRecord, error: SandboxError
    ) -> SandboxLifecycleRecord:
        if error.code is SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE:
            if lifecycle.state in {
                SandboxLifecycleState.RUNNING,
                SandboxLifecycleState.COLLECTING,
            }:
                return await self._transition(
                    lifecycle, SandboxLifecycleState.INDETERMINATE
                )
        if lifecycle.state is SandboxLifecycleState.REQUESTED:
            return await self._transition(lifecycle, SandboxLifecycleState.FAILED)
        if lifecycle.state in {
            SandboxLifecycleState.PROVISIONED,
            SandboxLifecycleState.UPLOADING,
            SandboxLifecycleState.RUNNING,
            SandboxLifecycleState.COLLECTING,
        }:
            return await self._transition(lifecycle, SandboxLifecycleState.FAILED)
        return lifecycle

    async def _transition(
        self,
        lifecycle: SandboxLifecycleRecord,
        state: SandboxLifecycleState,
        *,
        execution_started: bool | None = None,
        provider_session_ref: str | None = None,
        cleanup_attempts: int | None = None,
    ) -> SandboxLifecycleRecord:
        return await self._lifecycle.update(
            record=lifecycle.transition(
                state=state,
                execution_started=execution_started,
                provider_session_ref=provider_session_ref,
                cleanup_attempts=cleanup_attempts,
            )
        )

    async def _verified_download_stream(
        self,
        chunks: AsyncIterator[bytes],
        state: dict[str, int | str],
    ) -> AsyncIterator[bytes]:
        hasher = hashlib.sha256()
        size = 0
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                    "Sandbox output was not a byte stream.",
                )
            size += len(chunk)
            if size > self._limits.download_changed_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox output exceeded the download ceiling.",
                )
            hasher.update(chunk)
            yield chunk
        state["size"] = size
        state["digest"] = hasher.hexdigest()

    def _safe_preview(self, value: str, terms: tuple[str, ...]) -> str:
        redacted = value
        for term in sorted(set(terms), key=len, reverse=True):
            redacted = redacted.replace(term, self._REDACTION)
        encoded = redacted.encode("utf-8")
        if len(encoded) <= self._limits.combined_command_preview_bytes:
            return redacted
        prefix = encoded[: self._limits.combined_command_preview_bytes].decode(
            "utf-8", errors="ignore"
        )
        return prefix + self._TRUNCATION_NOTE

    def _preview_would_truncate(self, value: str, terms: tuple[str, ...]) -> bool:
        redacted = value
        for term in sorted(set(terms), key=len, reverse=True):
            redacted = redacted.replace(term, self._REDACTION)
        return (
            len(redacted.encode("utf-8")) > self._limits.combined_command_preview_bytes
        )

    @staticmethod
    def _artifact_idempotency(operation_id: str, path: str) -> str:
        return (
            "sandbox:"
            + hashlib.sha256(f"{operation_id}\x00{path}".encode("utf-8")).hexdigest()
        )

    @staticmethod
    def _request_digest(request: SandboxRunRequest) -> str:
        return canonical_json_sha256(
            {
                "create_request": request.create_request.model_dump(mode="json"),
                "command_sha256": hashlib.sha256(
                    request.command.encode("utf-8")
                ).hexdigest(),
                "deliverables": [
                    item.model_dump(mode="json") for item in request.deliverables
                ],
                "collect_patch": request.collect_patch,
            }
        )


__all__ = ("SandboxLifecycleCoordinator",)
