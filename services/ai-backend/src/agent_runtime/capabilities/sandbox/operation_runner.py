"""Coordinator-owned runtime implementation of ``SandboxOperationRunnerPort``.

This is deliberately an anti-bypass composition boundary.  It receives the
gateway's immutable virtual-path manifest, translates it to the established
``SandboxLifecycleCoordinator`` request, and publishes only the bounded result
envelope through A2.  It has no provider/session/backend/host-workspace
dependency and cannot execute a command except by invoking the coordinator.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import hashlib
import re
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfile
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxCreateRequest,
    SandboxEgressPolicy,
    SandboxError,
    SandboxErrorCode,
    SandboxRunRequest,
    SandboxRunResult,
    SandboxSnapshot,
)
from agent_runtime.capabilities.sandbox.operation_adapter import (
    SandboxOperationAvailability,
    SandboxOperationLaunch,
    SandboxOperationRunResult,
    SandboxOperationRunnerPort,
    SandboxPatchManifestRef,
)
from agent_runtime.capabilities.sandbox.ports import SandboxSnapshotContentPort
from agent_runtime.capabilities.sandbox.result_publisher import (
    SandboxResultPublication,
    SandboxResultPublisherPort,
)
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxSnapshotFileStorePort,
    SandboxSnapshotManifest,
    SandboxSnapshotSourceKind,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    RawSnapshotEntry,
    WorkspaceManifestBuilder,
    WorkspacePatchBuilder,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec


_MAX_RESULT_BYTES = 128 * 1024
_RESULT_TRUNCATION_NOTE = "\n[sandbox: output truncated to result ceiling]"
_ARTIFACT_BLOB_REF = re.compile(r"^artifact-blob://sha256/[0-9a-f]{64}$")


@runtime_checkable
class SandboxLifecycleCoordinatorPort(Protocol):
    """The sole execution authority available to this runner."""

    async def run(self, request: SandboxRunRequest) -> SandboxRunResult:
        """Execute the immutable request exactly once at the provider boundary."""
        ...


class SandboxSnapshotStoreContentSource(SandboxSnapshotContentPort):
    """The coordinator's injected reader over C1/A2 immutable content refs.

    AC7's existing coordinator asks for ``ArtifactRef`` while the v2.1
    snapshot contract can also address a digest-pinned artifact blob.  The
    runner stores that already-validated logical ref in the coordinator-local
    ``artifact_id`` slot; this adapter resolves it only through the injected
    snapshot store.  The value is never provider-visible (the runtime sends
    virtual path plus bytes only) or returned in an operation result.

    Runtime composition constructs *one* instance and injects it into both the
    coordinator and the runner's enclosing bundle.  It is stateless, so
    concurrent launches cannot overwrite each other's snapshot routing.
    """

    def __init__(self, *, store: SandboxSnapshotFileStorePort) -> None:
        self._store = store

    async def open(self, ref: ArtifactRef) -> AsyncIterator[bytes]:
        content_ref = ref.artifact_id
        lowered = content_ref.lower()
        if (
            not content_ref
            or content_ref.startswith(("/", "~", "\\"))
            or lowered.startswith(("file://", "filesystem://"))
            or (len(content_ref) >= 3 and content_ref[1:3] in {":/", ":\\"})
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content did not match the approved manifest.",
            )
        if _ARTIFACT_BLOB_REF.fullmatch(content_ref) is None:
            try:
                ArtifactContentRefCodec.parse(content_ref)
            except Exception as exc:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content did not match the approved manifest.",
                ) from exc
        return await self._store.open(content_ref=content_ref)


class SandboxLifecycleOperationRunner(SandboxOperationRunnerPort):
    """Concrete gateway runner with no direct provider or host-write seam."""

    def __init__(
        self,
        *,
        coordinator: SandboxLifecycleCoordinatorPort,
        result_publisher: SandboxResultPublisherPort,
        limits: SandboxLimitProfile,
        availability: SandboxOperationAvailability,
    ) -> None:
        self._coordinator = coordinator
        self._result_publisher = result_publisher
        self._limits = limits
        self._availability = availability

    @property
    def availability(self) -> SandboxOperationAvailability:
        """Expose the composition-root verified capability posture unchanged."""
        return self._availability

    async def run(
        self, *, request: SandboxOperationLaunch
    ) -> SandboxOperationRunResult:
        """Run one immutable launch through the coordinator and publish its result.

        Availability and all unexpected failures are typed, bounded
        ``SandboxError`` instances.  Coordinator errors propagate unchanged so
        its persisted no-blind-retry state remains the authority.
        """

        if not self.availability.available:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
                "Sandbox execution is unavailable.",
            )
        coordinator_request, collect_patch = self._coordinator_request(request)
        try:
            coordinator_result = await self._coordinator.run(coordinator_request)
            patch = None
            if collect_patch:
                if coordinator_result.patch is None:
                    raise SandboxError(
                        SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE,
                        "Sandbox patch collection was not confirmed.",
                    )
                patch = await self._publish_patch(
                    request=request,
                    result=coordinator_result,
                    baseline_snapshot_digest=(
                        coordinator_request.create_request.snapshot.manifest_sha256
                    ),
                )
                # This is a reviewable proposal, not a write.  C1/UI later
                # invokes its own explicit apply operation using this immutable
                # artifact-backed handoff; neither the overlay nor the host
                # workspace changes during command completion.
            elif coordinator_result.patch is not None:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox returned a patch outside its approved overlay scope.",
                )
            artifact_ref = await self._publish_result(
                request=request,
                result=coordinator_result,
                patch=patch,
            )
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak provider/storage detail
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "The sandbox operation could not be confirmed.",
            ) from exc

        try:
            ArtifactContentRefCodec.parse(artifact_ref)
            return SandboxOperationRunResult(
                run_id=request.run_id,
                operation_id=request.operation_id,
                result_ref=artifact_ref,
                safe_summary=self._safe_summary(coordinator_result),
                patch=patch,
            )
        except (ValueError, ValidationError) as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox result publication did not return an immutable artifact revision.",
            ) from exc

    def _coordinator_request(
        self, launch: SandboxOperationLaunch
    ) -> tuple[SandboxRunRequest, bool]:
        snapshot, collect_patch = self._coordinator_snapshot(launch.snapshot)
        return SandboxRunRequest(
            create_request=SandboxCreateRequest(
                run_id=launch.run_id,
                operation_id=launch.operation_id,
                snapshot=snapshot,
                egress=SandboxEgressPolicy(mode=launch.egress_mode),
                secret_refs=(),
                limit_profile=self._limits.name,
                approval_id=f"operation:{launch.operation_id}",
                owner_tag=launch.run_id,
                idempotency_key=launch.idempotency_key,
            ),
            command=launch.command,
            deliverables=(),
            collect_patch=collect_patch,
            redaction_terms=(),
        ), collect_patch

    def _coordinator_snapshot(
        self, manifest: SandboxSnapshotManifest
    ) -> tuple[SandboxSnapshot, bool]:
        """Re-validate and translate the v2.1 virtual manifest for AC7 coordinator."""

        # Revalidation defends against an unsafe Pydantic ``model_construct``
        # or a mutable caller object before any coordinator/provider side
        # effect.  It also rejects physical/file refs before coordinator.run.
        try:
            verified = SandboxSnapshotManifest.model_validate(
                manifest.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_INVALID,
                "Sandbox snapshot is invalid.",
            ) from exc
        # A model operation is authorized only against a nonempty, sealed C1
        # selection.  Reject before creating the coordinator request so this
        # guard holds even if a caller bypassed plan materialization with a
        # constructed manifest.
        if not verified.entries:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            )
        raw_entries: list[RawSnapshotEntry] = []
        for entry in verified.entries:
            raw_entries.append(
                RawSnapshotEntry(
                    path=entry.virtual_path,
                    sha256=entry.content_digest,
                    size_bytes=entry.size_bytes,
                    executable=entry.executable,
                    payload_ref=ArtifactRef(
                        # See SandboxSnapshotStoreContentSource.  This stays
                        # local to the coordinator's byte-reader seam.
                        artifact_id=entry.content_ref,
                        sha256=entry.content_digest,
                        size_bytes=entry.size_bytes,
                    ),
                )
            )
        transfer = WorkspaceManifestBuilder.build(
            workspace_id=f"sandbox:{verified.manifest_digest}",
            root_grant_id=f"sandbox-snapshot:{verified.manifest_digest}",
            raw_entries=raw_entries,
            limits=self._limits,
        )
        if len(transfer.entries) != len(verified.entries):
            # AC7 historically elides excluded paths.  A gateway manifest is
            # an immutable approved fact, so D3 must not execute a silently
            # smaller snapshot: fail before the coordinator/provider instead.
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_INVALID,
                "Sandbox snapshot contains an unsupported virtual path.",
            )
        return (
            WorkspaceManifestBuilder.to_sandbox_snapshot(
                transfer,
                snapshot_id=verified.snapshot_id,
            ),
            any(
                entry.source_kind is SandboxSnapshotSourceKind.OVERLAY
                for entry in verified.entries
            ),
        )

    async def _publish_result(
        self,
        *,
        request: SandboxOperationLaunch,
        result: SandboxRunResult,
        patch: SandboxPatchManifestRef | None,
    ) -> str:
        if result.artifacts:
            # The current AC7 deliverable port returns digest/size but not an
            # immutable revision URI.  Do not downgrade that incomplete shape
            # into a model-visible output.  Gateway launch presently asks for
            # no deliverables; future wiring must make this revision-aware.
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox deliverables require immutable artifact revisions.",
            )
        content = self._bounded_result_bytes(result, patch=patch)
        publication = SandboxResultPublication(
            run_id=request.run_id,
            operation_id=request.operation_id,
            content_digest=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            idempotency_key=(
                "sandbox-result:"
                + hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
            ),
        )
        return await self._result_publisher.publish_result(
            publication=publication,
            chunks=self._single_chunk(content),
        )

    async def _publish_patch(
        self,
        *,
        request: SandboxOperationLaunch,
        result: SandboxRunResult,
        baseline_snapshot_digest: str,
    ) -> SandboxPatchManifestRef:
        patch = result.patch
        if patch is None:  # pragma: no cover - guarded by run() above
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE,
                "Sandbox patch collection was not confirmed.",
            )
        WorkspacePatchBuilder.verify_patch(patch, require_complete=True)
        if patch.baseline_manifest_sha256 != baseline_snapshot_digest:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox patch does not match the approved snapshot.",
            )
        content = canonical_json_bytes(
            {"v": 1, "kind": "sandbox_patch", "patch": patch.model_dump(mode="json")}
        )
        if len(content) > self._limits.download_changed_bytes:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Sandbox patch representation exceeds the publication ceiling.",
            )
        publication = SandboxResultPublication(
            run_id=request.run_id,
            operation_id=request.operation_id,
            document_kind="patch",
            content_digest=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            idempotency_key=(
                "sandbox-patch:"
                + hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
            ),
        )
        patch_ref = await self._result_publisher.publish_patch(
            publication=publication,
            chunks=self._single_chunk(content),
        )
        try:
            ArtifactContentRefCodec.parse(patch_ref)
            return SandboxPatchManifestRef(
                patch_ref=patch_ref,
                baseline_snapshot_digest=patch.baseline_manifest_sha256,
                manifest_digest=patch.manifest_sha256,
                complete=True,
            )
        except (ValueError, ValidationError) as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox patch publication did not return an immutable artifact revision.",
            ) from exc

    @staticmethod
    async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
        yield content

    @staticmethod
    def _safe_summary(result: SandboxRunResult) -> str:
        if result.state.value == "cleanup_pending":
            return "Sandbox command completed; cleanup confirmation is pending."
        if result.exit_code in (None, 0):
            return "Sandbox command completed."
        return "Sandbox command completed with a non-zero exit status."

    @staticmethod
    def _bounded_result_bytes(
        result: SandboxRunResult, *, patch: SandboxPatchManifestRef | None
    ) -> bytes:
        """Encode only coordinator-safe fields within the published-result ceiling."""

        document: dict[str, object] = {
            "v": 1,
            "state": result.state.value,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "output_truncated": result.output_truncated,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "patch_ref": patch.patch_ref if patch is not None else None,
        }
        encoded = canonical_json_bytes(document)
        if len(encoded) <= _MAX_RESULT_BYTES:
            return encoded

        # Coordinator previews are individually bounded.  This second bound
        # applies to the aggregate result artifact so a large stdout *and*
        # stderr cannot exceed the A2 result-publication ceiling.
        document["output_truncated"] = True
        empty_size = len(canonical_json_bytes({**document, "stdout": "", "stderr": ""}))
        per_stream_budget = max(0, (_MAX_RESULT_BYTES - empty_size - 64) // 2)
        while True:
            document["stdout"] = SandboxLifecycleOperationRunner._truncate_text(
                result.stdout, per_stream_budget
            )
            document["stderr"] = SandboxLifecycleOperationRunner._truncate_text(
                result.stderr, per_stream_budget
            )
            encoded = canonical_json_bytes(document)
            if len(encoded) <= _MAX_RESULT_BYTES or per_stream_budget == 0:
                break
            per_stream_budget = max(0, per_stream_budget - 64)
        return encoded

    @staticmethod
    def _truncate_text(value: str, byte_budget: int) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= byte_budget:
            return value
        note = _RESULT_TRUNCATION_NOTE.encode("utf-8")
        if byte_budget <= len(note):
            return note[:byte_budget].decode("utf-8", errors="ignore")
        return (
            encoded[: byte_budget - len(note)].decode("utf-8", errors="ignore")
            + _RESULT_TRUNCATION_NOTE
        )


__all__ = (
    "SandboxLifecycleCoordinatorPort",
    "SandboxLifecycleOperationRunner",
    "SandboxSnapshotStoreContentSource",
    "SandboxResultPublication",
    "SandboxResultPublisherPort",
)
