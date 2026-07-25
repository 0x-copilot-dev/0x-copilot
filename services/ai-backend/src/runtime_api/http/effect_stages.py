"""C3 workspace-only receipt route over canonical A4 effect stages."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from starlette import status as http_status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.workspace_approval_service import (
    WorkspaceApprovalDecisionService,
)
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
from agent_runtime.surfaces_v2.ledger_models import EffectDecisionKind
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.workspace_approval import (
    WorkspaceApprovalDecisionReceipt,
    WorkspaceApprovalDecisionRequest,
)

_ERROR_STATUS: dict[type[EffectStageError], int] = {
    # This route is intentionally non-enumerable: foreign, unknown, and
    # non-workspace stages all resolve to the same public 404.
    EffectStageNotFound: http_status.HTTP_404_NOT_FOUND,
    EffectStageForbidden: http_status.HTTP_404_NOT_FOUND,
    EffectStageStaleRevision: http_status.HTTP_409_CONFLICT,
    EffectStageDigestMismatch: http_status.HTTP_409_CONFLICT,
    EffectStageInvalidTransition: http_status.HTTP_409_CONFLICT,
    EffectStageIdempotencyConflict: http_status.HTTP_409_CONFLICT,
    EffectStagePolicyBlocked: http_status.HTTP_409_CONFLICT,
    EffectStageMalformedEvent: http_status.HTTP_409_CONFLICT,
}


class WorkspaceEffectStageRoutes:
    """HTTP boundary for desktop's digest-pinned workspace decision receipt."""

    @classmethod
    async def record_decision(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: WorkspaceApprovalDecisionRequest = Body(...),
        run_id: str = Query(..., min_length=1),
    ) -> WorkspaceApprovalDecisionReceipt:
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
            )
            return WorkspaceApprovalDecisionReceipt.from_state(state)
        except EffectStageError as exc:
            raise cls._http(exc) from exc

    @staticmethod
    def _service(request: Request) -> WorkspaceApprovalDecisionService:
        service = getattr(
            request.app.state, "workspace_approval_decision_service", None
        )
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Workspace approval decisions are not configured.",
            )
        return service

    @staticmethod
    def _http(exc: EffectStageError) -> HTTPException:
        return HTTPException(
            status_code=_ERROR_STATUS.get(
                type(exc), http_status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=exc.safe_message,
        )


def register_workspace_effect_stage_routes(router: APIRouter) -> None:
    """Mount only C3's workspace approval receipt endpoint.

    Legacy ``/stages`` routes retain their existing ``StagedWriteView`` wire
    contract.  Canonical A4 stages use this separate path so callers cannot
    accidentally treat an unpinned legacy response as C2 permit evidence.
    """

    router.add_api_route(
        "/effect-stages/{stage_id}/decisions",
        WorkspaceEffectStageRoutes.record_decision,
        methods=["POST"],
        response_model=WorkspaceApprovalDecisionReceipt,
        name="record_workspace_effect_stage_decision",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = ["WorkspaceEffectStageRoutes", "register_workspace_effect_stage_routes"]
