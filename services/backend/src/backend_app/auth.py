"""Service-to-service authentication for backend internal APIs."""

from __future__ import annotations

from dataclasses import dataclass
import os

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    PERMISSION_SCOPES_HEADER,
    ROLES_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class ScopedIdentity:
    org_id: str
    user_id: str
    # A10: roles + permission_scopes from the trusted upstream envelope.
    # Empty tuples in the dev-query branch (BackendServiceAuthenticator
    # only forwards what the facade already verified). Routes that gate
    # on RBAC use ``RequireScopes(...)`` / ``RequireRoles(...)`` from
    # backend_app.identity.rbac which consults these fields.
    roles: tuple[str, ...] = ()
    permission_scopes: tuple[str, ...] = ()


class BackendServiceAuthenticator:
    """Class-scoped service authentication for backend routes."""

    @classmethod
    def internal_scoped_identity(
        cls, request: Request, *, org_id: str, user_id: str
    ) -> ScopedIdentity:
        """Return header identity for authenticated service calls, dev query scope otherwise."""

        if cls._verify_service_token(request):
            return ScopedIdentity(
                org_id=cls._required_header(request, ORG_HEADER),
                user_id=cls._required_header(request, USER_HEADER),
                roles=cls._optional_csv_header(request, ROLES_HEADER),
                permission_scopes=cls._optional_csv_header(
                    request, PERMISSION_SCOPES_HEADER
                ),
            )
        return ScopedIdentity(org_id=org_id, user_id=user_id)

    @classmethod
    def scoped_identity(
        cls, request: Request, *, org_id: str, user_id: str
    ) -> ScopedIdentity:
        """Return trusted upstream identity, falling back to query identity only in dev."""

        if cls._verify_service_token(request, allow_missing_in_development=True):
            return ScopedIdentity(
                org_id=cls._required_header(request, ORG_HEADER),
                user_id=cls._required_header(request, USER_HEADER),
                roles=cls._optional_csv_header(request, ROLES_HEADER),
                permission_scopes=cls._optional_csv_header(
                    request, PERMISSION_SCOPES_HEADER
                ),
            )
        return ScopedIdentity(org_id=org_id, user_id=user_id)

    @classmethod
    def _verify_service_token(
        cls,
        request: Request,
        *,
        allow_missing_in_development: bool = True,
    ) -> bool:
        expected = cls._service_token()
        environment = cls._environment()
        if (
            not expected
            and environment != "production"
            and allow_missing_in_development
        ):
            return False
        if not expected:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENTERPRISE_SERVICE_TOKEN is not configured",
            )
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if supplied != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid service token")
        return True

    @staticmethod
    def _service_token() -> str:
        return os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()

    @staticmethod
    def _environment() -> str:
        return os.environ.get("BACKEND_ENVIRONMENT", "development").strip().lower()

    @staticmethod
    def _required_header(request: Request, header: str) -> str:
        value = request.headers.get(header, "").strip()
        if not value:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Missing {header}")
        return value

    @staticmethod
    def _optional_csv_header(request: Request, header: str) -> tuple[str, ...]:
        # Roles + permission scopes ride as comma-separated CSV in the
        # facade -> backend service-headers. Absent header -> empty
        # tuple (no permissions). Whitespace-only entries are dropped.
        raw = request.headers.get(header, "")
        if not raw:
            return ()
        return tuple(item.strip() for item in raw.split(",") if item.strip())
