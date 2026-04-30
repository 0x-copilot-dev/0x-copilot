"""Compatibility imports for PostgreSQL runtime schema catalogs."""

from agent_runtime.persistence.schema.postgres import (
    AGENT_RUNTIME_TABLES,
    POSTGRES_AGENT_RUNTIME_MIGRATION_SQL,
    PostgresMigration,
    PostgresMigrationCatalog,
)

__all__ = [
    "AGENT_RUNTIME_TABLES",
    "POSTGRES_AGENT_RUNTIME_MIGRATION_SQL",
    "PostgresMigration",
    "PostgresMigrationCatalog",
]
