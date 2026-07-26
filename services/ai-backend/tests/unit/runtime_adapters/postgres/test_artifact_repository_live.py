"""Live Postgres artifact races and global quarantine semantics."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from agent_runtime.artifacts.contracts import ArtifactScope
from agent_runtime.artifacts.errors import (
    ArtifactConflictError,
    ArtifactIdempotencyConflictError,
)
from agent_runtime.persistence.records import (
    LegalHoldReasonCode,
    LegalHoldRecord,
    LegalHoldScope,
)
from runtime_adapters._artifact_repository import ArtifactRetentionScope
from runtime_adapters.artifact_references import PostgresArtifactReferenceStore
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)
from runtime_adapters.postgres.artifact_gc import PostgresArtifactGarbageCollector
from runtime_adapters.postgres.artifact_store import PostgresArtifactMetadataStore
from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore
from tests.unit.runtime_adapters._artifact_fixtures import (
    NOW,
    digest,
    make_append_command,
    make_create_command,
    make_delete_command,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is required for live artifact repository tests.",
    ),
]


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


@pytest_asyncio.fixture
async def postgres_artifacts(tmp_path):
    parent = PostgresRuntimeApiStore(
        os.environ["TEST_DATABASE_URL"],
        role="api",
    )
    await parent.open()
    await parent.migrate()
    layout = FileStoreLayout(tmp_path / "shared-artifact-volume")
    coordinator = FileArtifactPublicationCoordinator(layout)
    blob = FileArtifactBlobStore(layout, coordinator)
    metadata = PostgresArtifactMetadataStore(parent, blob)
    references = PostgresArtifactReferenceStore(parent, blob)
    gc = PostgresArtifactGarbageCollector(parent, blob)
    try:
        yield parent, blob, metadata, references, gc
    finally:
        await parent.close()


async def _publish(blob, body: bytes) -> None:
    await blob.put_stream(
        expected_digest=digest(body),
        chunks=_chunks(body),
        byte_limit=len(body),
    )


class TestPostgresArtifactRepositoryLive:
    async def test_concurrent_same_key_replays_and_different_digest_conflicts(
        self, postgres_artifacts
    ) -> None:
        _, blob, metadata, _, _ = postgres_artifacts
        await _publish(blob, b"revision one")
        command = make_create_command()
        first, second = await asyncio.gather(
            metadata.create_artifact(command),
            metadata.create_artifact(command),
        )
        assert {first.replayed, second.replayed} == {False, True}

        await _publish(blob, b"different")
        conflicting = make_create_command(
            2,
            body=b"different",
            key=command.idempotency.key,
            request_digest=digest(b"different request"),
        )
        with pytest.raises(ArtifactIdempotencyConflictError):
            await metadata.create_artifact(conflicting)

    async def test_concurrent_compare_and_append_has_one_winner(
        self, postgres_artifacts
    ) -> None:
        _, blob, metadata, _, _ = postgres_artifacts
        await _publish(blob, b"revision one")
        await metadata.create_artifact(make_create_command())
        await _publish(blob, b"revision two")

        outcomes = await asyncio.gather(
            metadata.append_revision(make_append_command(key="append-a")),
            metadata.append_revision(make_append_command(key="append-b")),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
        assert sum(isinstance(value, ArtifactConflictError) for value in outcomes) == 1

    async def test_global_two_org_digest_restore_and_second_phase_reaper(
        self, postgres_artifacts
    ) -> None:
        _, blob, metadata, _, gc = postgres_artifacts
        body = b"shared postgres body"
        scope_a = ArtifactScope(
            org_id="pg_org_a",
            user_id="pg_user_a",
            conversation_id="pg_conv_a",
            run_id="pg_run_a",
            trace_id="pg_trace_a",
        )
        scope_b = ArtifactScope(
            org_id="pg_org_b",
            user_id="pg_user_b",
            conversation_id="pg_conv_b",
            run_id="pg_run_b",
            trace_id="pg_trace_b",
        )
        await _publish(blob, body)
        await metadata.create_artifact(
            make_create_command(10, body=body, scope=scope_a)
        )
        await metadata.create_artifact(
            make_create_command(11, body=body, scope=scope_b)
        )
        await metadata.soft_delete(make_delete_command(10, scope=scope_a))
        purge_a = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id=scope_a.org_id),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidate = purge_a.eligible_candidates[0]
        assert not await gc.collect_if_unreferenced(
            org_id=scope_a.org_id,
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )

        await metadata.soft_delete(make_delete_command(11, scope=scope_b))
        await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id=scope_b.org_id),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        assert await gc.collect_if_unreferenced(
            org_id=scope_a.org_id,
            candidate=candidate,
            grace_before=NOW + timedelta(days=1),
        )
        restored = make_create_command(12, body=body, scope=scope_b)
        await metadata.create_artifact(restored)
        assert (
            await gc.reap_quarantine(
                older_than=NOW + timedelta(days=2),
                limit=10,
            )
        ).reaped_blob_keys == ()

    async def test_late_legal_hold_after_purge_withholds_physical_reap(
        self,
        postgres_artifacts,
    ) -> None:
        """The scope row closes the hold gap after metadata is gone."""

        parent, blob, metadata, _references, gc = postgres_artifacts
        body = b"late-hold-postgres-cleanup"
        scope = ArtifactScope(
            org_id="pg_cleanup_hold_org",
            user_id="pg_cleanup_hold_user",
            conversation_id="pg_cleanup_hold_conversation",
            run_id="pg_cleanup_hold_run",
            trace_id="pg_cleanup_hold_trace",
        )
        await _publish(blob, body)
        await metadata.create_artifact(make_create_command(41, body=body, scope=scope))
        await metadata.soft_delete(make_delete_command(41, scope=scope))
        purge = await metadata.purge_tombstones(
            scope=ArtifactRetentionScope(org_id=scope.org_id),
            deleted_before=NOW + timedelta(days=1),
            limit=10,
        )
        candidate = purge.eligible_candidates[0]
        cutoff = datetime.now(UTC) + timedelta(days=1)
        assert await gc.collect_if_unreferenced(
            org_id=scope.org_id,
            candidate=candidate,
            grace_before=cutoff,
        )

        hold = LegalHoldRecord(
            id="lh_pg_cleanup_late_hold",
            org_id=scope.org_id,
            scope=LegalHoldScope.CONVERSATION,
            resource_id=scope.conversation_id,
            subject_user_id=scope.user_id,
            reason_code=LegalHoldReasonCode.LEGAL_REQUEST,
            created_by_user_id="pg_cleanup_retention_admin",
            create_idempotency_key="pg-cleanup-hold-create-001",
            create_request_digest=hashlib.sha256(b"pg-cleanup-hold").hexdigest(),
        )
        await parent.create_legal_hold(
            record=hold,
            audit_event={
                "org_id": scope.org_id,
                "user_id": hold.created_by_user_id,
                "actor_type": "user",
                "action": "legal_hold.created",
                "resource_type": "legal_hold",
                "resource_id": hold.id,
                "outcome": "success",
                "metadata": {"scope": "conversation"},
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

        reaped = await gc.reap_quarantine(older_than=cutoff, limit=10)

        assert reaped.reaped_blob_keys == ()
        assert reaped.withheld_blob_keys == (candidate.blob_key,)
        assert blob.coordinator.quarantine_path(candidate.blob_key).exists()
