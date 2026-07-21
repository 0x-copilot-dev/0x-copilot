"""``backend-http`` SurfaceSpec store adapter for the team/web deployment (PRD-08).

The desktop single-user host keeps the durable :class:`FileSurfaceSpecStore`;
the shared team deployment persists generated specs in the core backend's
org-scoped registry (``services/backend`` ``surface_specs`` module). This module
is the client half: :class:`BackendHttpSurfaceSpecStore` speaks the internal
``/internal/v1/surfaces/specs`` endpoints via the same internal-client
conventions as :mod:`agent_runtime.capabilities.mcp.backend_provider` (base URL +
``ENTERPRISE_SERVICE_TOKEN`` + org/user headers).

Two properties make an HTTP-backed store safe on the render path:

* **The store port is synchronous** (:class:`SurfaceSpecStorePort` — frozen in
  PRD-07). The projector's rung-2 read and the generator both call it without
  ``await``, so this adapter uses a synchronous :class:`httpx.Client`.
* **A read-through, per-process cache with a TTL** (plan D10, 10 min default)
  so repeated render-path lookups never hammer the backend. Both hits *and*
  misses are cached within the TTL; a local ``put`` refreshes the cache in place
  so a freshly generated spec is served without a round-trip.

Every network path is best-effort: any HTTP failure degrades to a miss (``get``
/ ``get_stored`` return ``None``) or a dropped write (``put`` logs and returns),
never an exception on the surface path. Generation-failure recording has no
team-registry endpoint in this PRD (a non-goal); ``record_failure`` is a local
no-op and ``has_failure`` is always ``False``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

import httpx
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec
from agent_runtime.capabilities.surfaces.store import (
    InMemorySurfaceSpecStore,
    SpecKey,
    StoredSpec,
    SurfaceSpecStorePort,
)

_LOGGER = logging.getLogger(__name__)


class _Env:
    """Env keys owned by the backend-http surface-spec store."""

    STORE_BACKEND = "SURFACE_SPEC_STORE_BACKEND"
    # Base URL of the core backend's internal API. A dedicated override, then
    # the shared ``BACKEND_BASE_URL`` the rest of the runtime already uses.
    BACKEND_URL = "SURFACE_SPEC_BACKEND_URL"
    FALLBACK_BACKEND_URL = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"
    CACHE_TTL_SECONDS = "SURFACE_SPEC_CACHE_TTL_SECONDS"


_SPECS_PATH = "/internal/v1/surfaces/specs"
_DEFAULT_TTL_SECONDS = 600.0
_DEFAULT_TIMEOUT_SECONDS = 10.0
_GENERATED_ORIGIN = "generated"


# A lazily-built, process-shared sync client so per-run stores amortise
# connections instead of each opening (and leaking) their own. Tests inject
# their own client and never touch this.
_SHARED_CLIENT: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None:
        _SHARED_CLIENT = httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
    return _SHARED_CLIENT


@dataclass
class _CacheEntry:
    """A cached lookup result (present or absent) with its expiry."""

    value: object
    expires_at: float


class BackendHttpSurfaceSpecStore:
    """:class:`SurfaceSpecStorePort` backed by the core backend's spec registry."""

    def __init__(
        self,
        *,
        base_url: str,
        org_id: str,
        user_id: str,
        http_client: httpx.Client | None = None,
        service_token: str = "",
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._org_id = org_id
        self._user_id = user_id
        self._client = http_client if http_client is not None else _shared_client()
        self._service_token = service_token.strip()
        self._ttl = max(0.0, ttl_seconds)
        self._timeout = timeout_seconds
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str],
        org_id: str,
        user_id: str,
        http_client: httpx.Client | None = None,
    ) -> "BackendHttpSurfaceSpecStore":
        base_url = (
            environ.get(_Env.BACKEND_URL, "").strip()
            or environ.get(_Env.FALLBACK_BACKEND_URL, "").strip()
        )
        ttl = _parse_ttl(environ.get(_Env.CACHE_TTL_SECONDS))
        return cls(
            base_url=base_url,
            org_id=org_id,
            user_id=user_id,
            http_client=http_client,
            service_token=environ.get(_Env.SERVICE_TOKEN, ""),
            ttl_seconds=ttl,
        )

    # -- PRD-02 projector read seam ------------------------------------------

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        """Return the latest cached spec for ``(server, tool)`` or ``None``."""

        slug_server = builtin.server_slug(server)
        slug_tool = builtin.tool_slug(tool)
        cache_key = f"tool:{slug_server}:{slug_tool}"
        hit, cached = self._cache_lookup(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        view = self._fetch_view(params={"server": slug_server, "tool": slug_tool})
        spec = self._view_to_spec(view)
        self._cache_put(cache_key, spec)
        return spec

    # -- PRD-07 generation store ---------------------------------------------

    def get_stored(self, key: SpecKey) -> StoredSpec | None:
        """Return the stored spec for the full ``key`` or ``None``."""

        cache_key = f"key:{key.digest()}"
        hit, cached = self._cache_lookup(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        view = self._fetch_view(
            params={
                "server": key.server,
                "tool": key.tool,
                "shape_hash": key.output_shape_hash,
                "schema_version": key.spec_schema_version,
                "skill_version": key.skill_version,
            }
        )
        stored = self._view_to_stored(view)
        self._cache_put(cache_key, stored)
        return stored

    def put(self, key: SpecKey, stored: StoredSpec) -> None:
        """Persist ``stored`` under ``key`` via PUT; refresh the local cache.

        Best-effort: an HTTP failure is logged and dropped so a generation task
        never crashes. On success the caches for both the full key and the
        coarse ``(server, tool)`` read are refreshed so the just-generated spec
        is served without a round-trip.
        """

        body = {
            "server": key.server,
            "tool": key.tool,
            "output_shape_hash": key.output_shape_hash,
            "spec_schema_version": key.spec_schema_version,
            "skill_version": key.skill_version,
            "origin": _GENERATED_ORIGIN,
            "generator_model": stored.generator_model,
            "spec": stored.spec.model_dump(mode="json", exclude_none=True),
        }
        try:
            response = self._client.put(
                f"{self._base_url}{_SPECS_PATH}",
                params=self._identity_params(),
                json=body,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            _LOGGER.warning("[surfaces.backend_store] put_failed key=%s", key.digest())
            return
        # Serve the fresh spec from cache immediately (in-process upgrade).
        self._cache_put(f"key:{key.digest()}", stored)
        self._cache_put(f"tool:{key.server}:{key.tool}", stored.spec)

    def record_failure(self, key: SpecKey, reason: str, raw_output: str) -> None:
        """No-op: the team registry has no failure endpoint in this PRD."""

        del key, reason, raw_output

    def has_failure(self, key: SpecKey) -> bool:
        """Always ``False``: failures are not tracked in the team registry."""

        del key
        return False

    # -- internals ------------------------------------------------------------

    def _fetch_view(self, *, params: Mapping[str, object]) -> dict[str, object] | None:
        """GET the registry; return the ``spec`` view dict, or ``None`` on miss/error."""

        if not self._base_url:
            return None
        query = {**self._identity_params(), **params}
        try:
            response = self._client.get(
                f"{self._base_url}{_SPECS_PATH}",
                params=query,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            _LOGGER.warning(
                "[surfaces.backend_store] get_failed params=%s", dict(params)
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        view = payload.get("spec")
        return view if isinstance(view, dict) else None

    @staticmethod
    def _view_to_spec(view: dict[str, object] | None) -> SurfaceSpec | None:
        if view is None:
            return None
        raw_spec = view.get("spec")
        if not isinstance(raw_spec, dict):
            return None
        try:
            return SurfaceSpec.model_validate(raw_spec)
        except Exception:  # noqa: BLE001 - a corrupt remote spec is a miss, never a crash
            _LOGGER.warning("[surfaces.backend_store] invalid_remote_spec")
            return None

    def _view_to_stored(self, view: dict[str, object] | None) -> StoredSpec | None:
        spec = self._view_to_spec(view)
        if spec is None or view is None:
            return None
        payload = {
            "spec": spec,
            "server": view.get("server", spec.source.server),
            "tool": view.get("tool", spec.source.tool),
            "output_shape_hash": view.get("output_shape_hash", ""),
            "spec_schema_version": view.get("spec_schema_version", spec.spec_version),
            "skill_version": view.get("skill_version", 1),
            "generator_model": view.get("generator_model", ""),
        }
        created_at = view.get("created_at")
        if isinstance(created_at, str):
            payload["created_at"] = created_at
        try:
            return StoredSpec.model_validate(payload)
        except Exception:  # noqa: BLE001 - a malformed record is a miss, never a crash
            _LOGGER.warning("[surfaces.backend_store] invalid_remote_record")
            return None

    def _identity_params(self) -> dict[str, str]:
        return {"org_id": self._org_id, "user_id": self._user_id}

    def _headers(self) -> dict[str, str]:
        if not self._service_token:
            return {}
        return {
            SERVICE_TOKEN_HEADER: self._service_token,
            ORG_HEADER: self._org_id,
            USER_HEADER: self._user_id,
        }

    def _cache_lookup(self, cache_key: str) -> tuple[bool, object]:
        """Return ``(hit, value)``; ``hit`` is False when absent or expired."""

        entry = self._cache.get(cache_key)
        if entry is None:
            return False, None
        if self._clock() >= entry.expires_at:
            self._cache.pop(cache_key, None)
            return False, None
        return True, entry.value

    def _cache_put(self, cache_key: str, value: object) -> None:
        if self._ttl <= 0:
            return
        self._cache[cache_key] = _CacheEntry(
            value=value, expires_at=self._clock() + self._ttl
        )


def _parse_ttl(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return _DEFAULT_TTL_SECONDS
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def build_surface_spec_store(
    *,
    environ: Mapping[str, str],
    org_id: str | None = None,
    user_id: str | None = None,
    http_client: httpx.Client | None = None,
) -> SurfaceSpecStorePort:
    """Compose the SurfaceSpec store selected by ``SURFACE_SPEC_STORE_BACKEND``.

    Mirrors the ``RUNTIME_STORE_BACKEND`` pattern (plan D10):

    * ``memory`` — :class:`InMemorySurfaceSpecStore` (default test posture).
    * ``file``   — :class:`FileSurfaceSpecStore` (desktop single-user); falls
      back to in-memory when no file root is configured.
    * ``backend`` — :class:`BackendHttpSurfaceSpecStore` (team/web deployment).

    An unset/unknown value preserves the pre-PRD-08 auto behaviour: the durable
    file store when a root is configured, else in-memory. ``org_id`` / ``user_id``
    scope the backend-http adapter's calls; ``http_client`` is a test seam.
    """

    from agent_runtime.capabilities.surfaces.store import (  # noqa: PLC0415
        FileSurfaceSpecStore,
    )

    backend = environ.get(_Env.STORE_BACKEND, "").strip().lower()

    if backend == "backend":
        return BackendHttpSurfaceSpecStore.from_env(
            environ=environ,
            org_id=org_id or "",
            user_id=user_id or "",
            http_client=http_client,
        )
    if backend == "memory":
        return InMemorySurfaceSpecStore()
    if backend == "file":
        file_store = FileSurfaceSpecStore.from_env(dict(environ))
        return file_store if file_store is not None else InMemorySurfaceSpecStore()

    # Unset / unknown: preserve prior auto-selection (file when configured).
    file_store = FileSurfaceSpecStore.from_env(dict(environ))
    return file_store if file_store is not None else InMemorySurfaceSpecStore()


__all__ = [
    "BackendHttpSurfaceSpecStore",
    "build_surface_spec_store",
]
