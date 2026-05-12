"""Run lifecycle coordinator (P22 / PR 1).

Owns: ``create_run``, ``cancel_run``. Single source of truth for run-state
transitions on the API side. The worker uses persistence ports directly and
does not depend on this coordinator.

PR 1 of the P22 split (see ``docs/refactor/19-runtime-api-service-split.md``)
ships this as a thin forwarder onto :class:`RuntimeApiService`. PR 4 will move
the method bodies here and reduce the legacy class to a 1-line delegator. PR 5
deletes the legacy class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from runtime_api.schemas import (
    CancelRunRequest,
    CancelRunResponse,
    CreateRunRequest,
    CreateRunResponse,
)

if TYPE_CHECKING:
    from agent_runtime.api.service import RuntimeApiService


class RunCoordinator:
    """Coordinate run lifecycle commands.

    Public surface tracked by PRD §3.3. Methods forward to the legacy
    :class:`RuntimeApiService` during PR 1; implementation moves here in PR 4.
    """

    def __init__(self, *, legacy: "RuntimeApiService") -> None:
        self._legacy = legacy

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        return await self._legacy.create_run(request)

    async def cancel_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        return await self._legacy.cancel_run(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            request=request,
        )
