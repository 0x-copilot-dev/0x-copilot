"""Authentication helpers for the product-facing facade."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hashlib
import hmac
import json
import os
from typing import Any

from enterprise_service_contracts.headers import (
    AUTH_HEADER,
    CONNECTOR_SCOPES_HEADER,
    ORG_HEADER,
    PERMISSION_SCOPES_HEADER,
    ROLES_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Request identity derived from a verified enterprise auth token."""

    org_id: str
    user_id: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def scoped_params(
        self, extra: dict[str, object] | None = None
    ) -> dict[str, object]:
        params: dict[str, object] = {"org_id": self.org_id, "user_id": self.user_id}
        if extra:
            params.update(extra)
        return params

    def scoped_payload(
        self,
        payload: dict[str, object] | None = None,
        *,
        include_request_context: bool = False,
    ) -> dict[str, object]:
        scoped = dict(payload or {})
        scoped["org_id"] = self.org_id
        scoped["user_id"] = self.user_id
        scoped.pop("runtime_context", None)
        if include_request_context:
            scoped["request_context"] = {
                **dict(
                    scoped.get("request_context")
                    if isinstance(scoped.get("request_context"), dict)
                    else {}
                ),
                "roles": self.roles,
                "permission_scopes": self.permission_scopes,
                "connector_scopes": self.connector_scopes,
            }
        return scoped


class FacadeAuthenticator:
    """Class-scoped auth behavior for the product-facing facade."""

    @classmethod
    def authenticate_request(cls, request: Request) -> AuthenticatedIdentity:
        """Validate the client bearer token and return trusted identity claims."""

        header = request.headers.get(AUTH_HEADER, "")
        if not header.lower().startswith("bearer "):
            if cls._is_dev_auth_bypass_enabled():
                return cls._development_identity()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
        token = header.split(" ", maxsplit=1)[1].strip()
        return cls.verify_identity_token(token, cls._auth_secret())

    @classmethod
    def service_headers(cls, identity: AuthenticatedIdentity) -> dict[str, str]:
        """Return service-to-service headers for upstream requests."""

        return {
            SERVICE_TOKEN_HEADER: cls._service_token(),
            ORG_HEADER: identity.org_id,
            USER_HEADER: identity.user_id,
            ROLES_HEADER: ",".join(identity.roles),
            PERMISSION_SCOPES_HEADER: ",".join(identity.permission_scopes),
            CONNECTOR_SCOPES_HEADER: json.dumps(
                identity.connector_scopes, separators=(",", ":")
            ),
        }

    @classmethod
    def verify_identity_token(cls, token: str, secret: str) -> AuthenticatedIdentity:
        """Verify a compact HMAC-signed JSON identity token."""

        try:
            payload_part, signature_part = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Malformed bearer token"
            ) from exc
        expected = cls._sign(payload_part.encode("ascii"), secret)
        if not hmac.compare_digest(signature_part, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
        try:
            payload = json.loads(cls._b64decode(payload_part).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid bearer token payload"
            ) from exc
        return cls._identity_from_payload(payload)

    @classmethod
    def _identity_from_payload(cls, payload: object) -> AuthenticatedIdentity:
        if not isinstance(payload, dict):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid bearer token payload"
            )
        org_id = cls._nonempty_str(payload.get("org_id"), "org_id")
        user_id = cls._nonempty_str(payload.get("user_id"), "user_id")
        roles = cls._string_tuple(payload.get("roles") or ("employee",))
        permission_scopes = cls._string_tuple(payload.get("permission_scopes") or ())
        connector_scopes = cls._connector_scopes(payload.get("connector_scopes") or {})
        return AuthenticatedIdentity(
            org_id=org_id,
            user_id=user_id,
            roles=roles,
            permission_scopes=permission_scopes,
            connector_scopes=connector_scopes,
        )

    @classmethod
    def _auth_secret(cls) -> str:
        return cls._required_secret("ENTERPRISE_AUTH_SECRET")

    @classmethod
    def _service_token(cls) -> str:
        value = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if value:
            return value
        if cls._environment() != "production":
            return ""
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ENTERPRISE_SERVICE_TOKEN is not configured",
        )

    @classmethod
    def _development_identity(cls) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            org_id=os.environ.get("FACADE_DEV_ORG_ID", "org_123").strip() or "org_123",
            user_id=os.environ.get("FACADE_DEV_USER_ID", "user_123").strip()
            or "user_123",
            roles=("employee",),
            permission_scopes=("runtime:use",),
            connector_scopes={},
        )

    @staticmethod
    def _environment() -> str:
        return os.environ.get("FACADE_ENVIRONMENT", "development").strip().lower()

    @classmethod
    def _is_dev_auth_bypass_enabled(cls) -> bool:
        # Two gates:
        #  1. ``FACADE_ENVIRONMENT=development`` — the legacy gate.
        #  2. The deployment profile must allow it. Production profiles
        #     (``single_tenant_managed`` / ``single_tenant_self_hosted``) keep
        #     ``dev_auth_bypass_allowed=False`` even when the env claims to be
        #     development, so a leaked dev env var cannot accidentally relax
        #     auth in a regulated deploy.
        if cls._environment() != "development":
            return False
        if os.environ.get("DEV_AUTH_BYPASS", "").strip().lower() != "true":
            return False
        return cls._deployment_allows_dev_bypass()

    @staticmethod
    def _deployment_allows_dev_bypass() -> bool:
        # Imported lazily so the auth module stays free of import-time side
        # effects; profile loading touches env vars and is exercised via the
        # ``app.state.deployment`` cache in normal request flow.
        from backend_facade.deployment_profile import (
            DeploymentProfileError,
            DeploymentProfileLoader,
        )

        try:
            return DeploymentProfileLoader.load().toggles.dev_auth_bypass_allowed
        except DeploymentProfileError:
            # If the profile itself is misconfigured, fail closed: refuse
            # bypass. The profile loader at app startup is the canonical place
            # to surface the configuration error to the operator.
            return False

    @classmethod
    def _required_secret(cls, name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} is not configured"
            )
        return value

    @classmethod
    def _sign(cls, payload: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
        return cls._b64encode(digest)

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))

    @staticmethod
    def _nonempty_str(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, f"Missing {field_name} claim"
            )
        return value.strip()

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list | tuple | set):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Identity claim must be a list"
            )
        normalized = tuple(str(item).strip() for item in value if str(item).strip())
        return normalized

    @classmethod
    def _connector_scopes(cls, value: object) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, dict):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "connector_scopes must be an object"
            )
        return {
            str(connector): cls._string_tuple(scopes)
            for connector, scopes in value.items()
        }
