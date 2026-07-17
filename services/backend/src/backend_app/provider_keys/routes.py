"""Public ``/v1/settings/provider-keys`` routes (Phase 2 BYOK).

Frozen wire contract (facade re-exposes these verbatim):

  GET    /v1/settings/provider-keys
      -> 200 {"keys": [{"provider", "key_hint", "updated_at"}]}
  PUT    /v1/settings/provider-keys/{provider}   body {"api_key": "..."}
      -> 200 {"provider", "key_hint", "updated_at"}
      -> 422 on unknown provider (path enum), 400 on format mismatch
  DELETE /v1/settings/provider-keys/{provider}
      -> 204

``key_hint`` is the ONLY key material on this surface — plaintext never
appears in any response, log line, or audit row. Identity follows the
sibling ``/v1/settings/*`` routes: RBAC via ``RequireScopes(RUNTIME_USE)``
plus the trusted facade-headers envelope (query identity in dev).
"""

from __future__ import annotations

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator, ScopedIdentity
from backend_app.identity.rbac import RequireScopes
from backend_app.provider_keys.service import (
    ProviderKeyFormatError,
    ProviderKeysService,
)
from backend_app.provider_keys.store import ProviderApiKeyRecord, ProviderName


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class ProviderKeyResponse(BaseModel):
    """One stored key, hint-only. NEVER carries plaintext."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    key_hint: str
    updated_at: str


class ProviderKeyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[ProviderKeyResponse]


class SetProviderKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_provider_keys_routes(
    app: FastAPI,
    *,
    service: ProviderKeysService,
) -> None:
    """Attach the three ``/v1/settings/provider-keys`` routes to ``app``."""

    @app.get(
        "/v1/settings/provider-keys",
        response_model=ProviderKeyListResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_provider_keys(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProviderKeyListResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        records = service.list_keys(org_id=identity.org_id, user_id=identity.user_id)
        return ProviderKeyListResponse(
            keys=[_to_response(record) for record in records]
        )

    @app.put(
        "/v1/settings/provider-keys/{provider}",
        response_model=ProviderKeyResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_provider_key(
        request: Request,
        provider: ProviderName,
        body: SetProviderKeyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProviderKeyResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        try:
            saved = service.set_key(
                org_id=identity.org_id,
                user_id=identity.user_id,
                provider=provider,
                api_key=body.api_key,
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except ProviderKeyFormatError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return _to_response(saved)

    @app.delete(
        "/v1/settings/provider-keys/{provider}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_provider_key(
        request: Request,
        provider: ProviderName,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        service.delete_key(
            org_id=identity.org_id,
            user_id=identity.user_id,
            provider=provider,
            request_ip=_request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(request: Request, *, org_id: str, user_id: str) -> ScopedIdentity:
    return BackendServiceAuthenticator.scoped_identity(
        request, org_id=org_id, user_id=user_id
    )


def _to_response(record: ProviderApiKeyRecord) -> ProviderKeyResponse:
    return ProviderKeyResponse(
        provider=record.provider.value,
        key_hint=record.key_hint,
        updated_at=record.updated_at.isoformat(),
    )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "ProviderKeyListResponse",
    "ProviderKeyResponse",
    "SetProviderKeyRequest",
    "register_provider_keys_routes",
]
