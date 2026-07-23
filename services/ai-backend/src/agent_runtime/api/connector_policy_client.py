"""httpx client that persists a per-connector write-policy override (PRD-C2).

The gate-time policy choice is stored by PRD-C1 on the core backend's
``connectors`` table via ``PATCH /v1/connectors/{connector_id}/write-policy``
(``RequireScopes(RUNTIME_USE)`` + service-token identity lane). C1 keys that route
by the backend row id (``conn_…``), not the connector slug, so this client:

1. ``GET /v1/connectors?slug=<slug>`` to resolve the row id, then
2. ``PATCH /v1/connectors/{id}/write-policy`` with ``{"write_policy": ...}``.

Both hops ride the same base-URL + ``ENTERPRISE_SERVICE_TOKEN`` + org/user-header
conventions as :mod:`agent_runtime.capabilities.surfaces.backend_store`. Unlike
that best-effort render-path store, this client is on the CONSENT path: any
failure raises :class:`GatePolicyPersistError` so the coordinator can fail the
decision closed (HTTP 502) rather than record consent without its policy.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)

_LOGGER = logging.getLogger(__name__)

_CONNECTORS_PATH = "/v1/connectors"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class GatePolicyPersistError(RuntimeError):
    """Raised when the write-policy override could not be persisted.

    Carries only a safe, actionable message — never the backend's raw error.
    """


class _Env:
    """Env keys owned by the connector write-policy client."""

    BACKEND_URL = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class HttpConnectorWritePolicyClient:
    """:class:`ConnectorWritePolicyClient` over the backend connectors API."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        service_token: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = http_client
        self._service_token = service_token.strip()
        self._timeout = timeout_seconds

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str],
        http_client: httpx.AsyncClient,
    ) -> "HttpConnectorWritePolicyClient":
        return cls(
            base_url=environ.get(_Env.BACKEND_URL, "").strip(),
            http_client=http_client,
            service_token=environ.get(_Env.SERVICE_TOKEN, ""),
        )

    async def put_override(
        self,
        *,
        org_id: str,
        user_id: str,
        connector_slug: str,
        write_policy: str,
    ) -> None:
        """Resolve the connector row by slug and PATCH its write policy.

        Raises :class:`GatePolicyPersistError` on a missing base URL, an unknown
        connector, or any non-2xx / transport failure — the coordinator maps that
        to a 502 and leaves the decision unrecorded (fail closed).
        """

        if not self._base_url:
            raise GatePolicyPersistError("connector policy backend not configured")
        connector_id = await self._resolve_connector_id(
            org_id=org_id, user_id=user_id, slug=connector_slug
        )
        params = {"org_id": org_id, "user_id": user_id}
        url = f"{self._base_url}{_CONNECTORS_PATH}/{connector_id}/write-policy"
        try:
            response = await self._client.patch(
                url,
                params=params,
                json={"write_policy": write_policy},
                headers=self._headers(org_id=org_id, user_id=user_id),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _LOGGER.warning(
                "[surfaces_v2] write_policy patch failed slug=%s", connector_slug
            )
            raise GatePolicyPersistError(
                "failed to persist connector write policy"
            ) from exc

    async def _resolve_connector_id(
        self, *, org_id: str, user_id: str, slug: str
    ) -> str:
        """Return the backend connector row id for ``slug`` (raises when absent)."""

        params = {"org_id": org_id, "user_id": user_id, "slug": slug}
        try:
            response = await self._client.get(
                f"{self._base_url}{_CONNECTORS_PATH}",
                params=params,
                headers=self._headers(org_id=org_id, user_id=user_id),
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GatePolicyPersistError(
                "failed to resolve connector for write policy"
            ) from exc
        connectors = payload.get("connectors") if isinstance(payload, dict) else None
        if isinstance(connectors, list):
            for row in connectors:
                if isinstance(row, dict) and row.get("slug") == slug:
                    row_id = row.get("id")
                    if isinstance(row_id, str) and row_id:
                        return row_id
        raise GatePolicyPersistError("connector not found for write policy")

    def _headers(self, *, org_id: str, user_id: str) -> dict[str, str]:
        if not self._service_token:
            return {}
        return {
            SERVICE_TOKEN_HEADER: self._service_token,
            ORG_HEADER: org_id,
            USER_HEADER: user_id,
        }


_SHARED_ASYNC_CLIENT: httpx.AsyncClient | None = None


def _shared_async_client() -> httpx.AsyncClient:
    global _SHARED_ASYNC_CLIENT
    if _SHARED_ASYNC_CLIENT is None:
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)
    return _SHARED_ASYNC_CLIENT


def build_connector_write_policy_client(
    environ: Mapping[str, str],
) -> HttpConnectorWritePolicyClient | None:
    """Compose the client from env, or ``None`` when no backend URL is set.

    ``None`` keeps the coordinator's gate-policy path fail-closed (a write_policy
    on a decision then 502s) without wiring a client the deployment can't reach.
    """

    base_url = environ.get(_Env.BACKEND_URL, "").strip()
    if not base_url:
        return None
    return HttpConnectorWritePolicyClient.from_env(
        environ=environ,
        http_client=_shared_async_client(),
    )


__all__ = [
    "GatePolicyPersistError",
    "HttpConnectorWritePolicyClient",
    "build_connector_write_policy_client",
]
