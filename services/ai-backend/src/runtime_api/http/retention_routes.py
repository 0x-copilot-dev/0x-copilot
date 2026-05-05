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
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
)
from agent_runtime.retention import (
    DEPLOYMENT_DEFAULT_TTL_SECONDS,
    RetentionPolicyResolver,
)
from runtime_api.http.routes import RuntimeApiRoutes
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.retention import (
    RetentionEffectivePolicyEntry,
    RetentionEffectiveResponse,
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
    async def effective(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> RetentionEffectiveResponse:
        """Per-kind effective TTL view (PR 4.3).

        Re-uses the same ``RetentionPolicyResolver`` the sweeper uses
        — so the displayed value is always the value that gets applied.
        Reads are not gated by ``ADMIN_RETENTION``: any tenant member
        can see their org's retention summary in the Privacy & data
        panel. Per-resource (user / conversation / assistant) overrides
        are deliberately not surfaced here; they are visible only via
        ``GET /v1/retention/policies`` (admin scope).
        """

        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = RuntimeApiRoutes.service(request).persistence
        rows = await persistence.list_retention_policies(org_id=org_id)
        resolver = RetentionPolicyResolver(
            org_id=org_id,
            policies=rows,
            deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
        )
        # Map every supported kind so the FE renders a deterministic
        # table; an absent kind means the resolver returned ``None``
        # (no per-tenant policy + no deployment default), which the FE
        # renders as "indefinite".
        org_policy_id_by_kind: dict[RetentionKind, str] = {
            row.kind: row.id for row in rows if row.scope.value == "org"
        }
        entries: dict[RetentionKind, RetentionEffectivePolicyEntry] = {}
        for kind in RetentionKind:
            resolved = resolver.resolve(kind=kind)
            entries[kind] = RetentionEffectivePolicyEntry(
                kind=kind,
                ttl_seconds=resolved.ttl_seconds,
                source_scope=resolved.source_scope,
                source_policy_id=(
                    org_policy_id_by_kind.get(kind)
                    if resolved.source_scope is not None
                    and resolved.source_scope.value == "org"
                    else None
                ),
            )
        return RetentionEffectiveResponse(effective=entries)

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
    """Build the ``/v1/retention/*`` router.

    The CRUD policies routes (``/v1/retention/policies*``) require
    ``admin:retention``. The PR 4.3 ``/v1/retention/effective`` read
    is exposed to any tenant member — the Privacy & data panel needs
    to render the org's effective TTLs without requiring admin scope.
    The two routers are sister-mounted so both share the
    ``runtime:use`` gate but only the CRUD path requires admin.
    """

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


class RetentionMemberRouter:
    """Build the ``/v1/retention/effective`` member-readable router (PR 4.3).

    Sibling to :class:`RetentionAdminRouter`; carries only the
    ``runtime:use`` gate so the Privacy & data panel can render the
    effective TTL summary for any signed-in tenant member.
    """

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(
            prefix="/v1/retention",
            tags=["retention"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "/effective",
            RetentionAdminRoutes.effective,
            methods=["GET"],
            response_model=RetentionEffectiveResponse,
            name=Keys.RouteName.RETENTION_EFFECTIVE,
        )
        return router
