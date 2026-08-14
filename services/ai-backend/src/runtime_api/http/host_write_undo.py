"""HTTP routes for undoing the agent's writes to the user's disk.

Two endpoints mounted on ``/v1/agent``:

- ``GET  /runs/{run_id}/host-writes``        → what this run changed on disk
- ``POST /runs/{run_id}/host-writes/revert`` → put it back (optionally one call)

Identity comes from the verified session only; a caller-supplied ``org_id`` is
never read, and the service re-reads the run through persistence before it
touches the journal. A missing run and someone else's run are the same 404 — the
existence of another tenant's run is not something a probe may learn.

The revert body carries at most a ``tool_call_id``. It never carries a path, a
digest, or a root: the only writable targets are the ones the floor already
admitted and recorded, so this route cannot be steered at a new one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.host_write_undo_service import (
    HostWriteRevertReport,
    HostWriteUndoListing,
    HostWriteUndoNotFoundError,
    HostWriteUndoService,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes


class HostWriteRevertRequest(BaseModel):
    """Optional narrowing to a single tool call.

    Omitted (``None``) means "undo everything this run wrote", which is the
    coarse action a user reaches for after a run goes wrong. Supplying the id
    rewinds exactly one tool call and leaves every later write standing.
    """

    tool_call_id: str | None = Field(default=None, min_length=1, max_length=256)


class HostWriteUndoRoutes:
    """Thin identity boundary over :class:`HostWriteUndoService`."""

    @classmethod
    async def list_host_writes(
        cls,
        request: Request,
        identity: Identity,
        run_id: str = Path(min_length=1, max_length=160),
    ) -> HostWriteUndoListing:
        """Return every captured change this run made to the real filesystem."""

        try:
            return await cls._service(request).list_writes(
                org_id=identity.org_id, run_id=run_id
            )
        except HostWriteUndoNotFoundError as exc:
            raise cls._not_found() from exc

    @classmethod
    async def revert_host_writes(
        cls,
        request: Request,
        identity: Identity,
        body: HostWriteRevertRequest,
        run_id: str = Path(min_length=1, max_length=160),
    ) -> HostWriteRevertReport:
        """Restore the captured prior content, auditing the act."""

        try:
            return await cls._service(request).revert(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                tool_call_id=body.tool_call_id,
            )
        except HostWriteUndoNotFoundError as exc:
            raise cls._not_found() from exc

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")

    @staticmethod
    def _service(request: Request) -> HostWriteUndoService:
        service = getattr(request.app.state, "host_write_undo_service", None)
        if service is None:
            # Every non-desktop image: no object store, so nothing was ever
            # captured and there is nothing to undo. 503 rather than 404 because
            # the run may well exist — it is the capability that is absent.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Agent-write undo is not available on this deployment.",
            )
        return service


def register_host_write_undo_routes(router: APIRouter) -> None:
    """Attach the host-write listing + revert endpoints to ``/v1/agent``."""

    router.add_api_route(
        "/runs/{run_id}/host-writes",
        HostWriteUndoRoutes.list_host_writes,
        methods=["GET"],
        response_model=HostWriteUndoListing,
        name="list_host_writes",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/runs/{run_id}/host-writes/revert",
        HostWriteUndoRoutes.revert_host_writes,
        methods=["POST"],
        response_model=HostWriteRevertReport,
        name="revert_host_writes",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = (
    "HostWriteRevertRequest",
    "HostWriteUndoRoutes",
    "register_host_write_undo_routes",
)
