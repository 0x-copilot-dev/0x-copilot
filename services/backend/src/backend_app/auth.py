"""Service-to-service authentication for backend internal APIs."""

from __future__ import annotations

from dataclasses import dataclass
import os

from fastapi import HTTPException, Request, status

SERVICE_TOKEN_HEADER = "x-enterprise-service-token"
ORG_HEADER = "x-enterprise-org-id"
USER_HEADER = "x-enterprise-user-id"


@dataclass(frozen=True)
class ScopedIdentity:
    org_id: str
    user_id: str


class BackendServiceAuthenticator:
    """Class-scoped service authentication for backend routes."""

    @classmethod
    def require_service_request(cls, request: Request) -> None:
        """Require the shared service token when configured or in production."""

        expected = cls._service_token()
        environment = cls._environment()
        if not expected and environment != "production":
            return
        if not expected:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENTERPRISE_SERVICE_TOKEN is not configured",
            )
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if supplied != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid service token")

    @classmethod
    def scoped_identity(cls, request: Request, *, org_id: str, user_id: str) -> ScopedIdentity:
        """Return trusted upstream identity, falling back to query identity only in dev."""

        expected = cls._service_token()
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if expected and supplied == expected:
            return ScopedIdentity(
                org_id=cls._required_header(request, ORG_HEADER),
                user_id=cls._required_header(request, USER_HEADER),
            )
        if cls._environment() == "production":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing service identity")
        return ScopedIdentity(org_id=org_id, user_id=user_id)

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
