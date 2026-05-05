"""Admin CRUD HTTP routes for C8 retention policies.

The sweeper itself runs in the worker; these routes let operators seed
and tune per-tenant policies without dropping into psql. Every route is
gated by the ``admin:retention`` scope (router-level dependency); the
router-level ``runtime:use`` keeps the path consistent with the rest of
``/v1/*``. Until A10 ships across every deployment, the soft fall-back
to ``RBAC_MODE=audit`` keeps the routes usable while operators wire
their roles.
"""

from __future__ import annotations

from datetime import datetime, timezone

from enterprise_service_contracts.scopes import ADMIN_RETENTION, RUNTIME_USE
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from agent_runtime.api.constants import Keys
from agent_runtime.persistence.records.retention import RetentionPolicyRecord
from runtime_api.http.routes import RuntimeApiRoutes
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.retention import (
    RetentionPolicyListResponse,
    RetentionPolicyUpsertRequest,
    RetentionPolicyView,
)


class RetentionAdminRoutes:
    """Per-tenant retention policy admin endpoints."""

    @classmethod
    async def list_policies(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> RetentionPolicyListResponse:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = RuntimeApiRoutes.service(request).persistence
        rows = await persistence.list_retention_policies(org_id=org_id)
        return RetentionPolicyListResponse(
            policies=tuple(cls._to_view(row) for row in rows)
        )

    @classmethod
    async def upsert_policy(
        cls,
        request: Request,
        payload: RetentionPolicyUpsertRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> RetentionPolicyView:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        cls._validate_resource_id(payload)
        record = RetentionPolicyRecord(
            org_id=org_id,
            scope=payload.scope,
            resource_id=payload.resource_id,
            kind=payload.kind,
            ttl_seconds=payload.ttl_seconds,
            created_by_user_id=user_id,
        )
        persistence = RuntimeApiRoutes.service(request).persistence
        persisted = await persistence.upsert_retention_policy(record)
        return cls._to_view(persisted)

    @classmethod
    async def delete_policy(
        cls,
        request: Request,
        policy_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> dict[str, str]:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = RuntimeApiRoutes.service(request).persistence
        await persistence.delete_retention_policy(org_id=org_id, policy_id=policy_id)
        return {"status": "deleted"}

    @staticmethod
    def _validate_resource_id(payload: RetentionPolicyUpsertRequest) -> None:
        from agent_runtime.persistence.records.retention import RetentionScope

        if payload.scope is RetentionScope.ORG and payload.resource_id is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "resource_id must be null when scope='org'",
            )
        if payload.scope is not RetentionScope.ORG and payload.resource_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "resource_id is required when scope is not 'org'",
            )

    @staticmethod
    def _to_view(record: RetentionPolicyRecord) -> RetentionPolicyView:
        return RetentionPolicyView(
            id=record.id,
            org_id=record.org_id,
            scope=record.scope,
            resource_id=record.resource_id,
            kind=record.kind,
            ttl_seconds=int(record.ttl_seconds),
            created_by_user_id=record.created_by_user_id,
            created_at=record.created_at or datetime.now(timezone.utc),
            updated_at=record.updated_at or datetime.now(timezone.utc),
        )


class RetentionAdminRouter:
    """Build the ``/v1/retention/*`` router."""

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(
            prefix="/v1/retention",
            tags=["retention-admin"],
            dependencies=[
                Depends(RequireScopes(RUNTIME_USE)),
                Depends(RequireScopes(ADMIN_RETENTION)),
            ],
        )
        router.add_api_route(
            "/policies",
            RetentionAdminRoutes.list_policies,
            methods=["GET"],
            response_model=RetentionPolicyListResponse,
            name=Keys.RouteName.RETENTION_LIST,
        )
        router.add_api_route(
            "/policies",
            RetentionAdminRoutes.upsert_policy,
            methods=["POST"],
            response_model=RetentionPolicyView,
            name=Keys.RouteName.RETENTION_UPSERT,
        )
        router.add_api_route(
            "/policies/{policy_id}",
            RetentionAdminRoutes.delete_policy,
            methods=["DELETE"],
            name=Keys.RouteName.RETENTION_DELETE,
        )
        return router
