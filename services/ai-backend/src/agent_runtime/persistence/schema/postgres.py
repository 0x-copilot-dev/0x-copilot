"""PostgreSQL schema migration metadata for durable agent runtime persistence.

The canonical SQL now lives in
``services/ai-backend/migrations/0001_initial_runtime_persistence.sql`` and is
applied by the yoyo-backed :class:`MigrationRunner`. The
``POSTGRES_AGENT_RUNTIME_MIGRATION_SQL`` constant is kept as a thin shim that
reads the same file at module import so legacy callers (tests, catalog) see
identical content.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.constants import Values


AGENT_RUNTIME_TABLES = (
    "agent_conversations",
    "agent_messages",
    "agent_runs",
    "runtime_events",
    "runtime_outbox_events",
    "runtime_consumer_cursors",
    "runtime_async_tasks",
    "runtime_subagent_results",
    "runtime_tool_invocations",
    "runtime_approval_requests",
    "runtime_memory_scopes",
    "runtime_memory_items",
    "runtime_context_payloads",
    "runtime_compression_events",
    "runtime_capability_snapshots",
    "runtime_audit_log",
    "runtime_legal_holds",
    "runtime_deletion_evidence",
    "runtime_checkpoints",
)
# Tables added in subsequent migrations. Listed separately because
# ``AGENT_RUNTIME_TABLES`` is asserted to live entirely inside the
# initial migration; later-migration tables fail that check.
USAGE_BUDGET_TABLES = (
    "usage_budgets",
    "usage_budget_state",
    "usage_budget_reservations",
)


def _migration_sql(filename: str) -> str:
    """Read a migration .sql file once at import time."""

    # postgres.py -> schema/ -> persistence/ -> agent_runtime/ -> src/ -> ai-backend/
    migrations_dir = Path(__file__).resolve().parents[4] / "migrations"
    return (migrations_dir / filename).read_text()


# Concatenation of the initial schema and subsequent forward migrations.
# Historically these were separate code paths inside the legacy migrate();
# they are now ordered yoyo migrations on disk. The combined string is
# preserved here for backward compatibility with callers that inspect it.
POSTGRES_AGENT_RUNTIME_MIGRATION_SQL = (
    _migration_sql("0001_initial_runtime_persistence.sql")
    + "\n"
    + _migration_sql("0002_runtime_events_presentation.sql")
    + "\n"
    + _migration_sql("0003_audit_hardening.sql")
)


class PostgresMigration(RuntimeContract):
    """One deterministic PostgreSQL migration."""

    migration_id: str
    sql: str


class PostgresMigrationCatalog:
    """Migration catalog for CI validation and future PostgreSQL adapters."""

    @classmethod
    def initial_runtime_persistence(cls) -> PostgresMigration:
        """Return the first migration implementing runtime persistence tables."""

        return PostgresMigration(
            migration_id=Values.MIGRATION_ID,
            sql=POSTGRES_AGENT_RUNTIME_MIGRATION_SQL.strip(),
        )

    @classmethod
    def ordered_migrations(cls) -> tuple[PostgresMigration, ...]:
        """Return migrations in deterministic apply order."""

        return (cls.initial_runtime_persistence(),)
