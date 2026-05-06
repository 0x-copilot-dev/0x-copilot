"""``/internal/v1/me/api-keys`` (PR B3 / 8.0.3g).

Routes:

* ``GET /internal/v1/me/api-keys`` — list active keys (no plaintext).
* ``POST /internal/v1/me/api-keys`` — mint a new key. Returns the
  plaintext bearer ONCE; the server only stores the HMAC hash.
* ``DELETE /internal/v1/me/api-keys/{api_key_id}`` — revoke.
* ``POST /internal/v1/me/api-keys/{api_key_id}/rotate`` — atomically
  mint a new key linked to the rotated row, then revoke the old one.
* ``POST /internal/v1/auth/api-keys/verify`` — service-token-protected
  bearer verifier consumed by the facade's auth path. Parses the
  ``atlas_pk_*`` bearer, constant-time-verifies the secret against
  the stored hash, stamps ``last_used_at`` + ``last_used_ip``, and
  returns the row's identity claims so the facade can mint upstream
  service-token headers under the key's identity.

The bearer-auth path (separate consumer of the same store) lives in
``backend_app/api_keys/auth.py``. Caller-supplied scopes for a new key
must be a subset of the caller's own scopes — keys can narrow but
never widen privileges.
"""

from __future__ import annotations

from datetime import datetime, timezone

