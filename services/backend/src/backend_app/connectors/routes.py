"""Public ``/v1/connectors`` routes — Phase 11 P11-A2.

Routes are presentation-only; ACL + audit + state-machine invariants
live in :mod:`backend_app.connectors.service`. The route layer is
responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating service-layer exceptions to HTTP status codes (404 for
   :class:`ConnectorNotFound`, 403 for :class:`ConnectorForbidden`,
   400 for :class:`ConnectorInvalidRequest`).
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/connectors.ts``.

Endpoint coverage (connectors-prd §4):

* §4.1  ``GET    /v1/connectors``
* §4.2  ``GET    /v1/connectors/{id}``
* §4.3  ``POST   /v1/connectors/{slug}/start-oauth`` (alias of existing MCP path)
* §4.4  ``POST   /v1/connectors/oauth-callback`` (alias of existing MCP path)
* §4.5  ``POST   /v1/connectors/{id}/refresh``
* §4.6  ``POST   /v1/connectors/{id}/disconnect``
* §4.7  ``PATCH  /v1/connectors/{id}/scopes``
* §4.8  ``GET    /v1/connectors/{id}/audit``

The §4.9 SSE stream lives in :mod:`backend_app.connectors.sse` and is
registered alongside the routes from :func:`register_connector_routes`.
"""

from __future__ import annotations

from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.connectors.service import (
    ConnectorForbidden,
    ConnectorInvalidRequest,
    ConnectorNotFound,
    ConnectorsService,
)
from backend_app.connectors.store import (
    ConnectorAccessMode,
    ConnectorRecord,
    ConnectorScopeEntry,
)
from backend_app.identity.rbac import RequireScopes


# ---------------------------------------------------------------------------
# Request / response models (Python mirrors of api-types/src/connectors.ts)
# ---------------------------------------------------------------------------


class ConnectorScopeEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str
    granted: bool = True
    description: str = ""


