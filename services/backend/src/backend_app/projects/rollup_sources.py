"""Rollup source adapters for the projects service (PRD-07).

Each destination's store answers a grouped, viewer-scoped ``count_by_project``.
These thin adapters declare which ``ProjectActivityCounts`` fields each source
fills and delegate to the store, so ``ProjectsService`` composes a page's counts
from a uniform ``ProjectRollupSource`` list without importing the ``library`` /
``todos`` / ``inbox`` / ``routines`` stores directly (they arrive as injected
sources, wired in ``app.py`` once every store exists).

``chats`` is deliberately NOT a source here: its rows live in ``ai-backend`` and
the facade fills the number (``backend`` counting them would invert the
``ai-backend → backend`` dependency into a cycle).
"""

from __future__ import annotations

from typing import Protocol


class _CountByProjectStore(Protocol):
    """Structural shape of a destination store that can group by project."""

    def count_by_project(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        caller_user_id: str,
        caller_roles: tuple[str, ...],
    ) -> dict[str, dict[str, int]]: ...


class _CountMembersStore(Protocol):
    """Structural shape of the projects store's batched member count."""

    def count_members_by_project(
        self, *, tenant_id: str, project_ids: tuple[str, ...]
    ) -> dict[str, int]: ...


class StoreRollupSource:
    """Adapts a destination store's ``count_by_project`` into a rollup source."""

    def __init__(self, *, store: _CountByProjectStore, fields: tuple[str, ...]) -> None:
        self._store = store
        self.fields = fields

    def count_by_project(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        caller_user_id: str,
        caller_roles: tuple[str, ...],
    ) -> dict[str, dict[str, int]]:
        return self._store.count_by_project(
            tenant_id=tenant_id,
            project_ids=project_ids,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )


class MembersRollupSource:
    """Members count sourced from the projects store's own membership rows.

    Replaces the former per-project ``list_memberships_for_project(limit=501)``
    scan (N+1) with one batched grouped read.
    """

    fields = ("members",)

    def __init__(self, store: _CountMembersStore) -> None:
        self._store = store

    def count_by_project(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        caller_user_id: str,
        caller_roles: tuple[str, ...],
    ) -> dict[str, dict[str, int]]:
        raw = self._store.count_members_by_project(
            tenant_id=tenant_id, project_ids=project_ids
        )
        return {pid: {"members": count} for pid, count in raw.items()}
