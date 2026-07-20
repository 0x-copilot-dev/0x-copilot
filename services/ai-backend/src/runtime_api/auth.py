"""Trusted service-token identity parsing for incoming runtime API requests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os

from copilot_service_contracts.headers import (
    CONNECTOR_SCOPES_HEADER,
    ORG_HEADER,
    PERMISSION_SCOPES_HEADER,
    ROLES_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class TrustedRequestIdentity:
    """Verified caller identity extracted from trusted service-token headers.

    Populated only after the service token has been validated; untrusted
    header values are never promoted into this type.
    """

    org_id: str
    user_id: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: dict[str, tuple[str, ...]] | None = None


class RuntimeServiceAuthenticator:
    """Validates the service token and extracts per-tenant identity from headers."""

    @classmethod
    def trusted_identity_from_request(
        cls, request: Request
    ) -> TrustedRequestIdentity | None:
        """Lenient: returns identity when present, ``None`` in dev when absent.

        Used by internal routes that gate on the service token only and
        don't carry per-tenant identity (``/internal/v1/audit/cursor``,
        the system-skills lister). Tenant routes must use
        :meth:`require_identity` (or the ``Identity`` Depends) which
        raises 401 on absence — that's the path that closes Bug 1.
        """

        expected = cls._service_token()
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if expected:
            if supplied != expected:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Invalid service token"
                )
        elif cls._environment() == "production":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENTERPRISE_SERVICE_TOKEN is not configured",
            )
        elif not supplied and not request.headers.get(ORG_HEADER, "").strip():
            # No service token + no identity headers + dev → "open" mode
            # for internal routes that don't need identity.
            return None

        org_id = cls._required_header(request, ORG_HEADER)
        user_id = cls._required_header(request, USER_HEADER)
        return TrustedRequestIdentity(
            org_id=org_id,
            user_id=user_id,
            roles=cls._csv_header(request, ROLES_HEADER) or ("employee",),
            permission_scopes=cls._csv_header(request, PERMISSION_SCOPES_HEADER),
            connector_scopes=cls._connector_scopes(
                request.headers.get(CONNECTOR_SCOPES_HEADER, "{}")
            ),
        )

    @classmethod
    def require_service_token(cls, request: Request) -> None:
        """Service-token-only gate for internal routes with no tenant scope.

        Used by the account-merge endpoint (PRD §6.4), whose absorbed /
        survivor coordinates arrive in the body — per-tenant identity
        headers are deliberately not required, so
        :meth:`trusted_identity_from_request` (which demands them once a
        token is configured) is the wrong gate. Mirrors its token logic
        exactly: 401 on mismatch, 503 in production without a configured
        token, open in dev when no token is set.
        """

        expected = cls._service_token()
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if expected:
            if supplied != expected:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Invalid service token"
                )
        elif cls._environment() == "production":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENTERPRISE_SERVICE_TOKEN is not configured",
            )

    @classmethod
    def require_identity(cls, request: Request) -> TrustedRequestIdentity:
        """Strict identity resolver — never returns ``None``.

        W0.1: this is the path used by the ``Identity`` FastAPI dependency
        and every tenant-scoped route. Identity headers are required;
        absence raises 401. Bug 1 from the W0 QA report
        (``org_id and user_id are required`` on /sources, /subagents,
        /drafts) is closed because new routes use this strict path
        instead of inheriting the lenient None-fallback.
        """

        expected = cls._service_token()
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if expected:
            if supplied != expected:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Invalid service token"
                )
        elif cls._environment() == "production":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENTERPRISE_SERVICE_TOKEN is not configured",
            )

        org_id = cls._required_header(request, ORG_HEADER)
        user_id = cls._required_header(request, USER_HEADER)
        return TrustedRequestIdentity(
            org_id=org_id,
            user_id=user_id,
            roles=cls._csv_header(request, ROLES_HEADER) or ("employee",),
            permission_scopes=cls._csv_header(request, PERMISSION_SCOPES_HEADER),
            connector_scopes=cls._connector_scopes(
                request.headers.get(CONNECTOR_SCOPES_HEADER, "{}")
            ),
        )

    @staticmethod
    def _service_token() -> str:
        """Return the expected service token from the environment, empty if unset."""

        return os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()

    @staticmethod
    def _environment() -> str:
        """Return the normalised runtime environment name."""

        return os.environ.get("RUNTIME_ENVIRONMENT", "development").lower()

    @staticmethod
    def _required_header(request: Request, header: str) -> str:
        """Extract a required header value or raise 401."""

        value = request.headers.get(header, "").strip()
        if not value:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Missing {header}")
        return value

    @staticmethod
    def _csv_header(request: Request, header: str) -> tuple[str, ...]:
        """Parse a comma-separated header value into a tuple of stripped strings."""

        value = request.headers.get(header, "")
        return tuple(part.strip() for part in value.split(",") if part.strip())

    @staticmethod
    def _connector_scopes(value: str) -> dict[str, tuple[str, ...]]:
        """Parse the JSON-encoded connector-scopes header into a typed mapping."""
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid connector scope header"
            ) from exc
        if not isinstance(decoded, dict):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid connector scope header"
            )
        normalized: dict[str, tuple[str, ...]] = {}
        for connector, scopes in decoded.items():
            if not isinstance(scopes, list | tuple):
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Invalid connector scope header"
                )
            normalized[str(connector)] = tuple(str(scope) for scope in scopes)
        return normalized