from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.api_keys.auth import (
    ApiKeyHasher,
    InvalidApiKey,
    parse_bearer,
    render_bearer,
)
from backend_app.api_keys.store import ApiKeyRow, ApiKeyStore
from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class ApiKeySummary(BaseModel):
    """Public, non-sensitive view of a stored row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    key_prefix: str
    scopes: tuple[str, ...]
    last_used_at: str | None
    created_at: str
    rotated_from_id: str | None
    # PR 8.3 — drives the FE tab strip. Default keeps existing rows on
    # the personal track; the field is required on the wire so old
    # clients that ignore unknown fields still get the truth.
    kind: str = "personal"


class ApiKeyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: tuple[ApiKeySummary, ...]


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=128)
    scopes: tuple[str, ...] = ()


class CreateApiKeyResponse(BaseModel):
    """Plaintext returned ONCE on mint; subsequent reads via the
    listing endpoint never see this field."""

    model_config = ConfigDict(extra="forbid")

    key: ApiKeySummary
    plaintext: str


class VerifyApiKeyRequest(BaseModel):
    """Body for the facade-only verifier route."""

    model_config = ConfigDict(extra="forbid")

    bearer: str = Field(min_length=1, max_length=512)


class VerifyApiKeyResponse(BaseModel):
    """Identity claims minted from the verified row.

    Distinct from ``ScopedIdentity`` — that object carries upstream
    transport headers; this one is the wire shape the facade reads.
    The facade then materialises it into ``AuthenticatedIdentity``
    and forwards through the standard service-token path.
    """

    model_config = ConfigDict(extra="forbid")

    org_id: str
    user_id: str
    api_key_id: str
    label: str
    key_prefix: str
    scopes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_api_key_routes(
    app: FastAPI,
    *,
    api_key_store: ApiKeyStore,
    api_key_hasher: ApiKeyHasher,
    identity_store: IdentityStore,
) -> None:
    """Attach API-key CRUD routes to the app."""

    @app.get(
        "/internal/v1/me/api-keys",
        response_model=ApiKeyListResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_api_keys(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ApiKeyListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        rows = api_key_store.list_for_user(
            org_id=identity.org_id, user_id=identity.user_id
        )
        return ApiKeyListResponse(keys=tuple(_to_summary(row) for row in rows))

    @app.post(
        "/internal/v1/me/api-keys",
        response_model=CreateApiKeyResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_api_key(
        request: Request,
        payload: CreateApiKeyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> CreateApiKeyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Caller scopes are read off request.state when the auth
        # middleware populates it; in dev mode the set is empty so a
        # caller-requested narrower scope set is still accepted.
        caller_scopes = (
            request.state.scopes
            if hasattr(request.state, "scopes") and request.state.scopes
            else set()
        )
        requested = set(payload.scopes)
        if caller_scopes and not requested.issubset(caller_scopes):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "scope_widens_caller")
        prefix, plaintext = api_key_hasher.mint()
        row = ApiKeyRow(
            org_id=identity.org_id,
            user_id=identity.user_id,
            label=payload.label.strip(),
            key_prefix=prefix,
            secret_hash=api_key_hasher.hash(plaintext),
            scopes=tuple(payload.scopes),
        )
        with api_key_store.transaction() as conn:
            saved = api_key_store.insert(row, conn=conn)
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="api_key.create",
                    metadata={
                        "api_key_id": saved.id,
                        "key_prefix": saved.key_prefix,
                        "label": saved.label,
                        "scopes": list(saved.scopes),
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return CreateApiKeyResponse(
            key=_to_summary(saved),
            plaintext=render_bearer(prefix, plaintext),
        )

    @app.delete(
        "/internal/v1/me/api-keys/{api_key_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def revoke_api_key(
        request: Request,
        api_key_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        with api_key_store.transaction() as conn:
            ok = api_key_store.revoke(
                org_id=identity.org_id,
                user_id=identity.user_id,
                api_key_id=api_key_id,
                conn=conn,
            )
            if not ok:
                # Same opacity as a "doesn't exist" response — never
                # reveal whether the id belongs to a different user.
                raise HTTPException(status.HTTP_404_NOT_FOUND, "api_key_not_found")
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="api_key.revoke",
                    metadata={"api_key_id": api_key_id},
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )

    @app.post(
        "/internal/v1/me/api-keys/{api_key_id}/rotate",
        response_model=CreateApiKeyResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def rotate_api_key(
        request: Request,
        api_key_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> CreateApiKeyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Locate the existing row scoped to the caller. Treat absence
        # as 404 to keep the cross-user-existence channel closed.
        existing_rows = {
            row.id: row
            for row in api_key_store.list_for_user(
                org_id=identity.org_id,
                user_id=identity.user_id,
                include_revoked=True,
            )
        }
        old = existing_rows.get(api_key_id)
        if old is None or old.revoked_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "api_key_not_found")
        prefix, plaintext = api_key_hasher.mint()
        new = ApiKeyRow(
            org_id=identity.org_id,
            user_id=identity.user_id,
            label=old.label,
            key_prefix=prefix,
            secret_hash=api_key_hasher.hash(plaintext),
            scopes=old.scopes,
            rotated_from_id=old.id,
        )
        with api_key_store.transaction() as conn:
            saved = api_key_store.insert(new, conn=conn)
            api_key_store.revoke(
                org_id=identity.org_id,
                user_id=identity.user_id,
                api_key_id=old.id,
                conn=conn,
            )
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="api_key.rotate",
                    metadata={
                        "old_api_key_id": old.id,
                        "new_api_key_id": saved.id,
                        "key_prefix": saved.key_prefix,
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return CreateApiKeyResponse(
            key=_to_summary(saved),
            plaintext=render_bearer(prefix, plaintext),
        )

    # ---------------------------------------------------------------
    # PR 8.3 — workspace-issued admin tokens. Same store, ``kind`` flag
    # set on insert; the bearer-verify path is unchanged because the
    # row's identity is its mint user (the admin). Admin scope is
    # required at this surface; the personal routes above are caller-
    # scoped to the user's own row.
    # ---------------------------------------------------------------

    @app.get(
        "/internal/v1/workspace/api-keys",
        response_model=ApiKeyListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS))],
    )
    def list_workspace_api_keys(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ApiKeyListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        rows = api_key_store.list_for_workspace(org_id=identity.org_id)
        return ApiKeyListResponse(keys=tuple(_to_summary(row) for row in rows))

    @app.post(
        "/internal/v1/workspace/api-keys",
        response_model=CreateApiKeyResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(ADMIN_USERS))],
    )
    def create_workspace_api_key(
        request: Request,
        payload: CreateApiKeyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> CreateApiKeyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # The workspace key is owned by the calling admin (audit
        # attribution). Scope-narrowing rule mirrors the personal flow:
        # requested scopes ⊆ caller's scopes.
        caller_scopes = (
            request.state.scopes
            if hasattr(request.state, "scopes") and request.state.scopes
            else set()
        )
        requested = set(payload.scopes)
        if caller_scopes and not requested.issubset(caller_scopes):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "scope_widens_caller")
        prefix, plaintext = api_key_hasher.mint()
        row = ApiKeyRow(
            org_id=identity.org_id,
            user_id=identity.user_id,
            label=payload.label.strip(),
            key_prefix=prefix,
            secret_hash=api_key_hasher.hash(plaintext),
            scopes=tuple(payload.scopes),
            kind="workspace",
        )
        with api_key_store.transaction() as conn:
            saved = api_key_store.insert(row, conn=conn)
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="api_key.workspace.create",
                    metadata={
                        "api_key_id": saved.id,
                        "key_prefix": saved.key_prefix,
                        "label": saved.label,
                        "scopes": list(saved.scopes),
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return CreateApiKeyResponse(
            key=_to_summary(saved),
            plaintext=render_bearer(prefix, plaintext),
        )

    @app.delete(
        "/internal/v1/workspace/api-keys/{api_key_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(ADMIN_USERS))],
    )
    def revoke_workspace_api_key(
        request: Request,
        api_key_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # We allow any admin to revoke any workspace-kind key; that's
        # the point of admin scope. The store's revoke() requires
        # user_id to match — we look up the row first to recover the
        # original mint user, then delegate.
        rows = api_key_store.list_for_workspace(
            org_id=identity.org_id, include_revoked=True
        )
        target = next((r for r in rows if r.id == api_key_id), None)
        if target is None or target.revoked_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "api_key_not_found")
        with api_key_store.transaction() as conn:
            ok = api_key_store.revoke(
                org_id=identity.org_id,
                user_id=target.user_id,
                api_key_id=api_key_id,
                conn=conn,
            )
            if not ok:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "api_key_not_found")
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=target.user_id,
                    action="api_key.workspace.revoke",
                    metadata={"api_key_id": api_key_id},
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )

    @app.post(
        "/internal/v1/workspace/api-keys/{api_key_id}/rotate",
        response_model=CreateApiKeyResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(ADMIN_USERS))],
    )
    def rotate_workspace_api_key(
        request: Request,
        api_key_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> CreateApiKeyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        rows = api_key_store.list_for_workspace(
            org_id=identity.org_id, include_revoked=True
        )
        old = next((r for r in rows if r.id == api_key_id), None)
        if old is None or old.revoked_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "api_key_not_found")
        prefix, plaintext = api_key_hasher.mint()
        new = ApiKeyRow(
            org_id=identity.org_id,
            # Carry forward the original mint-user attribution.
            user_id=old.user_id,
            label=old.label,
            key_prefix=prefix,
            secret_hash=api_key_hasher.hash(plaintext),
            scopes=old.scopes,
            rotated_from_id=old.id,
            kind="workspace",
        )
        with api_key_store.transaction() as conn:
            saved = api_key_store.insert(new, conn=conn)
            api_key_store.revoke(
                org_id=identity.org_id,
                user_id=old.user_id,
                api_key_id=old.id,
                conn=conn,
            )
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=old.user_id,
                    action="api_key.workspace.rotate",
                    metadata={
                        "old_api_key_id": old.id,
                        "new_api_key_id": saved.id,
                        "key_prefix": saved.key_prefix,
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return CreateApiKeyResponse(
            key=_to_summary(saved),
            plaintext=render_bearer(prefix, plaintext),
        )

    @app.post(
        "/internal/v1/auth/api-keys/verify",
        response_model=VerifyApiKeyResponse,
    )
    def verify_api_key(
        request: Request,
        payload: VerifyApiKeyRequest,
    ) -> VerifyApiKeyResponse:
        """Service-token-protected bearer verifier.

        Called by the facade when it sees ``Authorization: Bearer
        atlas_pk_*``. Returns the row's identity (org_id, user_id,
        scopes) on success; raises 401 on every failure mode (parse
        error, unknown prefix, hash mismatch, revoked row) so the
        edge can't bisect existence via timing.
        """

        # The route is internal-only — protect with the standard
        # service-token guard so a malicious browser request can't
        # exercise the bearer-verify oracle directly.
        BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=request.headers.get("x-enterprise-org-id", "system"),
            user_id=request.headers.get("x-enterprise-user-id", "system"),
        )
        try:
            parsed = parse_bearer(payload.bearer)
        except InvalidApiKey as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid_api_key"
            ) from exc
        row = api_key_store.find_active_by_prefix(key_prefix=parsed.prefix)
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_api_key")
        if not api_key_hasher.verify(parsed.secret, row.secret_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_api_key")
        # Stamp last-used best-effort. A failure here is non-fatal —
        # the bearer is already authenticated.
        try:
            api_key_store.stamp_last_used(
                api_key_id=row.id,
                when=utcnow(),
                ip=_request_ip(request),
            )
        except Exception:  # pragma: no cover — best-effort
            pass
        return VerifyApiKeyResponse(
            org_id=row.org_id,
            user_id=row.user_id,
            api_key_id=row.id,
            label=row.label,
            key_prefix=row.key_prefix,
            scopes=row.scopes,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_summary(row: ApiKeyRow) -> ApiKeySummary:
    return ApiKeySummary(
        id=row.id,
        label=row.label,
        key_prefix=row.key_prefix,
        scopes=row.scopes,
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        created_at=row.created_at.isoformat(),
        rotated_from_id=row.rotated_from_id,
        kind=row.kind,
    )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


# Re-export for callers that want to time-stamp via the same datetime
# function the routes use (e.g. the bearer-auth verifier path).
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ApiKeyListResponse",
    "ApiKeySummary",
    "CreateApiKeyRequest",
    "CreateApiKeyResponse",
    "register_api_key_routes",
    "utcnow",
]
