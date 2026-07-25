"""Migration contract for durable immutable usage-attribution edges."""

from __future__ import annotations

from agent_runtime.persistence.schema.migrate import MigrationRunner


def test_usage_attribution_edge_migration_and_rollback_are_manifested() -> None:
    migrations_dir = MigrationRunner.migrations_dir()
    forward = (migrations_dir / "0010_usage_attribution_edges.sql").read_text()
    rollback = (
        migrations_dir / "0010_usage_attribution_edges.rollback.sql"
    ).read_text()

    assert "CREATE TABLE runtime_usage_attribution_edges" in forward
    assert "FOREIGN KEY (org_id, usage_record_id)" in forward
    assert "REFERENCES runtime_model_call_usage (org_id, id)" in forward
    assert "ON UPDATE CASCADE" in forward
    assert "idx_runtime_usage_attribution_edges_natural" in forward
    assert "ENABLE ROW LEVEL SECURITY" in forward
    assert "GRANT SELECT, INSERT ON runtime_usage_attribution_edges" in forward
    assert (
        "UPDATE"
        not in forward.split("TO enterprise_app", maxsplit=1)[0].split(
            "GRANT", maxsplit=1
        )[-1]
    )
    assert "DROP TABLE IF EXISTS runtime_usage_attribution_edges" in rollback
    assert (
        "DROP CONSTRAINT IF EXISTS runtime_model_call_usage_org_id_id_key" in rollback
    )
    assert MigrationRunner.expected_manifest() == MigrationRunner.actual_manifest()
