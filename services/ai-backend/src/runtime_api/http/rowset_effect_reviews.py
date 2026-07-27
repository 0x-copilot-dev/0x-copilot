"""Owner-scoped review and exact-action routes for canonical row-set effects."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette import status as http_status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.rowset_effect_review import (
    RowSetEffectReview,
    RowSetEffectReviewService,
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
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.rowset_effect_review import (
    RowSetActionRequest,
    RowSetDecisionRequest,
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


class RowSetEffectReviewRoutes:
    """HTTP boundary over one review service and the shared effect outbox."""

    @classmethod
    async def review(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        run_id: str = Query(..., min_length=1),
    ) -> RowSetEffectReview:
        return await cls._invoke(
            request,
            "review",
            org_id=identity.org_id,
            user_id=identity.user_id,
            run_id=run_id,
            stage_id=stage_id,
        )

    @classmethod
    async def record_decisions(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: RowSetDecisionRequest = Body(...),
        run_id: str = Query(..., min_length=1),
    ) -> RowSetEffectReview:
        return await cls._invoke(
            request,
            "record_row_decisions",
            org_id=identity.org_id,
            user_id=identity.user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=payload.revision,
            proposal_digest=payload.proposal_digest,
            target_digest=payload.target_digest,
            decisions=payload.decisions,
        )

    @classmethod
    async def apply(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: RowSetActionRequest = Body(...),
        run_id: str = Query(..., min_length=1),
    ) -> RowSetEffectReview:
        if payload.basis_ledger_id is not None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="basis_ledger_id is not valid for an initial apply",
            )
        return await cls._invoke(
            request,
            "apply",
            org_id=identity.org_id,
            user_id=identity.user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=payload.revision,
            proposal_digest=payload.proposal_digest,
            target_digest=payload.target_digest,
            row_keys=payload.row_keys,
            basis_sequence_no=payload.basis_sequence_no,
        )

    @classmethod
    async def retry(
        cls,
        request: Request,
        stage_id: str,
        identity: Identity,
        payload: RowSetActionRequest = Body(...),
        run_id: str = Query(..., min_length=1),
    ) -> RowSetEffectReview:
        if payload.basis_ledger_id is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="basis_ledger_id is required for failed-row retry",
            )
        return await cls._invoke(
            request,
            "retry",
            org_id=identity.org_id,
            user_id=identity.user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=payload.revision,
            proposal_digest=payload.proposal_digest,
            target_digest=payload.target_digest,
            row_keys=payload.row_keys,
            basis_sequence_no=payload.basis_sequence_no,
            basis_ledger_id=payload.basis_ledger_id,
        )

    @classmethod
    async def _invoke(
        cls,
        request: Request,
        method: str,
        **kwargs: object,
    ) -> RowSetEffectReview:
        service = cls._service(request)
        try:
            operation = getattr(service, method)
            return await operation(**kwargs)
        except EffectStageError as exc:
            raise cls._http(exc) from exc

    @staticmethod
    def _service(request: Request) -> RowSetEffectReviewService:
        service = getattr(request.app.state, "rowset_effect_review_service", None)
        if service is None:
            raise HTTPException(
                http_status.HTTP_503_SERVICE_UNAVAILABLE,
                "Row-set review is not configured.",
            )
        return service

    @staticmethod
    def _http(exc: EffectStageError) -> HTTPException:
        code = _ERROR_STATUS.get(type(exc), http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        if code == http_status.HTTP_404_NOT_FOUND:
            return HTTPException(status_code=code, detail="resource not found")
        return HTTPException(status_code=code, detail=exc.safe_message)


def register_rowset_effect_review_routes(router: APIRouter) -> None:
    dependencies = [Depends(RequireScopes(RUNTIME_USE))]
    prefix = "/effect-stages/{stage_id}/rowset"
    router.add_api_route(
        f"{prefix}/review",
        RowSetEffectReviewRoutes.review,
        methods=["GET"],
        response_model=RowSetEffectReview,
        name="get_rowset_effect_review",
        dependencies=dependencies,
    )
    router.add_api_route(
        f"{prefix}/decisions",
        RowSetEffectReviewRoutes.record_decisions,
        methods=["POST"],
        response_model=RowSetEffectReview,
        name="record_rowset_effect_decisions",
        dependencies=dependencies,
    )
    router.add_api_route(
        f"{prefix}/apply",
        RowSetEffectReviewRoutes.apply,
        methods=["POST"],
        response_model=RowSetEffectReview,
        name="apply_rowset_effect",
        dependencies=dependencies,
    )
    router.add_api_route(
        f"{prefix}/retry",
        RowSetEffectReviewRoutes.retry,
        methods=["POST"],
        response_model=RowSetEffectReview,
        name="retry_failed_rowset_effect",
        dependencies=dependencies,
    )


__all__ = ["RowSetEffectReviewRoutes", "register_rowset_effect_review_routes"]
