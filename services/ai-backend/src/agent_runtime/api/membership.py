"""Workspace membership resolver for cross-user write validation.

The runtime consults this before accepting any cross-user write — currently only
approval forwarding. Two implementations are provided:

- :class:`HttpWorkspaceMembershipResolver` — production; calls the backend's
  ``/internal/v1/users/{user_id}`` endpoint over the service-token lane and
  caches results with separate positive (5 min) and negative (30 s) TTLs.
- :class:`InMemoryWorkspaceMembershipResolver` — tests/dev; backed by an
  explicit membership dict so unit tests need no HTTP fakes.

The negative TTL is intentionally short so a freshly added member clears the
cache quickly. The positive TTL is longer because the revocation cascade runs
a separate sweep that calls ``invalidate`` on revoked users directly.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class _Env:
    """Environment variable names and defaults for the membership cache TTLs."""

    POSITIVE_TTL_SECONDS = "RUNTIME_MEMBERSHIP_POSITIVE_TTL_SECONDS"
    NEGATIVE_TTL_SECONDS = "RUNTIME_MEMBERSHIP_NEGATIVE_TTL_SECONDS"
    BACKEND_BASE_URL = "BACKEND_BASE_URL"

    DEFAULT_POSITIVE_TTL_SECONDS = 300.0
    DEFAULT_NEGATIVE_TTL_SECONDS = 30.0


@runtime_checkable
class WorkspaceMembershipResolver(Protocol):
    """Port for resolving whether a user is an active member of a given org."""

    async def is_active_member(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> bool:
        """Return ``True`` iff ``user_id`` is an active member of ``org_id``.

        Any uncertainty — 5xx from the backend, malformed response — must raise
        :class:`MembershipResolverUnavailable` rather than returning ``False``
        so the API layer maps it to 503 (retryable) instead of 422 (invalid
        target), preventing a flapping backend from silently blocking forwards.
        """


class MembershipResolverUnavailable(RuntimeError):
    """Raised when the resolver cannot determine membership due to a transient failure.

    Distinct from a definitive ``False`` result so callers can return 503
    (retryable) rather than 422 (definitive invalid target).
    """


@dataclass(frozen=True)
class _CachedAnswer:
    """Immutable cache entry holding the membership result and its expiry monotonic clock value."""

    is_active: bool
    expires_at_monotonic: float


class _MembershipCache:
    """Bounded per-(org, user) TTL cache with separate positive and negative windows.

    Uses monotonic time so wall-clock adjustments don't corrupt expiry. Eviction
    prefers expired entries; when none exist the lexicographically smallest key is
    dropped to keep memory bounded without a full LRU scan.
    """

    def __init__(
        self,
        *,
        positive_ttl_seconds: float,
        negative_ttl_seconds: float,
        max_entries: int = 4096,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._positive_ttl = positive_ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._max_entries = max_entries
        self._clock = clock or time.monotonic
        self._entries: dict[tuple[str, str], _CachedAnswer] = {}

    def get(self, *, org_id: str, user_id: str) -> bool | None:
        """Return the cached membership result, or ``None`` on a miss or expired entry."""
        key = (org_id, user_id)
        cached = self._entries.get(key)
        if cached is None:
            return None
        if cached.expires_at_monotonic <= self._clock():
            # Lazy expiry: remove on first read past the deadline rather than
            # running a background sweep.
            self._entries.pop(key, None)
            return None
        return cached.is_active

    def put(self, *, org_id: str, user_id: str, is_active: bool) -> None:
        """Store a membership result with the appropriate TTL for its polarity."""
        ttl = self._positive_ttl if is_active else self._negative_ttl
        if ttl <= 0:
            return
        if len(self._entries) >= self._max_entries:
            self._evict_one()
        self._entries[(org_id, user_id)] = _CachedAnswer(
            is_active=is_active,
            expires_at_monotonic=self._clock() + ttl,
        )

    def invalidate(self, *, org_id: str, user_id: str) -> None:
        """Force-drop an entry so a just-revoked user is no longer treated as active."""
        self._entries.pop((org_id, user_id), None)

    def _evict_one(self) -> None:
        """Remove entries to make room: expired first, then the lexicographically smallest key."""
        now = self._clock()
        expired_keys = [
            key
            for key, value in self._entries.items()
            if value.expires_at_monotonic <= now
        ]
        if expired_keys:
            for key in expired_keys:
                self._entries.pop(key, None)
            return
        # No expired entries: drop the first key in insertion order (deterministic,
        # avoids a full sort scan on the hot path).
        oldest_key = next(iter(self._entries))
        self._entries.pop(oldest_key, None)


class InMemoryWorkspaceMembershipResolver:
    """Test/dev membership resolver backed by an explicit (org_id, user_id) → bool dict.

    Tests construct the resolver with the membership truth they need; the runtime
    treats it identically to the HTTP implementation.
    """

    def __init__(
        self,
        active_members: dict[tuple[str, str], bool] | None = None,
    ) -> None:
        self._members: dict[tuple[str, str], bool] = dict(active_members or {})

    def set(self, *, org_id: str, user_id: str, is_active: bool) -> None:
        """Add or overwrite a membership entry."""
        self._members[(org_id, user_id)] = is_active

    def remove(self, *, org_id: str, user_id: str) -> None:
        """Remove a membership entry (no-op if absent)."""
        self._members.pop((org_id, user_id), None)

    async def is_active_member(self, *, org_id: str, user_id: str) -> bool:
        """Return the configured membership state, defaulting to ``False`` for unknown users."""
        return self._members.get((org_id, user_id), False)


HttpFetcher = Callable[[str, dict[str, str]], Awaitable[tuple[int, dict[str, object]]]]
"""Callable type for HTTP GET: ``(url, headers) -> (status_code, json_body)``.

