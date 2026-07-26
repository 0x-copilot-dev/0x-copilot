"""Static contract for C1's durable PostgreSQL workspace-overlay migration."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.persistence.schema.migrate import MigrationRunner

_MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
_FORWARD = (_MIGRATIONS / "0014_workspace_overlay_manifests.sql").read_text()
_ROLLBACK = (_MIGRATIONS / "0014_workspace_overlay_manifests.rollback.sql").read_text()


def test_workspace_overlay_manifest_is_bound_to_its_tenant_run_parent() -> None:
    assert "CREATE TABLE runtime_workspace_overlay_manifests" in _FORWARD
    assert "PRIMARY KEY (org_id, run_id)" in _FORWARD
    assert "REFERENCES agent_runs (org_id, id)" in _FORWARD
    assert "ON UPDATE CASCADE" in _FORWARD
    assert "ON DELETE CASCADE" in _FORWARD
    assert "agent_runs_org_id_id_key UNIQUE (org_id, id)" in _FORWARD


def test_workspace_overlay_manifest_is_metadata_only_and_rls_forced() -> None:
    assert "manifest_json   jsonb NOT NULL" in _FORWARD
    assert " bytea" not in _FORWARD.lower()
    assert "host_path" not in _FORWARD.lower()
    assert "commit_permit" not in _FORWARD.lower()
    assert (
        "ALTER TABLE runtime_workspace_overlay_manifests ENABLE ROW LEVEL SECURITY"
        in _FORWARD
    )
    assert (
        "ALTER TABLE runtime_workspace_overlay_manifests FORCE ROW LEVEL SECURITY"
        in _FORWARD
    )
    assert "CREATE POLICY workspace_overlay_tenant_isolation" in _FORWARD
    assert "CREATE POLICY workspace_overlay_worker_access" in _FORWARD
    assert "CREATE POLICY workspace_overlay_worker_run_lookup" in _FORWARD


def test_workspace_overlay_rollback_removes_child_before_parent_constraint() -> None:
    assert _ROLLBACK.index(
        "DROP TABLE IF EXISTS runtime_workspace_overlay_manifests"
    ) < _ROLLBACK.index("DROP CONSTRAINT IF EXISTS agent_runs_org_id_id_key")


def test_workspace_overlay_migration_is_manifested() -> None:
    assert "0014_workspace_overlay_manifests" in MigrationRunner.actual_manifest()
    assert MigrationRunner.actual_manifest() == MigrationRunner.expected_manifest()
