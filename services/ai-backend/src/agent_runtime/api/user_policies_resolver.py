"""Run-start resolver for the per-(org, user) policy snapshot (PR 8.0.5).

Calls backend's ``/internal/v1/policies/runtime`` aggregate route once
when ``RunService.create_run`` is composing the ``AgentRuntimeContext``,
and packs the response into ``AgentRuntimeContext.user_policies_json``
so every downstream consumer (tool-use gate, memory authorizer,
provider-kwargs builder, retention resolver) reads from the same
frozen snapshot for the lifetime of the run.

Why a single aggregate fetch:

* Two HTTP calls per run cost twice the tail latency on cold start.
* The two endpoints already exist (``/internal/v1/policies/tool-use``
  + ``/internal/v1/policies/privacy``) — backend's aggregate route is
  a 30-LOC fan-out + join over them, so we don't duplicate the
  validation logic on this side.

Why a Protocol + a default implementation:

* Tests pin a deterministic in-process resolver (no network) without
  monkey-patching httpx.
* The default implementation reads ``BACKEND_BASE_URL`` +
  ``ENTERPRISE_SERVICE_TOKEN`` and falls back to "no policy" (= empty
  dict, = deployment defaults) when either is missing — exactly the
  same shape ``MembershipResolverUnavailable`` uses for the workspace
  membership resolver.
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
    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class UserPoliciesResolver(Protocol):
    """Resolve the per-(org, user) policy snapshot at run start.

    Implementations MUST return the empty dict (not raise) when the
    backend lane is not configured or the fetch fails — the runtime
    falls back to deployment defaults rather than refusing the run.
    """

    async def resolve(self, *, org_id: str, user_id: str) -> JsonObject:
        """Return ``{"tool_use": {...}, "privacy": {...}}`` or ``{}``."""


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


class HttpUserPoliciesResolver:
    """Production resolver: GET backend's aggregate runtime-policy route.

    Uses an injected ``httpx.AsyncClient`` so reuse across runs is
    a question for the caller (the API service holds its long-lived
    client; tests inject a one-shot client). Failures are swallowed
    + logged: an unreachable backend must not break run-start.
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
        # The aggregate route returns ``{"tool_use": {...}, "privacy": {...}}``;
        # we trust shape (RuntimePolicyResponse pydantic model on backend
        # already validated it) and pass the dict straight to consumers.
        return body


class NullUserPoliciesResolver:
    """Resolver that always returns ``{}``. Used when the trusted-backend
    lane isn't configured (dev / single-process runs). Consumers fall
    through to deployment defaults exactly as they did pre-8.0.5."""

    async def resolve(self, *, org_id: str, user_id: str) -> JsonObject:
        return {}


# ---------------------------------------------------------------------------
# Factory — picks the right resolver from env
# ---------------------------------------------------------------------------


class UserPoliciesResolverFactory:
    """Pick a resolver based on env. Mirrors the membership-resolver
    factory pattern (PR 1.4.1) so the wiring is grep-able."""

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> UserPoliciesResolver:
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
