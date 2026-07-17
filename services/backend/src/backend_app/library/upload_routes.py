"""FastAPI routes for Library blob upload / finalize / download.

These routes are the only path through which the Library destination
hands out object-store URLs. Bytes never cross the API boundary:

* ``POST /v1/library/files/upload-grant`` mints a signed PUT URL the
  caller redeems directly against the storage adapter. The response
  carries ``{ upload_url, blob_ref, max_size_bytes, expires_at }``
  per the sub-PRD.
* ``POST /v1/library/files/{id}/finalize`` is the caller's confirm
  ack. Backend HEADs the bytes through the blob-store port (no
  proxy), pulls the size + sha256 the adapter recorded, and updates
  the row metadata. The route returns the canonical row view.
* ``GET /v1/library/files/{id}/download`` returns either a JSON
  ``{ url, expires_at }`` (default) or a 302 redirect to the signed
  URL (``?redirect=1``) so an ``<a download>`` link works in the
  browser without a fetch dance.
* The Dataset surface mirrors the same three endpoints under
  ``/v1/library/datasets/...``.

A separate ``GET /_blobs/{blob_ref:path}`` dev byte-pump is also
registered when the wired store is :class:`LocalDiskBlobStore`. The
pump is the local-only target of the signed URLs; production S3
signed URLs target s3.amazonaws.com directly and the pump never
mounts.

This module ships a minimal in-memory row store (``LibraryRowStore``)
so the routes are self-contained for the merge order. P7-A1 lands a
production row store; at merge the port stays the same and the
in-memory adapter becomes a tests-only adapter, identical to how
``InMemorySourceStorage`` co-exists with the S3 adapter for the
adapter registry.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

from copilot_service_contracts.scopes import LIBRARY_READ, LIBRARY_WRITE
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.library.blob_store import (
    BlobKind,
    BlobNotFoundError,
    BlobSizeLimitExceededError,
    BlobStoreError,
    BlobStorePort,
    BlobTokenInvalidError,
    DATASET_MIME_SIZE_LIMITS,
    LocalDiskBlobStore,
    MIME_SIZE_LIMITS,
    SIGNED_URL_MAX_TTL_SECONDS,
    build_blob_ref,
    ensure_tenant,
    size_limit_for,
)


# ---------------------------------------------------------------------------
# Minimal row metadata store (P7-A1 ships the production version)
# ---------------------------------------------------------------------------


_ALLOWED_FILE_MIMES = frozenset(MIME_SIZE_LIMITS.keys())
_ALLOWED_DATASET_MIMES = frozenset(DATASET_MIME_SIZE_LIMITS.keys())


@dataclass
class LibraryRow:
    """Row metadata for a Library file or dataset.

    Intentionally minimal — P7-A1 owns the full schema. The fields
    here are the bare minimum the blob layer needs to enforce tenant
    isolation and finalize semantics.
    """

    item_id: str
    tenant_id: str
    owner_user_id: str
    kind: BlobKind
    blob_ref: str
    content_type: str
    name: str
    size_bytes_max: int
    size_bytes: int | None = None
    sha256: str | None = None
    finalized_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LibraryRowStore(Protocol):
    """Adapter contract for Library item metadata."""

    def insert(self, row: LibraryRow) -> LibraryRow: ...
    def get(self, *, item_id: str) -> LibraryRow | None: ...
    def finalize(
        self,
        *,
        item_id: str,
        size_bytes: int,
        sha256: str | None,
    ) -> LibraryRow: ...
    def delete(self, *, item_id: str) -> bool: ...


class InMemoryLibraryRowStore:
    """Test/dev row store. P7-A1 ships the Postgres adapter."""

    def __init__(self) -> None:
        self._rows: dict[str, LibraryRow] = {}
        self._lock = threading.Lock()

    def insert(self, row: LibraryRow) -> LibraryRow:
        with self._lock:
            if row.item_id in self._rows:
                raise ValueError(f"duplicate item_id: {row.item_id}")
            self._rows[row.item_id] = row
        return row

    def get(self, *, item_id: str) -> LibraryRow | None:
        with self._lock:
            return self._rows.get(item_id)

    def finalize(
        self,
        *,
        item_id: str,
        size_bytes: int,
        sha256: str | None,
    ) -> LibraryRow:
        with self._lock:
            existing = self._rows.get(item_id)
            if existing is None:
                raise KeyError(item_id)
            existing.size_bytes = size_bytes
            existing.sha256 = sha256
            existing.finalized_at = datetime.now(timezone.utc)
            return existing

    def delete(self, *, item_id: str) -> bool:
        with self._lock:
            return self._rows.pop(item_id, None) is not None


# ---------------------------------------------------------------------------
# Wire DTOs (Pydantic)
# ---------------------------------------------------------------------------


class UploadGrantRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., min_length=3, max_length=128)
    size_bytes_max: int = Field(..., gt=0)
    ttl_seconds: int = Field(
        default=SIGNED_URL_MAX_TTL_SECONDS,
        gt=0,
        le=SIGNED_URL_MAX_TTL_SECONDS,
    )


class UploadGrantResponse(BaseModel):
    item_id: str
    blob_ref: str
    upload_url: str
    method: Literal["PUT", "POST"] = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    max_size_bytes: int
    expires_at: int


class FinalizeRequest(BaseModel):
    size_bytes: int = Field(..., gt=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class LibraryItemView(BaseModel):
    item_id: str
    tenant_id: str
    owner_user_id: str
    kind: Literal["file", "dataset"]
    name: str
    content_type: str
    blob_ref: str
    size_bytes: int | None
    sha256: str | None
    finalized: bool
    created_at: datetime
    finalized_at: datetime | None


class DownloadUrlResponse(BaseModel):
    url: str
    expires_at: int


def _row_to_view(row: LibraryRow) -> LibraryItemView:
    return LibraryItemView(
        item_id=row.item_id,
        tenant_id=row.tenant_id,
        owner_user_id=row.owner_user_id,
        kind=row.kind,
        name=row.name,
        content_type=row.content_type,
        blob_ref=row.blob_ref,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        finalized=row.finalized_at is not None,
        created_at=row.created_at,
        finalized_at=row.finalized_at,
    )


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_library_blob_routes(
    app: FastAPI,
    *,
    blob_store: BlobStorePort,
    row_store: LibraryRowStore,
) -> None:
    """Attach Library blob routes to ``app``.

    Also mounts the dev byte-pump when ``blob_store`` is a
    :class:`LocalDiskBlobStore`. Production deploys inject an
    :class:`S3BlobStore` and the pump never mounts; the signed URLs
    target S3 directly.
    """

    app.state.library_blob_store = blob_store
    app.state.library_row_store = row_store

    _register_grant_finalize_routes(
        app,
        kind="file",
        blob_store=blob_store,
        row_store=row_store,
    )
    _register_grant_finalize_routes(
        app,
        kind="dataset",
        blob_store=blob_store,
        row_store=row_store,
    )

    if isinstance(blob_store, LocalDiskBlobStore):
        _register_dev_byte_pump(app, blob_store)


def _register_grant_finalize_routes(
    app: FastAPI,
    *,
    kind: BlobKind,
    blob_store: BlobStorePort,
    row_store: LibraryRowStore,
) -> None:
    """Mount the (grant, finalize, download, delete) quartet for ``kind``."""

    segment = "files" if kind == "file" else "datasets"
    allowed_mimes = _ALLOWED_FILE_MIMES if kind == "file" else _ALLOWED_DATASET_MIMES

    @app.post(
        f"/v1/library/{segment}/upload-grant",
        response_model=UploadGrantResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(LIBRARY_WRITE))],
        name=f"library_{kind}_upload_grant",
    )
    def upload_grant(
        request: Request,
        payload: UploadGrantRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> UploadGrantResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if payload.content_type not in allowed_mimes:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"content_type {payload.content_type!r} not allowed for {kind}",
            )
        try:
            cap = size_limit_for(kind, payload.content_type)
            if payload.size_bytes_max > cap:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"size_bytes_max={payload.size_bytes_max} exceeds per-mime cap={cap}",
                )
            item_id = _new_item_id(kind)
            blob_ref = build_blob_ref(
                kind=kind, tenant_id=identity.org_id, blob_id=item_id
            )
            row_store.insert(
                LibraryRow(
                    item_id=item_id,
                    tenant_id=identity.org_id,
                    owner_user_id=identity.user_id,
                    kind=kind,
                    blob_ref=blob_ref,
                    content_type=payload.content_type,
                    name=payload.name,
                    size_bytes_max=payload.size_bytes_max,
                )
            )
            grant = blob_store.presign_upload(
                blob_ref=blob_ref,
                tenant_id=identity.org_id,
                content_type=payload.content_type,
                size_bytes_max=payload.size_bytes_max,
                ttl_seconds=payload.ttl_seconds,
            )
        except BlobSizeLimitExceededError as exc:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
        except BlobStoreError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return UploadGrantResponse(
            item_id=item_id,
            blob_ref=grant.blob_ref,
            upload_url=grant.upload_url,
            method="PUT",
            headers=grant.headers,
            max_size_bytes=grant.max_size_bytes,
            expires_at=grant.expires_at,
        )

    @app.post(
        f"/v1/library/{segment}/{{item_id}}/finalize",
        response_model=LibraryItemView,
        dependencies=[Depends(RequireScopes(LIBRARY_WRITE))],
        name=f"library_{kind}_finalize",
    )
    def finalize(
        request: Request,
        payload: FinalizeRequest,
        item_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> LibraryItemView:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        row = _load_row_or_404(row_store, item_id=item_id, kind=kind)
        _assert_tenant_owns(row, identity_org_id=identity.org_id)
        if payload.size_bytes > row.size_bytes_max:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"size_bytes={payload.size_bytes} exceeds grant size_bytes_max="
                f"{row.size_bytes_max}",
            )
        # HEAD via the blob store to confirm the bytes really arrived;
        # never proxy bytes through the API.
        try:
            meta = blob_store.head(blob_ref=row.blob_ref, tenant_id=identity.org_id)
        except BlobTokenInvalidError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        if not meta.exists:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "blob not present at storage layer; complete upload before finalize",
            )
        # Server-recorded size wins if the storage layer reported it.
        recorded_size = (
            meta.size_bytes if meta.size_bytes is not None else payload.size_bytes
        )
        recorded_sha = meta.sha256 or payload.sha256
        updated = row_store.finalize(
            item_id=item_id, size_bytes=recorded_size, sha256=recorded_sha
        )
        return _row_to_view(updated)

    @app.get(
        f"/v1/library/{segment}/{{item_id}}/download",
        dependencies=[Depends(RequireScopes(LIBRARY_READ))],
        name=f"library_{kind}_download",
    )
    def download(
        request: Request,
        item_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        redirect: int = Query(0, ge=0, le=1),
        ttl_seconds: int = Query(
            SIGNED_URL_MAX_TTL_SECONDS, gt=0, le=SIGNED_URL_MAX_TTL_SECONDS
        ),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        row = _load_row_or_404(row_store, item_id=item_id, kind=kind)
        _assert_tenant_owns(row, identity_org_id=identity.org_id)
        if row.finalized_at is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "library item is not finalized; bytes are not downloadable yet",
            )
        try:
            signed = blob_store.presign_download(
                blob_ref=row.blob_ref,
                tenant_id=identity.org_id,
                ttl_seconds=ttl_seconds,
            )
        except BlobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except BlobTokenInvalidError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        if redirect:
            return RedirectResponse(signed.url, status_code=status.HTTP_302_FOUND)
        body = DownloadUrlResponse(url=signed.url, expires_at=signed.expires_at)
        return Response(
            content=body.model_dump_json(),
            media_type="application/json",
        )

    @app.delete(
        f"/v1/library/{segment}/{{item_id}}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(LIBRARY_WRITE))],
        name=f"library_{kind}_delete",
    )
    def delete_item(
        request: Request,
        item_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        row = _load_row_or_404(row_store, item_id=item_id, kind=kind)
        _assert_tenant_owns(row, identity_org_id=identity.org_id)
        # Row delete first so a partial blob-delete failure doesn't leave a
        # dangling row that points to soon-to-be-gone bytes. The blob
        # delete is idempotent (LocalDisk + S3 both swallow 404), so it's
        # safe to retry.
        row_store.delete(item_id=item_id)
        try:
            blob_store.delete(blob_ref=row.blob_ref, tenant_id=identity.org_id)
        except BlobTokenInvalidError:
            # Tenant mismatch can't happen here because we re-asserted
            # above, but treat as best-effort cleanup either way.
            pass
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_row_or_404(
    row_store: LibraryRowStore, *, item_id: str, kind: BlobKind
) -> LibraryRow:
    row = row_store.get(item_id=item_id)
    if row is None or row.kind != kind:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "library item not found")
    return row


def _assert_tenant_owns(row: LibraryRow, *, identity_org_id: str) -> None:
    """Reject cross-tenant reads without leaking existence.

    The blob_ref carries the tenant inline; the row also stores
    ``tenant_id``. We check both: a future row-store bug that
    accidentally mixes tenants would still be caught by the
    ``ensure_tenant`` guard inside the blob store before any URL is
    minted, but the dual check keeps the error message precise.
    """

    if row.tenant_id != identity_org_id:
        # Same status code as "not found" so an attacker can't probe for
        # other tenants' item_ids.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "library item not found")
    try:
        ensure_tenant(row.blob_ref, tenant_id=identity_org_id)
    except BlobTokenInvalidError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "library item not found"
        ) from None


def _new_item_id(kind: BlobKind) -> str:
    prefix = "lf" if kind == "file" else "ld"
    return f"{prefix}_{secrets.token_urlsafe(16)}"


# ---------------------------------------------------------------------------
# Dev byte-pump (LocalDiskBlobStore only)
# ---------------------------------------------------------------------------


def _register_dev_byte_pump(app: FastAPI, store: LocalDiskBlobStore) -> None:
    """Local-only PUT/GET endpoint that signed URLs target.

    Production never mounts this because the wired store is S3 and the
    signed URLs go straight to s3.amazonaws.com. In dev the URLs are
    localhost-relative, validated via HMAC token. The endpoint
    delegates to ``store.write_bytes`` / ``store.read_bytes``, both of
    which call ``hmac.compare_digest`` under the hood.
    """

    @app.put(
        "/_blobs/{blob_ref:path}",
        include_in_schema=False,
        dependencies=[Depends(public_route())],
    )
    async def dev_blob_put(
        request: Request,
        blob_ref: str = Path(...),
        op: str = Query(...),
        exp: int = Query(...),
        sig: str = Query(...),
    ) -> Response:
        if op != "put":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong op for PUT")
        body = await request.body()
        try:
            store.write_bytes(blob_ref=blob_ref, payload=body, token=sig, exp=exp)
        except BlobTokenInvalidError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/_blobs/{blob_ref:path}",
        include_in_schema=False,
        dependencies=[Depends(public_route())],
    )
    def dev_blob_get(
        blob_ref: str = Path(...),
        op: str = Query(...),
        exp: int = Query(...),
        sig: str = Query(...),
    ) -> Response:
        if op != "get":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong op for GET")
        try:
            payload = store.read_bytes(blob_ref=blob_ref, token=sig, exp=exp)
        except BlobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except BlobTokenInvalidError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        return Response(content=payload, media_type="application/octet-stream")


__all__ = [
    "FinalizeRequest",
    "InMemoryLibraryRowStore",
    "LibraryItemView",
    "LibraryRow",
    "LibraryRowStore",
    "UploadGrantRequest",
    "UploadGrantResponse",
    "register_library_blob_routes",
]
