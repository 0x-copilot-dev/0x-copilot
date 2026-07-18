"""AC9 — generic desktop MCP OAuth coordinator.

The missing piece between Electron's system-browser / loopback delivery and the
existing backend OAuth authority. It is the *per-MCP-server* auth layer, not a
parallel credential path: it drives the same
:class:`~backend_app.service.McpRegistryService` ``start_auth`` / ``complete_auth``
flow (state + PKCE + TokenVault) that web connectors use, adding only what the
desktop transport requires:

* the redirect URI is **reconstructed** from a validated loopback port + fixed
  path (or the fixed deep-link URI) — an arbitrary redirect is never accepted;
* the callback caller's verified identity must **match** the org/user recorded
  when the session started (closes the confused-deputy gap where mere state
  possession would be enough);
* preview/admin-setup profiles fail closed *before* a browser opens;
* the callback returns only safe connection metadata — never a provider token
  or client secret (those stay encrypted in the backend ``TokenVault``).

The session TTL is five minutes (AC9), enforced by
:data:`DESKTOP_OAUTH_TTL` on the service the coordinator drives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from backend_app.connectors.profile_catalog import (
    ConnectorReleaseStage,
    DesktopConnectorProfile,
    DesktopProfileCatalog,
)
from backend_app.contracts import (
    InternalMcpAuthRequest,
    McpAuthCallbackRequest,
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerRecord,
    McpTransport,
)
from backend_app.service import McpRegistryService

DESKTOP_OAUTH_TTL = timedelta(minutes=5)

_LOOPBACK_HOST = "127.0.0.1"
_LOOPBACK_PATH = "/connectors/oauth/cb"
_DEEP_LINK_URI = "enterprise://oauth/callback"


class DesktopOAuthError(ValueError):
    """Safe, stable-coded desktop OAuth failure.

    ``code`` is one of the AC9 stable error identifiers; the string form is a
    safe public message. No provider detail, token, or state leaks through it.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class DesktopOAuthCallback(BaseModel):
    """Where the desktop should receive the OAuth code — port/uri only.

    The backend reconstructs the redirect target from these validated fields;
    it never trusts a full redirect URI supplied by the client.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["desktop_loopback", "desktop_deep_link"]
    port: int | None = None

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not (1024 <= value <= 65535):
            raise DesktopOAuthError(
                "connector_oauth_redirect_unsupported",
                "loopback port must be an unprivileged port",
            )
        return value

    def redirect_uri(self) -> str:
        """Reconstruct the exact redirect URI from validated fields."""

        if self.kind == "desktop_loopback":
            if self.port is None:
                raise DesktopOAuthError(
                    "connector_oauth_redirect_unsupported",
                    "loopback callback requires a port",
                )
            return f"http://{_LOOPBACK_HOST}:{self.port}{_LOOPBACK_PATH}"
        return _DEEP_LINK_URI

    def profile_mode(self) -> str:
        return "loopback_pkce" if self.kind == "desktop_loopback" else "deep_link_pkce"


class DesktopStartResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    oauth_session_id: str
    authorization_url: str
    state: str
    expires_at: datetime
    requested_permissions: tuple[str, ...]


class DesktopConnectionResult(BaseModel):
    """Safe post-callback metadata — no token or secret ever appears here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str
    connector_slug: str
    display_group: str
    auth_state: McpAuthState


@dataclass
class _PendingSession:
    org_id: str
    user_id: str
    slug: str
    server_id: str


