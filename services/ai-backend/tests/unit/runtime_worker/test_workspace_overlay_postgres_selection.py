"""Composition checks for C1's PostgreSQL workspace-overlay repository."""

from __future__ import annotations

from types import SimpleNamespace

from runtime_adapters.postgres.workspace_overlay_store import (
    PostgresWorkspaceOverlayStore,
)
from runtime_worker.loop import RuntimeWorker


class _PostgresPersistence:
    """Only the private pool seam the production adapter legitimately borrows."""

    def _role_connection(self, _role: str) -> object:
        raise AssertionError("selection must not open a database connection")


def _selection_target(*, backend: str, persistence: object) -> object:
    target = object.__new__(RuntimeWorker)
    target.settings = SimpleNamespace(store=SimpleNamespace(backend=backend))
    target.persistence = persistence
    return target


def test_postgres_runtime_store_selects_the_durable_c1_overlay_adapter() -> None:
    persistence = _PostgresPersistence()
    target = _selection_target(backend="postgres", persistence=persistence)

    selected = RuntimeWorker._default_workspace_overlay_store(target)

    assert isinstance(selected, PostgresWorkspaceOverlayStore)
    assert selected._store is persistence


def test_postgres_without_the_runtime_pool_seam_fails_closed() -> None:
    target = _selection_target(backend="postgres", persistence=object())

    assert RuntimeWorker._default_workspace_overlay_store(target) is None
