"""Identity-bound administrative legal-hold lifecycle routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from copilot_service_contracts.scopes import ADMIN_RETENTION, RUNTIME_USE

from agent_runtime.api.legal_hold_contracts import (
    LegalHoldCreateRequest,
    LegalHoldListResponse,
    LegalHoldReleaseRequest,
    LegalHoldView,
)
from agent_runtime.api.legal_hold_service import (
    LegalHoldNotFoundError,
    LegalHoldService,
)
from agent_runtime.persistence.records import LegalHoldConflict
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes


class LegalHoldRoutes:
    """Transport layer over the narrow legal-hold application service."""

    @staticmethod
    def _require_retention_admin(identity: Identity) -> None:
        """Enforce this destructive control plane in every RBAC mode.

        ``RequireScopes`` intentionally supports audit-only rollout mode for
        ordinary routes.  A legal-hold list leaks protected target identifiers
        to a tenant administrator, and create/release changes deletion rights,
        so this route must never inherit audit-mode pass-through behavior.
        """

        required = {RUNTIME_USE, ADMIN_RETENTION}
        if not required.issubset(identity.permission_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Retention administrator permission is required",
            )

    @staticmethod
    def _service(request: Request) -> LegalHoldService:
        return LegalHoldService(request.app.state.runtime_persistence)

    @classmethod
    async def create(
        cls,
        request: Request,
        payload: LegalHoldCreateRequest,
        identity: Identity,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=8, max_length=255
        ),
    ) -> LegalHoldView:
        cls._require_retention_admin(identity)
        try:
            return await cls._service(request).create(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                request=payload,
                idempotency_key=idempotency_key,
            )
        except LegalHoldNotFoundError as exc:
            # Same response for cross-tenant and absent targets.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Legal-hold target not found",
            ) from exc
        except LegalHoldConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Legal-hold request conflicts",
            ) from exc

    @classmethod
    async def list(
        cls,
        request: Request,
        identity: Identity,
        include_released: bool = Query(False),
        limit: int = Query(50, ge=1, le=100),
    ) -> LegalHoldListResponse:
        cls._require_retention_admin(identity)
        return LegalHoldListResponse(
            holds=await cls._service(request).list(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                include_released=include_released,
                limit=limit,
            )
        )

    @classmethod
    async def release(
        cls,
        request: Request,
        hold_id: str,
        payload: LegalHoldReleaseRequest,
        identity: Identity,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=8, max_length=255
        ),
    ) -> LegalHoldView:
        cls._require_retention_admin(identity)
        try:
            return await cls._service(request).release(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                hold_id=hold_id,
                request=payload,
                idempotency_key=idempotency_key,
            )
        except LegalHoldNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Legal hold not found",
            ) from exc
        except LegalHoldConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Legal-hold request conflicts",
            ) from exc


class LegalHoldRouter:
    """Build the admin-only retention control-plane router."""

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(
            prefix="/v1/retention/legal-holds",
            tags=["retention-admin"],
            dependencies=[
                Depends(RequireScopes(RUNTIME_USE)),
                Depends(RequireScopes(ADMIN_RETENTION)),
            ],
        )
        router.add_api_route(
            "",
            LegalHoldRoutes.list,
            methods=["GET"],
            response_model=LegalHoldListResponse,
            name="legal_hold_list",
        )
        router.add_api_route(
            "",
            LegalHoldRoutes.create,
            methods=["POST"],
            response_model=LegalHoldView,
            status_code=status.HTTP_201_CREATED,
            name="legal_hold_create",
        )
        router.add_api_route(
            "/{hold_id}/release",
            LegalHoldRoutes.release,
            methods=["POST"],
            response_model=LegalHoldView,
            name="legal_hold_release",
        )
        return router


__all__ = ("LegalHoldRouter", "LegalHoldRoutes")
