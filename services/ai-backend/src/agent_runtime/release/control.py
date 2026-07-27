"""Read-only production release access and development-only local mutation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationScope,
    HarnessManifest,
    HarnessManifestPointer,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.release.local_control import (
    LocalReleaseControlPolicy,
    ReleaseControlCommand,
    ReleaseControlCommandName,
)
from agent_runtime.release.manifest import (
    ReleaseManifestVerifier,
    VerifiedHarnessManifest,
)
from agent_runtime.surfaces_v2.canonical_json import sha256_hex


class ReleaseActivationError(RuntimeError):
    """A manifest activation or rollback violates release lineage."""


@dataclass(frozen=True)
class LocalReleaseExport:
    """Explicit local export result with a content digest."""

    payload: bytes
    payload_digest: str


class RuntimeReleaseReader:
    """Production-safe reader: verify active state without any write method."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        verifier: ReleaseManifestVerifier,
    ) -> None:
        self._repository = repository
        self._verifier = verifier

    async def active_manifest(
        self,
        *,
        scope: EvaluationScope,
    ) -> VerifiedHarnessManifest | None:
        pointer = await self._repository.get_active_harness_manifest(scope)
        if pointer is None:
            return None
        manifest = await self._repository.get_harness_manifest(
            scope,
            manifest_id=pointer.manifest_id,
            revision=pointer.manifest_revision,
        )
        if manifest is None:
            raise ReleaseActivationError("active manifest pointer is dangling")
        if manifest.payload_digest != pointer.manifest_payload_digest:
            raise ReleaseActivationError("active manifest pointer digest conflicts")
        return self._verifier.verify(manifest)


