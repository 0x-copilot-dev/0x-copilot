"""Public ``/v1/tools`` routes + internal ``/internal/v1/tools`` routes (Phase 10 P10-A2).

Routes are presentation-only; ACL + audit + invariants live in
:class:`ToolsService`. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating service exceptions to HTTP status codes:
   * :class:`ToolNotFound`        → 404 (cross-audit §1.3: 404-not-403)
   * :class:`ToolForbidden`       → 403
   * :class:`ToolInvalidRequest`  → 400
   * :class:`ToolConflict`        → 409
   * :class:`ToolNotImplemented`  → 501 (P10-A2 test-call stub)
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/tools.ts``.
4. Parsing repeatable ``filter[<axis>]=<value>`` query params with the
   §4.12 allowlist enforced up front (cross-audit §1.5).

P10-A4 wires the facade pass-through; this file ships the backend
surface. SSE registration (``GET /v1/tools/stream``) ships in
``backend_app.tools.sse`` and is registered via the same composer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.tools.service import (
    ToolConflict,
    ToolForbidden,
    ToolInvalidRequest,
    ToolNotFound,
    ToolNotImplemented,
    ToolsService,
)
from backend_app.tools.store import (
    ToolInvocationRecord,
    ToolRecord,
    VALID_SORTS,
)


# ---------------------------------------------------------------------------
# Wire models (Python mirrors of api-types/src/tools.ts)
# ---------------------------------------------------------------------------


class CreateToolRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    name: str
    description: str | None = None
    scope: str
    args_schema: dict[str, Any] | None = None
    returns_schema: dict[str, Any] | None = None
    transport: dict[str, Any]
    project_id: str | None = None
    tags: list[str] | None = None
    skill_page_ref: dict[str, Any] | None = None
    code_ref: dict[str, Any] | None = None


class UpdateToolRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    scope: str | None = None
    status: str | None = None
    status_reason: str | None = None
    tags: list[str] | None = None
    args_schema: dict[str, Any] | None = None
    returns_schema: dict[str, Any] | None = None
    transport: dict[str, Any] | None = None
    project_id: str | None = None


class TestToolCallRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    args: dict[str, Any]


class SetStatusRequestModel(BaseModel):
    """Body of ``POST /v1/tools/{id}/disable`` and ``POST .../enable``.

    Both routes accept an optional reason. ``status`` is set by the route
    handler (matching the URL verb), so the body never carries it.
    """

    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


# ---------------------------------------------------------------------------
# Allowlists for filter + sort (cross-audit §1.5 + tools-prd §4.12)
# ---------------------------------------------------------------------------


_ALLOWED_FILTER_AXES = frozenset(
    {"kind", "scope", "status", "project_id", "owner_user_id", "tag"}
)


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params."""

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _enforce_filter_allowlist(request: Request) -> None:
    """Reject any ``filter[<axis>]`` whose axis isn't in the allowlist.

    Cross-audit §1.5 binding: unknown filter axes must 400 with a clear
    error code so typos don't silently no-op into "list everything".
    """

    for key in request.query_params.keys():
        if not key.startswith("filter["):
            continue
        if not key.endswith("]"):
            continue
        axis = key[len("filter[") : -1]
        if axis not in _ALLOWED_FILTER_AXES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"error": "filter_not_allowed", "axis": axis},
            )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_tool_routes(
    app: FastAPI,
    *,
    service: ToolsService,
) -> None:
    """Attach ``/v1/tools/*`` routes to ``app``.

    Routes registered:

    * §4.1 ``GET    /v1/tools``                — list/search
    * §4.2 ``GET    /v1/tools/{id}``           — detail
    * §4.3 ``POST   /v1/tools``                — register
    * §4.4 ``PATCH  /v1/tools/{id}``           — edit
    * §4.5 ``POST   /v1/tools/{id}/test``      — test-call (501 stub in P10-A2)
    * §4.6 ``POST   /v1/tools/{id}/enable``    — re-enable
    *      ``POST   /v1/tools/{id}/disable``   — pause
    * §4.7 ``DELETE /v1/tools/{id}``           — soft-delete
    * §4.8 ``GET    /v1/tools/{id}/invocations`` — history
    * §4.9 ``GET    /v1/tools/{id}/usage``     — projection

    Internal routes:

    * ``GET    /internal/v1/tools/by_ids``           — bulk fetch
    * ``POST   /internal/v1/tools/{id}/invocations`` — record an invocation
    * ``POST   /internal/v1/tools/{id}/error``       — bump error counter
    """

    # ---- §4.1 list ----------------------------------------------------
    @app.get(
        "/v1/tools",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_tools(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="name"),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        _enforce_filter_allowlist(request)
        kinds = _parse_repeatable_filter(request, "kind") or None
        scopes = _parse_repeatable_filter(request, "scope") or None
        statuses = _parse_repeatable_filter(request, "status") or None
        project_ids = _parse_repeatable_filter(request, "project_id") or None
        owner_user_ids = _parse_repeatable_filter(request, "owner_user_id") or None
        tags = _parse_repeatable_filter(request, "tag") or None

        # Enum validation — bail early on a bad value rather than letting
        # the store's empty-result-set hide the typo.
        if kinds is not None:
            for value in kinds:
                if value not in {"mcp", "openapi", "builtin", "code", "skill"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_kind_invalid"
                    )
        if scopes is not None:
            for value in scopes:
                if value not in {"read", "write", "both"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_scope_invalid"
                    )
        if statuses is not None:
            for value in statuses:
                if value not in {"enabled", "disabled", "error", "pending_review"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_status_invalid"
                    )
        if sort not in VALID_SORTS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sort_invalid")

        try:
            rows, next_cursor = service.list_tools(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                kinds=kinds,
                scopes=scopes,
                statuses=statuses,
                project_ids=project_ids,
                owner_user_ids=owner_user_ids,
                tags=tags,
                q=q,
                cursor=cursor,
                limit=limit,
                sort=sort,
            )
        except ToolInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc

        return {
            "tools": [_tool_to_wire(r, service) for r in rows],
            "next_cursor": next_cursor,
        }

    # ---- §4.2 detail --------------------------------------------------
    @app.get(
        "/v1/tools/{tool_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_tool(
        request: Request,
        tool_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_tool(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                tool_id=tool_id,
            )
        except ToolNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
        return {
            "tool": _tool_to_wire(record, service),
            # P10-A2 returns empty consumer rollups; P10-A4 wires the
            # ai-backend join that lists agents / routines that grant
            # the tool. The shape is stable.
            "consumers": {
                "agents": [],
                "routines": [],
                "chats_with_grant": 0,
            },
        }

    # ---- §4.3 register ------------------------------------------------
    @app.post(
        "/v1/tools",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_tool(
        request: Request,
        payload: CreateToolRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.create_tool(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                payload=payload.model_dump(exclude_none=True),
            )
        except ToolInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _tool_to_wire(record, service)

    # ---- §4.4 PATCH ---------------------------------------------------
    @app.patch(
        "/v1/tools/{tool_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_tool(
        request: Request,
        tool_id: str,
        payload: UpdateToolRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            record = service.update_tool(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                tool_id=tool_id,
                patch=patch_dict,
            )
        except ToolNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
        except ToolForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        except ToolConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ToolInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _tool_to_wire(record, service)

    # ---- §4.5 test-call (501 stub) ------------------------------------
    @app.post(
        "/v1/tools/{tool_id}/test",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def test_tool_call(
        request: Request,
        tool_id: str,
        payload: TestToolCallRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.run_test_call(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                tool_id=tool_id,
                args=payload.args,
            )
        except ToolNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
        except ToolForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        except ToolInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        except ToolNotImplemented as exc:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                exc.code or "code_sandbox_not_yet_wired",
            ) from exc
        # Unreachable while P10-A3 is pending.
        return {"status": "ok", "result": None, "latency_ms": 0}

    # ---- §4.6 enable / disable ----------------------------------------
    @app.post(
        "/v1/tools/{tool_id}/disable",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def disable_tool(
        request: Request,
        tool_id: str,
        payload: SetStatusRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        return _set_status_route(
            request,
            tool_id=tool_id,
            new_status="disabled",
            reason=payload.reason,
            org_id=org_id,
            user_id=user_id,
            service=service,
        )

    @app.post(
        "/v1/tools/{tool_id}/enable",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def enable_tool(
        request: Request,
        tool_id: str,
        payload: SetStatusRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        return _set_status_route(
            request,
            tool_id=tool_id,
            new_status="enabled",
            reason=payload.reason,
            org_id=org_id,
            user_id=user_id,
            service=service,
        )

    # ---- §4.7 DELETE --------------------------------------------------
    @app.delete(
        "/v1/tools/{tool_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_tool(
        request: Request,
        tool_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.delete_tool(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                tool_id=tool_id,
            )
        except ToolNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
        except ToolForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- §4.8 invocations --------------------------------------------
    @app.get(
        "/v1/tools/{tool_id}/invocations",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_tool_invocations(
        request: Request,
        tool_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_id: str | None = Query(default=None),
        since_iso: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        caller_kinds = _parse_repeatable_filter(request, "caller_kind") or None
        statuses = _parse_repeatable_filter(request, "status") or None
        if caller_kinds is not None:
            for value in caller_kinds:
                if value not in {"agent", "routine", "chat"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_caller_kind_invalid"
                    )
        if statuses is not None:
            for value in statuses:
                if value not in {"ok", "error"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_status_invalid"
                    )
        since: datetime | None = None
        if since_iso is not None:
            try:
                since = datetime.fromisoformat(since_iso)
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "since_iso_invalid"
                ) from exc
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        try:
            rows, next_cursor = service.list_invocations(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                tool_id=tool_id,
                after_id=after_id,
                since=since,
                caller_kinds=caller_kinds,
                statuses=statuses,
                limit=limit,
            )
        except ToolNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
        return {
            "invocations": [_invocation_to_wire(r) for r in rows],
            "next_cursor": next_cursor,
        }

    # ---- §4.9 usage ---------------------------------------------------
    @app.get(
        "/v1/tools/{tool_id}/usage",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_tool_usage(
        request: Request,
        tool_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # ACL: same read gate as the detail view.
        try:
            service.get_tool(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                tool_id=tool_id,
            )
        except ToolNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
        windows = service.compute_usage(tenant_id=identity.org_id, tool_id=tool_id)
        return {
            "tool_id": tool_id,
            "windows": {
                "window_24h": _projection_to_wire(windows["window_24h"]),
                "window_7d": _projection_to_wire(windows["window_7d"]),
                "window_30d": _projection_to_wire(windows["window_30d"]),
            },
        }


# ---------------------------------------------------------------------------
# Internal routes (§4.11) — service-token gated
# ---------------------------------------------------------------------------


class ByIdsRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    tool_ids: list[str]


class RecordInvocationRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    run_id: str
    caller_kind: str
    caller_ref: dict[str, Any]
    args_summary: str = ""
    result_summary: str | None = None
    status: str
    error_kind: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    latency_ms: int = 0


class BumpErrorRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str


def register_tool_internal_routes(
    app: FastAPI,
    *,
    service: ToolsService,
) -> None:
    """Internal routes consumed by ai-backend (§4.11).

    Service-token verification is handled by
    :meth:`BackendServiceAuthenticator.internal_scoped_identity` — cross-
    tenant inserts collapse to 403.
    """

    @app.post("/internal/v1/tools/by_ids")
    def bulk_fetch_tools(
        payload: ByIdsRequestModel,
        request: Request,
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.tenant_id, user_id=""
        )
        if identity.org_id != payload.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross_tenant_rejected")
        out: list[dict[str, Any]] = []
        for tool_id in payload.tool_ids:
            record = service._store.get_tool(  # noqa: SLF001 — internal route
                tenant_id=identity.org_id, tool_id=tool_id
            )
            if record is not None:
                out.append(_tool_to_wire(record, service))
        return {"tools": out}

    @app.post(
        "/internal/v1/tools/{tool_id}/invocations",
        status_code=status.HTTP_201_CREATED,
    )
    def record_tool_invocation(
        tool_id: str,
        payload: RecordInvocationRequestModel,
        request: Request,
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.tenant_id, user_id=""
        )
        if identity.org_id != payload.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross_tenant_rejected")
        if payload.caller_kind not in {"agent", "routine", "chat"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "caller_kind_invalid")
        if payload.status not in {"ok", "error"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "status_invalid")

        started_at = _parse_iso(payload.started_at) or datetime.now(timezone.utc)
        ended_at = _parse_iso(payload.ended_at) or started_at

        record = ToolInvocationRecord(
            tool_id=tool_id,
            tenant_id=identity.org_id,
            run_id=payload.run_id,
            caller_kind=payload.caller_kind,  # type: ignore[arg-type]
            caller_ref=payload.caller_ref,
            args_summary=payload.args_summary,
            result_summary=payload.result_summary,
            status=payload.status,  # type: ignore[arg-type]
            error_kind=payload.error_kind,  # type: ignore[arg-type]
            started_at=started_at,
            ended_at=ended_at,
            latency_ms=payload.latency_ms,
        )
        try:
            stored = service.record_invocation(
                tenant_id=identity.org_id,
                tool_id=tool_id,
                record=record,
            )
        except ToolInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _invocation_to_wire(stored)

    @app.post("/internal/v1/tools/{tool_id}/error")
    def bump_tool_error_counter(
        tool_id: str,
        payload: BumpErrorRequestModel,
        request: Request,
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.tenant_id, user_id=""
        )
        if identity.org_id != payload.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross_tenant_rejected")
        record = service.bump_error_counter(tenant_id=identity.org_id, tool_id=tool_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found")
        return _tool_to_wire(record, service)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_status_route(
    request: Request,
    *,
    tool_id: str,
    new_status: str,
    reason: str | None,
    org_id: str,
    user_id: str,
    service: ToolsService,
) -> dict[str, Any]:
    identity = BackendServiceAuthenticator.scoped_identity(
        request, org_id=org_id, user_id=user_id
    )
    try:
        record = service.set_status(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            tool_id=tool_id,
            new_status=new_status,
            reason=reason,
        )
    except ToolNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool_not_found") from exc
    except ToolForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_or_admin_only") from exc
    except ToolInvalidRequest as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
        ) from exc
    return _tool_to_wire(record, service)


def _tool_to_wire(record: ToolRecord, service: ToolsService) -> dict[str, Any]:
    """Marshal a :class:`ToolRecord` into the wire shape (api-types/tools.ts).

    The ``usage`` projection is computed at read time from the existing
    ``runtime_tool_invocations`` rows (cross-audit §5.5 TU-1) — no
    parallel tracker.
    """

    usage = service.compute_rolled_up_usage(
        tenant_id=record.tenant_id, tool_id=record.id
    )
    out: dict[str, Any] = {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "name": record.name,
        "description": record.description,
        "kind": record.kind,
        "scope": record.scope,
        "status": record.status,
        "args_schema": record.args_schema,
        "returns_schema": record.returns_schema,
        "transport": record.transport,
        "owner_user_id": record.owner_user_id,
        "project_id": record.project_id,
        "tags": list(record.tags),
        "usage": usage,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }
    if record.status_reason is not None:
        out["status_reason"] = record.status_reason
    if record.skill_page_ref is not None:
        out["skill_page_ref"] = record.skill_page_ref
    if record.code_ref is not None:
        out["code_ref"] = record.code_ref
    return out


def _invocation_to_wire(record: ToolInvocationRecord) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": record.id,
        "tool_id": record.tool_id,
        "tenant_id": record.tenant_id,
        "run_id": record.run_id,
        "caller_kind": record.caller_kind,
        "caller_ref": record.caller_ref,
        "args_summary": record.args_summary,
        "status": record.status,
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat(),
        "latency_ms": record.latency_ms,
    }
    if record.result_summary is not None:
        out["result_summary"] = record.result_summary
    if record.error_kind is not None:
        out["error_kind"] = record.error_kind
    return out


def _projection_to_wire(projection: dict[str, Any]) -> dict[str, Any]:
    """Adapter from the service's internal projection shape to the wire
    `ToolUsageProjection`.

    The internal shape uses ``calls`` / ``p50_latency_ms`` / etc.; the
    wire uses ``calls_24h`` / ``calls_30d`` / ``p50_latency_ms_30d`` /
    ``success_rate_30d`` / ``last_used_at`` on the rolled-up view, and
    ``calls`` / ``p50_latency_ms`` / ``success_rate`` / ``last_used_at``
    on the per-window view.

    For per-window calls the route returns the per-window shape (no
    suffix) — frontends already know which window they asked for.
    """

    return {
        "calls": projection["calls"],
        "p50_latency_ms": projection["p50_latency_ms"],
        "success_rate": projection["success_rate"],
        "last_used_at": projection["last_used_at"],
    }


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


__all__ = [
    "CreateToolRequestModel",
    "SetStatusRequestModel",
    "TestToolCallRequestModel",
    "UpdateToolRequestModel",
    "register_tool_internal_routes",
    "register_tool_routes",
]