Production injects a thin httpx wrapper; tests inject a lambda or fake so the
resolver stays testable without a real HTTP dependency.
"""


class HttpWorkspaceMembershipResolver:
    """Production resolver that calls services/backend via the service-token lane.

    The HTTP fetcher is injected so this class stays testable without a real
    httpx dependency. The bounded TTL cache is per-instance (one per process)
    and uses separate TTLs for positive and negative results.
    """

    def __init__(
        self,
        *,
        fetch: HttpFetcher,
        backend_base_url: str | None = None,
        cache: _MembershipCache | None = None,
        service_token: str | None = None,
    ) -> None:
        self._fetch = fetch
        self._backend_base_url = (
            backend_base_url
            or os.environ.get(_Env.BACKEND_BASE_URL)
            or "http://backend:8100"
        ).rstrip("/")
        self._cache = cache or _MembershipCache(
            positive_ttl_seconds=_env_float(
                _Env.POSITIVE_TTL_SECONDS, _Env.DEFAULT_POSITIVE_TTL_SECONDS
            ),
            negative_ttl_seconds=_env_float(
                _Env.NEGATIVE_TTL_SECONDS, _Env.DEFAULT_NEGATIVE_TTL_SECONDS
            ),
        )
        self._service_token = service_token or os.environ.get(
            "ENTERPRISE_SERVICE_TOKEN", ""
        )

    async def is_active_member(self, *, org_id: str, user_id: str) -> bool:
        """Return cached membership when available; otherwise fetch from the backend."""
        cached = self._cache.get(org_id=org_id, user_id=user_id)
        if cached is not None:
            return cached
        is_active = await self._lookup(org_id=org_id, user_id=user_id)
        self._cache.put(org_id=org_id, user_id=user_id, is_active=is_active)
        return is_active

    def invalidate(self, *, org_id: str, user_id: str) -> None:
        """Force-expire the cache entry for a user that was just revoked."""
        self._cache.invalidate(org_id=org_id, user_id=user_id)

    async def _lookup(self, *, org_id: str, user_id: str) -> bool:
        """Fetch the user record from the backend and return whether the user is active."""
        url = f"{self._backend_base_url}/internal/v1/users/{user_id}"
        headers: dict[str, str] = {
            "x-enterprise-service-token": self._service_token,
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": user_id,
        }
        try:
            status_code, body = await self._fetch(url, headers)
        except MembershipResolverUnavailable:
            raise
        except Exception as exc:
            raise MembershipResolverUnavailable(
                "Identity backend unreachable while resolving membership."
            ) from exc

        if status_code == 404:
            return False
        if status_code >= 500:
            # 5xx is transient — raise so the caller maps it to 503.
            raise MembershipResolverUnavailable(
                f"Identity backend returned {status_code}."
            )
        if status_code != 200:
            # Any other 4xx (e.g. 401 when the service token is missing)
            # is treated as a definitive negative; log for operator visibility
            # without leaking detail to the API response.
            logger.warning(
                "membership_lookup_unexpected_status",
                extra={
                    "metadata": {
                        "status_code": status_code,
                        "org_id": org_id,
                    }
                },
            )
            return False

        body_org_id = body.get("org_id") if isinstance(body, dict) else None
        body_status = body.get("status") if isinstance(body, dict) else None
        removed_at = body.get("removed_at") if isinstance(body, dict) else None
        # All three conditions must hold: correct org, active status, not removed.
        return (
            body_org_id == org_id
            and body_status == "active"
            and removed_at in (None, "")
        )


def _env_float(name: str, default: float) -> float:
    """Read an environment variable as a float, falling back to ``default`` on any parse error."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default