class LocalReleaseControlService:
    """Loopback-only development/dogfood activation, rollback, and export."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        verifier: ReleaseManifestVerifier,
        policy: LocalReleaseControlPolicy,
        scope: EvaluationScope,
        allowed_variant_digests: Mapping[str, str] | None = None,
    ) -> None:
        self._repository = repository
        self._verifier = verifier
        self._policy = policy
        self._scope = scope
        self._allowed_variant_digests = (
            None if allowed_variant_digests is None else dict(allowed_variant_digests)
        )

    def verify(
        self,
        *,
        manifest: HarnessManifest,
        peer_host: str,
    ) -> VerifiedHarnessManifest:
        """Verify one externally signed manifest without persisting it."""

        self._policy.authorize_peer(peer_host)
        self._policy.authorize_command(
            ReleaseControlCommand(
                name=ReleaseControlCommandName.VERIFY,
                manifest_ref=manifest.manifest_ref,
            )
        )
        return self._verifier.verify(manifest)

    async def install(
        self,
        *,
        manifest: HarnessManifest,
        activation_decision_id: str,
        peer_host: str,
    ) -> HarnessManifestPointer:
        """Verify and atomically activate an externally signed local release."""

        self._authorize_mutation(
            peer_host=peer_host,
            command_name=ReleaseControlCommandName.OVERRIDE,
            target_manifest_digest=manifest.payload_digest,
            rationale="activate externally signed local manifest",
        )
        verified = self._verifier.verify(manifest)
        self._validate_catalog(verified.manifest)
        current = await self._repository.get_active_harness_manifest(self._scope)
        current_ref = await self._pointer_manifest_ref(pointer=current)
        if manifest.previous_manifest_ref != current_ref:
            raise ReleaseActivationError(
                "manifest previous reference does not match active pointer"
            )
        await self._repository.put_harness_manifest(self._scope, verified.manifest)
        replacement = self._pointer_for(
            manifest=verified.manifest,
            activation_decision_id=activation_decision_id,
            previous_manifest_ref=current_ref,
            pointer_version=1 if current is None else current.pointer_version + 1,
        )
        return await self._repository.compare_and_set_active_harness_manifest(
            self._scope,
            expected=current,
            replacement=replacement,
        )

    def _validate_catalog(self, manifest: HarnessManifest) -> None:
        catalog = self._allowed_variant_digests
        if catalog is None:
            return
        for assignment in manifest.assignments:
            expected = catalog.get(assignment.variant_ref)
            if expected is None or expected != assignment.variant_digest:
                raise ReleaseActivationError(
                    "manifest references an unknown or changed release variant"
                )

    async def rollback(
        self,
        *,
        target_manifest_id: str,
        target_manifest_revision: str,
        activation_decision_id: str,
        rationale: str,
        peer_host: str,
    ) -> HarnessManifestPointer:
        """Atomically restore only the immediately preceding verified manifest."""

        target = await self._repository.get_harness_manifest(
            self._scope,
            manifest_id=target_manifest_id,
            revision=target_manifest_revision,
        )
        if target is None:
            raise ReleaseActivationError("rollback target manifest is missing")
        self._authorize_mutation(
            peer_host=peer_host,
            command_name=ReleaseControlCommandName.ROLLBACK,
            target_manifest_digest=target.payload_digest,
            rationale=rationale,
        )
        verified = self._verifier.verify(target)
        current = await self._repository.get_active_harness_manifest(self._scope)
        if current is None or current.previous_manifest_ref is None:
            raise ReleaseActivationError("active manifest has no rollback predecessor")
        if verified.manifest.manifest_ref != current.previous_manifest_ref:
            raise ReleaseActivationError(
                "rollback target is not the active manifest predecessor"
            )
        current_ref = await self._pointer_manifest_ref(pointer=current)
        replacement = self._pointer_for(
            manifest=verified.manifest,
            activation_decision_id=activation_decision_id,
            previous_manifest_ref=current_ref,
            pointer_version=current.pointer_version + 1,
        )
        return await self._repository.compare_and_set_active_harness_manifest(
            self._scope,
            expected=current,
            replacement=replacement,
        )

    async def export(
        self,
        *,
        output_path: str,
        peer_host: str,
    ) -> LocalReleaseExport:
        """Return repository-owned export bytes without writing an arbitrary path."""

        command = ReleaseControlCommand(
            name=ReleaseControlCommandName.EXPORT,
            output_path=output_path,
        )
        self._policy.authorize_peer(peer_host)
        self._policy.authorize_command(command)
        payload = await self._repository.export_scope(self._scope)
        return LocalReleaseExport(
            payload=payload,
            payload_digest=sha256_hex(payload),
        )

    def _authorize_mutation(
        self,
        *,
        peer_host: str,
        command_name: ReleaseControlCommandName,
        target_manifest_digest: str,
        rationale: str,
    ) -> None:
        command = ReleaseControlCommand(
            name=command_name,
            target_manifest_digest=target_manifest_digest,
            rationale=rationale,
        )
        self._policy.authorize_peer(peer_host)
        self._policy.authorize_command(command)

    async def _pointer_manifest_ref(
        self,
        *,
        pointer: HarnessManifestPointer | None,
    ) -> str | None:
        if pointer is None:
            return None
        manifest = await self._repository.get_harness_manifest(
            self._scope,
            manifest_id=pointer.manifest_id,
            revision=pointer.manifest_revision,
        )
        if manifest is None:
            raise ReleaseActivationError("active manifest pointer is dangling")
        if manifest.payload_digest != pointer.manifest_payload_digest:
            raise ReleaseActivationError("active manifest pointer digest conflicts")
        return manifest.manifest_ref

    @staticmethod
    def _pointer_for(
        *,
        manifest: HarnessManifest,
        activation_decision_id: str,
        previous_manifest_ref: str | None,
        pointer_version: int,
    ) -> HarnessManifestPointer:
        values: dict[str, object] = {
            "pointer_version": pointer_version,
            "manifest_id": manifest.manifest_id,
            "manifest_revision": manifest.revision,
            "manifest_payload_digest": manifest.payload_digest,
            "activation_decision_id": activation_decision_id,
            "previous_manifest_ref": previous_manifest_ref,
            "updated_at": datetime.now(timezone.utc),
        }
        return HarnessManifestPointer(
            **values,
            pointer_digest=HarnessManifestPointer.digest_for(**values),
        )


__all__ = (
    "LocalReleaseControlService",
    "LocalReleaseExport",
    "ReleaseActivationError",
    "RuntimeReleaseReader",
)
