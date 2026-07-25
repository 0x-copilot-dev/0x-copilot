"""Global dedup, retention eligibility, quarantine, and restoration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from agent_runtime.artifacts.contracts import ArtifactScope
from agent_runtime.artifacts.errors import ArtifactBlobUnavailableError
from runtime_adapters._artifact_repository import ArtifactRetentionScope
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    FileArtifactReferenceStore,
    InMemoryArtifactReferenceStore,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.file.artifact_gc import FileArtifactGarbageCollector
from runtime_adapters.file.artifact_metadata_store import FileArtifactMetadataStore
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_gc import (
    InMemoryArtifactGarbageCollector,
)
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from tests.unit.runtime_adapters._artifact_fixtures import (
    NOW,
    digest,
    make_create_command,
    make_delete_command,
)

_BODY = b"globally deduplicated artifact"
_ORG_A = ArtifactScope(
    org_id="org_a",
    user_id="user_a",
    conversation_id="conv_a",
    run_id="run_a",
    trace_id="trace_a",
)
_ORG_B = ArtifactScope(
    org_id="org_b",
    user_id="user_b",
    conversation_id="conv_b",
    run_id="run_b",
    trace_id="trace_b",
)


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _read(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


@pytest.fixture(params=("in_memory", "file"))
def artifact_bundle(request, tmp_path):
    if request.param == "in_memory":
        coordinator = InMemoryArtifactPublicationCoordinator()
        blob = InMemoryArtifactBlobStore(coordinator)
        references = InMemoryArtifactReferenceStore(coordinator)
        metadata = InMemoryArtifactMetadataStore(coordinator, references)
        gc = InMemoryArtifactGarbageCollector(coordinator, metadata, references)
        return coordinator, blob, references, metadata, gc
    layout = FileStoreLayout(tmp_path / "artifact-repository")
    coordinator = FileArtifactPublicationCoordinator(layout)
    blob = FileArtifactBlobStore(layout, coordinator)
    references = FileArtifactReferenceStore(layout, coordinator)
    metadata = FileArtifactMetadataStore(layout, coordinator, references)
    gc = FileArtifactGarbageCollector(layout, coordinator, references)
    return coordinator, blob, references, metadata, gc


async def _publish(blob) -> None:
    await blob.put_stream(
        expected_digest=digest(_BODY),
        chunks=_chunks(_BODY),
        byte_limit=len(_BODY),
    )


class TestArtifactRetentionAndGlobalGc:
    async def test_all_external_reference_kinds_acquire_release_and_block_gc(
        self,
        artifact_bundle,
    ) -> None:
        _, blob, references, metadata, gc = artifact_bundle
        await _publish(blob)
        await metadata.create_artifact(make_create_command(body=_BODY))
        await metadata.soft_delete(make_delete_command())
        purged = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id="org_artifacts"),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidate = purged.eligible_candidates[0]
        kinds = (
            ArtifactReferenceKind.EFFECT,
            ArtifactReferenceKind.RECEIPT,
            ArtifactReferenceKind.AUDIT,
            ArtifactReferenceKind.LEGAL_HOLD,
        )
        edges = []
        for kind in kinds:
            edge = ArtifactReferenceEdge(
                org_id=_ORG_B.org_id,
                edge_id=f"{kind.value}-edge",
                user_id=_ORG_B.user_id,
                blob_key=candidate.blob_key,
                reference_kind=kind,
                reference_id=f"{kind.value}:retention-owner",
                created_at=NOW,
            )
            acquired = await references.acquire(edge)
            assert await references.acquire(edge) == acquired
            edges.append(edge)

        assert not await gc.collect_if_unreferenced(
            org_id="org_artifacts",
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )
        for index, edge in enumerate(edges):
            released = await references.release(
                org_id=edge.org_id,
                edge_id=edge.edge_id,
                released_at=NOW + timedelta(hours=1),
            )
            assert released is not None
            assert (
                await references.release(
                    org_id=edge.org_id,
                    edge_id=edge.edge_id,
                    released_at=NOW + timedelta(hours=2),
                )
                == released
            )
            if index < len(edges) - 1:
                assert not await gc.collect_if_unreferenced(
                    org_id="org_artifacts",
                    candidate=candidate,
                    grace_before=NOW + timedelta(days=1),
                )
        assert await gc.collect_if_unreferenced(
            org_id="org_artifacts",
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )

    async def test_tombstone_is_protected_until_retention_purge(
        self, artifact_bundle
    ) -> None:
        _, blob, _, metadata, gc = artifact_bundle
        await _publish(blob)
        await metadata.create_artifact(make_create_command(body=_BODY))
        await metadata.soft_delete(make_delete_command())

        assert (
            await metadata.list_unreferenced_content(
                org_id="org_artifacts",
                older_than=NOW + timedelta(days=1),
                limit=10,
            )
            == ()
        )
        result = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id="org_artifacts"),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidates = await metadata.list_unreferenced_content(
            org_id="org_artifacts",
            older_than=NOW + timedelta(days=1),
            limit=10,
        )

        assert candidates == result.eligible_candidates
        assert len(candidates) == 1
        assert await gc.collect_if_unreferenced(
            org_id="org_artifacts",
            candidate=candidates[0],
            grace_before=NOW + timedelta(days=1),
        )

    async def test_two_org_digest_is_global_and_reaped_in_two_phases(
        self, artifact_bundle
    ) -> None:
        _, blob, _, metadata, gc = artifact_bundle
        await _publish(blob)
        await metadata.create_artifact(make_create_command(1, body=_BODY, scope=_ORG_A))
        await metadata.create_artifact(make_create_command(2, body=_BODY, scope=_ORG_B))
        await metadata.soft_delete(make_delete_command(1, scope=_ORG_A))
        purge_a = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id=_ORG_A.org_id),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidate = purge_a.eligible_candidates[0]

        assert not await gc.collect_if_unreferenced(
            org_id=_ORG_A.org_id,
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )
        assert await _read(await blob.open_stream(candidate.blob_key)) == _BODY

        await metadata.soft_delete(make_delete_command(2, scope=_ORG_B))
        await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id=_ORG_B.org_id),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        assert await gc.collect_if_unreferenced(
            org_id=_ORG_A.org_id,
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )
        with pytest.raises(ArtifactBlobUnavailableError):
            await blob.stat(candidate.blob_key)

        reaped = await gc.reap_quarantine(
            older_than=NOW + timedelta(days=2),
            limit=10,
        )
        assert reaped.reaped_blob_keys == (candidate.blob_key,)
        with pytest.raises(ArtifactBlobUnavailableError):
            await blob.stat(candidate.blob_key)

    async def test_late_external_reference_restores_and_blocks_reaper(
        self, artifact_bundle
    ) -> None:
        _, blob, references, metadata, gc = artifact_bundle
        await _publish(blob)
        await metadata.create_artifact(make_create_command(body=_BODY))
        await metadata.soft_delete(make_delete_command())
        purged = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id="org_artifacts"),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidate = purged.eligible_candidates[0]
        assert await gc.collect_if_unreferenced(
            org_id="org_artifacts",
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )

        await references.put(
            ArtifactReferenceEdge(
                org_id=_ORG_B.org_id,
                edge_id="late-reference",
                user_id=_ORG_B.user_id,
                blob_key=candidate.blob_key,
                reference_kind=ArtifactReferenceKind.LEGAL_HOLD,
                reference_id="hold:late",
                created_at=NOW + timedelta(hours=1),
            )
        )
        result = await gc.reap_quarantine(
            older_than=NOW + timedelta(days=2),
            limit=10,
        )

        assert result.reaped_blob_keys == ()
        assert await _read(await blob.open_stream(candidate.blob_key)) == _BODY

    async def test_publication_and_reference_add_win_gc_races(
        self, artifact_bundle
    ) -> None:
        _, blob, references, metadata, gc = artifact_bundle
        await _publish(blob)
        await metadata.create_artifact(make_create_command(body=_BODY))
        await metadata.soft_delete(make_delete_command())
        purged = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id="org_artifacts"),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidate = purged.eligible_candidates[0]
        publish = make_create_command(2, body=_BODY, scope=_ORG_B)

        await asyncio.gather(
            gc.collect_if_unreferenced(
                org_id="org_artifacts",
                candidate=candidate,
                grace_before=NOW + timedelta(days=1),
            ),
            metadata.create_artifact(publish),
        )
        assert await _read(await blob.open_stream(candidate.blob_key)) == _BODY

        await metadata.soft_delete(make_delete_command(2, scope=_ORG_B))
        purged_b = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id=_ORG_B.org_id),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        next_candidate = (
            purged_b.eligible_candidates[0]
            if purged_b.eligible_candidates
            else candidate
        )
        edge = ArtifactReferenceEdge(
            org_id=_ORG_B.org_id,
            edge_id="racing-reference",
            user_id=_ORG_B.user_id,
            blob_key=candidate.blob_key,
            reference_kind=ArtifactReferenceKind.AUDIT,
            reference_id="audit:racing",
            created_at=NOW + timedelta(hours=2),
        )
        await asyncio.gather(
            gc.collect_if_unreferenced(
                org_id=_ORG_B.org_id,
                candidate=next_candidate,
                grace_before=NOW + timedelta(days=1),
            ),
            references.put(edge),
        )
        assert await _read(await blob.open_stream(candidate.blob_key)) == _BODY
