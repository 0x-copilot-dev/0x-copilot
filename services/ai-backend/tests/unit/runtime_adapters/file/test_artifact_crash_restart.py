"""Crash/restart and quarantine guarantees for the file artifact adapters."""

from __future__ import annotations

import json
import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest

from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.file.artifact_gc import FileArtifactGarbageCollector
from runtime_adapters.file.artifact_metadata_store import FileArtifactMetadataStore
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    FileArtifactReferenceStore,
)
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)
from tests.unit.runtime_adapters._artifact_fixtures import (
    NOW,
    SCOPE,
    artifact_id,
    make_append_command,
    make_create_command,
    make_delete_command,
)


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _read(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


class TestFileArtifactCrashRestart:
    async def test_published_blob_and_range_survive_restart(self, tmp_path) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        original = FileArtifactBlobStore(layout)
        body = b"crash-safe artifact bytes"
        written = await original.put_stream(
            expected_digest=None,
            chunks=_chunks(body[:5], body[5:]),
            byte_limit=len(body),
        )

        reopened = FileArtifactBlobStore(layout)
        stream = await reopened.open_stream(
            written.blob_key,
            start=6,
            end=9,
        )

        assert await _read(stream) == body[6:10]
        assert not tuple((layout.objects_dir / ".incoming").glob("*.part"))

    def test_orphaned_partial_is_quarantined_without_deletion(self, tmp_path) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        incoming = layout.objects_dir / ".incoming"
        FileStoreLayout.ensure_dir(incoming)
        partial = incoming / "interrupted.part"
        partial.write_bytes(b"incomplete but retained")

        FileArtifactBlobStore(layout)

        quarantined = tuple(
            (layout.objects_dir / ".partial-quarantine").glob("interrupted-*.partial")
        )
        assert not partial.exists()
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == b"incomplete but retained"

    async def test_metadata_revision_outbox_and_tombstone_survive_restart(
        self, tmp_path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        coordinator = FileArtifactPublicationCoordinator(layout)
        blobs = FileArtifactBlobStore(layout, coordinator)
        original = FileArtifactMetadataStore(layout, coordinator)
        await blobs.put_stream(
            expected_digest=None,
            chunks=_chunks(b"revision one"),
            byte_limit=len(b"revision one"),
        )
        await original.create_artifact(make_create_command())
        await blobs.put_stream(
            expected_digest=None,
            chunks=_chunks(b"revision two"),
            byte_limit=len(b"revision two"),
        )
        appended = await original.append_revision(make_append_command())
        await original.soft_delete(make_delete_command())
        expected_outbox = original.pending_outbox_rows

        reopened = FileArtifactMetadataStore(layout)
        record = await reopened.get_artifact(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
            include_deleted=True,
        )
        revision_one = await reopened.get_revision(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
            revision=1,
            include_deleted=True,
        )

        assert record is not None
        assert record.artifact.deleted_at is not None
        assert record.current_revision == appended.record.current_revision
        assert revision_one == make_create_command().record.current_revision
        assert reopened.pending_outbox_rows == expected_outbox

    async def test_torn_final_transaction_is_ignored_on_restart(self, tmp_path) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        coordinator = FileArtifactPublicationCoordinator(layout)
        blobs = FileArtifactBlobStore(layout, coordinator)
        original = FileArtifactMetadataStore(layout, coordinator)
        await blobs.put_stream(
            expected_digest=None,
            chunks=_chunks(b"revision one"),
            byte_limit=len(b"revision one"),
        )
        created = await original.create_artifact(make_create_command())
        ledger_path = layout.state_path("artifact_repository")
        with open(ledger_path, "ab") as handle:
            handle.write(b'{"schema_version":1,"op":"append"')

        reopened = FileArtifactMetadataStore(layout)
        record = await reopened.get_artifact(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
        )

        assert record == created.record
        assert len(reopened.pending_outbox_rows) == 1
        committed = [
            json.loads(line)
            for line in ledger_path.read_text().splitlines()
            if line.endswith("}")
        ]
        assert len(committed) == 1

    async def test_cancelled_stream_removes_unique_partial(self, tmp_path) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        blobs = FileArtifactBlobStore(layout)

        async def cancelled() -> AsyncIterator[bytes]:
            yield b"partial"
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await blobs.put_stream(
                expected_digest=None,
                chunks=cancelled(),
                byte_limit=100,
            )
        assert not tuple((layout.objects_dir / ".incoming").glob("*.part"))

    async def test_move_before_state_is_recovered_and_late_reference_restores(
        self, tmp_path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        original_coordinator = FileArtifactPublicationCoordinator(layout)
        blobs = FileArtifactBlobStore(layout, original_coordinator)
        body = b"moved before durable state"
        written = await blobs.put_stream(
            expected_digest=None,
            chunks=_chunks(body),
            byte_limit=len(body),
        )
        active = layout.object_path(written.blob_key)
        quarantine = original_coordinator.quarantine_path(written.blob_key)
        FileStoreLayout.ensure_dir(quarantine.parent)
        os.replace(active, quarantine)

        recovered = FileArtifactPublicationCoordinator(layout)
        assert written.blob_key in recovered.candidates
        assert written.blob_key in recovered.quarantine
        references = FileArtifactReferenceStore(layout, recovered)
        await references.put(
            ArtifactReferenceEdge(
                org_id="org_recovery",
                edge_id="edge_recovery",
                user_id="user_recovery",
                blob_key=written.blob_key,
                reference_kind=ArtifactReferenceKind.RECEIPT,
                reference_id="receipt:recovery",
                created_at=NOW,
            )
        )
        reopened = FileArtifactBlobStore(layout, recovered)
        assert await _read(await reopened.open_stream(written.blob_key)) == body

    async def test_published_before_metadata_failure_is_durably_discovered_and_reaped(
        self, tmp_path
    ) -> None:
        """A crash after publication cannot leave an invisible permanent blob."""

        layout = FileStoreLayout(tmp_path / "store")
        coordinator = FileArtifactPublicationCoordinator(layout)
        blobs = FileArtifactBlobStore(layout, coordinator)
        references = FileArtifactReferenceStore(layout, coordinator)
        metadata = FileArtifactMetadataStore(layout, coordinator, references)
        body = b"published but never committed"
        written = await blobs.put_stream(
            expected_digest=None,
            chunks=_chunks(body),
            byte_limit=len(body),
        )

        now = datetime.now(timezone.utc)
        candidates = await metadata.list_unreferenced_content(
            org_id="org_orphan_recovery",
            older_than=now + timedelta(seconds=1),
            limit=10,
        )
        candidate = next(
            value for value in candidates if value.blob_key == written.blob_key
        )
        collector = FileArtifactGarbageCollector(layout, coordinator, references)
        assert await collector.collect_if_unreferenced(
            org_id="org_orphan_recovery",
            candidate=candidate,
            grace_before=now + timedelta(seconds=1),
        )
        assert written.blob_key in coordinator.quarantine

        reopened = FileArtifactPublicationCoordinator(layout)
        assert written.blob_key in reopened.quarantine
        reap = await FileArtifactGarbageCollector(
            layout,
            reopened,
            FileArtifactReferenceStore(layout, reopened),
        ).reap_quarantine(
            older_than=now + timedelta(days=1),
            limit=10,
        )
        assert reap.reaped_blob_keys == (written.blob_key,)
