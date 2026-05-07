"""Run-start resolver for catalog connectors the agent may suggest.

Calls backend's ``/internal/v1/me/suggestible-connectors`` once when
``RunService.create_run`` is composing the ``AgentRuntimeContext``,
and packs the response into
``AgentRuntimeContext.suggested_connectors`` so the system prompt
template (in ``execution.factory``) and the discovery service
(``api.mcp_discovery_service``) read from the same frozen snapshot
for the lifetime of the run.

Mirrors the ``UserPoliciesResolver`` shape so the wiring is grep-able
and the dev-mode "no service token configured" fallback behaves
identically — empty tuple when the trusted-backend lane isn't set up,
deployment continues with no agent suggestions rather than failing
the run.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Protocol, runtime_checkable

import httpx

from agent_runtime.execution.contracts import CatalogSuggestionCard

_LOGGER = logging.getLogger(__name__)


class _Env:
    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    # PR 4.4.7 — ai-backend dev startup historically only set
    # ``MCP_BACKEND_REGISTRY_URL``; ``BACKEND_BASE_URL`` was reserved for
    # production. Accept the dev var as a fallback so progressive
    # discovery works in ``make dev`` without a Makefile change.
    BACKEND_BASE_URL_FALLBACK = "MCP_BACKEND_REGISTRY_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 5.0


@runtime_checkable
class SuggestibleConnectorsResolver(Protocol):
    """Resolve the suggestible-connectors snapshot at run start.

    Implementations MUST return an empty tuple (not raise) when the
    backend lane is not configured or the fetch fails — the runtime
    falls back to "no suggestions" rather than refusing the run.
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
    """Production resolver: GET backend's suggestible-connectors route.

    Same lifecycle as ``HttpUserPoliciesResolver`` — injected long-lived
    ``httpx.AsyncClient``, swallow network errors, log them, return
    empty tuple. Run-start must not be a single point of failure.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        backend_url: str,
        service_token: str,
    ) -> None:
        # ``http_client=None`` means "create a short-lived client per
        # resolve". The default RuntimeApiAppFactory wiring uses this
        # path to avoid threading a long-lived client through every
        # service constructor (mirrors the membership resolver's
        # ``_httpx_membership_fetcher``). Tests inject an explicit
        # client backed by ``httpx.MockTransport``.
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
                # Backend should be the source of truth on shape; an
                # individual malformed row gets dropped rather than
                # poisoning the rest of the snapshot.
                continue
        return tuple(cards)


class NullSuggestibleConnectorsResolver:
    """Resolver that always returns empty. Used when the trusted-backend
    lane isn't configured (dev / single-process / tests). Consumers
    treat empty as "no suggestions surfaced this run", which is the
    safe default."""

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        exclude_paused: Iterable[str],
    ) -> tuple[CatalogSuggestionCard, ...]:
        return ()


class SuggestibleConnectorsResolverFactory:
    """Pick a resolver based on env. Mirrors
    ``UserPoliciesResolverFactory`` so the two run-start fetches are
    wired identically.

    ``http_client`` is optional — when omitted the factory still wires
    a working ``HttpSuggestibleConnectorsResolver`` whose ``resolve``
    creates a per-call short-lived ``httpx.AsyncClient``. That matches
    the ``HttpWorkspaceMembershipResolver`` lifecycle (per-call client)
    so callers don't have to plumb a long-lived client through service
    construction. Pass an explicit ``http_client`` only for tests or
    advanced production wiring that wants connection reuse.
    """

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> SuggestibleConnectorsResolver:
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
