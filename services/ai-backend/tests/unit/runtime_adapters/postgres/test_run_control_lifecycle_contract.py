"""Hermetic Postgres lifecycle contract for run-control event replay."""

from __future__ import annotations

import inspect

from runtime_adapters.postgres import PostgresRuntimeApiStore


def test_user_facing_replay_joins_live_conversation_scope() -> None:
    source = inspect.getsource(PostgresRuntimeApiStore.list_events_after)

    assert "JOIN agent_runs r" in source
    assert "NOT EXISTS" in source
    assert "c.deleted_at IS NOT NULL" in source


def test_lifecycle_scan_keeps_tombstoned_events_visible_to_retention() -> None:
    source = inspect.getsource(
        PostgresRuntimeApiStore.list_lifecycle_reference_events_window
    )

    assert "FROM runtime_events" in source
    assert "deleted_at" not in source


def test_history_delete_retains_events_until_existing_retention_sweep() -> None:
    delete_source = inspect.getsource(PostgresRuntimeApiStore.delete_user_history)
    tombstone_source = inspect.getsource(PostgresRuntimeApiStore._sweep_events_chunked)
    purge_source = inspect.getsource(
        PostgresRuntimeApiStore._sweep_events_tombstoned_chunked
    )

    assert "SELECT COUNT(*) AS count" in delete_source
    assert "FROM runtime_events" in delete_source
    assert "DELETE FROM runtime_events" not in delete_source
    assert "UPDATE runtime_events" in tombstone_source
    assert "DELETE FROM runtime_events" in purge_source