class ConnectorResponseModel(BaseModel):
    """Wire mirror of ``Connector`` (packages/api-types/src/connectors.ts)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    slug: str
    display_name: str
    description: str
    status: str
    status_reason: str | None = None
    # Per-connector agent access mode (Tools destination 3-way segment).
    # Always emitted now that the access-mode PATCH exists (PRD-06 D2); the
    # api-types ``Connector.access_mode`` mirror is correspondingly required.
    access_mode: ConnectorAccessMode
    owner_user_id: str
    scopes: list[ConnectorScopeEntryModel]
    last_sync_at: str | None = None
    last_error_at: str | None = None
    created_at: str
    updated_at: str


class ConnectorCatalogEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    display_name: str
    description: str = ""
    icon_hint: str | None = None


class ConnectorListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connectors: list[ConnectorResponseModel]
    available: list[ConnectorCatalogEntryModel]
    next_cursor: str | None = None


class ItemRefModel(BaseModel):
    """Minimal wire mirror of ``ItemRef`` (packages/api-types/src/refs.ts).

    Routes only construct ItemRefs for the consumers projection; the
    full discriminated union lives on the FE. Here we serialise the
    bare ``kind`` + ``id`` pair that the FE's ItemLink registry needs.
    """

    model_config = ConfigDict(extra="forbid")
    kind: str
    id: str


class ConnectorConsumersModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[ItemRefModel]
    tools: list[ItemRefModel]
    projects: list[ItemRefModel]
    chats_with_grant: int


class ConnectorDetailResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: ConnectorResponseModel
    consumers: ConnectorConsumersModel


class StartOAuthResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_url: str
    state: str


class OAuthCallbackRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)


class PatchScopesRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[ConnectorScopeEntryModel] = Field(..., min_length=0)


class PatchScopesResponseModel(BaseModel):
    """``202 Accepted`` — the server is requesting a re-OAuth round-trip.

    The destination wraps the existing MCP OAuth start path so the
    response is the same authorization-URL shape the chat composer
    consumes.
    """

    model_config = ConfigDict(extra="forbid")
    reauth_url: str
    state: str


class SetAccessModeRequestModel(BaseModel):
    """``PATCH /v1/connectors/{id}/access-mode`` body.

    ``access_mode`` is carried as a plain string and validated against the
    ``ConnectorAccessMode`` union *in the route* so a value outside the union
    is a deterministic ``400 invalid_request`` (PRD-06 DoD 2) rather than
    FastAPI's default ``422`` body-validation shape. Mirrors
    ``SetConnectorAccessModeRequest`` in
    ``packages/api-types/src/connectors.ts``.
    """

    model_config = ConfigDict(extra="forbid")
    access_mode: str


class SetAccessModeResponseModel(BaseModel):
    """``200 OK`` — the reconciled connector row with its new ``access_mode``.

    ``200`` (not the scopes route's ``202``): an access-mode change is
    complete when the row is written; it never triggers a re-OAuth round-trip.
    """

    model_config = ConfigDict(extra="forbid")
    connector: ConnectorResponseModel


class RefreshConnectorResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: ConnectorResponseModel


class DisconnectConnectorResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: ConnectorResponseModel


class ConnectorAuditEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    connector_id: str
    tenant_id: str
    ts: str
    actor_user_id: str
    action: str
    correlation_id: str | None = None


class ConnectorAuditResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[ConnectorAuditEntryModel]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_connector_routes(app: FastAPI, *, service: ConnectorsService) -> None:
    """Attach ``/v1/connectors`` routes to ``app``."""

    @app.get(
        "/v1/connectors",
        response_model=ConnectorListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_connectors(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        q: str | None = Query(default=None),
        installed: bool | None = Query(default=None),
    ) -> ConnectorListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        statuses = _parse_repeatable_filter(request, "status") or None
        slugs = _parse_repeatable_filter(request, "slug") or None
        records, next_cursor = service.list_connectors(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            statuses=statuses,
            slugs=slugs,
            installed=installed,
            q=q,
            cursor=cursor,
            limit=limit,
        )
        # Available catalog is filtered to slugs the caller has NOT
        # installed yet — same UX rule as the MCP catalog endpoint.
        installed_slugs = {r.slug for r in records}
        available = [
            ConnectorCatalogEntryModel(
                slug=entry.slug,
                display_name=entry.display_name,
                description=entry.description,
                icon_hint=entry.icon_hint,
            )
            for entry in service.catalog
            if entry.slug not in installed_slugs
        ]
        return ConnectorListResponseModel(
            connectors=[_to_wire(record) for record in records],
            available=available,
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/connectors/{connector_id}",
        response_model=ConnectorDetailResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_connector(
        request: Request,
        connector_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ConnectorDetailResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_connector(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                connector_id=connector_id,
            )
        except ConnectorNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_not_found"
            ) from exc
        consumers = service.project_consumers(
            tenant_id=identity.org_id, connector_id=record.id
        )
        return ConnectorDetailResponseModel(
            connector=_to_wire(record),
            consumers=ConnectorConsumersModel(
                agents=[_to_item_ref(row, "agent") for row in consumers["agents"]],
                tools=[_to_item_ref(row, "tool") for row in consumers["tools"]],
                projects=[
                    _to_item_ref(row, "project") for row in consumers["projects"]
                ],
                chats_with_grant=int(consumers["chats_with_grant"]),
            ),
        )

    @app.post(
        "/v1/connectors/{slug}/start-oauth",
        response_model=StartOAuthResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def start_oauth(
        request: Request,
        slug: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> StartOAuthResponseModel:
        """Alias of the existing MCP OAuth start path.

        Production wiring delegates to
        :meth:`McpRegistryService.start_auth`; the destination's route
        is the public surface the FE consumes. In this branch the
        delegation is a stub returning a deterministic URL so the
        wiring layer can substitute the real client at boot.
        """

        BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # The actual OAuth client is wired by the destination's binder
        # at boot (see ``app.py``). The route returns the substituted
        # response; in dev / tests the stub returns a deterministic URL
        # so the FE wizard can render.
        oauth_client = getattr(
            request.app.state, "connector_oauth_start", _default_oauth_start
        )
        return StartOAuthResponseModel(**oauth_client(slug=slug))

    @app.post(
        "/v1/connectors/oauth-callback",
        response_model=ConnectorResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def oauth_callback(
        request: Request,
        payload: OAuthCallbackRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ConnectorResponseModel:
        """Alias of the existing MCP OAuth callback path.

        Production wiring delegates to
        :meth:`McpRegistryService.complete_auth`; the destination's
        write-through helper (:meth:`ConnectorsService.write_through_from_mcp`)
        is the second leg — it denormalizes the row + appends the
        ``connector.connected`` audit row. The stub here only exercises
        the second leg so the route's response shape is exercisable
        without the full OAuth round-trip wired.
        """

        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        callback = getattr(request.app.state, "connector_oauth_callback", None)
        if callback is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "connector_oauth_not_configured",
            )
        record = callback(
            org_id=identity.org_id,
            user_id=identity.user_id,
            code=payload.code,
            state=payload.state,
        )
        return _to_wire(record)

    @app.post(
        "/v1/connectors/{connector_id}/refresh",
        response_model=RefreshConnectorResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def refresh_connector(
        request: Request,
        connector_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RefreshConnectorResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.refresh_token(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                connector_id=connector_id,
            )
        except ConnectorNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_not_found"
            ) from exc
        except ConnectorForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        return RefreshConnectorResponseModel(connector=_to_wire(record))

    @app.post(
        "/v1/connectors/{connector_id}/disconnect",
        response_model=DisconnectConnectorResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def disconnect_connector(
        request: Request,
        connector_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> DisconnectConnectorResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.disconnect(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                connector_id=connector_id,
            )
        except ConnectorNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_not_found"
            ) from exc
        except ConnectorForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        return DisconnectConnectorResponseModel(connector=_to_wire(record))

    @app.patch(
        "/v1/connectors/{connector_id}/scopes",
        response_model=PatchScopesResponseModel,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_scopes(
        request: Request,
        connector_id: str,
        payload: PatchScopesRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> PatchScopesResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        scopes = tuple(
            ConnectorScopeEntry(
                scope=s.scope, granted=s.granted, description=s.description
            )
            for s in payload.scopes
        )
        try:
            service.patch_scopes(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                connector_id=connector_id,
                scopes=scopes,
            )
        except ConnectorNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_not_found"
            ) from exc
        except ConnectorForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        except ConnectorInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        # Trigger a re-OAuth flow. Wired through the existing
        # MCP OAuth start path at boot; the stub returns a deterministic
        # URL so the wizard can render in tests.
        oauth_client = getattr(
            request.app.state, "connector_oauth_start", _default_oauth_start
        )
        result = oauth_client(slug=connector_id)
        return PatchScopesResponseModel(
            reauth_url=result["authorization_url"], state=result["state"]
        )

    @app.patch(
        "/v1/connectors/{connector_id}/access-mode",
        response_model=SetAccessModeResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def set_access_mode(
        request: Request,
        connector_id: str,
        payload: SetAccessModeRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SetAccessModeResponseModel:
        """Set the per-connector agent access mode (PRD-06 D2).

        200 (not 202) — the change is durable the moment the row is written;
        no re-OAuth round-trip is involved. Authorization is the connectors
        write boundary: owner-or-admin only, 404 (not 403) for a cross-tenant
        id so existence never leaks. Idempotent — a set-to-current-value
        returns the unchanged row and writes zero audit rows.
        """

        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            mode = ConnectorAccessMode(payload.access_mode)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_request") from exc
        try:
            record = service.set_access_mode(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                connector_id=connector_id,
                access_mode=mode,
            )
        except ConnectorNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_not_found"
            ) from exc
        except ConnectorForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "owner_or_admin_only"
            ) from exc
        return SetAccessModeResponseModel(connector=_to_wire(record))

    @app.get(
        "/v1/connectors/{connector_id}/audit",
        response_model=ConnectorAuditResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_audit(
        request: Request,
        connector_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ConnectorAuditResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            entries, next_cursor = service.list_audit(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                connector_id=connector_id,
                cursor=cursor,
                limit=limit,
            )
        except ConnectorNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_not_found"
            ) from exc
        return ConnectorAuditResponseModel(
            entries=[
                ConnectorAuditEntryModel(
                    id=entry.audit_id,
                    connector_id=entry.target_id,
                    tenant_id=entry.tenant_id,
                    ts=entry.ts.isoformat(),
                    actor_user_id=entry.actor_user_id,
                    action=entry.action,
                    correlation_id=entry.correlation_id,
                )
                for entry in entries
            ],
            next_cursor=next_cursor,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """``filter[<axis>]=<value>`` repeatable query parameter (OR semantics).

    Matches the inbox / todos route convention (cross-audit §1.5).
    """

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _to_wire(record: ConnectorRecord) -> ConnectorResponseModel:
    return ConnectorResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        slug=record.slug,
        display_name=record.display_name,
        description=record.description,
        status=record.status,
        status_reason=record.status_reason,
        access_mode=record.access_mode,
        owner_user_id=record.owner_user_id,
        scopes=[
            ConnectorScopeEntryModel(
                scope=s.scope, granted=s.granted, description=s.description
            )
            for s in record.scopes
        ],
        last_sync_at=record.last_sync_at.isoformat() if record.last_sync_at else None,
        last_error_at=record.last_error_at.isoformat()
        if record.last_error_at
        else None,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


def _to_item_ref(row: dict[str, Any], default_kind: str) -> ItemRefModel:
    """Project a service-layer consumer row into an ItemRef-shaped model."""

    return ItemRefModel(
        kind=str(row.get("kind", default_kind)),
        id=str(row.get("id", "")),
    )


def _default_oauth_start(*, slug: str) -> dict[str, str]:
    """Deterministic dev / test stub for the OAuth start path.

    Production deploys override this via ``app.state.connector_oauth_start``
    with the real :class:`McpRegistryService.start_auth` wrapper.
    """

    return {
        "authorization_url": f"https://auth.example/{slug}/authorize?state=stub",
        "state": "stub-state",
    }


__all__ = [
    "ConnectorAuditEntryModel",
    "ConnectorAuditResponseModel",
    "ConnectorCatalogEntryModel",
    "ConnectorConsumersModel",
    "ConnectorDetailResponseModel",
    "ConnectorListResponseModel",
    "ConnectorResponseModel",
    "ConnectorScopeEntryModel",
    "DisconnectConnectorResponseModel",
    "OAuthCallbackRequestModel",
    "PatchScopesRequestModel",
    "PatchScopesResponseModel",
    "RefreshConnectorResponseModel",
    "SetAccessModeRequestModel",
    "SetAccessModeResponseModel",
    "StartOAuthResponseModel",
    "register_connector_routes",
]
