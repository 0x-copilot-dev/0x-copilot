"""Shared metadata-store contract for every A2 repository backend."""

from __future__ import annotations

import pytest
from collections.abc import AsyncIterator

from agent_runtime.artifacts.contracts import ArtifactListQuery
from agent_runtime.artifacts.errors import (
    ArtifactConflictError,
    ArtifactIdempotencyConflictError,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_metadata_store import FileArtifactMetadataStore
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from tests.unit.runtime_adapters._artifact_fixtures import (
    NOW,
    SCOPE,
    artifact_id,
    digest,
    make_append_command,
    make_create_command,
    make_delete_command,
)


@pytest.fixture(params=("in_memory", "file"))
def artifact_metadata_store(request, tmp_path):
    if request.param == "in_memory":
        metadata = InMemoryArtifactMetadataStore()
        metadata.blob_store = InMemoryArtifactBlobStore(metadata.coordinator)
        return metadata
    if request.param == "file":
        layout = FileStoreLayout(tmp_path / "artifact-store")
        coordinator = FileArtifactPublicationCoordinator(layout)
        metadata = FileArtifactMetadataStore(layout, coordinator)
        metadata.blob_store = FileArtifactBlobStore(layout, coordinator)
        return metadata
    raise AssertionError(f"unsupported artifact backend: {request.param}")


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _publish(store, body: bytes) -> None:
    await store.blob_store.put_stream(
        expected_digest=digest(body),
        chunks=_chunks(body),
        byte_limit=len(body),
    )


class TestArtifactMetadataStoreConformance:
    async def test_create_get_and_idempotent_replay(
        self, artifact_metadata_store
    ) -> None:
        command = make_create_command()
        await _publish(artifact_metadata_store, b"revision one")

        created = await artifact_metadata_store.create_artifact(command)
        replayed = await artifact_metadata_store.create_artifact(command)
        fetched = await artifact_metadata_store.get_artifact(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
        )

        assert created.replayed is False
        assert replayed.replayed is True
        assert fetched == created.record
        assert len(artifact_metadata_store.pending_outbox_rows) == 1

    async def test_idempotency_key_rejects_different_request(
        self, artifact_metadata_store
    ) -> None:
        await _publish(artifact_metadata_store, b"revision one")
        await artifact_metadata_store.create_artifact(make_create_command(key="same"))
        conflicting = make_create_command(
            ordinal=2,
            key="same",
            request_digest=digest(b"different request"),
        )

        with pytest.raises(ArtifactIdempotencyConflictError):
            await artifact_metadata_store.create_artifact(conflicting)

        assert (
            await artifact_metadata_store.get_artifact(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id(2),
            )
            is None
        )
        assert len(artifact_metadata_store.pending_outbox_rows) == 1

    async def test_append_preserves_revision_history_and_checks_parent(
        self, artifact_metadata_store
    ) -> None:
        await _publish(artifact_metadata_store, b"revision one")
        await artifact_metadata_store.create_artifact(make_create_command())
        await _publish(artifact_metadata_store, b"revision two")
        appended = await artifact_metadata_store.append_revision(make_append_command())

        revision_one = await artifact_metadata_store.get_revision(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
            revision=1,
        )
        revision_two = await artifact_metadata_store.get_revision(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
            revision=2,
        )

        assert revision_one == make_create_command().record.current_revision
        assert revision_two == appended.record.current_revision
        assert appended.record.artifact.current_revision == 2
        assert len(artifact_metadata_store.pending_outbox_rows) == 2

        with pytest.raises(ArtifactConflictError):
            await artifact_metadata_store.append_revision(
                make_append_command(key="stale-parent")
            )

    async def test_reads_are_scoped_and_soft_delete_is_metadata_only(
        self, artifact_metadata_store
    ) -> None:
        await _publish(artifact_metadata_store, b"revision one")
        await artifact_metadata_store.create_artifact(make_create_command())
        deleted = await artifact_metadata_store.soft_delete(make_delete_command())

        assert deleted is not None
        assert deleted.artifact.deleted_at is not None
        assert (
            await artifact_metadata_store.get_artifact(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id(1),
            )
            is None
        )
        assert (
            await artifact_metadata_store.get_artifact(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id(1),
                include_deleted=True,
            )
            == deleted
        )
        assert (
            await artifact_metadata_store.get_artifact(
                org_id=SCOPE.org_id,
                user_id="another-user",
                artifact_id=artifact_id(1),
                include_deleted=True,
            )
            is None
        )

    async def test_missing_delete_replay_cannot_delete_later_artifact(
        self, artifact_metadata_store
    ) -> None:
        delete = make_delete_command()
        assert await artifact_metadata_store.soft_delete(delete) is None
        await _publish(artifact_metadata_store, b"revision one")
        await artifact_metadata_store.create_artifact(make_create_command())

        assert await artifact_metadata_store.soft_delete(delete) is None
        assert (
            await artifact_metadata_store.get_artifact(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id(1),
            )
            is not None
        )

    async def test_keyset_page_is_stable(self, artifact_metadata_store) -> None:
        await _publish(artifact_metadata_store, b"revision one")
        await artifact_metadata_store.create_artifact(make_create_command(1))
        await artifact_metadata_store.create_artifact(
            make_create_command(2, created_at=NOW)
        )
        query = ArtifactListQuery(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            run_id=SCOPE.run_id,
            limit=1,
        )

        first = await artifact_metadata_store.list_artifacts(query)
        second = await artifact_metadata_store.list_artifacts(
            query.model_copy(update={"cursor": first.next_cursor})
        )

        assert [row.artifact.artifact_id for row in first.artifacts] == [artifact_id(1)]
        assert first.next_cursor is not None
        assert [row.artifact.artifact_id for row in second.artifacts] == [
            artifact_id(2)
        ]
        assert second.next_cursor is None
