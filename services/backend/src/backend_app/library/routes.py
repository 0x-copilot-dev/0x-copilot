"""Public ``/v1/library`` routes — Phase 7 P7-A1 CRUD.

Routes are presentation-only; ACL + audit + invariants live in
:class:`LibraryService`. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating the service exceptions to HTTP status codes:
   * :class:`LibraryNotFound`        → 404 (cross-audit §1.3 404-not-403)
   * :class:`LibraryForbidden`       → 403
   * :class:`LibraryInvalidRequest`  → 400
   * :class:`LibraryConflict`        → 409 (page version_etag mismatch)
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/library.ts``.
4. Parsing repeatable ``filter[<axis>]=<value>`` query params (cross-
   audit §1.5 multi-value OR semantics).

Out of scope of P7-A1 (other agents own these):

* ``POST /v1/library/files`` (signed-URL initiate) + ``…/finalize`` — P7-A2.
* ``POST /v1/library/datasets`` + ``…/finalize`` — P7-A2.
* ``GET /v1/library/{id}/preview`` + ``…/download`` — P7-A2.
* ``POST /v1/library/search`` + ``GET /v1/library/search/stream`` — P7-A3.
"""

from __future__ import annotations

from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.library.service import (
    LibraryConflict,
    LibraryForbidden,
    LibraryInvalidRequest,
    LibraryNotFound,
    LibraryService,
)
from backend_app.library.store import (
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryItemRecord,
    LibraryPageRecord,
)


# ---------------------------------------------------------------------------
# Wire models (Python mirrors of api-types/src/library.ts)
# ---------------------------------------------------------------------------


class CreatePageRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    markdown: str
    project_id: str | None = None
    tags: list[str] | None = None
    source: dict[str, Any] | None = None


class PatchLibraryItemRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    title: str | None = None
    markdown: str | None = None
    tags: list[str] | None = None
    project_id: str | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_library_routes(
    app: FastAPI,
    *,
    service: LibraryService,
) -> None:
    """Attach ``/v1/library`` routes to ``app``.

    P7-A1 ships five routes: list, get, create-page, patch, delete.
    File-upload + dataset-ingest + preview/download + search are not
    registered here; their handlers land in P7-A2 / P7-A3 alongside the
    indexer.
    """

    @app.get(
        "/v1/library",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_library_items(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="updated_at:desc"),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        kinds = _parse_repeatable_filter(request, "kind") or None
        project_ids = _parse_repeatable_filter(request, "project_id") or None
        owner_user_ids = _parse_repeatable_filter(request, "owner_user_id") or None
        source_kinds = _parse_repeatable_filter(request, "source.kind") or None
        tags = _parse_repeatable_filter(request, "tag") or None
        index_statuses = _parse_repeatable_filter(request, "index_status") or None
        file_kinds = _parse_repeatable_filter(request, "file_kind") or None

        rows, next_cursor, counts = service.list_items(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            kinds=kinds,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
            source_kinds=source_kinds,
            tags=tags,
            index_statuses=index_statuses,
            file_kinds=file_kinds,
            q=q,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
        return {
            "items": [_to_wire(r) for r in rows],
            "next_cursor": next_cursor,
            "counts_by_kind": counts,
        }

    @app.get(
        "/v1/library/{item_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_library_item(
        request: Request,
        item_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
            )
        except LibraryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "library_item_not_found"
            ) from exc
        return _to_wire(record)

    @app.post(
        "/v1/library/pages",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_library_page(
        request: Request,
        payload: CreatePageRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.create_page(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                payload=payload.model_dump(exclude_none=True),
            )
        except LibraryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record)

    @app.patch(
        "/v1/library/{item_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_library_item(
        request: Request,
        item_id: str,
        payload: PatchLibraryItemRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            record = service.update_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
                patch=patch_dict,
                expected_etag=if_match,
            )
        except LibraryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "library_item_not_found"
            ) from exc
        except LibraryForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except LibraryConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except LibraryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record)

    @app.delete(
        "/v1/library/{item_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_library_item(
        request: Request,
        item_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.delete_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
            )
        except LibraryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "library_item_not_found"
            ) from exc
        except LibraryForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params.

    Cross-audit §1.5 binding — every list endpoint that accepts a
    filter axis MUST accept repeated occurrences as an OR.
    """

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _common_fields(record: LibraryItemRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "owner_user_id": record.owner_user_id,
        "project_id": record.project_id,
        "kind": record.kind,
        "source": record.source,
        "tags": list(record.tags),
        "index_status": record.index_status,
        "index_error": record.index_error,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "last_accessed_at": (
            record.last_accessed_at.isoformat() if record.last_accessed_at else None
        ),
    }


def _to_wire(record: LibraryItemRecord) -> dict[str, Any]:
    """Marshal a record into the wire shape from api-types/library.ts."""

    base = _common_fields(record)
    if isinstance(record, LibraryFileRecord):
        base.update(
            {
                "file_kind": record.file_kind,
                "name": record.name,
                "mime": record.mime,
                "size_bytes": record.size_bytes,
                "blob_ref": record.blob_ref,
                "thumbnail_blob_ref": record.thumbnail_blob_ref,
                "checksum_sha256": record.checksum_sha256,
            }
        )
        return base
    if isinstance(record, LibraryPageRecord):
        base.update(
            {
                "title": record.title,
                "markdown": record.markdown,
                "version": record.version,
                "version_etag": record.version_etag,
            }
        )
        return base
    # Dataset
    assert isinstance(record, LibraryDatasetRecord)
    base.update(
        {
            "name": record.name,
            "description": record.description,
            "schema": list(record.columns_schema),
            "row_count": record.row_count,
            "size_bytes": record.size_bytes,
            "blob_ref": record.blob_ref,
            "format": record.format,
            "checksum_sha256": record.checksum_sha256,
        }
    )
    return base


__all__ = [
    "CreatePageRequestModel",
    "PatchLibraryItemRequestModel",
    "register_library_routes",
]
