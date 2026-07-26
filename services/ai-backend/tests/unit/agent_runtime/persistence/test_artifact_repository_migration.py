"""Static contract for the additive 0007 artifact repository migration."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.persistence.schema.migrate import MigrationRunner
from agent_runtime.persistence.schema.postgres import ARTIFACT_REPOSITORY_TABLES


_MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
_FORWARD = (_MIGRATIONS / "0007_artifact_repository.sql").read_text()
_ROLLBACK = (_MIGRATIONS / "0007_artifact_repository.rollback.sql").read_text()
_DO_RLS = (_MIGRATIONS / "staged" / "do_rls.sql").read_text()
_UNDO_RLS = (_MIGRATIONS / "staged" / "undo_rls.sql").read_text()
_PHYSICAL_CLEANUP_FORWARD = next(
    _MIGRATIONS.glob("*_artifact_physical_cleanup_scopes.sql")
).read_text()
_PHYSICAL_CLEANUP_ROLLBACK = next(
    _MIGRATIONS.glob("*_artifact_physical_cleanup_scopes.rollback.sql")
).read_text()


class TestArtifactRepositoryMigration:
    def test_creates_the_declared_storage_tables_only(self) -> None:
        for table in ARTIFACT_REPOSITORY_TABLES:
            assert f"CREATE TABLE {table} (" in _FORWARD

        assert "CREATE TABLE runtime_outbox_events" not in _FORWARD
        assert " bytea" not in _FORWARD.lower()
        assert "artifact_body" not in _FORWARD.lower()
        assert "content_bytes" not in _FORWARD.lower()

    def test_revision_rows_are_immutable_content_references(self) -> None:
        assert "content_digest text NOT NULL" in _FORWARD
        assert "blob_key text NOT NULL" in _FORWARD
        assert "blob_key = content_digest" in _FORWARD
        assert "PRIMARY KEY (org_id, artifact_id, revision)" in _FORWARD
        assert "runtime_artifact_revisions_byte_size_check" in _FORWARD
        assert "idx_runtime_artifact_revisions_blob" in _FORWARD

    def test_reference_edges_cover_every_non_artifact_hold(self) -> None:
        assert "CREATE TABLE runtime_artifact_reference_edges (" in _FORWARD
        for kind in ("artifact", "effect", "receipt", "audit", "legal_hold"):
            assert f"'{kind}'" in _FORWARD
        assert "released_at timestamptz" in _FORWARD
        assert "idx_runtime_artifact_reference_edges_live_blob" in _FORWARD
        assert "WHERE released_at IS NULL" in _FORWARD

    def test_every_table_is_rls_forced_and_staged_scripts_track_it(self) -> None:
        for table in ARTIFACT_REPOSITORY_TABLES:
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in _FORWARD
            assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in _FORWARD
            assert f"ALTER TABLE {table}" in _DO_RLS
            assert f"ALTER TABLE {table}" in _UNDO_RLS
        assert (
            "CREATE POLICY tenant_isolation ON runtime_artifact_reference_edges"
            in _FORWARD
        )
        assert (
            "CREATE POLICY artifact_worker_global_select "
            "ON runtime_artifact_reference_edges" in _FORWARD
        )
        assert (
            "CREATE POLICY artifact_gc_worker_global "
            "ON runtime_artifact_gc_quarantine" in _FORWARD
        )

    def test_rollback_drops_dependents_before_artifacts(self) -> None:
        positions = [
            _ROLLBACK.index("DROP TABLE IF EXISTS runtime_artifact_gc_quarantine"),
            _ROLLBACK.index("DROP TABLE IF EXISTS runtime_artifact_gc_candidates"),
            _ROLLBACK.index("DROP TABLE IF EXISTS runtime_artifact_reference_edges"),
            _ROLLBACK.index("DROP TABLE IF EXISTS runtime_artifact_idempotency"),
            _ROLLBACK.index("DROP TABLE IF EXISTS runtime_artifact_revisions"),
            _ROLLBACK.index("DROP TABLE IF EXISTS runtime_artifacts"),
        ]
        assert positions == sorted(positions)

    def test_manifest_covers_migration_0007(self) -> None:
        actual = MigrationRunner.actual_manifest()
        expected = MigrationRunner.expected_manifest()

        assert "0007_artifact_repository" in actual
        assert actual == expected

    def test_physical_cleanup_scopes_preserve_hold_ownership_after_purge(self) -> None:
        """The later cleanup migration is intentionally additive to 0007."""

        migration_id = next(
            _MIGRATIONS.glob("*_artifact_physical_cleanup_scopes.sql")
        ).stem
        assert "CREATE TABLE runtime_artifact_gc_candidate_scopes" in (
            _PHYSICAL_CLEANUP_FORWARD
        )
        assert "FOREIGN KEY (provenance_org_id, blob_key)" in (
            _PHYSICAL_CLEANUP_FORWARD
        )
        assert "ON DELETE CASCADE" in _PHYSICAL_CLEANUP_FORWARD
        assert "ENABLE ROW LEVEL SECURITY" in _PHYSICAL_CLEANUP_FORWARD
        assert "FORCE ROW LEVEL SECURITY" in _PHYSICAL_CLEANUP_FORWARD
        assert "artifact-gc-hold:" in _PHYSICAL_CLEANUP_FORWARD
        assert "runtime_artifact_hold_pin_or_release" in _PHYSICAL_CLEANUP_FORWARD
        assert "DROP TABLE IF EXISTS runtime_artifact_gc_candidate_scopes" in (
            _PHYSICAL_CLEANUP_ROLLBACK
        )
        assert migration_id in MigrationRunner.actual_manifest()
