"""AC9 — desktop MCP connector routes (desktop-only OAuth transport).

These are the *desktop variant* of the connector OAuth surface. They are
distinct from the shipped web routes in :mod:`backend_app.connectors.routes`
(``/v1/connectors/{slug}/start-oauth`` + ``/v1/connectors/oauth-callback``,
whose wire shapes the web redirect flow depends on and which stay unchanged).

The desktop transport needs a richer body — a loopback-port / deep-link
``callback`` the backend reconstructs into a fixed redirect URI, a single-use
``oauth_session_id``, and an optional ``code`` (the callback may instead carry a
provider ``error``). Folding those into the shared web shapes would be a
breaking change, so they live here and speak the desktop-only variant declared
in ``packages/api-types/src/connectors-desktop.ts``.

All three routes forward to :class:`DesktopMcpOAuthCoordinator`, which drives
the same backend OAuth authority (state + PKCE + ``TokenVault``) the web
connectors use. Provider tokens and client secrets never cross these routes —
responses carry only safe connection metadata.

Registration order: the two literal paths (``/desktop/catalog`` +
``/desktop/oauth-callback``) and the three-segment ``/{slug}/desktop/start-oauth``
never collide with the single-segment ``/v1/connectors/{connector_id}`` route,
so ordering relative to :func:`register_connector_routes` is not load-bearing.
"""

from __future__ import annotations

from typing import Literal

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.connectors.oauth_coordinator import (
    DesktopMcpOAuthCoordinator,
    DesktopOAuthCallback,
    DesktopOAuthError,
)
from backend_app.connectors.profile_catalog import (
    ConnectorReleaseStage,
    DesktopConnectorProfile,
    DesktopProfileCatalog,
    ProfileCatalogError,
    ResolvedConnectorProfile,
)
from backend_app.identity.rbac import RequireScopes

# ---------------------------------------------------------------------------
# Stable error code → HTTP status. Unknown codes fail closed at 400.
# ---------------------------------------------------------------------------

_ERROR_STATUS: dict[str, int] = {
    "connector_profile_unavailable": status.HTTP_404_NOT_FOUND,
    "connector_preview_disabled": status.HTTP_403_FORBIDDEN,
    "connector_admin_setup_required": status.HTTP_403_FORBIDDEN,
    # The connector exists but has no OAuth client configured — not connectable
    # yet. 409 Conflict (a state conflict), not a 500.
    "connector_oauth_setup_required": status.HTTP_409_CONFLICT,
    "connector_oauth_redirect_unsupported": status.HTTP_400_BAD_REQUEST,
    "connector_oauth_state_invalid": status.HTTP_400_BAD_REQUEST,
    "connector_oauth_expired": status.HTTP_400_BAD_REQUEST,
    "connector_oauth_denied": status.HTTP_400_BAD_REQUEST,
    "connector_oauth_exchange_failed": status.HTTP_502_BAD_GATEWAY,
}


# ---------------------------------------------------------------------------
# Wire models — Python mirrors of api-types/src/connectors-desktop.ts
# ---------------------------------------------------------------------------


class _DesktopLoopbackCallbackModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["desktop_loopback"]
    port: int = Field(..., ge=1024, le=65535)
    path: Literal["/connectors/oauth/cb"] = "/connectors/oauth/cb"


class _DesktopDeepLinkCallbackModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["desktop_deep_link"]
    uri: Literal["enterprise://oauth/callback"] = "enterprise://oauth/callback"


class DesktopStartOAuthRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback: _DesktopLoopbackCallbackModel | _DesktopDeepLinkCallbackModel = Field(
        ..., discriminator="kind"
    )
    requested_product_scope: Literal["read", "draft"] = "read"

    def to_callback(self) -> DesktopOAuthCallback:
        if self.callback.kind == "desktop_loopback":
            return DesktopOAuthCallback(
                kind="desktop_loopback", port=self.callback.port
            )
        return DesktopOAuthCallback(kind="desktop_deep_link")


class DesktopStartOAuthResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oauth_session_id: str
    authorization_url: str
    state: str
    expires_at: str
    requested_permissions: list[str]


class DesktopOAuthCallbackRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oauth_session_id: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    code: str | None = None
    error: str | None = None
    error_description: str | None = None


class DesktopConnectionResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: str
    connector_slug: str
    display_group: str
    auth_state: str


class DesktopCapabilitySummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    status: Literal["supported", "scope_required", "unsupported"]
    read_only: bool


class DesktopCatalogEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    display_name: str
    description: str
    display_group: str
    release_stage: Literal["stable", "preview"]
    availability: str
    requested_permissions: list[str]
    capabilities: list[DesktopCapabilitySummaryModel]
    unsupported_capabilities: list[str]
    reference_urls: list[str]


class DesktopCatalogResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[DesktopCatalogEntryModel]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_desktop_connector_routes(
    app: FastAPI,
    *,
    coordinator: DesktopMcpOAuthCoordinator,
    catalog: DesktopProfileCatalog,
    preview_enabled: bool = False,
) -> None:
    """Attach the desktop connector OAuth + catalog routes to ``app``."""

    @app.get(
        "/v1/connectors/desktop/catalog",
        response_model=DesktopCatalogResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def desktop_catalog(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> DesktopCatalogResponseModel:
        # Identity is verified even though the catalog is deployment-global:
        # keeps the desktop surface uniformly authenticated + audit-scoped.
        BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            resolved = catalog.reconcile(preview_enabled=preview_enabled)
        except ProfileCatalogError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "connector_catalog_invalid"
            ) from exc
        return DesktopCatalogResponseModel(
            entries=[_to_catalog_entry(row) for row in resolved]
        )

    @app.post(
        "/v1/connectors/{slug}/desktop/start-oauth",
        response_model=DesktopStartOAuthResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def desktop_start_oauth(
        request: Request,
        slug: str,
        payload: DesktopStartOAuthRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> DesktopStartOAuthResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            result = coordinator.start(
                slug=slug,
                org_id=identity.org_id,
                user_id=identity.user_id,
                callback=payload.to_callback(),
                requested_product_scope=payload.requested_product_scope,
            )
        except DesktopOAuthError as exc:
            raise _http_from_oauth_error(exc) from exc
        except ProfileCatalogError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_profile_unavailable"
            ) from exc
        return DesktopStartOAuthResponseModel(
            oauth_session_id=result.oauth_session_id,
            authorization_url=result.authorization_url,
            state=result.state,
            expires_at=result.expires_at.isoformat(),
            requested_permissions=list(result.requested_permissions),
        )

    @app.post(
        "/v1/connectors/desktop/oauth-callback",
        response_model=DesktopConnectionResultModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def desktop_oauth_callback(
        request: Request,
        payload: DesktopOAuthCallbackRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> DesktopConnectionResultModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            result = coordinator.complete(
                oauth_session_id=payload.oauth_session_id,
                state=payload.state,
                caller_org_id=identity.org_id,
                caller_user_id=identity.user_id,
                code=payload.code,
                error=payload.error,
                error_description=payload.error_description,
            )
        except DesktopOAuthError as exc:
            raise _http_from_oauth_error(exc) from exc
        except ProfileCatalogError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "connector_profile_unavailable"
            ) from exc
        return DesktopConnectionResultModel(
            server_id=result.server_id,
            connector_slug=result.connector_slug,
            display_group=result.display_group,
            auth_state=result.auth_state.value,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http_from_oauth_error(exc: DesktopOAuthError) -> HTTPException:
    code = _ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST)
    return HTTPException(code, exc.code)


def _to_catalog_entry(row: ResolvedConnectorProfile) -> DesktopCatalogEntryModel:
    profile = row.profile
    return DesktopCatalogEntryModel(
        slug=profile.connector_slug,
        display_name=row.display_name,
        description=row.description,
        display_group=profile.display_group,
        release_stage=(
            "preview"
            if profile.release_stage is ConnectorReleaseStage.PREVIEW
            else "stable"
        ),
        availability=row.availability.value,
        requested_permissions=[
            perm.identifier
            for perm in profile.permissions
            if perm.required_for == "read"
        ],
        capabilities=[
            _to_capability(profile, tool.tool_name) for tool in profile.tools
        ],
        unsupported_capabilities=list(profile.unsupported_capabilities),
        reference_urls=list(profile.reference_urls),
    )


def _to_capability(
    profile: DesktopConnectorProfile, tool_name: str
) -> DesktopCapabilitySummaryModel:
    tool = next(t for t in profile.tools if t.tool_name == tool_name)
    if tool.product_scope == "read":
        cap_status: Literal["supported", "scope_required", "unsupported"] = "supported"
    else:
        # draft/write tools need a broader scope + reauthorization to enable.
        cap_status = "scope_required"
    return DesktopCapabilitySummaryModel(
        id=tool.tool_name,
        label=tool.tool_name,
        status=cap_status,
        read_only=tool.product_scope == "read",
    )


__all__ = [
    "DesktopCatalogEntryModel",
    "DesktopCatalogResponseModel",
    "DesktopConnectionResultModel",
    "DesktopOAuthCallbackRequestModel",
    "DesktopStartOAuthRequestModel",
    "DesktopStartOAuthResponseModel",
    "register_desktop_connector_routes",
]
