"""Run-start resolver for per-connector access modes (PRD-06 D3b).

Fetches ``/internal/v1/mcp/cards`` once at run-create and packs each card's
``access_mode`` into ``AgentRuntimeContext.connector_access_modes`` (keyed by
``server_id``) so the runtime re-check (``McpPermissionPolicy``) reads a frozen
snapshot for the lifetime of the run. Cards the backend already omitted (an
``off`` connector is dropped from the listing entirely) simply don't appear, so
the frozen snapshot is the defense-in-depth mirror of the authoritative,
immediately-effective backend ``proxy_internal_rpc`` gate.

Falls back to an empty mapping (never raises) when the trusted backend lane is
not configured or the fetch fails — run-start is never blocked on connector
availability, and an empty snapshot means "no per-connector gate this run",
which is safe because the authoritative gate still runs on every tool call.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import httpx

_LOGGER = logging.getLogger(__name__)


class _Env:
    """Environment variable names for backend URL and service token."""

    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    BACKEND_BASE_URL_FALLBACK = "MCP_BACKEND_REGISTRY_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    """Service-to-service header names for the trusted backend lane."""

    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 5.0


@runtime_checkable
class ConnectorAccessModesResolver(Protocol):
    """Port for fetching the per-run connector-access-mode snapshot.

    Implementations must return an empty mapping (never raise) when the
    backend lane is unavailable so run-start degrades gracefully.
    """

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, str]:
        """Return ``{server_id: access_mode}`` or an empty mapping."""


class HttpConnectorAccessModesResolver:
    """Production resolver that GETs the backend's ``/internal/v1/mcp/cards``.

    Network errors are swallowed and logged; the empty-mapping fallback means
    run-start is never a single point of failure. ``off`` connectors are
    already omitted from the listing server-side, so they never appear here.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        backend_url: str,
        service_token: str,
    ) -> None:
        self._client = http_client
        self._backend_url = backend_url.rstrip("/")
        self._service_token = service_token

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, str]:
        """Fetch cards and project ``{server_id: access_mode}``; empty on failure."""
        params = {"org_id": org_id, "user_id": user_id}
        headers = {
            _Headers.SERVICE_TOKEN: self._service_token,
            _Headers.ORG: org_id,
            _Headers.USER: user_id,
        }
        url = f"{self._backend_url}/internal/v1/mcp/cards"
        try:
            if self._client is not None:
                response = await self._client.get(
                    url, params=params, headers=headers, timeout=_FETCH_TIMEOUT_SECONDS
                )
            else:
                async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
                    response = await client.get(url, params=params, headers=headers)
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            _LOGGER.warning(
                "connector_access_modes.fetch_failed",
                extra={
                    "metadata": {
                        "org_id": org_id,
                        "user_id": user_id,
                        "error_class": exc.__class__.__name__,
                    }
                },
            )
            return {}
        if response.status_code >= 400:
            _LOGGER.warning(
                "connector_access_modes.fetch_non_2xx",
                extra={
                    "metadata": {
                        "org_id": org_id,
                        "user_id": user_id,
                        "status_code": response.status_code,
                    }
                },
            )
            return {}
        try:
            body = response.json()
        except ValueError:
            return {}
        if not isinstance(body, dict):
            return {}
        servers = body.get("servers", [])
        if not isinstance(servers, list):
            return {}
        modes: dict[str, str] = {}
        for card in servers:
            if not isinstance(card, dict):
                continue
            server_id = card.get("server_id")
            access_mode = card.get("access_mode")
            if isinstance(server_id, str) and isinstance(access_mode, str):
                modes[server_id] = access_mode
        return modes


class NullConnectorAccessModesResolver:
    """No-op resolver that always returns an empty mapping.

    Used when the trusted-backend lane is not configured. Consumers treat an
    empty mapping as "no per-connector gate this run", which is safe because
    the authoritative backend gate still runs on every tool call.
    """

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, str]:
        return {}


class ConnectorAccessModesResolverFactory:
    """Select the appropriate resolver from environment configuration."""

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> ConnectorAccessModesResolver:
        """Return the best available resolver given the current environment."""
        backend_url = (
            os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
            or os.environ.get(_Env.BACKEND_BASE_URL_FALLBACK, "").strip()
        )
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token:
            return NullConnectorAccessModesResolver()
        return HttpConnectorAccessModesResolver(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "ConnectorAccessModesResolver",
    "ConnectorAccessModesResolverFactory",
    "HttpConnectorAccessModesResolver",
    "NullConnectorAccessModesResolver",
]
