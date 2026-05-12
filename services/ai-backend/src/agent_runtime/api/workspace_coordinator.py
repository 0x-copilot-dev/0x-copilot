"""Workspace admin coordinator (P22 / PR 1).

Owns: ``get_workspace_defaults``, ``update_workspace_defaults``,
``request_workspace_export``, ``record_workspace_delete_attempt``.

These operations affect a workspace as a whole rather than a single
conversation or run. The existing :class:`WorkspaceDefaultsService` continues
to be the underlying domain service; this coordinator is the
controller-facing surface that the legacy ``RuntimeApiService`` previously
provided.

PR 1 of the P22 split (see ``docs/refactor/19-runtime-api-service-split.md``)
ships this as a thin forwarder onto :class:`RuntimeApiService`. PR 4 will move
the method bodies here and reduce the legacy class to a 1-line delegator. PR 5
deletes the legacy class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from runtime_api.schemas import (
    UpdateWorkspaceDefaultsRequest,
    WorkspaceDefaultsResponse,
)

if TYPE_CHECKING:
    from agent_runtime.api.service import RuntimeApiService


class WorkspaceCoordinator:
    """Coordinate workspace-level admin operations.

    Public surface tracked by PRD §3.3. Methods forward to the legacy
    :class:`RuntimeApiService` during PR 1; implementation moves here in PR 4.
    """

    def __init__(self, *, legacy: "RuntimeApiService") -> None:
        self._legacy = legacy

    async def get_workspace_defaults(self, *, org_id: str) -> WorkspaceDefaultsResponse:
        return await self._legacy.get_workspace_defaults(org_id=org_id)

    async def update_workspace_defaults(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        request: UpdateWorkspaceDefaultsRequest,
    ) -> WorkspaceDefaultsResponse:
        return await self._legacy.update_workspace_defaults(
            org_id=org_id,
            actor_user_id=actor_user_id,
            request=request,
        )

    async def request_workspace_export(
        self,
        *,
        org_id: str,
        actor_user_id: str,
    ) -> dict[str, str]:
        return await self._legacy.request_workspace_export(
            org_id=org_id,
            actor_user_id=actor_user_id,
        )

    async def record_workspace_delete_attempt(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        typed_confirmation_correct: bool,
    ) -> None:
        await self._legacy.record_workspace_delete_attempt(
            org_id=org_id,
            actor_user_id=actor_user_id,
            typed_confirmation_correct=typed_confirmation_correct,
        )
