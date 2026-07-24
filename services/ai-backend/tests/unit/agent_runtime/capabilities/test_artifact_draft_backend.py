"""B1 ``/drafts`` → canonical Artifact Repository convergence tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.artifacts import ArtifactScope, ArtifactService
from agent_runtime.capabilities.backends.artifact_draft_backend import (
    ArtifactDraftBackend,
    ArtifactDraftPathBinding,
)
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore

pytestmark = pytest.mark.anyio


def _draft_id() -> str:
    return "deadbeefcafe1234deadbeefcafe1234"


class _Scopes:
    scope = ArtifactScope(
        org_id="org_acme",
        user_id="user_sarah",
        conversation_id="conv_1",
        run_id="run_1",
        trace_id="trace_1",
    )

    async def resolve_run(self, *, org_id: str, user_id: str, run_id: str):
        if (org_id, user_id, run_id) == (
            self.scope.org_id,
            self.scope.user_id,
            self.scope.run_id,
        ):
            return self.scope
        return None


def _service() -> ArtifactService:
    coordinator = InMemoryArtifactPublicationCoordinator()
    return ArtifactService(
        metadata=InMemoryArtifactMetadataStore(coordinator),
        blobs=InMemoryArtifactBlobStore(coordinator),
        run_scopes=_Scopes(),
    )


def _backend(
    *,
    artifacts: ArtifactService | None = None,
    legacy: InMemoryDraftStore | None = None,
) -> ArtifactDraftBackend:
    return ArtifactDraftBackend(
        artifacts=artifacts or _service(),
        org_id="org_acme",
        conversation_id="conv_1",
        run_id="run_1",
        user_id="user_sarah",
        legacy_store=legacy,
    )


class TestArtifactDraftBackend:
    async def test_new_write_edit_read_uses_artifact_revisions_only(self) -> None:
        artifacts = _service()
        backend = _backend(artifacts=artifacts)
        path = f"/{_draft_id()}.md"

        created = await backend.awrite(path, "# Launch\nfirst")
        edited = await backend.aedit(path, "first", "second")
        read = await backend.aread(path)

        assert created.path == f"/drafts/{_draft_id()}.md"
        assert edited.error is None
        assert read.file_data is not None
        assert read.file_data["content"] == "# Launch\nsecond"
        binding = ArtifactDraftPathBinding(
            org_id="org_acme",
            user_id="user_sarah",
            conversation_id="conv_1",
            run_id="run_1",
            draft_id=_draft_id(),
        )
        record = await artifacts.get_metadata(
            org_id="org_acme", user_id="user_sarah", artifact_id=binding.artifact_id
        )
        assert record.artifact.current_revision == 2
        assert record.current_revision.revision.source_ref == binding.source_ref
        assert record.current_revision.revision.author.value == "model"

    async def test_replayed_same_bytes_do_not_create_a_second_revision(self) -> None:
        artifacts = _service()
        backend = _backend(artifacts=artifacts)
        path = f"/{_draft_id()}.md"

        await backend.awrite(path, "same bytes")
        await backend.awrite(path, "same bytes")

        binding = backend._binding(path)
        assert binding is not None
        record = await artifacts.get_metadata(
            org_id="org_acme", user_id="user_sarah", artifact_id=binding.artifact_id
        )
        assert record.artifact.current_revision == 1

    async def test_legacy_read_imports_once_without_a_legacy_write(self) -> None:
        legacy = InMemoryDraftStore()
        await legacy.insert_version(
            DraftRecord(
                draft_id=_draft_id(),
                version=1,
                org_id="org_acme",
                conversation_id="conv_1",
                run_id="run_1",
                user_id="user_sarah",
                title="Legacy draft",
                content_text="# Legacy\ncontent",
                status=DraftStatus.DRAFT,
                created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            )
        )
        artifacts = _service()
        backend = _backend(artifacts=artifacts, legacy=legacy)

        first = await backend.aread(f"/{_draft_id()}.md")
        second = await backend.aread(f"/{_draft_id()}.md")

        assert first.file_data == second.file_data
        assert len(legacy.versions[("org_acme", _draft_id())]) == 1
        binding = backend._binding(f"/{_draft_id()}.md")
        assert binding is not None
        record = await artifacts.get_metadata(
            org_id="org_acme", user_id="user_sarah", artifact_id=binding.artifact_id
        )
        assert record.current_revision.revision.author.value == "import"

    async def test_legacy_draft_remains_listed_until_first_read_imports_it(
        self,
    ) -> None:
        legacy = InMemoryDraftStore()
        await legacy.insert_version(
            DraftRecord(
                draft_id=_draft_id(),
                version=1,
                org_id="org_acme",
                conversation_id="conv_1",
                run_id="run_1",
                user_id="user_sarah",
                title="Legacy draft",
                content_text="legacy body",
                status=DraftStatus.DRAFT,
                created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            )
        )
        backend = _backend(legacy=legacy)

        listing = await backend.als("/")

        assert listing.entries is not None
        assert [entry["path"] for entry in listing.entries] == [f"/{_draft_id()}.md"]

    async def test_legacy_row_from_another_run_is_not_imported(self) -> None:
        legacy = InMemoryDraftStore()
        await legacy.insert_version(
            DraftRecord(
                draft_id=_draft_id(),
                version=1,
                org_id="org_acme",
                conversation_id="conv_1",
                run_id="run_other",
                user_id="user_sarah",
                title="Other run",
                content_text="must not leak",
                status=DraftStatus.DRAFT,
                created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            )
        )
        artifacts = _service()
        backend = _backend(artifacts=artifacts, legacy=legacy)

        result = await backend.aread(f"/{_draft_id()}.md")

        assert result.error == "file_not_found"

    async def test_binding_changes_when_any_authority_scope_changes(self) -> None:
        common = dict(
            org_id="org_acme",
            user_id="user_sarah",
            conversation_id="conv_1",
            run_id="run_1",
            draft_id=_draft_id(),
        )
        baseline = ArtifactDraftPathBinding(**common)
        assert (
            ArtifactDraftPathBinding(**(common | {"run_id": "run_2"})).artifact_id
            != baseline.artifact_id
        )
        assert (
            ArtifactDraftPathBinding(**(common | {"user_id": "user_other"})).artifact_id
            != baseline.artifact_id
        )

    async def test_invalid_path_never_creates_an_artifact(self) -> None:
        backend = _backend()
        result = await backend.awrite("/drafts/not-a-uuid.md", "body")
        assert result.error == "invalid_path"
