"""``POST /v1/agent/surfaces/{surface_id}/write-back`` — Save on an edited surface.

The connector half of the design's Save table. The user edited cells in place,
the client batched the deltas, and this is where they arrive. The route is a
thin shim over
:class:`~agent_runtime.capabilities.surfaces.write_back.SurfaceWriteBackCoordinator`,
which maps them onto one connector write op and **stages** them.

**This route cannot send anything.** It returns the same ``StagedWriteView`` the
stage routes return, with the rows sitting ``STAGED``. Execution is
``POST /v1/agent/stages/{stage_id}/apply`` — a second, deliberate user gesture
against a different service, whose stager is the only one composed with a commit
queue. Nothing here is a shortcut to it.

Registered under the same ``SURFACES_V2`` gate as the stage routes: flag off ⇒
the route does not exist ⇒ 404, byte-identical.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette import status as http_status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.capabilities.surfaces.write_back import (
    RunNotFound,
    SurfaceNotFound,
    SurfaceWriteBackCoordinator,
    SurfaceWriteBackError,
    WriteOpsUnavailable,
)
from agent_runtime.capabilities.surfaces.write_mapping import (
    SurfaceRowEdit,
    WriteMappingError,
    WriteMappingRejected,
    WriteMappingUnavailable,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.stages import StagedWriteView


class _Messages:
    """Safe public messages this route owns. Constants only — never model output."""

    #: The deployment composed no coordinator (no ports, or no stage service).
    #: Distinct from every domain error below, which mean the lane RAN and
    #: refused; this one means it was never wired.
    UNCONFIGURED = "Surface write-back is not configured for this deployment."
    #: A surface id is not an authorization capability: absent and foreign share
    #: one opaque answer, as the stage routes do.
    NOT_FOUND = "resource not found"
    #: Last resort when a typed error carries no safe message of its own.
    FAILED = "save failed"


#: Typed domain error → HTTP status. ``WriteMappingUnavailable`` is a 422 rather
#: than a 503 on purpose: it is not an outage, it is "no provider key is
#: configured", which the user fixes in Settings. Every mapped error carries a
#: safe public message and no path here appends a ledger event.
_ERROR_STATUS: dict[type[Exception], int] = {
    SurfaceNotFound: http_status.HTTP_404_NOT_FOUND,
    RunNotFound: http_status.HTTP_404_NOT_FOUND,
    WriteOpsUnavailable: http_status.HTTP_503_SERVICE_UNAVAILABLE,
    WriteMappingUnavailable: http_status.HTTP_422_UNPROCESSABLE_ENTITY,
    WriteMappingRejected: http_status.HTTP_422_UNPROCESSABLE_ENTITY,
    WriteMappingError: http_status.HTTP_502_BAD_GATEWAY,
    SurfaceWriteBackError: http_status.HTTP_422_UNPROCESSABLE_ENTITY,
}


class SurfaceWriteBackRequest(BaseModel):
    """Body: the run that owns the surface, plus the batched per-row edits.

    ``edits`` reuses the domain contract verbatim rather than mirroring it — a
    second ``*Input`` shape is how the wire and the domain drift, and the domain
    one already bounds every field it carries.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    edits: list[SurfaceRowEdit] = Field(min_length=1, max_length=200)


class SurfaceWriteBackRoutes:
    """Route handler for the connector write-back endpoint."""

    @classmethod
    async def write_back(
        cls,
        request: Request,
        surface_id: str,
        identity: Identity,
        payload: SurfaceWriteBackRequest = Body(...),
    ) -> StagedWriteView:
        """Stage the user's batched cell edits. Never sends; never approves."""

        coordinator = cls._coordinator(request)
        try:
            state = await coordinator.save(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=payload.run_id,
                surface_id=surface_id,
                edits=payload.edits,
            )
        except (SurfaceWriteBackError, WriteMappingError) as exc:
            raise cls._http(exc) from exc
        return StagedWriteView.from_state(run_id=payload.run_id, state=state)

    # -- helpers -------------------------------------------------------------

    @classmethod
    def _coordinator(cls, request: Request) -> SurfaceWriteBackCoordinator:
        """Return the coordinator the app composed, or 503 — never build one.

        ``RuntimeApiAppFactory.default_surface_write_back_coordinator`` owns the
        wiring, so the deployment's answer to "can this stage a connector save?"
        is decided once at boot. A route that assembles itself from whatever
        ``app.state`` happens to hold is a second composition root, and the two
        drift; it also turns a missing binding into a 500 on the first save
        instead of a 503 the operator can read.
        """

        coordinator = getattr(request.app.state, "surface_write_back_coordinator", None)
        if coordinator is None:
            raise HTTPException(
                http_status.HTTP_503_SERVICE_UNAVAILABLE,
                _Messages.UNCONFIGURED,
            )
        return coordinator

    @staticmethod
    def _http(exc: Exception) -> HTTPException:
        """Map a typed domain error to a safe HTTPException (502 as last resort)."""

        code = _ERROR_STATUS.get(type(exc), http_status.HTTP_502_BAD_GATEWAY)
        if code == http_status.HTTP_404_NOT_FOUND:
            return HTTPException(status_code=code, detail=_Messages.NOT_FOUND)
        message = getattr(exc, "safe_message", None)
        return HTTPException(status_code=code, detail=message or _Messages.FAILED)


def register_surface_write_back_routes(router: APIRouter) -> None:
    """Attach the write-back endpoint (flag-gated by the caller) to ``/v1/agent``."""

    router.add_api_route(
        "/surfaces/{surface_id}/write-back",
        SurfaceWriteBackRoutes.write_back,
        methods=["POST"],
        response_model=StagedWriteView,
        name="surface_write_back",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = [
    "SurfaceWriteBackRequest",
    "SurfaceWriteBackRoutes",
    "register_surface_write_back_routes",
]
