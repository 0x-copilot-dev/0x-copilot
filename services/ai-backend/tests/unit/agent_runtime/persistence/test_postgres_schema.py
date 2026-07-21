from __future__ import annotations

from agent_runtime.persistence.schema.postgres import (
    AGENT_RUNTIME_TABLES,
    POSTGRES_AGENT_RUNTIME_MIGRATION_SQL,
    PostgresMigrationCatalog,
)


class PostgresSchemaTestMixin:
    TENANT_SCOPED_TABLES = frozenset(
        table for table in AGENT_RUNTIME_TABLES if table != "runtime_consumer_cursors"
    )

    def table_segment(self, table_name: str) -> str:
        # The baseline is pg_dump-generated: "CREATE TABLE <name> (" … ");".
        marker = f"CREATE TABLE {table_name} ("
        start = POSTGRES_AGENT_RUNTIME_MIGRATION_SQL.index(marker)
        end = POSTGRES_AGENT_RUNTIME_MIGRATION_SQL.index("\n);", start)
        return POSTGRES_AGENT_RUNTIME_MIGRATION_SQL[start:end]


class TestPostgresSchema(PostgresSchemaTestMixin):
    def test_initial_migration_covers_runtime_persistence_table_set(self) -> None:
        migration = PostgresMigrationCatalog.initial_runtime_persistence()
        ordered = PostgresMigrationCatalog.ordered_migrations()

        assert migration.migration_id == "0001_agent_runtime_persistence"
        assert ordered == (migration,)
        assert "agent_state" not in migration.sql
        for table_name in AGENT_RUNTIME_TABLES:
            assert f"CREATE TABLE {table_name} (" in migration.sql

    def test_tenant_tables_have_org_id_and_required_replay_indexes(self) -> None:
        import re

        for table_name in self.TENANT_SCOPED_TABLES:
            assert re.search(
                r"^\s*org_id text NOT NULL", self.table_segment(table_name), re.M
            ), table_name

        assert "idx_runtime_events_run_sequence" in POSTGRES_AGENT_RUNTIME_MIGRATION_SQL
        assert (
            "runtime_events USING btree (run_id, sequence_no)"
            in POSTGRES_AGENT_RUNTIME_MIGRATION_SQL
        )
        assert (
            "idx_runtime_outbox_status_available"
            in POSTGRES_AGENT_RUNTIME_MIGRATION_SQL
        )
        assert "locked_by text" in POSTGRES_AGENT_RUNTIME_MIGRATION_SQL
        assert (
            "lock_expires_at timestamp with time zone"
            in POSTGRES_AGENT_RUNTIME_MIGRATION_SQL
        )
