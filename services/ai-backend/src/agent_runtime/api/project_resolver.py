"""Cross-service resolver for a project's ``default_connector_allowlist``.

P6.5-A2 — inheritance hook at conversation create.

When a new conversation is created with ``project_id`` set AND the caller
did not pass an explicit ``enabled_connectors`` map, the coordinator
inherits the project's ``default_connector_allowlist`` (per
``docs/atlas-new-design/destinations/projects-extensions-prd.md`` §5.4).

The allowlist field is owned by the Projects destination in the
``backend`` service. ai-backend MUST NOT import backend's Python — every
cross-service read goes through this Port (root ``CLAUDE.md`` service-
boundary rule).

The pattern mirrors :mod:`agent_runtime.api.user_policies_resolver` and
:mod:`agent_runtime.api.inbox_producer`:

* :class:`ProjectResolverPort` — pure-protocol surface; tests inject a fake.
* :class:`HttpProjectResolver` — production impl. GET
  ``/internal/v1/projects/{project_id}/connector-allowlist`` with the
  service-token + ``x-enterprise-org-id`` / ``x-enterprise-user-id``
  headers backend's trusted lane requires.
* :class:`NullProjectResolver` — no-op for tests and the trusted-backend
  lane being unconfigured. Returns ``None`` so the coordinator falls
  through to workspace defaults (existing Phase 1 behavior).
* :class:`ProjectResolverFactory` — env-driven selector; identical
  ``BACKEND_BASE_URL`` + ``ENTERPRISE_SERVICE_TOKEN`` gate as siblings.

Hard rules baked into the contract:

* No PII in this module — only the project id flows on the wire; we
  never log project names, descriptions, or member identifiers.
* Cross-tenant guard — the ``x-enterprise-org-id`` header is sent on
  every call. Backend enforces tenant isolation server-side; the client
  never trusts the response if it would imply a cross-tenant leak (we
  scope by the request's org_id, full stop).
* The resolver returns ``None`` on every failure mode (network,
  non-2xx, malformed JSON, missing column, unknown project). The
  coordinator interprets ``None`` as "no project default; fall through
  to workspace defaults" — never a hard error at conversation create.

Return shape:

* ``None`` — no project default (the project's allowlist is ``null``,
  the column doesn't exist yet, the resolver isn't configured, or the
  fetch failed). Caller falls through to workspace defaults.
* ``()`` (empty tuple) — explicit empty allowlist. The PRD reads this
  as "no connectors allowed in this project" — caller seeds the
  conversation with an empty connector map and stops there.
* ``("salesforce", "gmail", ...)`` — allowlist of connector slugs
  (``ConnectorSlug`` per the PRD §5.1). Caller materializes each slug
  as an active entry in ``enabled_connectors``.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import httpx


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


class _ResponseFields:
    """Names of the fields the resolver reads from the backend response."""

    ALLOWLIST = "default_connector_allowlist"


_FETCH_TIMEOUT_SECONDS = 5.0
_INTERNAL_ALLOWLIST_PATH_TEMPLATE = (
    "/internal/v1/projects/{project_id}/connector-allowlist"
)


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class ProjectResolverPort(Protocol):
    """Port for fetching a project's connector allowlist at conversation create.

    Implementations must return ``None`` (never raise) when the backend
    lane is not configured, the project does not exist for this tenant,
    or the fetch fails — the runtime degrades to workspace defaults
    rather than refusing the create.
    """

    async def fetch_connector_allowlist(
        self,
        *,
        org_id: str,
        user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        """Return the project's allowlist (possibly empty), or ``None`` if no default."""


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


class HttpProjectResolver:
    """Production resolver that GETs the backend's internal project endpoint.

    The injected ``httpx.AsyncClient`` lifecycle is the caller's
    responsibility: the API service holds a long-lived client; tests
    inject a one-shot client. Network and HTTP errors are swallowed and
    logged so an unreachable backend never blocks conversation create —
    the coordinator falls through to workspace defaults on ``None``.

    The cross-tenant guard is enforced server-side via the
    ``x-enterprise-org-id`` header; the response is treated as scoped
    to that org and trusted only insofar as the backend validated it.
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

    async def fetch_connector_allowlist(
        self,
        *,
        org_id: str,
        user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        """Fetch the project's allowlist, returning ``None`` on every failure mode."""

        url = self._backend_url + _INTERNAL_ALLOWLIST_PATH_TEMPLATE.format(
            project_id=project_id
        )
        try:
            response = await self._client.get(
                url,
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
                "project_resolver.fetch_failed",
                extra={
                    "metadata": {
                        "org_id": org_id,
                        "project_id": project_id,
                        "error_class": exc.__class__.__name__,
                    }
                },
            )
            return None
        if response.status_code == 404:
            # Project missing for this tenant — no default; let the
            # coordinator fall through to workspace defaults rather than
            # refusing the create.
            return None
        if response.status_code >= 400:
            _LOGGER.warning(
                "project_resolver.fetch_non_2xx",
                extra={
                    "metadata": {
                        "org_id": org_id,
                        "project_id": project_id,
                        "status_code": response.status_code,
                    }
                },
            )
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        raw = body.get(_ResponseFields.ALLOWLIST)
        return _AllowlistCoercer.coerce(raw)


class NullProjectResolver:
    """No-op resolver that always returns ``None``.

    Used when the trusted-backend lane is not configured (or in tests).
    Consumers fall through to workspace defaults — the existing Phase 1
    behavior before §5.4 inheritance landed.
    """

    async def fetch_connector_allowlist(
        self,
        *,
        org_id: str,
        user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        """Return ``None`` unconditionally."""
        return None


# ---------------------------------------------------------------------------
# Coercion — wire payload → typed tuple
# ---------------------------------------------------------------------------


class _AllowlistCoercer:
    """Validate + normalize the raw allowlist field from the backend response.

    Encapsulates the wire-shape rules so :class:`HttpProjectResolver`
    stays as a thin transport. Returns ``None`` on every malformed shape
    — the resolver contract treats malformed remote payloads as "no
    default" (fail-open to workspace defaults, never crash create).
    """

    @classmethod
    def coerce(cls, value: object) -> tuple[str, ...] | None:
        """Coerce a remote ``default_connector_allowlist`` field into the typed shape."""

        if value is None:
            return None
        if not isinstance(value, list | tuple):
            # The backend should never send this; treat as "no default"
            # rather than crashing the create.
            return None
        slugs: list[str] = []
        for entry in value:
            if not isinstance(entry, str):
                return None
            stripped = entry.strip()
            if not stripped:
                return None
            slugs.append(stripped)
        return tuple(slugs)


# ---------------------------------------------------------------------------
# Factory — picks the right resolver from env
# ---------------------------------------------------------------------------


class ProjectResolverFactory:
    """Select the appropriate resolver from environment configuration.

    Returns :class:`NullProjectResolver` when ``BACKEND_BASE_URL``,
    ``ENTERPRISE_SERVICE_TOKEN``, or an ``http_client`` are missing, so
    callers always get a functioning resolver regardless of the
    deployment configuration.
    """

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> ProjectResolverPort:
        """Return the best available resolver given the current environment."""
        backend_url = os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token or http_client is None:
            return NullProjectResolver()
        return HttpProjectResolver(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "HttpProjectResolver",
    "NullProjectResolver",
    "ProjectResolverFactory",
    "ProjectResolverPort",
]
