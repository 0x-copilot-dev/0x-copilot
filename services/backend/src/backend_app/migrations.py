"""PostgreSQL schema constants for the backend service.

The canonical SQL now lives in ``services/backend/migrations/`` and is applied
by the yoyo-backed :class:`MigrationRunner`. These string constants are kept
as a thin shim that reads from the same files so any legacy importer keeps
working unchanged.
"""

from __future__ import annotations

from pathlib import Path


def _migration_sql(filename: str) -> str:
    # migrations.py -> backend_app/ -> src/ -> backend/
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    return (migrations_dir / filename).read_text()


_INITIAL_SQL = _migration_sql("0001_initial_mcp_skills.sql")
POSTGRES_AUDIT_HARDENING_SQL = _migration_sql("0002_audit_hardening.sql")

# The original module split MCP and skills into separate constants for
# documentation, but they were always concatenated on apply. The split is
# preserved by extracting the same regions from the canonical file.
_SKILLS_MARKER = "CREATE TABLE IF NOT EXISTS skills ("
_skills_offset = _INITIAL_SQL.index(_SKILLS_MARKER)

POSTGRES_MCP_REGISTRY_MIGRATION_SQL = _INITIAL_SQL[:_skills_offset]
POSTGRES_SKILLS_REGISTRY_MIGRATION_SQL = _INITIAL_SQL[_skills_offset:]

POSTGRES_BACKEND_MIGRATION_SQL = (
    POSTGRES_MCP_REGISTRY_MIGRATION_SQL
    + "\n"
    + POSTGRES_SKILLS_REGISTRY_MIGRATION_SQL
    + "\n"
    + POSTGRES_AUDIT_HARDENING_SQL
)
