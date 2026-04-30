"""Trusted service identity for runtime API requests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os

from enterprise_service_contracts.headers import (
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
    org_id: str
    user_id: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: dict[str, tuple[str, ...]] | None = None


class RuntimeServiceAuthenticator:
    """Class-scoped trusted service identity parsing for runtime API requests."""

    @classmethod
    def trusted_identity_from_request(
        cls, request: Request
    ) -> TrustedRequestIdentity | None:
        """Return upstream-authenticated identity when service headers are present."""

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
        elif not supplied:
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

    @staticmethod
    def _service_token() -> str:
        return os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()

    @staticmethod
    def _environment() -> str:
        return os.environ.get("RUNTIME_ENVIRONMENT", "development").lower()

    @staticmethod
    def _required_header(request: Request, header: str) -> str:
        value = request.headers.get(header, "").strip()
        if not value:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Missing {header}")
        return value

    @staticmethod
    def _csv_header(request: Request, header: str) -> tuple[str, ...]:
        value = request.headers.get(header, "")
        return tuple(part.strip() for part in value.split(",") if part.strip())

    @staticmethod
    def _connector_scopes(value: str) -> dict[str, tuple[str, ...]]:
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