@dataclass
class DesktopMcpOAuthCoordinator:
    """Coordinates desktop MCP OAuth over the existing backend authority."""

    mcp_service: McpRegistryService
    catalog: DesktopProfileCatalog
    preview_enabled: bool = False
    _pending: dict[str, _PendingSession] = field(default_factory=dict, repr=False)

    def start(
        self,
        *,
        slug: str,
        org_id: str,
        user_id: str,
        callback: DesktopOAuthCallback,
        requested_product_scope: Literal["read", "draft"] = "read",
    ) -> DesktopStartResult:
        """Begin OAuth for a desktop connector and return the authorization URL."""

        profile = self.catalog.get(slug)
        self._assert_available(profile, callback)
        self._ensure_server(profile, org_id=org_id, user_id=user_id)

        redirect_uri = callback.redirect_uri()
        response = self.mcp_service.start_auth(
            server_id=profile.server_id,
            request=InternalMcpAuthRequest(
                org_id=org_id,
                user_id=user_id,
                redirect_uri=redirect_uri,
            ),
        )
        state = self._state_from_auth_url(response.auth_url)
        self._pending[state] = _PendingSession(
            org_id=org_id,
            user_id=user_id,
            slug=slug,
            server_id=profile.server_id,
        )
        return DesktopStartResult(
            oauth_session_id=state,
            authorization_url=response.auth_url,
            state=state,
            expires_at=response.expires_at,
            requested_permissions=self._requested_permissions(
                profile, requested_product_scope
            ),
        )

    def complete(
        self,
        *,
        oauth_session_id: str,
        state: str,
        caller_org_id: str,
        caller_user_id: str,
        code: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> DesktopConnectionResult:
        """Complete OAuth, enforcing owner match, and return safe metadata."""

        if oauth_session_id != state:
            self._drop(oauth_session_id)
            self._drop(state)
            raise DesktopOAuthError("connector_oauth_state_invalid")

        pending = self._pending.get(oauth_session_id)
        if pending is None:
            raise DesktopOAuthError("connector_oauth_state_invalid")
        if pending.org_id != caller_org_id or pending.user_id != caller_user_id:
            # Confused-deputy: a caller presenting someone else's state.
            self._drop(oauth_session_id)
            raise DesktopOAuthError("connector_oauth_state_invalid")

        try:
            response = self.mcp_service.complete_auth(
                McpAuthCallbackRequest(
                    state=state,
                    code=code,
                    error=error,
                    error_description=error_description,
                )
            )
        except ValueError as exc:
            self._drop(oauth_session_id)
            raise self._map_completion_error(error) from exc
        finally:
            # Single-use: the session cannot be replayed regardless of outcome.
            self._drop(oauth_session_id)

        profile = self.catalog.get(pending.slug)
        return DesktopConnectionResult(
            server_id=response.server_id,
            connector_slug=pending.slug,
            display_group=profile.display_group,
            auth_state=response.auth_state,
        )

    # -- helpers -------------------------------------------------------------

    def _assert_available(
        self, profile: DesktopConnectorProfile, callback: DesktopOAuthCallback
    ) -> None:
        if callback.profile_mode() not in profile.callback_modes:
            raise DesktopOAuthError("connector_oauth_redirect_unsupported")
        if (
            profile.release_stage is ConnectorReleaseStage.PREVIEW
            and not self.preview_enabled
        ):
            raise DesktopOAuthError("connector_preview_disabled")
        if profile.requires_admin_setup or profile.has_tenant_template:
            raise DesktopOAuthError("connector_admin_setup_required")

    def _ensure_server(
        self,
        profile: DesktopConnectorProfile,
        *,
        org_id: str,
        user_id: str,
    ) -> None:
        """Idempotently ensure the MCP server record exists for this user.

        Never overwrites an existing installation (which may already carry the
        user's pre-registered OAuth client). Only materializes a minimal record
        when the profile's server has not been installed yet.
        """

        existing = self.mcp_service.store.get_server(
            org_id=org_id, server_id=profile.server_id
        )
        if existing is not None and existing.user_id == user_id:
            return
        record = McpServerRecord(
            server_id=profile.server_id,
            org_id=org_id,
            user_id=user_id,
            name=self._slug_name(profile.connector_slug),
            display_name=profile.connector_slug.title(),
            url=profile.endpoint_template,
            transport=McpTransport(profile.transport),
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=McpAuthState.UNAUTHENTICATED,
            health=McpServerHealth.HEALTHY,
            enabled=True,
        )
        self.mcp_service.store.create_server(record)

    def _requested_permissions(
        self,
        profile: DesktopConnectorProfile,
        requested_product_scope: str,
    ) -> tuple[str, ...]:
        wanted = {"read"}
        if requested_product_scope == "draft":
            wanted.add("draft")
        return tuple(
            perm.identifier
            for perm in profile.permissions
            if perm.required_for in wanted
        )

    @staticmethod
    def _state_from_auth_url(auth_url: str) -> str:
        query = parse_qs(urlsplit(auth_url).query)
        values = query.get("state") or []
        if not values or not values[0]:
            raise DesktopOAuthError("connector_oauth_exchange_failed")
        return values[0]

    @staticmethod
    def _map_completion_error(error: str | None) -> DesktopOAuthError:
        if error is not None:
            return DesktopOAuthError("connector_oauth_denied")
        return DesktopOAuthError("connector_oauth_exchange_failed")

    def _drop(self, oauth_session_id: str) -> None:
        self._pending.pop(oauth_session_id, None)

    @staticmethod
    def _slug_name(slug: str) -> str:
        return slug.replace("-", "_")


__all__ = [
    "DESKTOP_OAUTH_TTL",
    "DesktopConnectionResult",
    "DesktopMcpOAuthCoordinator",
    "DesktopOAuthCallback",
    "DesktopOAuthError",
    "DesktopStartResult",
]
