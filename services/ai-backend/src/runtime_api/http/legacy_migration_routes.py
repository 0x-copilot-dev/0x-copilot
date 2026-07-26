"""Service-token, tenant-bound endpoint for evidence-gated E2 migration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_runtime.api.legacy_migration_service import (
    LegacyMigrationAuditError,
    LegacyMigrationError,
    LegacyMigrationService,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.validation import ValueNormalizer
from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationReport,
    LegacyMigrationStateError,
)
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.http.errors import RuntimeApiError
from runtime_api.rbac import public_route
from runtime_api.schemas.legacy_migration import LegacyMigrationRunRequest


class LegacyMigrationApiRoutes:
    """Run the E2 prerequisite with explicit service-token authorization."""

    @classmethod
    async def run(
        cls,
        request: Request,
        migration_id: str,
        payload: LegacyMigrationRunRequest,
    ) -> LegacyMigrationReport:
        # This is a service-to-service control-plane route, but it is still
        # tenant scoped.  Bind the trusted header identity to the body rather
        # than letting a bearer for tenant A inventory or mutate tenant B.
        # ``require_identity`` also validates the service token.
        identity = RuntimeServiceAuthenticator.require_identity(request)
        if identity.org_id != payload.org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tenant identity does not match the migration request",
            )
        try:
            migration_id = ValueNormalizer.normalize_id(migration_id, "migration_id")
        except ValueError as exc:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "The migration request is invalid.",
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            ) from exc
        service = getattr(request.app.state, "legacy_migration_service", None)
        if not isinstance(service, LegacyMigrationService):
            raise RuntimeApiError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Legacy migration evidence is not configured.",
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                retryable=False,
            )
        try:
            if payload.dry_run:
                return await service.dry_run(
                    org_id=payload.org_id,
                    migration_id=migration_id,
                )
            return await service.apply(
                org_id=payload.org_id,
                migration_id=migration_id,
                batch_size=payload.batch_size,
            )
        except (LegacyMigrationError, LegacyMigrationStateError) as exc:
            retryable = not isinstance(exc, LegacyMigrationAuditError)
            raise RuntimeApiError(
                RuntimeErrorCode.DEPENDENCY_ERROR,
                getattr(
                    exc, "safe_message", "Legacy migration evidence is unavailable."
                ),
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                retryable=retryable,
            ) from exc


class LegacyMigrationApiRouter:
    """Build the non-facade internal E2 migration control-plane route."""

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/internal/v1/admin/e2", tags=["runtime-internal"])
        router.add_api_route(
            "/legacy-migrations/{migration_id}",
            LegacyMigrationApiRoutes.run,
            methods=["POST"],
            response_model=LegacyMigrationReport,
            name="internal_e2_legacy_migration",
            dependencies=[Depends(public_route())],
        )
        return router


__all__ = ("LegacyMigrationApiRouter", "LegacyMigrationApiRoutes")
