"""PRD-07 — assert the 0003 conversation-project migration + rollback shape.

Named with ``migration`` so it runs under ``pytest tests/ -k migration`` (DoD 4).
"""

from __future__ import annotations

from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"


def test_up_migration_adds_column_and_partial_index() -> None:
    sql = (_MIGRATIONS / "0004_conversation_project.sql").read_text()
    assert "ADD COLUMN" in sql
    assert "project_id" in sql
    assert "idx_agent_conversations_project" in sql
    # Partial index predicate keeps the index lean + matches the count query.
    assert "project_id IS NOT NULL" in sql
    assert "deleted_at IS NULL" in sql


def test_rollback_drops_both_index_and_column() -> None:
    sql = (_MIGRATIONS / "0004_conversation_project.rollback.sql").read_text()
    assert "DROP INDEX" in sql
    assert "idx_agent_conversations_project" in sql
    assert "DROP COLUMN" in sql
    assert "project_id" in sql
