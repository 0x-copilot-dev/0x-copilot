"""CAS and restart-safety tests for E2 legacy-migration checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationCheckpoint,
    LegacyMigrationStateError,
    LegacyMigrationStatus,
)
from runtime_adapters.file.legacy_migration_store import (
    FileLegacyMigrationCheckpointStore,
)
from runtime_adapters.in_memory.legacy_migration_store import (
    InMemoryLegacyMigrationCheckpointStore,
)


pytestmark = pytest.mark.anyio

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)
ORG = "org_e2_checkpoint"
MIGRATION = "e2_cohort_checkpoint"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _checkpoint(*, source_digest: str = "a" * 64) -> LegacyMigrationCheckpoint:
    return LegacyMigrationCheckpoint(
        org_id=ORG,
        migration_id=MIGRATION,
        source_digest=source_digest,
        after_draft_id=None,
        status=LegacyMigrationStatus.RUNNING,
        report_digest=None,
        revision=0,
        created_at=NOW,
        updated_at=NOW,
    )


class TestInMemoryLegacyMigrationCheckpointStore:
    async def test_cas_rejects_stale_writer_and_source_mismatch(self) -> None:
        store = InMemoryLegacyMigrationCheckpointStore()
        initial = await store.load_or_create(checkpoint=_checkpoint())
        advanced = await store.compare_and_set(
            expected=initial,
            after_draft_id=f"{1:032x}",
            status=LegacyMigrationStatus.RUNNING,
            report_digest="b" * 64,
            updated_at=NOW + timedelta(seconds=1),
        )

        assert advanced is not None
        assert advanced.revision == 1
        assert (
            await store.compare_and_set(
                expected=initial,
                after_draft_id=f"{2:032x}",
                status=LegacyMigrationStatus.RUNNING,
                report_digest="c" * 64,
                updated_at=NOW + timedelta(seconds=2),
            )
            is None
        )
        with pytest.raises(LegacyMigrationStateError):
            await store.load_or_create(checkpoint=_checkpoint(source_digest="d" * 64))

    async def test_completed_state_only_allows_safe_terminal_downgrade(self) -> None:
        store = InMemoryLegacyMigrationCheckpointStore()
        initial = await store.load_or_create(checkpoint=_checkpoint())
        completed = await store.compare_and_set(
            expected=initial,
            after_draft_id=f"{1:032x}",
            status=LegacyMigrationStatus.COMPLETED,
            report_digest="b" * 64,
            updated_at=NOW + timedelta(seconds=1),
        )
        assert completed is not None
        with pytest.raises(LegacyMigrationStateError):
            await store.compare_and_set(
                expected=completed,
                after_draft_id=f"{1:032x}",
                status=LegacyMigrationStatus.RUNNING,
                report_digest=None,
                updated_at=NOW + timedelta(seconds=2),
            )
        audit_pending = await store.compare_and_set(
            expected=completed,
            after_draft_id=f"{1:032x}",
            status=LegacyMigrationStatus.AUDIT_PENDING,
            report_digest="c" * 64,
            updated_at=NOW + timedelta(seconds=3),
        )
        assert audit_pending is not None


class TestFileLegacyMigrationCheckpointStore:
    async def test_state_survives_reopen_and_persists_only_safe_metadata(
        self, tmp_path
    ) -> None:
        store = FileLegacyMigrationCheckpointStore(root=tmp_path)
        initial = await store.load_or_create(checkpoint=_checkpoint())
        advanced = await store.compare_and_set(
            expected=initial,
            after_draft_id=f"{1:032x}",
            status=LegacyMigrationStatus.RUNNING,
            report_digest="b" * 64,
            updated_at=NOW + timedelta(seconds=1),
        )
        assert advanced is not None

        restarted = FileLegacyMigrationCheckpointStore(root=tmp_path)
        restored = await restarted.load(org_id=ORG, migration_id=MIGRATION)
        assert restored == advanced
        state_files = list((tmp_path / "e2_legacy_migration").glob("*.json"))
        assert len(state_files) == 1
        stored = json.loads(state_files[0].read_text(encoding="utf-8"))
        assert set(stored) == {"checkpoint"}
        assert set(stored["checkpoint"]) == {
            "after_draft_id",
            "created_at",
            "migration_id",
            "org_id",
            "report_digest",
            "revision",
            "source_digest",
            "status",
            "updated_at",
        }
        encoded = state_files[0].read_text(encoding="utf-8")
        assert "content_text" not in encoded
        assert "target_args" not in encoded

    async def test_corrupt_checkpoint_fails_closed(self, tmp_path) -> None:
        store = FileLegacyMigrationCheckpointStore(root=tmp_path)
        await store.load_or_create(checkpoint=_checkpoint())
        state_file = next((tmp_path / "e2_legacy_migration").glob("*.json"))
        state_file.write_text("not-json", encoding="utf-8")

        with pytest.raises(LegacyMigrationStateError):
            await store.load(org_id=ORG, migration_id=MIGRATION)
