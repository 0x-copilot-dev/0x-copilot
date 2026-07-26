"""Generic owner-scoped MCP effect decision route.

Workspace effects retain their separate receipt-bearing ``/decisions`` route.
This singular route is deliberately limited to the standard MCP executor and
delegates all state changes and queue work to the shared A4/A5 decision service.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette import status as http_status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.effect_stage_decision_service import EffectStageDecisionService
from agent_runtime.effects.errors import (
    EffectStageDigestMismatch,
    EffectStageError,
    EffectStageForbidden,
    EffectStageIdempotencyConflict,
    EffectStageInvalidTransition,
    EffectStageMalformedEvent,
    EffectStageNotFound,
    EffectStagePolicyBlocked,
    EffectStageStaleRevision,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.effect_stage_decision import (
    EffectStageDecisionRequest,
    EffectStageDecisionResponse,
)

_ERROR_STATUS: dict[type[EffectStageError], int] = {
    EffectStageNotFound: http_status.HTTP_404_NOT_FOUND,
    EffectStageForbidden: http_status.HTTP_404_NOT_FOUND,
    EffectStageStaleRevision: http_status.HTTP_409_CONFLICT,
    EffectStageDigestMismatch: http_status.HTTP_409_CONFLICT,
    EffectStageInvalidTransition: http_status.HTTP_409_CONFLICT,
    EffectStageIdempotencyConflict: http_status.HTTP_409_CONFLICT,
    EffectStagePolicyBlocked: http_status.HTTP_409_CONFLICT,
    EffectStageMalformedEvent: http_status.HTTP_409_CONFLICT,
}


class EffectStageDecisionRoutes:
    """HTTP boundary for normal external MCP effect approval/rejection."""

    @classmethod
    async def record_decision(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: EffectStageDecisionRequest = Body(...),
        run_id: str = Query(..., min_length=1),
    ) -> EffectStageDecisionResponse:
        service = cls._service(request)
        try:
            state = await service.record_decision(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                stage_id=stage_id,
                revision=payload.revision,
                decision=EffectDecisionKind(payload.decision),
                proposal_digest=payload.proposal_digest,
                target_digest=payload.target_digest,
                allowed_executors=frozenset({EffectExecutorKind.MCP}),
            )
            return EffectStageDecisionResponse.from_state(state)
        except EffectStageError as exc:
            raise cls._http(exc) from exc

    @staticmethod
    def _service(request: Request) -> EffectStageDecisionService:
        service = getattr(request.app.state, "effect_stage_decision_service", None)
        if service is None:
            raise HTTPException(
                http_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Effect stage decisions are not configured.",
            )
        return service

    @staticmethod
    def _http(exc: EffectStageError) -> HTTPException:
        code = _ERROR_STATUS.get(type(exc), http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        if code == http_status.HTTP_404_NOT_FOUND:
            return HTTPException(status_code=code, detail="resource not found")
        return HTTPException(status_code=code, detail=exc.safe_message)


def register_effect_stage_decision_routes(router: APIRouter) -> None:
    """Mount the generic MCP-only decision endpoint.

    The singular suffix intentionally avoids the workspace route's existing
    receipt contract at ``/effect-stages/{stage_id}/decisions``.
    """

    router.add_api_route(
        "/effect-stages/{stage_id}/decision",
        EffectStageDecisionRoutes.record_decision,
        methods=["POST"],
        response_model=EffectStageDecisionResponse,
        name="record_effect_stage_decision",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = ["EffectStageDecisionRoutes", "register_effect_stage_decision_routes"]
