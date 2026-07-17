"""FastAPI router for the tier-2 adapter registry.

Internal-only surface mounted under ``/internal/v1/adapter_registry``.
The facade re-exposes the app-facing subset under ``/v1/adapter_registry``
(see ``backend_facade.adapter_registry_routes``).
"""

from __future__ import annotations

from copilot_service_contracts.scopes import ADMIN_AUDIT_EXPORT, RUNTIME_USE
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)

from backend_app.adapter_registry.models import (
    AdapterCandidateListResponse,
    AdapterCandidateStatus,
    AdapterCandidateSubmission,
    AdapterCandidateView,
    AdapterRegistryOptOutRequest,
    AdapterRegistryOptOutResponse,
    AdapterReviewDecisionRequest,
    PromotedAdaptersResponse,
)
from backend_app.adapter_registry.registry_service import AdapterRegistryService
from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes


def register_adapter_registry_routes(
    app: FastAPI,
    *,
    service: AdapterRegistryService,
) -> None:
    """Attach adapter-registry routes to the FastAPI app."""

    app.state.adapter_registry_service = service

    @app.post(
        "/internal/v1/adapter_registry/candidates",
        response_model=AdapterCandidateView,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def submit_candidate(
        request: Request,
        payload: AdapterCandidateSubmission,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AdapterCandidateView:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = service.submit_candidate(
            tenant_id=identity.org_id,
            submitter_user_id=identity.user_id,
            submission=payload,
        )
        view = service.get_candidate(
            candidate_id=record.candidate_id,
            viewer_tenant_id=identity.org_id,
        )
        if view is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "candidate vanished after insert",
            )
        return view

    @app.get(
        "/internal/v1/adapter_registry/candidates",
        response_model=AdapterCandidateListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def list_candidates(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        candidate_status: str | None = Query(
            None, alias="status", min_length=1, max_length=32
        ),
        limit: int = Query(50, ge=1, le=200),
    ) -> AdapterCandidateListResponse:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        parsed_status = _parse_status(candidate_status)
        views = service.list_candidates(status=parsed_status, limit=limit)
        return AdapterCandidateListResponse(candidates=views)

    @app.get(
        "/internal/v1/adapter_registry/candidates/{candidate_id}",
        response_model=AdapterCandidateView,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def get_candidate(
        request: Request,
        candidate_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AdapterCandidateView:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        view = service.get_candidate(
            candidate_id=candidate_id,
            viewer_is_admin=True,
        )
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate_not_found")
        return view

    @app.post(
        "/internal/v1/adapter_registry/candidates/{candidate_id}/decisions",
        response_model=AdapterCandidateView,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def decide_candidate(
        request: Request,
        payload: AdapterReviewDecisionRequest,
        candidate_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AdapterCandidateView:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.decide(
                candidate_id=candidate_id,
                reviewer_user_id=identity.user_id,
                reviewer_org_id=identity.org_id,
                action=payload.action,
                notes=payload.notes,
            )
        except ValueError as exc:
            message = str(exc)
            code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in message
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(code, message) from exc
        view = service.get_candidate(
            candidate_id=candidate_id,
            viewer_is_admin=True,
        )
        if view is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "candidate vanished after decision",
            )
        return view

    @app.get(
        "/internal/v1/adapter_registry/promoted",
        response_model=PromotedAdaptersResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_promoted(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> PromotedAdaptersResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        adapters = service.list_promoted_for_tenant(tenant_id=identity.org_id)
        return PromotedAdaptersResponse(adapters=adapters)

    @app.put(
        "/internal/v1/adapter_registry/opt-out",
        response_model=AdapterRegistryOptOutResponse,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def set_opt_out(
        request: Request,
        payload: AdapterRegistryOptOutRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AdapterRegistryOptOutResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        saved = service.set_tenant_opt_out(
            tenant_id=identity.org_id,
            actor_user_id=identity.user_id,
            opted_out=payload.opted_out,
        )
        return AdapterRegistryOptOutResponse(
            tenant_id=saved.tenant_id,
            opted_out=saved.opted_out,
            updated_at=saved.updated_at,
        )

    @app.get(
        "/internal/v1/adapter_registry/opt-out",
        response_model=AdapterRegistryOptOutResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_opt_out(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AdapterRegistryOptOutResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        existing = service.get_tenant_opt_out(tenant_id=identity.org_id)
        return AdapterRegistryOptOutResponse(
            tenant_id=existing.tenant_id,
            opted_out=existing.opted_out,
            updated_at=existing.updated_at,
        )


def _parse_status(raw: str | None) -> AdapterCandidateStatus | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return AdapterCandidateStatus(raw.strip())
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown status: {raw!r}",
        ) from exc


__all__ = ["register_adapter_registry_routes"]
