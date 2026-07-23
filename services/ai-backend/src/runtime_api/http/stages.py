"""HTTP routes for the single-artifact staged-write engine (PRD-D1).

Three endpoints mounted on ``/v1/agent`` and registered ONLY when ``SURFACES_V2``
is on (flag off ⇒ the routes do not exist ⇒ 404 — the cleanest byte-identical
guarantee):

- ``GET  /stages/{stage_id}?run_id=…``        → refetch after reconnect / 409
- ``POST /stages/{stage_id}/revisions``       → a user free-form edit (new rev)
- ``POST /stages/{stage_id}/decisions``       → approve / reject / restore

Every route carries ``run_id`` (query for GET, body for POST) because stage state
is a pure fold of that run's ledger — there is no stage→run table (SDR §6). Each
handler is a thin shim over :class:`~agent_runtime.api.stage_service.StageService`;
typed domain errors map to safe HTTP codes and NO route ever executes a write.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from starlette import status as http_status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.stage_service import StageService
from agent_runtime.surfaces_v2.staging import (
    EditConflict,
    MalformedDecision,
    StageForbidden,
    StageFrozen,
    StageNotFound,
    StagedWriteError,
    StaleRevision,
    UnsupportedDecision,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.stages import (
    StageDecisionRequest,
    StageRevisionRequest,
    StagedWriteView,
)

# Typed domain error → HTTP status. Every mapped error carries a safe public
# message; nothing here leaks internal state, and no mapping path emits an event.
_ERROR_STATUS: dict[type[StagedWriteError], int] = {
    StageNotFound: http_status.HTTP_404_NOT_FOUND,
    StageForbidden: http_status.HTTP_403_FORBIDDEN,
    StaleRevision: http_status.HTTP_409_CONFLICT,
    StageFrozen: http_status.HTTP_409_CONFLICT,
    EditConflict: http_status.HTTP_409_CONFLICT,
    UnsupportedDecision: http_status.HTTP_422_UNPROCESSABLE_ENTITY,
    MalformedDecision: http_status.HTTP_422_UNPROCESSABLE_ENTITY,
}


class StageRoutes:
    """Route handlers for the ``/v1/agent/stages`` endpoints."""

    @classmethod
    async def get_stage(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        run_id: str = Query(..., min_length=1),
    ) -> StagedWriteView:
        """Return the current folded staged-write view for a stage."""

        service = cls._service(request)
        try:
            state = await service.get_state(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                stage_id=stage_id,
            )
        except StagedWriteError as exc:
            raise cls._http(exc) from exc
        return StagedWriteView.from_state(run_id=run_id, state=state)

    @classmethod
    async def add_revision(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: StageRevisionRequest,
        run_id: str = Query(..., min_length=1),
    ) -> StagedWriteView:
        """Add a user free-form revision; server-diff yields authorship spans."""

        service = cls._service(request)
        try:
            state = await service.add_user_revision(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                stage_id=stage_id,
                base_rev=payload.base_rev,
                content_text=payload.content_text,
                title=payload.title,
            )
        except StagedWriteError as exc:
            raise cls._http(exc) from exc
        return StagedWriteView.from_state(run_id=run_id, state=state)

    @classmethod
    async def record_decision(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: StageDecisionRequest = Body(...),
        run_id: str = Query(..., min_length=1),
    ) -> StagedWriteView:
        """Record an approve / reject / restore decision. Nothing executes here."""

        service = cls._service(request)
        try:
            state = await service.record_decision(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                stage_id=stage_id,
                decision=payload.decision,
                rev=payload.rev,
            )
        except StagedWriteError as exc:
            raise cls._http(exc) from exc
        return StagedWriteView.from_state(run_id=run_id, state=state)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _service(request: Request) -> StageService:
        service = getattr(request.app.state, "stage_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Staged-write service is not configured.",
            )
        return service

    @staticmethod
    def _http(exc: StagedWriteError) -> HTTPException:
        """Map a typed domain error to a safe HTTPException (500 as last resort)."""

        code = _ERROR_STATUS.get(type(exc), http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        return HTTPException(status_code=code, detail=exc.safe_message)


def register_stage_routes(router: APIRouter) -> None:
    """Attach the staged-write endpoints (flag-gated by the caller) to ``/v1/agent``."""

    router.add_api_route(
        "/stages/{stage_id}",
        StageRoutes.get_stage,
        methods=["GET"],
        response_model=StagedWriteView,
        name="get_stage",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/stages/{stage_id}/revisions",
        StageRoutes.add_revision,
        methods=["POST"],
        response_model=StagedWriteView,
        name="add_stage_revision",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/stages/{stage_id}/decisions",
        StageRoutes.record_decision,
        methods=["POST"],
        response_model=StagedWriteView,
        name="record_stage_decision",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = ["StageRoutes", "register_stage_routes"]
