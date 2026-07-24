"""Durable C3 workspace proposal material and private host-session adapters.

The worker stores only virtual paths, content-addressed bytes, and C2 contracts.
No physical path, root handle, or one-use permit enters a model-visible object
or a queue command.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.desktop.workspace_authority import (
    WorkspaceAuthorityContractError,
    WorkspaceAuthorityPort,
    WorkspaceChangeEntry,
    WorkspaceCommitPermitSource,
    WorkspaceEffectExecutor,
    WorkspacePrecondition,
    WorkspacePrepareCommand,
    WorkspaceProposalMaterial,
    WorkspaceProposalResolver,
)
from agent_runtime.capabilities.desktop.broker_client import (
    WorkspaceCommitResult,
    WorkspacePreparedEffect,
)
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    OverlayEntry,
    WorkspaceEntryKind,
    WorkspaceOperation,
    blob_key_from_content_ref,
)
from agent_runtime.capabilities.workspace.effects import (
    WorkspaceGrantBinding,
    WorkspaceProposalStorePort,
    WorkspaceStoredProposal,
)
from agent_runtime.capabilities.workspace.ports import (
    WorkspaceBaseReadPort,
    WorkspaceOverlayStorePort,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    ArtifactReferenceRepositoryPort,
)

_MAX_MATERIAL_BYTES = 2 * 1024 * 1024
_MATERIAL_PREFIX = "workspace-material://sha256/"
_TARGET_PREFIX = "workspace-target://sha256/"
_PRECONDITION_PREFIX = "workspace-precondition://sha256/"


@dataclass(frozen=True)
class WorkspaceHostSession:
    """Main-established, run-scoped C2 authority; AI-backend cannot mint it."""

    grants: tuple[WorkspaceGrantBinding, ...]
    base_read: WorkspaceBaseReadPort
    read_capability: str
    authority: WorkspaceAuthorityPort
    permit_source: WorkspaceCommitPermitSource

    def __post_init__(self) -> None:
        if not self.read_capability:
            raise ValueError("workspace host session requires a read capability")
        names = [grant.mount_name for grant in self.grants]
        if len(names) != len(set(names)):
            raise ValueError("workspace host session mounts must be unique")


@runtime_checkable
class WorkspaceHostSessionRegistryPort(Protocol):
    """Read an already-established host session for a verified run scope."""

    def get(self, scope: EffectExecutionScope) -> WorkspaceHostSession | None:
        """Return current authority, or ``None`` when web/offline/revoked."""


class InMemoryWorkspaceHostSessionRegistry:
    """Hermetic registry and the desktop-host integration seam."""

    def __init__(self) -> None:
        self._sessions: dict[
            tuple[str, str, str], tuple[EffectExecutionScope, WorkspaceHostSession]
        ] = {}

    def bind(
        self, *, scope: EffectExecutionScope, session: WorkspaceHostSession
    ) -> None:
        self._sessions[self._key(scope)] = (scope, session)

    def revoke(self, *, scope: EffectExecutionScope) -> None:
        self._sessions.pop(self._key(scope), None)

    def get(self, scope: EffectExecutionScope) -> WorkspaceHostSession | None:
        stored = self._sessions.get(self._key(scope))
        return stored[1] if stored is not None and stored[0] == scope else None

    @staticmethod
    def _key(scope: EffectExecutionScope) -> tuple[str, str, str]:
        return scope.org_id, scope.user_id, scope.run_id


@dataclass(frozen=True)
class RuntimeWorkspaceProposalStore(WorkspaceProposalStorePort):
    """Persist exact proposal/target/precondition bodies as EFFECT references."""

    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort
    scope: EffectExecutionScope

    async def persist(
        self,
        *,
        operation_id: str,
        grant: WorkspaceGrantBinding,
        entries: tuple[OverlayEntry, ...],
    ) -> WorkspaceStoredProposal:
        del operation_id
        changes = _change_entries(grant=grant, entries=entries)
        target_body = {
            "grant_id": grant.grant_id,
            "mount": grant.mount_name,
            "targets": [
                {
                    "operation": entry.operation,
                    "relative_path": entry.relative_path,
                    "destination_relative_path": entry.destination_relative_path,
                }
                for entry in changes
            ],
        }
        target_bytes = canonical_json_bytes(target_body)
        target_digest = sha256_hex(target_bytes)
        precondition_bytes = canonical_json_bytes(
            {
                "grant_id": grant.grant_id,
                "preconditions": [
                    {
                        "relative_path": entry.relative_path,
                        "precondition": entry.precondition.model_dump(
                            mode="json", exclude_none=True
                        ),
                    }
                    for entry in changes
                ],
            }
        )
        precondition_digest = sha256_hex(precondition_bytes)
        broker_changes = [
            {
                key: value
                for key, value in entry.model_dump(
                    mode="json", exclude_none=True
                ).items()
                if key != "content_ref"
            }
            for entry in changes
        ]
        change_set_digest = sha256_hex(
            canonical_json_bytes(
                {
                    "grant_id": grant.grant_id,
                    "mount": grant.mount_name,
                    "entries": broker_changes,
                }
            )
        )
        proposal_bytes = canonical_json_bytes(
            {
                "grant_id": grant.grant_id,
                "mount": grant.mount_name,
                "change_set_digest": change_set_digest,
                "target_digest": target_digest,
                "entries": [
                    entry.model_dump(mode="json", exclude_none=True)
                    for entry in changes
                ],
            }
        )
        proposal_digest = sha256_hex(proposal_bytes)
        proposal_ref = f"{_MATERIAL_PREFIX}{proposal_digest}"
        target_ref = f"{_TARGET_PREFIX}{target_digest}"
        precondition_ref = f"{_PRECONDITION_PREFIX}{precondition_digest}"
        await self._persist_ref(
            reference=proposal_ref,
            digest=proposal_digest,
            body=proposal_bytes,
        )
        await self._persist_ref(
            reference=target_ref,
            digest=target_digest,
            body=target_bytes,
        )
        await self._persist_ref(
            reference=precondition_ref,
            digest=precondition_digest,
            body=precondition_bytes,
        )
        for change in changes:
            if change.content_ref is not None and change.content_digest is not None:
                await self._retain_existing_content(
                    reference=change.content_ref,
                    digest=change.content_digest,
                )
        return WorkspaceStoredProposal(
            proposal_content_ref=proposal_ref,
            proposal_digest=proposal_digest,
            target_ref=target_ref,
            target_digest=target_digest,
            precondition_ref=precondition_ref,
            precondition_digest=precondition_digest,
            display_target=f"{grant.mount_label} workspace change",
        )

    async def resolve_material(
        self,
        *,
        request: EffectExecutionRequest,
    ) -> WorkspaceProposalMaterial | None:
        body = await self.resolve_reference(
            reference=request.proposal_content_ref,
            expected_digest=request.proposal_digest,
        )
        if body is None:
            return None
        try:
            decoded = json.loads(body)
            if not isinstance(decoded, dict):
                return None
            return WorkspaceProposalMaterial(
                **decoded,
                proposal_digest=request.proposal_digest,
            )
        except Exception:
            return None

    def open(
        self,
        *,
        scope: EffectExecutionScope,
        reference: str,
    ) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            if scope != self.scope:
                return
            if reference.startswith("artifact-blob://sha256/"):
                digest = blob_key_from_content_ref(reference)
                if await self._has_edge(reference=reference, digest=digest):
                    async for chunk in await self.blobs.open_stream(digest):
                        yield chunk
                return
            digest = _digest_from_material_ref(reference)
            if digest is None or not await self._has_edge(
                reference=reference, digest=digest
            ):
                return
            async for chunk in await self.blobs.open_stream(digest):
                yield chunk

        return _stream()

    async def resolve_reference(
        self, *, reference: str, expected_digest: str
    ) -> bytes | None:
        if _digest_from_material_ref(reference) != expected_digest:
            return None
        if not await self._has_edge(reference=reference, digest=expected_digest):
            return None
        body = bytearray()
        async for chunk in await self.blobs.open_stream(expected_digest):
            if (
                not isinstance(chunk, bytes)
                or len(body) + len(chunk) > _MAX_MATERIAL_BYTES
            ):
                return None
            body.extend(chunk)
        result = bytes(body)
        return result if sha256_hex(result) == expected_digest else None

    async def _persist_ref(self, *, reference: str, digest: str, body: bytes) -> None:
        if len(body) > _MAX_MATERIAL_BYTES or sha256_hex(body) != digest:
            raise ValueError("workspace material is invalid")
        stored = await self.blobs.put_stream(
            expected_digest=digest,
            chunks=_one_chunk(body),
            byte_limit=_MAX_MATERIAL_BYTES,
        )
        if stored.blob_key != digest:
            raise ValueError("workspace material changed during storage")
        await self._acquire_edge(reference=reference, digest=digest)

    async def _retain_existing_content(self, *, reference: str, digest: str) -> None:
        if blob_key_from_content_ref(reference) != digest:
            raise ValueError("workspace content reference digest changed")
        stat = await self.blobs.stat(digest)
        if stat.blob_key != digest:
            raise ValueError("workspace content is unavailable")
        await self._acquire_edge(reference=reference, digest=digest)

    async def _acquire_edge(self, *, reference: str, digest: str) -> None:
        seed = f"{self.scope.org_id}\0workspace\0{reference}\0{digest}".encode()
        await self.references.acquire(
            ArtifactReferenceEdge(
                org_id=self.scope.org_id,
                edge_id=f"workspace-{hashlib.sha256(seed).hexdigest()}",
                user_id=self.scope.user_id,
                blob_key=digest,
                reference_kind=ArtifactReferenceKind.EFFECT,
                reference_id=reference,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def _has_edge(self, *, reference: str, digest: str) -> bool:
        return any(
            edge.reference_kind is ArtifactReferenceKind.EFFECT
            and edge.reference_id == reference
            and edge.blob_key == digest
            and edge.released_at is None
            for edge in await self.references.list_edges(
                org_id=self.scope.org_id,
                user_id=self.scope.user_id,
            )
        )


@dataclass(frozen=True)
class RuntimeWorkspaceProposalResolver(WorkspaceProposalResolver):
    """Resolve exact material and current C2 read authority at prepare time."""

    scope: EffectExecutionScope
    proposals: RuntimeWorkspaceProposalStore
    sessions: WorkspaceHostSessionRegistryPort
    overlay_store: WorkspaceOverlayStorePort

    async def resolve(
        self, *, scope: EffectExecutionScope, request: EffectExecutionRequest
    ) -> WorkspacePrepareCommand:
        if scope != self.scope:
            raise WorkspaceAuthorityContractError("workspace scope changed")
        material = await self.proposals.resolve_material(request=request)
        session = self.sessions.get(scope)
        if material is None or session is None:
            raise WorkspaceAuthorityContractError("workspace material is unavailable")
        grant = next(
            (
                item
                for item in session.grants
                if item.grant_id == material.grant_id
                and item.mount_name == material.mount
            ),
            None,
        )
        if grant is None or grant.status != "active" or grant.mode == "read_only":
            raise WorkspaceAuthorityContractError("workspace grant is unavailable")
        if any(entry.operation in {"delete", "move"} for entry in material.entries):
            if grant.mode != "read_write":
                raise WorkspaceAuthorityContractError(
                    "workspace destructive authority is unavailable"
                )
        if not await self._is_current_overlay_binding(
            request=request,
            material=material,
            grant=grant,
        ):
            raise WorkspaceAuthorityContractError(
                "workspace stage binding is no longer current"
            )
        # Validate the approved A5 request against the immutable C1 material
        # before any authority implementation gets a prepare call. This makes
        # even a minimal/fake authority incapable of widening target or
        # proposal digests.
        material.broker_wire(request)
        return WorkspacePrepareCommand(
            scope=scope,
            request=request,
            read_capability=session.read_capability,
            material=material,
        )

    async def _is_current_overlay_binding(
        self,
        *,
        request: EffectExecutionRequest,
        material: WorkspaceProposalMaterial,
        grant: WorkspaceGrantBinding,
    ) -> bool:
        manifest = await self.overlay_store.get_manifest(run_id=self.scope.run_id)
        paths = _material_virtual_paths(material)
        current = tuple(manifest.entry_at(path) for path in paths)
        if any(
            entry is None
            or entry.stage_id != request.stage_id
            or entry.stage_revision != request.revision
            for entry in current
        ):
            return False
        try:
            rebound = _change_entries(
                grant=grant,
                entries=tuple(entry for entry in current if entry is not None),
            )
        except (TypeError, ValueError):
            return False
        return rebound == material.entries


class RuntimeWorkspaceAuthority(WorkspaceAuthorityPort):
    """Delegate C2 transport only to the current main-established session."""

    def __init__(
        self,
        *,
        scope: EffectExecutionScope,
        sessions: WorkspaceHostSessionRegistryPort,
    ) -> None:
        self._scope = scope
        self._sessions = sessions
        self._prepared: dict[str, WorkspaceAuthorityPort] = {}

    async def prepare(
        self, command: WorkspacePrepareCommand
    ) -> WorkspacePreparedEffect:
        session = self._sessions.get(self._scope)
        if session is None:
            raise WorkspaceAuthorityContractError("workspace authority is unavailable")
        prepared = await session.authority.prepare(command)
        self._prepared[prepared.prepared_ref] = session.authority
        return prepared

    async def upload(self, prepared_ref: str, content_ref: str) -> None:
        await self._authority_for(prepared_ref).upload(prepared_ref, content_ref)

    async def commit(
        self, prepared_ref: str, commit_permit: str
    ) -> WorkspaceCommitResult:
        return await self._authority_for(prepared_ref).commit(
            prepared_ref, commit_permit
        )

    async def reconcile(self, claim_id: str) -> WorkspaceCommitResult:
        session = self._sessions.get(self._scope)
        if session is None:
            raise WorkspaceAuthorityContractError("workspace authority is unavailable")
        return await session.authority.reconcile(claim_id)

    async def abort(self, prepared_ref: str) -> None:
        authority = self._prepared.pop(prepared_ref, None)
        if authority is not None:
            await authority.abort(prepared_ref)

    def _authority_for(self, prepared_ref: str) -> WorkspaceAuthorityPort:
        authority = self._prepared.get(prepared_ref)
        if authority is None:
            raise WorkspaceAuthorityContractError(
                "workspace prepared authority is unavailable"
            )
        return authority


@dataclass(frozen=True)
class RuntimeWorkspacePermitSource(WorkspaceCommitPermitSource):
    """Read, but never mint, a C2 one-use permit from the active host session."""

    scope: EffectExecutionScope
    sessions: WorkspaceHostSessionRegistryPort

    async def take(
        self,
        *,
        scope: EffectExecutionScope,
        request: EffectExecutionRequest,
        prepared_ref: str,
    ) -> str | None:
        if scope != self.scope:
            return None
        session = self.sessions.get(scope)
        if session is None:
            return None
        return await session.permit_source.take(
            scope=scope,
            request=request,
            prepared_ref=prepared_ref,
        )


def workspace_executor(
    *,
    scope: EffectExecutionScope,
    proposals: RuntimeWorkspaceProposalStore,
    sessions: WorkspaceHostSessionRegistryPort,
    overlay_store: WorkspaceOverlayStorePort,
) -> WorkspaceEffectExecutor:
    """Build the typed C2 executor registered by the A5 worker composition."""

    return WorkspaceEffectExecutor(
        scope=scope,
        authority=RuntimeWorkspaceAuthority(scope=scope, sessions=sessions),
        proposal_resolver=RuntimeWorkspaceProposalResolver(
            scope=scope,
            proposals=proposals,
            sessions=sessions,
            overlay_store=overlay_store,
        ),
        permit_source=RuntimeWorkspacePermitSource(
            scope=scope,
            sessions=sessions,
        ),
    )


async def _one_chunk(body: bytes) -> AsyncIterator[bytes]:
    yield body


def _digest_from_material_ref(reference: str) -> str | None:
    for prefix in (_MATERIAL_PREFIX, _TARGET_PREFIX, _PRECONDITION_PREFIX):
        if reference.startswith(prefix):
            digest = reference.removeprefix(prefix)
            return digest if len(digest) == 64 else None
    return None


def _change_entries(
    *,
    grant: WorkspaceGrantBinding,
    entries: tuple[OverlayEntry, ...],
) -> tuple[WorkspaceChangeEntry, ...]:
    by_operation = {entry.operation for entry in entries}
    if by_operation == {WorkspaceOperation.MOVE}:
        source = next(
            (
                entry
                for entry in entries
                if entry.entry_kind is WorkspaceEntryKind.TOMBSTONE
            ),
            None,
        )
        destination = next(
            (entry for entry in entries if entry.entry_kind is WorkspaceEntryKind.MOVE),
            None,
        )
        if source is None or destination is None:
            raise ValueError("workspace move proposal is incomplete")
        if destination.content_ref is not None:
            raise ValueError(
                "an overlay-only file must be created, not moved, on the host"
            )
        return (
            WorkspaceChangeEntry(
                operation="move",
                relative_path=_relative_path(source.virtual_path, grant.mount_name),
                destination_relative_path=_relative_path(
                    destination.virtual_path, grant.mount_name
                ),
                precondition=_precondition(source),
            ),
        )

    result: list[WorkspaceChangeEntry] = []
    for index, entry in enumerate(entries):
        relative = _relative_path(entry.virtual_path, grant.mount_name)
        if entry.operation in {WorkspaceOperation.CREATE, WorkspaceOperation.REPLACE}:
            if (
                entry.content_ref is None
                or entry.content_digest is None
                or entry.byte_size is None
            ):
                raise ValueError("workspace file proposal has no immutable content")
            result.append(
                WorkspaceChangeEntry(
                    operation=entry.operation.value,
                    relative_path=relative,
                    content_slot=f"content_{index}",
                    content_ref=entry.content_ref,
                    content_digest=entry.content_digest,
                    content_size=entry.byte_size,
                    precondition=_precondition(entry),
                )
            )
        elif entry.operation is WorkspaceOperation.DELETE:
            result.append(
                WorkspaceChangeEntry(
                    operation="delete",
                    relative_path=relative,
                    precondition=_precondition(entry),
                )
            )
        elif entry.operation is WorkspaceOperation.MKDIR:
            result.append(
                WorkspaceChangeEntry(
                    operation="mkdir",
                    relative_path=relative,
                    precondition=_precondition(entry),
                )
            )
        else:
            raise ValueError("workspace proposal operation is unsupported")
    return tuple(result)


def _precondition(entry: OverlayEntry) -> WorkspacePrecondition:
    baseline = entry.baseline
    if baseline.existence is BaseExistence.MUST_NOT_EXIST:
        return WorkspacePrecondition(exists=False)
    kind = (
        "directory" if baseline.entry_kind is WorkspaceEntryKind.DIRECTORY else "file"
    )
    return WorkspacePrecondition(
        exists=True,
        kind=kind,
        stable_id=baseline.stable_file_id or baseline.opaque_generation,
        sha256=baseline.content_digest,
    )


def _relative_path(virtual_path: str, mount_name: str) -> str:
    prefix = f"/workspace/{mount_name}/"
    if not virtual_path.startswith(prefix):
        raise ValueError("workspace entry escaped its grant mount")
    relative = virtual_path.removeprefix(prefix)
    if not relative:
        raise ValueError("workspace mount root cannot be mutated")
    return relative


def _material_virtual_paths(
    material: WorkspaceProposalMaterial,
) -> tuple[str, ...]:
    paths: list[str] = []
    prefix = f"/workspace/{material.mount}/"
    for entry in material.entries:
        paths.append(f"{prefix}{entry.relative_path}")
        if entry.destination_relative_path is not None:
            paths.append(f"{prefix}{entry.destination_relative_path}")
    return tuple(paths)


__all__ = (
    "InMemoryWorkspaceHostSessionRegistry",
    "RuntimeWorkspaceProposalResolver",
    "RuntimeWorkspaceProposalStore",
    "WorkspaceHostSession",
    "WorkspaceHostSessionRegistryPort",
    "workspace_executor",
)
