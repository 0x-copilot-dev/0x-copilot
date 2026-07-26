"""Focused tests for the D3 artifact-backed immutable snapshot seam."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib

import pytest

from agent_runtime.artifacts.contracts import (
    ArtifactBlobStat,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.capabilities.sandbox.contracts import SandboxError, SandboxErrorCode
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxSnapshotBuilder,
    SandboxSnapshotInput,
    SandboxSnapshotLimits,
    SandboxSnapshotPlan,
    SandboxSnapshotSource,
    SandboxSnapshotSourceKind,
)
from agent_runtime.capabilities.sandbox.snapshot_file_store import (
    ArtifactRevisionSandboxSnapshotFileStore,
    C1A2SandboxSnapshotFileStore,
    SandboxSnapshotIdentity,
    SandboxSnapshotPlanAuthorityPort,
    TrustedSandboxSnapshotPlanProvider,
    WorkspaceOverlaySandboxSnapshotFileResolver,
)
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
    content_ref_for_blob,
)
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)

_ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000"
_ARTIFACT_REF = ArtifactContentRefCodec.format(_ARTIFACT_ID, 1)


def _source(kind: SandboxSnapshotSourceKind, ref: str) -> SandboxSnapshotSource:
    return SandboxSnapshotSource(kind=kind, source_ref=ref)


def _stored_revision(body: bytes) -> ArtifactStoredRevision:
    digest = hashlib.sha256(body).hexdigest()
    return ArtifactStoredRevision(
        revision=ArtifactRevision(
            artifact_id=_ARTIFACT_ID,
            revision=1,
            parent_revision=None,
            content_ref=_ARTIFACT_REF,
            content_digest=digest,
            byte_size=len(body),
            author=ArtifactAuthor.MODEL,
            source_ref=None,
            created_at="2026-07-26T00:00:00+00:00",
        ),
        blob_key=digest,
        range_supported=True,
    )


def _stored_record(
    stored: ArtifactStoredRevision,
    *,
    user_id: str = "user_1",
    run_id: str = "run_1",
) -> ArtifactStoredRecord:
    return ArtifactStoredRecord(
        artifact=Artifact(
            artifact_id=_ARTIFACT_ID,
            org_id="org_1",
            user_id=user_id,
            conversation_id="conversation_1",
            run_id=run_id,
            kind=ArtifactKind.FILE,
            title="snapshot input",
            media_type="text/plain",
            current_revision=1,
            created_by=ArtifactAuthor.MODEL,
            created_at="2026-07-26T00:00:00+00:00",
            updated_at="2026-07-26T00:00:00+00:00",
        ),
        current_revision=stored,
    )


@dataclass
class _Metadata:
    stored: ArtifactStoredRevision | None
    allowed_user_id: str | None = None
    artifact_run_id: str = "run_1"
    calls: list[tuple[str, str, str, int]] = field(default_factory=list)

    async def get_artifact(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        include_deleted: bool = False,
    ) -> ArtifactStoredRecord | None:
        assert include_deleted is False
        assert org_id == "org_1"
        assert artifact_id == _ARTIFACT_ID
        owner_user_id = self.allowed_user_id or "user_1"
        if self.stored is None or user_id != owner_user_id:
            return None
        return _stored_record(
            self.stored,
            user_id=owner_user_id,
            run_id=self.artifact_run_id,
        )

    async def get_revision(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
        include_deleted: bool = False,
    ) -> ArtifactStoredRevision | None:
        assert include_deleted is False
        self.calls.append((org_id, user_id, artifact_id, revision))
        if self.allowed_user_id is not None and user_id != self.allowed_user_id:
            return None
        return self.stored


@dataclass
class _BlobStore:
    body: bytes
    open_body: bytes | None = None
    stat_size: int | None = None
    open_calls: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    async def stat(self, blob_key: str) -> ArtifactBlobStat:
        assert blob_key == self.digest
        return ArtifactBlobStat(
            blob_key=blob_key,
            byte_size=len(self.body) if self.stat_size is None else self.stat_size,
            range_supported=True,
            created_at=datetime.now(UTC),
        )

    async def open_stream(
        self,
        blob_key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        assert start is None and end is None
        assert blob_key == self.digest
        self.open_calls.append(blob_key)
        body = self.body if self.open_body is None else self.open_body

        async def _stream() -> AsyncIterator[bytes]:
            yield body

        return _stream()


@dataclass
class _PlanAuthority(SandboxSnapshotPlanAuthorityPort):
    plan: SandboxSnapshotPlan | None
    identities: list[SandboxSnapshotIdentity] = field(default_factory=list)

    async def load_plan(
        self, *, identity: SandboxSnapshotIdentity
    ) -> SandboxSnapshotPlan | None:
        self.identities.append(identity)
        return self.plan


async def _collect(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


def _overlay_mutation(*, body: bytes, operation: WorkspaceOperation) -> OverlayMutation:
    digest = hashlib.sha256(body).hexdigest()
    path = "/workspace/project/report.csv"
    return OverlayMutation(
        kind=OverlayMutationKind.UPSERT,
        virtual_path=path,
        entry=OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.FILE,
            operation=operation,
            content_ref=content_ref_for_blob(digest),
            content_digest=digest,
            byte_size=len(body),
            baseline=(
                BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST)
                if operation is WorkspaceOperation.CREATE
                else BasePrecondition(
                    existence=BaseExistence.MUST_EXIST,
                    entry_kind=WorkspaceEntryKind.FILE,
                    content_digest=hashlib.sha256(b"before").hexdigest(),
                )
            ),
            author="agent",
        ),
    )


class TestArtifactRevisionSandboxSnapshotFileStore:
    async def test_resolves_exact_revision_in_verified_scope_and_streams_it(
        self,
    ) -> None:
        body = b"immutable artifact bytes"
        metadata = _Metadata(stored=_stored_revision(body))
        blobs = _BlobStore(body)
        store = ArtifactRevisionSandboxSnapshotFileStore(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            metadata_store=metadata,
            blob_store=blobs,
        )

        resolved = await store.resolve(
            source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT_REF),
            virtual_path="/workspace/input.txt",
        )

        assert resolved is not None
        assert resolved.source_ref == _ARTIFACT_REF
        assert resolved.content_ref == f"artifact-blob://sha256/{blobs.digest}"
        assert metadata.calls == [("org_1", "user_1", _ARTIFACT_ID, 1)]
        assert (
            await _collect(await store.open(content_ref=resolved.content_ref)) == body
        )
        assert blobs.open_calls == [blobs.digest]

    async def test_refuses_overlay_and_unresolved_artifact_without_any_fallback(
        self,
    ) -> None:
        metadata = _Metadata(stored=None)
        blobs = _BlobStore(b"unused")
        store = ArtifactRevisionSandboxSnapshotFileStore(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            metadata_store=metadata,
            blob_store=blobs,
        )

        assert (
            await store.resolve(
                source=_source(
                    SandboxSnapshotSourceKind.OVERLAY,
                    "workspace-overlay://runs/run_1/versions/2",
                ),
                virtual_path="/workspace/input.txt",
            )
            is None
        )
        assert metadata.calls == []
        assert (
            await store.resolve(
                source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT_REF),
                virtual_path="/workspace/input.txt",
            )
            is None
        )
        assert blobs.open_calls == []

    async def test_rejects_unresolved_or_mutated_blob_content(self) -> None:
        body = b"immutable artifact bytes"
        metadata = _Metadata(stored=_stored_revision(body))
        blobs = _BlobStore(body, open_body=b"changed after resolution")
        store = ArtifactRevisionSandboxSnapshotFileStore(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            metadata_store=metadata,
            blob_store=blobs,
        )

        with pytest.raises(SandboxError) as unauthorized:
            await store.open(content_ref=f"artifact-blob://sha256/{blobs.digest}")
        assert unauthorized.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH

        resolved = await store.resolve(
            source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT_REF),
            virtual_path="/workspace/input.txt",
        )
        assert resolved is not None
        with pytest.raises(SandboxError) as changed:
            await _collect(await store.open(content_ref=resolved.content_ref))
        assert changed.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH

    async def test_rejects_blob_metadata_that_does_not_match_the_revision(self) -> None:
        body = b"immutable artifact bytes"
        store = ArtifactRevisionSandboxSnapshotFileStore(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            metadata_store=_Metadata(stored=_stored_revision(body)),
            blob_store=_BlobStore(body, stat_size=len(body) + 1),
        )

        with pytest.raises(SandboxError) as error:
            await store.resolve(
                source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT_REF),
                virtual_path="/workspace/input.txt",
            )

        assert error.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH

    async def test_rejects_an_artifact_revision_outside_the_verified_user_scope(
        self,
    ) -> None:
        body = b"tenant-one artifact"
        metadata = _Metadata(
            stored=_stored_revision(body), allowed_user_id="user_owner"
        )
        store = ArtifactRevisionSandboxSnapshotFileStore(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_other"
            ),
            metadata_store=metadata,
            blob_store=_BlobStore(body),
        )

        assert (
            await store.resolve(
                source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT_REF),
                virtual_path="/workspace/input.txt",
            )
            is None
        )
        assert metadata.calls == []

    async def test_rejects_an_artifact_revision_outside_the_verified_run_scope(
        self,
    ) -> None:
        body = b"another run artifact"
        metadata = _Metadata(stored=_stored_revision(body), artifact_run_id="run_owner")
        store = ArtifactRevisionSandboxSnapshotFileStore(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            metadata_store=metadata,
            blob_store=_BlobStore(body),
        )

        assert (
            await store.resolve(
                source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT_REF),
                virtual_path="/workspace/input.txt",
            )
            is None
        )
        assert metadata.calls == []


class TestWorkspaceOverlaySandboxSnapshotFileResolver:
    async def test_later_overlay_edits_cannot_change_a_materialized_snapshot(
        self,
    ) -> None:
        old_body = b"before"
        overlay_store = InMemoryWorkspaceOverlayStore()
        first = await overlay_store.append_revision(
            run_id="run_1",
            expected_version=0,
            mutations=(
                _overlay_mutation(
                    body=old_body,
                    operation=WorkspaceOperation.CREATE,
                ),
            ),
        )
        new_body = b"after later edit"
        await overlay_store.append_revision(
            run_id="run_1",
            expected_version=first.version,
            mutations=(
                _overlay_mutation(
                    body=new_body,
                    operation=WorkspaceOperation.REPLACE,
                ),
            ),
        )
        blobs = _BlobStore(old_body)
        identity = SandboxSnapshotIdentity(
            run_id="run_1", org_id="org_1", user_id="user_1"
        )
        store = C1A2SandboxSnapshotFileStore(
            artifacts=ArtifactRevisionSandboxSnapshotFileStore(
                identity=identity,
                metadata_store=_Metadata(stored=None),
                blob_store=blobs,
            ),
            overlays=WorkspaceOverlaySandboxSnapshotFileResolver(
                identity=identity,
                overlay_store=overlay_store,
                blob_store=blobs,
            ),
        )
        plan = SandboxSnapshotPlan(
            entries=(
                SandboxSnapshotInput(
                    virtual_path="/workspace/project/report.csv",
                    source=_source(
                        SandboxSnapshotSourceKind.OVERLAY,
                        "workspace-overlay://runs/run_1/versions/1",
                    ),
                ),
            )
        )

        manifest = await SandboxSnapshotBuilder.materialize(
            plan=plan,
            store=store,
            limits=SandboxSnapshotLimits(),
        )

        assert (
            manifest.entries[0].content_digest == hashlib.sha256(old_body).hexdigest()
        )
        assert (
            await _collect(
                await store.open(content_ref=manifest.entries[0].content_ref)
            )
            == old_body
        )

    async def test_rejects_cross_run_or_missing_overlay_versions_without_fallback(
        self,
    ) -> None:
        overlay_store = InMemoryWorkspaceOverlayStore()
        await overlay_store.append_revision(
            run_id="run_1",
            expected_version=0,
            mutations=(
                _overlay_mutation(
                    body=b"before",
                    operation=WorkspaceOperation.CREATE,
                ),
            ),
        )
        resolver = WorkspaceOverlaySandboxSnapshotFileResolver(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            overlay_store=overlay_store,
            blob_store=_BlobStore(b"before"),
        )

        assert (
            await resolver.resolve_overlay_file(
                overlay_ref="workspace-overlay://runs/run_other/versions/1",
                virtual_path="/workspace/project/report.csv",
            )
            is None
        )
        assert (
            await resolver.resolve_overlay_file(
                overlay_ref="workspace-overlay://runs/run_1/versions/9",
                virtual_path="/workspace/project/report.csv",
            )
            is None
        )

    async def test_rechecks_overlay_blob_bytes_after_resolution(self) -> None:
        body = b"overlay bytes"
        overlay_store = InMemoryWorkspaceOverlayStore()
        await overlay_store.append_revision(
            run_id="run_1",
            expected_version=0,
            mutations=(
                _overlay_mutation(body=body, operation=WorkspaceOperation.CREATE),
            ),
        )
        resolver = WorkspaceOverlaySandboxSnapshotFileResolver(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            ),
            overlay_store=overlay_store,
            blob_store=_BlobStore(body, open_body=b"different overlay bytes"),
        )
        resolved = await resolver.resolve_overlay_file(
            overlay_ref="workspace-overlay://runs/run_1/versions/1",
            virtual_path="/workspace/project/report.csv",
        )

        assert resolved is not None
        with pytest.raises(SandboxError) as error:
            await _collect(await resolver.open(content_ref=resolved.content_ref))
        assert error.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH


class TestTrustedSandboxSnapshotPlanProvider:
    async def test_loads_only_an_authoritative_plan_for_the_verified_identity(
        self,
    ) -> None:
        plan = SandboxSnapshotPlan(
            entries=(
                {
                    "virtual_path": "/workspace/input.txt",
                    "source": {
                        "kind": "artifact",
                        "source_ref": _ARTIFACT_REF,
                    },
                },
            )
        )
        authority = _PlanAuthority(plan=plan)
        provider = TrustedSandboxSnapshotPlanProvider(authority=authority)

        assert (
            await provider.snapshot_for(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )
            == plan
        )
        assert authority.identities == [
            SandboxSnapshotIdentity(run_id="run_1", org_id="org_1", user_id="user_1")
        ]

    async def test_fails_closed_for_missing_identity_or_authoritative_plan(
        self,
    ) -> None:
        authority = _PlanAuthority(plan=None)
        provider = TrustedSandboxSnapshotPlanProvider(authority=authority)

        with pytest.raises(SandboxError) as missing_identity:
            await provider.snapshot_for(run_id="run_1", org_id=None, user_id="user_1")
        assert missing_identity.value.code is SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED
        assert authority.identities == []

        with pytest.raises(SandboxError) as missing_plan:
            await provider.snapshot_for(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )
        assert missing_plan.value.code is SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED

    async def test_rejects_an_authoritative_plan_with_another_run_overlay(self) -> None:
        authority = _PlanAuthority(
            plan=SandboxSnapshotPlan(
                entries=(
                    SandboxSnapshotInput(
                        virtual_path="/workspace/project/report.csv",
                        source=_source(
                            SandboxSnapshotSourceKind.OVERLAY,
                            "workspace-overlay://runs/run_other/versions/1",
                        ),
                    ),
                )
            )
        )
        provider = TrustedSandboxSnapshotPlanProvider(authority=authority)

        with pytest.raises(SandboxError) as error:
            await provider.snapshot_for(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )

        assert error.value.code is SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED
