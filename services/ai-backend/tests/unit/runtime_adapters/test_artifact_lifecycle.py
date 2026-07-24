"""Lifecycle composition, evidence, scoped tombstones, purge, and reaper tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.artifacts.contracts import ArtifactScope
from agent_runtime.artifacts.errors import ArtifactBlobUnavailableError
from agent_runtime.persistence.records import RetentionKind
from agent_runtime.settings import RuntimeSettings
from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)
from runtime_adapters._artifact_repository import ArtifactRetentionScope
from runtime_adapters.artifact_lifecycle import (
    ArtifactLifecycleJobs,
    ArtifactLifecycleSchedule,
)
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    FileArtifactReferenceStore,
    InMemoryArtifactReferenceStore,
)
from runtime_adapters.factory import RuntimeAdapterFactory
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
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest
from tests.unit.runtime_adapters._artifact_fixtures import (
    NOW,
    artifact_id,
    digest,
    make_create_command,
)


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _read(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


@pytest.fixture(params=("in_memory", "file"))
def lifecycle_bundle(request, tmp_path):
    if request.param == "in_memory":
        coordinator = InMemoryArtifactPublicationCoordinator()
        blobs = InMemoryArtifactBlobStore(coordinator)
        references = InMemoryArtifactReferenceStore(coordinator)
        metadata = InMemoryArtifactMetadataStore(coordinator, references)
        gc = InMemoryArtifactGarbageCollector(
            coordinator,
            metadata,
            references,
        )
    else:
        layout = FileStoreLayout(tmp_path / "artifact-lifecycle")
        coordinator = FileArtifactPublicationCoordinator(layout)
        blobs = FileArtifactBlobStore(layout, coordinator)
        references = FileArtifactReferenceStore(layout, coordinator)
        metadata = FileArtifactMetadataStore(layout, coordinator, references)
        gc = FileArtifactGarbageCollector(layout, coordinator, references)
    lifecycle = ArtifactLifecycleJobs(
        store=metadata,
        retention_purger=metadata,
        garbage_collector=gc,
        quarantine_reaper=gc,
    )
    return blobs, metadata, lifecycle


@pytest.fixture(params=("in_memory", "file"))
async def configured_runtime_ports(request, tmp_path, monkeypatch):
    if request.param == "in_memory":
        ports = RuntimeAdapterFactory.from_store(
            InMemoryRuntimeApiStore(),
            artifact_effects_v2=True,
        )
    else:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, PROFILE_SINGLE_USER_DESKTOP)
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_STORE_BACKEND": "file",
                "RUNTIME_FILE_STORE_ROOT": str(tmp_path / "runtime"),
                "ARTIFACT_EFFECTS_V2": "true",
            }
        )
        ports = RuntimeAdapterFactory.from_settings(settings)
    await ports.lifecycle.open()
    try:
        yield ports
    finally:
        await ports.lifecycle.close()


async def _seed(
    blobs,
    metadata,
    ordinal: int,
    body: bytes,
    scope,
    *,
    created_at=NOW,
) -> None:
    await blobs.put_stream(
        expected_digest=digest(body),
        chunks=_chunks(body),
        byte_limit=len(body),
    )
    await metadata.create_artifact(
        make_create_command(
            ordinal,
            body=body,
            scope=scope,
            created_at=created_at,
        )
    )


_ORG_A_CONV_A = ArtifactScope(
    org_id="lifecycle_org_a",
    user_id="lifecycle_user_a",
    conversation_id="lifecycle_conv_a",
    run_id="lifecycle_run_a",
    trace_id="lifecycle_trace_a",
)
_ORG_A_CONV_B = _ORG_A_CONV_A.model_copy(
    update={
        "conversation_id": "lifecycle_conv_b",
        "run_id": "lifecycle_run_b",
        "trace_id": "lifecycle_trace_b",
    }
)
_ORG_A_USER_B = _ORG_A_CONV_A.model_copy(
    update={
        "user_id": "lifecycle_user_b",
        "conversation_id": "lifecycle_conv_user_b",
        "run_id": "lifecycle_run_user_b",
        "trace_id": "lifecycle_trace_user_b",
    }
)
_ORG_B = _ORG_A_CONV_A.model_copy(
    update={
        "org_id": "lifecycle_org_b",
        "user_id": "lifecycle_user_org_b",
        "conversation_id": "lifecycle_conv_org_b",
        "run_id": "lifecycle_run_org_b",
        "trace_id": "lifecycle_trace_org_b",
    }
)


class TestArtifactLifecycleJobs:
    async def test_conversation_deletion_is_scoped_and_retains_evidence(
        self,
        lifecycle_bundle,
    ) -> None:
        blobs, metadata, lifecycle = lifecycle_bundle
        await _seed(blobs, metadata, 1, b"conversation a", _ORG_A_CONV_A)
        await _seed(blobs, metadata, 2, b"conversation b", _ORG_A_CONV_B)

        result = await lifecycle.tombstone_conversation(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            conversation_id=_ORG_A_CONV_A.conversation_id,
            deleted_at=NOW + timedelta(hours=1),
            evidence_id="evidence-conversation",
            reason="conversation deletion",
        )

        assert result.evidence.tombstoned_artifact_ids == (artifact_id(1),)
        assert result.evidence.inventory_before.artifact_rows == 1
        assert result.evidence.inventory_before.revision_rows == 1
        assert result.evidence.inventory_before.idempotency_rows == 1
        assert result.evidence.inventory_before.reference_edge_rows == 1
        assert (
            await metadata.get_artifact(
                org_id=_ORG_A_CONV_A.org_id,
                user_id=_ORG_A_CONV_A.user_id,
                artifact_id=artifact_id(1),
            )
            is None
        )
        assert (
            await metadata.get_artifact(
                org_id=_ORG_A_CONV_B.org_id,
                user_id=_ORG_A_CONV_B.user_id,
                artifact_id=artifact_id(2),
            )
            is not None
        )
        replay = await lifecycle.tombstone_conversation(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            conversation_id=_ORG_A_CONV_A.conversation_id,
            deleted_at=NOW + timedelta(hours=2),
            evidence_id="evidence-conversation",
            reason="ignored retry fields",
        )
        assert replay.evidence == result.evidence
        assert (
            await metadata.get_lifecycle_evidence(
                org_id=_ORG_A_CONV_A.org_id,
                evidence_id="evidence-conversation",
            )
            == result.evidence
        )

    async def test_user_deletion_does_not_tombstone_another_user(
        self,
        lifecycle_bundle,
    ) -> None:
        blobs, metadata, lifecycle = lifecycle_bundle
        await _seed(blobs, metadata, 1, b"user a", _ORG_A_CONV_A)
        await _seed(blobs, metadata, 2, b"user b", _ORG_A_USER_B)

        result = await lifecycle.tombstone_user(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            deleted_at=NOW + timedelta(hours=1),
            evidence_id="evidence-user",
            reason="account deletion",
        )

        assert result.evidence.tombstoned_artifact_ids == (artifact_id(1),)
        assert (
            await metadata.get_artifact(
                org_id=_ORG_A_USER_B.org_id,
                user_id=_ORG_A_USER_B.user_id,
                artifact_id=artifact_id(2),
            )
            is not None
        )

    async def test_org_deletion_does_not_tombstone_a_third_tenant(
        self,
        lifecycle_bundle,
    ) -> None:
        blobs, metadata, lifecycle = lifecycle_bundle
        await _seed(blobs, metadata, 1, b"org a", _ORG_A_CONV_A)
        await _seed(blobs, metadata, 2, b"org b", _ORG_B)

        result = await lifecycle.tombstone_org(
            org_id=_ORG_A_CONV_A.org_id,
            deleted_at=NOW + timedelta(hours=1),
            evidence_id="evidence-org",
            reason="organization deletion",
        )

        assert result.evidence.tombstoned_artifact_ids == (artifact_id(1),)
        assert (
            await metadata.get_artifact(
                org_id=_ORG_B.org_id,
                user_id=_ORG_B.user_id,
                artifact_id=artifact_id(2),
            )
            is not None
        )

    async def test_retention_job_purges_inventory_then_quarantines_and_reaps(
        self,
        lifecycle_bundle,
    ) -> None:
        blobs, metadata, lifecycle = lifecycle_bundle
        body = b"retention lifecycle"
        await _seed(blobs, metadata, 1, body, _ORG_A_CONV_A)
        await lifecycle.tombstone_conversation(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            conversation_id=_ORG_A_CONV_A.conversation_id,
            deleted_at=NOW + timedelta(hours=1),
            evidence_id="evidence-retention",
            reason="retention test",
        )
        scope = ArtifactRetentionScope(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            conversation_id=_ORG_A_CONV_A.conversation_id,
        )

        first = await lifecycle.run_retention(
            scope=scope,
            deleted_before=NOW + timedelta(days=1),
            candidate_grace_before=NOW + timedelta(days=1),
            quarantine_older_than=NOW - timedelta(days=1),
            limit=10,
        )
        inventory = await metadata.deletion_inventory(
            scope=ArtifactRetentionScope(org_id=_ORG_A_CONV_A.org_id)
        )

        assert first.purge.purged_artifact_ids == (artifact_id(1),)
        assert first.quarantined_blob_keys == (digest(body),)
        assert first.reap.reaped_blob_keys == ()
        assert inventory.artifact_rows == 0
        assert inventory.revision_rows == 0
        assert inventory.idempotency_rows == 0
        assert inventory.reference_edge_rows == 0
        assert inventory.gc_candidate_rows == 1
        assert inventory.quarantined_digest_rows == 1

        second = await lifecycle.run_retention(
            scope=scope,
            deleted_before=NOW + timedelta(days=1),
            candidate_grace_before=NOW + timedelta(days=1),
            quarantine_older_than=NOW + timedelta(days=2),
            limit=10,
        )
        assert second.reap.reaped_blob_keys == (digest(body),)
        final_inventory = await metadata.deletion_inventory(
            scope=ArtifactRetentionScope(org_id=_ORG_A_CONV_A.org_id)
        )
        assert final_inventory.gc_candidate_rows == 0
        assert final_inventory.quarantined_digest_rows == 0


class TestConfiguredArtifactLifecycle:
    async def test_conversation_delete_calls_live_hook_and_persists_evidence(
        self,
        configured_runtime_ports,
    ) -> None:
        ports = configured_runtime_ports
        conversation = await ports.persistence.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_A_CONV_A.org_id,
                user_id=_ORG_A_CONV_A.user_id,
                assistant_id="assistant",
            )
        )
        scope = _ORG_A_CONV_A.model_copy(
            update={"conversation_id": conversation.conversation_id}
        )
        await _seed(
            ports.artifact_blob_store,
            ports.artifact_metadata_store,
            1,
            b"live conversation hook",
            scope,
        )
        deleted_at = datetime.now(timezone.utc)

        await ports.persistence.soft_delete_conversation(
            org_id=scope.org_id,
            user_id=scope.user_id,
            conversation_id=scope.conversation_id,
            now=deleted_at,
        )
        await ports.persistence.soft_delete_conversation(
            org_id=scope.org_id,
            user_id=scope.user_id,
            conversation_id=scope.conversation_id,
            now=deleted_at + timedelta(seconds=1),
        )

        assert (
            await ports.artifact_metadata_store.get_artifact(
                org_id=scope.org_id,
                user_id=scope.user_id,
                artifact_id=artifact_id(1),
            )
            is None
        )
        evidence_id = ports.artifact_lifecycle_jobs.conversation_evidence_id(
            org_id=scope.org_id,
            user_id=scope.user_id,
            conversation_id=scope.conversation_id,
        )
        evidence = await ports.artifact_metadata_store.get_lifecycle_evidence(
            org_id=scope.org_id,
            evidence_id=evidence_id,
        )
        assert evidence is not None
        assert evidence.tombstoned_artifact_ids == (artifact_id(1),)
        assert evidence.inventory_before.reference_edge_rows == 1
        assert tuple(await ports.persistence.list_retention_orgs()) == (scope.org_id,)

    async def test_user_and_trusted_org_deletion_hooks_are_scoped(
        self,
        configured_runtime_ports,
    ) -> None:
        ports = configured_runtime_ports
        await _seed(
            ports.artifact_blob_store,
            ports.artifact_metadata_store,
            1,
            b"user hook",
            _ORG_A_CONV_A,
        )
        await _seed(
            ports.artifact_blob_store,
            ports.artifact_metadata_store,
            2,
            b"third tenant",
            _ORG_B,
        )

        await ports.persistence.delete_user_history(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            reason="erasure",
        )
        user_evidence = await ports.artifact_metadata_store.get_lifecycle_evidence(
            org_id=_ORG_A_CONV_A.org_id,
            evidence_id=ports.artifact_lifecycle_jobs.user_evidence_id(
                org_id=_ORG_A_CONV_A.org_id,
                user_id=_ORG_A_CONV_A.user_id,
            ),
        )
        assert user_evidence is not None
        assert user_evidence.tombstoned_artifact_ids == (artifact_id(1),)
        assert (
            await ports.artifact_metadata_store.get_artifact(
                org_id=_ORG_B.org_id,
                user_id=_ORG_B.user_id,
                artifact_id=artifact_id(2),
            )
            is not None
        )

        org_result = await ports.persistence.tombstone_artifacts_for_org_deletion(
            org_id=_ORG_B.org_id,
            deleted_at=datetime.now(timezone.utc),
        )
        assert org_result is not None
        org_evidence = await ports.artifact_metadata_store.get_lifecycle_evidence(
            org_id=_ORG_B.org_id,
            evidence_id=ports.artifact_lifecycle_jobs.org_evidence_id(
                org_id=_ORG_B.org_id
            ),
        )
        assert org_evidence is not None
        assert org_evidence.tombstoned_artifact_ids == (artifact_id(2),)

    async def test_dedicated_sweep_honors_legal_hold_then_quarantines_and_reaps(
        self,
        configured_runtime_ports,
    ) -> None:
        ports = configured_runtime_ports
        body = b"legal hold lifecycle"
        now = datetime.now(timezone.utc)
        await _seed(
            ports.artifact_blob_store,
            ports.artifact_metadata_store,
            1,
            body,
            _ORG_A_CONV_A,
            created_at=now - timedelta(days=41),
        )
        await ports.artifact_lifecycle_jobs.on_conversation_deleted(
            org_id=_ORG_A_CONV_A.org_id,
            user_id=_ORG_A_CONV_A.user_id,
            conversation_id=_ORG_A_CONV_A.conversation_id,
            deleted_at=now - timedelta(days=40),
        )
        hold = ArtifactReferenceEdge(
            org_id=_ORG_B.org_id,
            edge_id="legal-hold-live-sweep",
            user_id=_ORG_B.user_id,
            blob_key=digest(body),
            reference_kind=ArtifactReferenceKind.LEGAL_HOLD,
            reference_id="legal_hold:case-1",
            created_at=now - timedelta(days=39),
        )
        await ports.artifact_reference_provider.acquire(hold)
        ports.artifact_lifecycle_jobs.schedule = ArtifactLifecycleSchedule(
            metadata_retention_grace=timedelta(0),
            candidate_grace=timedelta(0),
            quarantine_grace=timedelta(days=1),
            limit=10,
        )

        first = await ports.persistence.sweep_retention_kind(
            org_id=_ORG_A_CONV_A.org_id,
            kind=RetentionKind.ARTIFACTS_TOMBSTONED,
            ttl_seconds=0,
            chunk_size=10,
        )
        assert first.deleted == 1
        assert (
            await _read(await ports.artifact_blob_store.open_stream(digest(body)))
            == body
        )

        await ports.artifact_reference_provider.release(
            org_id=hold.org_id,
            edge_id=hold.edge_id,
            released_at=datetime.now(timezone.utc),
        )
        second = await ports.persistence.sweep_retention_kind(
            org_id=_ORG_A_CONV_A.org_id,
            kind=RetentionKind.ARTIFACTS_TOMBSTONED,
            ttl_seconds=0,
            chunk_size=10,
        )
        assert second.deleted == 0
        with pytest.raises(ArtifactBlobUnavailableError):
            await ports.artifact_blob_store.stat(digest(body))
        inventory = await ports.artifact_metadata_store.deletion_inventory(
            scope=ArtifactRetentionScope(org_id=_ORG_A_CONV_A.org_id)
        )
        assert inventory.quarantined_digest_rows == 1

        ports.artifact_lifecycle_jobs.schedule = ArtifactLifecycleSchedule(
            metadata_retention_grace=timedelta(0),
            candidate_grace=timedelta(0),
            quarantine_grace=timedelta(0),
            limit=10,
        )
        await ports.persistence.sweep_retention_kind(
            org_id=_ORG_A_CONV_A.org_id,
            kind=RetentionKind.ARTIFACTS_TOMBSTONED,
            ttl_seconds=0,
            chunk_size=10,
        )
        final_inventory = await ports.artifact_metadata_store.deletion_inventory(
            scope=ArtifactRetentionScope(org_id=_ORG_A_CONV_A.org_id)
        )
        assert final_inventory.gc_candidate_rows == 0
        assert final_inventory.quarantined_digest_rows == 0

    async def test_rollout_off_has_no_artifact_org_or_org_delete_hook(self) -> None:
        store = InMemoryRuntimeApiStore()
        ports = RuntimeAdapterFactory.from_store(store, artifact_effects_v2=False)

        assert await store.list_retention_orgs() == ()
        assert (
            await store.tombstone_artifacts_for_org_deletion(
                org_id="artifact_only_org",
                deleted_at=datetime.now(timezone.utc),
            )
            is None
        )
        assert ports.require_artifact_service_storage() is None
