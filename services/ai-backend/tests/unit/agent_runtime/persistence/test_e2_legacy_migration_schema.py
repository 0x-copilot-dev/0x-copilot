"""Static safety contract for the E2 legacy-migration checkpoint migration."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.persistence.schema.migrate import MigrationRunner


_MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
_FORWARD = (_MIGRATIONS / "0015_e2_legacy_migration_checkpoints.sql").read_text()
_ROLLBACK = (
    _MIGRATIONS / "0015_e2_legacy_migration_checkpoints.rollback.sql"
).read_text()


def test_checkpoint_table_contains_only_safe_resumability_metadata() -> None:
    assert "CREATE TABLE IF NOT EXISTS runtime_e2_legacy_migrations" in _FORWARD
    assert "PRIMARY KEY (org_id, migration_id)" in _FORWARD
    table_definition = (
        _FORWARD.split(
            "CREATE TABLE IF NOT EXISTS runtime_e2_legacy_migrations", maxsplit=1
        )[1]
        .split("CREATE INDEX", maxsplit=1)[0]
        .lower()
    )
    for column in (
        "source_digest",
        "after_draft_id",
        "status",
        "report_digest",
        "revision",
    ):
        assert column in _FORWARD
    for forbidden in (
        "content_text",
        " bytea",
        "filesystem_path",
        "target_args",
        "approval",
    ):
        assert forbidden not in table_definition


def test_checkpoint_table_is_worker_only_and_least_privilege() -> None:
    assert (
        "ALTER TABLE runtime_e2_legacy_migrations ENABLE ROW LEVEL SECURITY;"
        in _FORWARD
    )
    assert (
        "ALTER TABLE runtime_e2_legacy_migrations FORCE ROW LEVEL SECURITY;" in _FORWARD
    )
    assert "CREATE POLICY e2_legacy_migration_worker_only" in _FORWARD
    assert "current_setting('app.role', true) = 'worker'" in _FORWARD
    assert "GRANT SELECT, INSERT, UPDATE" in _FORWARD
    assert "DELETE" not in _FORWARD


def test_checkpoint_rollback_and_manifest_are_bound_to_0015() -> None:
    assert "DROP TABLE IF EXISTS runtime_e2_legacy_migrations" in _ROLLBACK
    assert "0015_e2_legacy_migration_checkpoints" in MigrationRunner.actual_manifest()
    assert MigrationRunner.actual_manifest() == MigrationRunner.expected_manifest()
