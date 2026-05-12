"""Run-start resolver for catalog connectors the agent may proactively suggest.

Fetches ``/internal/v1/me/suggestible-connectors`` once at run-create time and
packs the result into ``AgentRuntimeContext.suggested_connectors`` so the system
prompt template and discovery service read from the same frozen snapshot for the
lifetime of the run. Falls back to an empty tuple when the backend lane is not
configured so run-start is never blocked on connector availability.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Protocol, runtime_checkable

import httpx

from agent_runtime.execution.contracts import CatalogSuggestionCard

_LOGGER = logging.getLogger(__name__)


class _Env:
    """Environment variable names for backend URL and service token."""

    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    # Accepted as a fallback so ``make dev`` works without needing BACKEND_BASE_URL;
    # MCP_BACKEND_REGISTRY_URL was historically the dev-only var name.
    BACKEND_BASE_URL_FALLBACK = "MCP_BACKEND_REGISTRY_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    """Service-to-service header names for the trusted backend lane."""

    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 5.0


@runtime_checkable
class SuggestibleConnectorsResolver(Protocol):
    """Port for fetching the per-run suggestible-connector snapshot.

    Implementations must return an empty tuple (never raise) when the
    backend lane is unavailable so run-start degrades gracefully to "no
    suggestions" rather than failing.
    """

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        exclude_paused: Iterable[str],
    ) -> tuple[CatalogSuggestionCard, ...]:
        """Return suggestible cards or an empty tuple."""


class HttpSuggestibleConnectorsResolver:
    """Production resolver that GETs the backend's suggestible-connectors endpoint.

    Network errors are swallowed and logged; the empty-tuple fallback means
    run-start is never a single point of failure. Pass an explicit
    ``http_client`` for connection reuse in production; omit it to get a
    per-call short-lived client (the default for dev and tests using
    ``httpx.MockTransport``).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        backend_url: str,
        service_token: str,
    ) -> None:
        # When http_client is None, resolve() creates a short-lived client per call
        # to avoid threading a long-lived client through every constructor.
        self._client = http_client
        self._backend_url = backend_url.rstrip("/")
        self._service_token = service_token

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        exclude_paused: Iterable[str],
    ) -> tuple[CatalogSuggestionCard, ...]:
        """Fetch and validate suggestible connector cards, returning empty on any failure."""
        excluded = ",".join(piece for piece in exclude_paused if piece)
        params = {
            "org_id": org_id,
            "user_id": user_id,
            "exclude_paused": excluded,
        }
        headers = {
            _Headers.SERVICE_TOKEN: self._service_token,
            _Headers.ORG: org_id,
            _Headers.USER: user_id,
        }
        url = f"{self._backend_url}/internal/v1/me/suggestible-connectors"
        try:
            if self._client is not None:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=_FETCH_TIMEOUT_SECONDS,
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
                "suggestible_connectors.fetch_failed",
                extra={
                    "metadata": {
                        "org_id": org_id,
                        "user_id": user_id,
                        "error_class": exc.__class__.__name__,
                    }
                },
            )
            return ()
        if response.status_code >= 400:
            _LOGGER.warning(
                "suggestible_connectors.fetch_non_2xx",
                extra={
                    "metadata": {
                        "org_id": org_id,
                        "user_id": user_id,
                        "status_code": response.status_code,
                    }
                },
            )
            return ()
        try:
            body = response.json()
        except ValueError:
            return ()
        if not isinstance(body, dict):
            return ()
        entries = body.get("entries", [])
        if not isinstance(entries, list):
            return ()
        cards: list[CatalogSuggestionCard] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                cards.append(
                    CatalogSuggestionCard.model_validate(
                        {
                            "slug": entry.get("slug"),
                            "display_name": entry.get("display_name"),
                            "description": entry.get("description") or "",
                            "scopes_summary": entry.get("scopes_summary"),
                            "brand_color": entry.get("brand_color"),
                            "requires_pre_registered_client": bool(
                                entry.get("requires_pre_registered_client", False)
                            ),
                        }
                    )
                )
            except Exception:
                # Drop individual malformed rows rather than poisoning the whole
                # snapshot — the backend is the authoritative source on shape.
                continue
        return tuple(cards)


class NullSuggestibleConnectorsResolver:
    """No-op resolver that always returns an empty tuple.

    Used when the trusted-backend lane is not configured. Consumers treat
    an empty tuple as "no suggestions this run", which is the safe default.
    """

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        exclude_paused: Iterable[str],
    ) -> tuple[CatalogSuggestionCard, ...]:
        return ()


class SuggestibleConnectorsResolverFactory:
    """Select the appropriate resolver from environment configuration.

    When ``BACKEND_BASE_URL`` (or the dev fallback) and ``ENTERPRISE_SERVICE_TOKEN``
    are set, returns an ``HttpSuggestibleConnectorsResolver``; otherwise returns a
    ``NullSuggestibleConnectorsResolver``. Pass an explicit ``http_client`` only for
    tests or production wiring that needs connection reuse — omitting it lets
    ``resolve`` create per-call clients, which is the default for dev.
    """

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> SuggestibleConnectorsResolver:
        """Return the best available resolver given the current environment."""
        backend_url = (
            os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
            or os.environ.get(_Env.BACKEND_BASE_URL_FALLBACK, "").strip()
        )
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token:
            return NullSuggestibleConnectorsResolver()
        return HttpSuggestibleConnectorsResolver(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "HttpSuggestibleConnectorsResolver",
    "NullSuggestibleConnectorsResolver",
    "SuggestibleConnectorsResolver",
    "SuggestibleConnectorsResolverFactory",
]
