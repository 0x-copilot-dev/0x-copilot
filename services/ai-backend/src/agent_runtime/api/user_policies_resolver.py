"""Run-start resolver for the per-(org, user) policy snapshot.

Fetches ``/internal/v1/policies/runtime`` once at run-create time and packs
the result into ``AgentRuntimeContext.user_policies_json`` so the tool-use
gate, memory authoriser, and retention resolver all read from the same frozen
snapshot for the lifetime of the run. Falls back to an empty dict when the
backend lane is not configured so run-start is never blocked on policy availability.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import httpx

from agent_runtime.execution.contracts import JsonObject

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — environment + network
# ---------------------------------------------------------------------------


class _Env:
    """Environment variable names for backend URL and service-token configuration."""

    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    """Service-to-service header names for the trusted backend lane."""

    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class UserPoliciesResolver(Protocol):
    """Port for fetching the per-(org, user) policy snapshot at run-create time.

    Implementations must return ``{}`` (never raise) when the backend lane is
    not configured or the fetch fails — the runtime degrades to deployment
    defaults rather than refusing the run.
    """

    async def resolve(self, *, org_id: str, user_id: str) -> JsonObject:
        """Return ``{"tool_use": {...}, "privacy": {...}}`` or ``{}`` on failure."""


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


class HttpUserPoliciesResolver:
    """Production resolver that GETs the backend's aggregate runtime-policy endpoint.

    The injected ``httpx.AsyncClient`` lifecycle is the caller's responsibility:
    the API service holds a long-lived client; tests inject a one-shot client.
    Network and HTTP errors are swallowed and logged so an unreachable backend
    never blocks run-start.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        backend_url: str,
        service_token: str,
    ) -> None:
        self._client = http_client
        self._backend_url = backend_url.rstrip("/")
        self._service_token = service_token

    async def resolve(self, *, org_id: str, user_id: str) -> JsonObject:
        """Fetch the runtime policy snapshot, returning ``{}`` on any network or HTTP error."""
        try:
            response = await self._client.get(
                f"{self._backend_url}/internal/v1/policies/runtime",
                params={"org_id": org_id, "user_id": user_id},
                headers={
                    _Headers.SERVICE_TOKEN: self._service_token,
                    _Headers.ORG: org_id,
                    _Headers.USER: user_id,
                },
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            _LOGGER.warning(
                "user_policies.fetch_failed",
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
                "user_policies.fetch_non_2xx",
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
        # The backend's aggregate route validates the sub-policy shape; trust it
        # and forward the dict directly rather than re-validating on this side.
        return body


class NullUserPoliciesResolver:
    """No-op resolver that always returns ``{}``.

    Used when the trusted-backend lane is not configured. Consumers fall
    through to deployment defaults, which is the same behaviour as before
    any policy enforcement was added.
    """

    async def resolve(self, *, org_id: str, user_id: str) -> JsonObject:
        """Return an empty policy dict unconditionally."""
        return {}


# ---------------------------------------------------------------------------
# Factory — picks the right resolver from env
# ---------------------------------------------------------------------------


class UserPoliciesResolverFactory:
    """Select the appropriate resolver from environment configuration.

    Returns ``NullUserPoliciesResolver`` when ``BACKEND_BASE_URL``,
    ``ENTERPRISE_SERVICE_TOKEN``, or an ``http_client`` are missing, so callers
    always get a functioning resolver regardless of the deployment configuration.
    """

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> UserPoliciesResolver:
        """Return the best available resolver given the current environment."""
        backend_url = os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token or http_client is None:
            return NullUserPoliciesResolver()
        return HttpUserPoliciesResolver(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "HttpUserPoliciesResolver",
    "NullUserPoliciesResolver",
    "UserPoliciesResolver",
    "UserPoliciesResolverFactory",
]
