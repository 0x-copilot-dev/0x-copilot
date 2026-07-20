"""Run-start resolver for the per-(org, user) policy snapshot.

Fetches ``/internal/v1/policies/runtime`` once at run-create time and packs
the result into ``AgentRuntimeContext.user_policies_json`` so the tool-use
gate, memory authoriser, and retention resolver all read from the same frozen
snapshot for the lifetime of the run. Falls back to an empty dict when the
backend lane is not configured so run-start is never blocked on policy availability.

BYOK provider keys: the snapshot may carry an optional top-level
``provider_keys`` mapping (``{"openai": "<decrypted>", ...}``). Those values
are plaintext credentials and must never reach a persisted surface —
:class:`ProviderKeysParser` splits them out of the snapshot before the rest is
stored on ``AgentRuntimeContext.user_policies_json`` (which IS persisted in
run records and outbox payloads). Because queue commands round-trip through
``model_dump(mode="json")`` (which excludes the in-memory ``provider_keys``
context field), the worker re-attaches keys at claim time via
:class:`ProviderKeysHydrator`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import httpx

from agent_runtime.execution.contracts import AgentRuntimeContext, JsonObject

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
# BYOK provider keys — snapshot splitting + worker-side re-hydration
# ---------------------------------------------------------------------------


class ProviderKeysParser:
    """Split the non-persistable ``provider_keys`` mapping out of a snapshot.

    The backend's aggregate route returns decrypted per-user provider keys as
    an optional top-level ``provider_keys`` field. Everything else in the
    snapshot is persisted verbatim in run records, so the keys must be
    removed before the snapshot is stored — they ride the separate in-memory
    ``AgentRuntimeContext.provider_keys`` field instead.

    Provider slugs are normalized to the runtime's canonical form
    (``ModelConfigResolver`` normalizes ``google`` → ``gemini``) so lookups
    by ``model_profile.provider`` always hit. Malformed entries (non-string
    slugs or values, empty strings) are dropped silently — a broken key row
    must degrade to "no user key", never block run-start.
    """

    SNAPSHOT_KEY = "provider_keys"
    # Wire slugs (settings surface: openai | anthropic | google) → runtime
    # provider slugs (post ``ModelConfigResolver._normalize_provider``).
    _PROVIDER_ALIASES: Mapping[str, str] = {"google": "gemini"}

    @classmethod
    def split(cls, snapshot: JsonObject) -> tuple[dict[str, str], JsonObject]:
        """Return ``(provider_keys, snapshot_without_keys)``.

        The input dict is never mutated. When the snapshot carries no
        ``provider_keys`` field the original dict is returned unchanged so
        equality-based tests and callers see an identical object.
        """

        if cls.SNAPSHOT_KEY not in snapshot:
            return {}, snapshot
        sanitized: JsonObject = {
            key: value for key, value in snapshot.items() if key != cls.SNAPSHOT_KEY
        }
        raw = snapshot.get(cls.SNAPSHOT_KEY)
        if not isinstance(raw, Mapping):
            return {}, sanitized
        keys: dict[str, str] = {}
        for provider, key in raw.items():
            if not isinstance(provider, str) or not isinstance(key, str):
                continue
            slug = provider.strip().lower()
            value = key.strip()
            if not slug or not value:
                continue
            keys[cls._PROVIDER_ALIASES.get(slug, slug)] = value
        return keys, sanitized


class ProviderKeysHydrator:
    """Re-attach non-persisted provider keys to a queue-deserialised context.

    ``RuntimeRunCommand`` round-trips through ``model_dump(mode="json")`` on
    every queue backend (including in-memory), which drops the excluded
    ``provider_keys`` field. The worker calls :meth:`hydrate` right before
    building the harness so model construction sees the user's keys without
    the keys ever being written to the outbox, run record, or events.
    """

    def __init__(self, *, resolver: UserPoliciesResolver) -> None:
        self._resolver = resolver

    async def hydrate(self, context: AgentRuntimeContext) -> AgentRuntimeContext:
        """Return a context copy carrying the user's provider keys, if any.

        No-op (returns the same object) when keys are already present or the
        resolver yields none — the run degrades to deployment env keys.
        """

        if context.provider_keys:
            return context
        snapshot = await self._resolver.resolve(
            org_id=context.org_id, user_id=context.user_id
        )
        keys, _ = ProviderKeysParser.split(snapshot)
        if not keys:
            return context
        return context.model_copy(update={"provider_keys": keys})


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
            if backend_url or service_token:
                # Partial configuration is almost always a deployment bug: the
                # snapshot (and with it BYOK provider keys) silently degrades
                # to empty and every keyless run fails at create. Say so once,
                # loudly, at wiring time instead of per-run.
                _LOGGER.warning(
                    "user-policies resolver disabled by partial configuration "
                    "(backend_base_url_set=%s service_token_set=%s "
                    "http_client_set=%s); BYOK provider keys will NOT reach runs",
                    bool(backend_url),
                    bool(service_token),
                    http_client is not None,
                )
            return NullUserPoliciesResolver()
        return HttpUserPoliciesResolver(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "HttpUserPoliciesResolver",
    "NullUserPoliciesResolver",
    "ProviderKeysHydrator",
    "ProviderKeysParser",
    "UserPoliciesResolver",
    "UserPoliciesResolverFactory",
]
