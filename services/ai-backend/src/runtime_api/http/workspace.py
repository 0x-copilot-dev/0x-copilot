"""HTTP routes for the Workspace pane data feeds (PR 1.5).

Two read-only endpoints under ``/v1/agent``:

- ``GET /conversations/{cid}/subagents``
- ``GET /conversations/{cid}/sources``

The handlers are thin shims around :class:`WorkspaceFeedService`. Identity is
resolved through the standard :class:`RuntimeServiceAuthenticator` headers
so that the runtime router's ``RUNTIME_USE`` scope dependency continues to
gate access for every request.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from agent_runtime.api.constants import Keys
from agent_runtime.api.workspace_feed_service import WorkspaceFeedService
from runtime_api.identity import Identity
from runtime_api.schemas import (
    SourceListResponse,
    SubagentListResponse,
    SubagentStatusFilter,
)


class WorkspaceFeedRoutes:
    """Route handlers for the Workspace pane subagents and sources data feeds."""

    @classmethod
    async def list_subagents(
        cls,
        request: Request,
        conversation_id: str,
        identity: Identity,
        status_filter: SubagentStatusFilter = Query(
            SubagentStatusFilter.ALL, alias="status"
        ),
        limit: int = Query(50, ge=1, le=200),
    ) -> SubagentListResponse:
        """Return subagent cards for the Agents tab, optionally filtered by status."""
        return await cls._service(request).list_subagents(
            org_id=identity.org_id,
            conversation_id=conversation_id,
            status_filter=status_filter,
            limit=limit,
        )

    @classmethod
    async def list_sources(
        cls,
        request: Request,
        conversation_id: str,
        identity: Identity,
        run_id: str | None = Query(None, min_length=1, max_length=128),
        limit: int = Query(200, ge=1, le=500),
    ) -> SourceListResponse:
        """Return deduplicated citation sources for the Sources tab, optionally scoped to one run."""
        return await cls._service(request).list_sources(
            org_id=identity.org_id,
            conversation_id=conversation_id,
            run_id=run_id,
            limit=limit,
        )

    @staticmethod
    def _service(request: Request) -> WorkspaceFeedService:
        """Return the wired WorkspaceFeedService or raise 503 if not configured."""
        service = getattr(request.app.state, "workspace_feed_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Workspace feed service is not configured.",
            )
        return service


def register_workspace_feed_routes(router: APIRouter) -> None:
    """Attach the workspace-feed endpoints to the ``/v1/agent`` router."""

    router.add_api_route(
        "/conversations/{conversation_id}/subagents",
        WorkspaceFeedRoutes.list_subagents,
        methods=["GET"],
        response_model=SubagentListResponse,
        name=Keys.RouteName.LIST_SUBAGENTS,
    )
    router.add_api_route(
        "/conversations/{conversation_id}/sources",
        WorkspaceFeedRoutes.list_sources,
        methods=["GET"],
        response_model=SourceListResponse,
        name=Keys.RouteName.LIST_SOURCES,
    )
