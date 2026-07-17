"""``/internal/v1/me/avatar`` (PR 8.3) — caller-scoped avatar pipeline.

POST accepts a multipart ``file`` (PNG / JPEG / WEBP, ≤ 200 KB after FE
resize). The bytes go to ``user_avatars`` and the URL on the user's
``user_profiles.avatar_url`` is updated to ``/v1/me/avatar/{user_id}?v=
<updated_at_epoch>`` so every browser cache-busts on each upload.

GET serves the bytes back with a private cache header + ETag — admin
members directories and the FE both fetch it the same way. The
``user_id`` is in the path so the caller can render *other* members'
avatars (subject to RLS — the ``app.current_org_id`` setting on the
session decides visibility).

DELETE removes the row and nulls ``avatar_url``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.avatar_store import AvatarStore
from backend_app.identity.me_store import MeStore, UserProfileRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)

# Tighter than the column CHECK; lets us reject with a 4xx rather than a
# DB error for the most common over-cap path. The FE resizes to ~60 KB
# typically; 200 KB is enough headroom for higher-quality WEBP.
_MAX_BYTES = 200_000
_ALLOWED_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def register_me_avatar_routes(
    app: FastAPI,
    *,
    avatar_store: AvatarStore,
    me_store: MeStore,
    identity_store: IdentityStore,
) -> None:
    """Mount the avatar routes."""

    @app.post(
        "/internal/v1/me/avatar",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def upload_my_avatar(
        request: Request,
        file: UploadFile = File(...),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        content_type = (file.content_type or "").lower()
        if content_type not in _ALLOWED_TYPES:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "avatar_unsupported_type",
            )
        content = await file.read()
        if not content:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "avatar_empty")
        if len(content) > _MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "avatar_too_large",
            )

        record = avatar_store.upsert(
            org_id=identity.org_id,
            user_id=identity.user_id,
            content_type=content_type,
            content=content,
        )
        # Update the profile pointer atomically with the audit record.
        url = _avatar_url(identity.user_id, record.updated_at)
        with me_store.transaction() as conn:
            existing = me_store.get_profile(
                org_id=identity.org_id, user_id=identity.user_id
            )
            sidecar = UserProfileRecord(
                user_id=identity.user_id,
                org_id=identity.org_id,
                title=existing.title if existing else None,
                timezone=existing.timezone if existing else None,
                locale=existing.locale if existing else None,
                working_hours=existing.working_hours if existing else None,
                avatar_url=url,
                bio=existing.bio if existing else None,
            )
            me_store.upsert_profile(sidecar, conn=conn)
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="user.avatar.update",
                    metadata={
                        "size_bytes": record.size_bytes,
                        "content_type": record.content_type,
                        "etag": record.etag,
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return {
            "avatar_url": url,
            "etag": record.etag,
            "size_bytes": record.size_bytes,
        }

    @app.get(
        "/internal/v1/me/avatar/{target_user_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_avatar(
        request: Request,
        target_user_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        # Identity established for the audit / RLS path; the actual
        # access check is the row's org_id matching the caller's org.
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = avatar_store.get(org_id=identity.org_id, user_id=target_user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "avatar_not_found")
        # Return a 304 when the client has the same bytes.
        if request.headers.get("if-none-match") == f'"{record.etag}"':
            return Response(status_code=status.HTTP_304_NOT_MODIFIED)
        return Response(
            content=record.bytes_,
            media_type=record.content_type,
            headers={
                "ETag": f'"{record.etag}"',
                # Private — avatars contain user-controlled content.
                "Cache-Control": "private, max-age=86400, must-revalidate",
            },
        )

    @app.delete(
        "/internal/v1/me/avatar",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_my_avatar(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        removed = avatar_store.delete(org_id=identity.org_id, user_id=identity.user_id)
        # Always null the URL on the profile (avoid orphaned pointers
        # if a previous DELETE failed to clear it).
        with me_store.transaction() as conn:
            existing = me_store.get_profile(
                org_id=identity.org_id, user_id=identity.user_id
            )
            if existing is not None:
                sidecar = UserProfileRecord(
                    user_id=identity.user_id,
                    org_id=identity.org_id,
                    title=existing.title,
                    timezone=existing.timezone,
                    locale=existing.locale,
                    working_hours=existing.working_hours,
                    avatar_url=None,
                    bio=existing.bio,
                )
                me_store.upsert_profile(sidecar, conn=conn)
            if removed:
                identity_store.append_identity_audit(
                    IdentityAuditEventRecord(
                        org_id=identity.org_id,
                        actor_user_id=identity.user_id,
                        subject_user_id=identity.user_id,
                        action="user.avatar.delete",
                        metadata={},
                        request_ip=_request_ip(request),
                        user_agent=request.headers.get("user-agent"),
                    ),
                    conn=conn,
                )


def _avatar_url(user_id: str, updated_at: datetime) -> str:
    # Cache-bust on every change so browsers + CDN edges drop stale.
    return f"/v1/me/avatar/{user_id}?v={int(updated_at.timestamp())}"


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = ["register_me_avatar_routes"]


# Silence unused-import warnings when the typing-only ``Any`` ends up
# pruned by the formatter; we still want it imported for future hooks
# (the route file is the canonical place to add per-content-type sniff
# helpers, which return ``Any``).
_ = Any
