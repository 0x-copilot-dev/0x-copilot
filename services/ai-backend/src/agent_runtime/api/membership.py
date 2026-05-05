"""Workspace membership resolver (PR 1.4.1).

The runtime calls this before accepting any cross-user write — today only
two-stage approval forwarding (the requester forwards a pending approval
to a different active member of the same org). Other future cross-user
writes (reassignment, share-recipient resolution) consume the same port.

Two impls:

- :class:`HttpWorkspaceMembershipResolver` — production. Calls
  ``GET /internal/v1/users/{user_id}`` on services/backend over the
  existing service-token lane and treats the response's ``status`` +
  ``org_id`` + ``removed_at`` as authoritative. Per-(org, user) TTL
  cache absorbs the hot-path cost.
- :class:`InMemoryWorkspaceMembershipResolver` — tests / dev. Wraps a
  ``dict[(org, user), bool]`` so unit tests don't need an HTTP fake.

The negative cache TTL is deliberately tight (30s) so a freshly added
member doesn't have to wait the full positive-TTL window before
forwards start landing. The positive TTL (5min) is acceptable because
membership revocation has a separate cascade in PR 1.4.1 Phase B
(:class:`ApprovalExpirySweeper`) which uses this same resolver to
re-validate pending assignments at every tick.
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
    POSITIVE_TTL_SECONDS = "RUNTIME_MEMBERSHIP_POSITIVE_TTL_SECONDS"
    NEGATIVE_TTL_SECONDS = "RUNTIME_MEMBERSHIP_NEGATIVE_TTL_SECONDS"
    BACKEND_BASE_URL = "BACKEND_BASE_URL"

    DEFAULT_POSITIVE_TTL_SECONDS = 300.0
    DEFAULT_NEGATIVE_TTL_SECONDS = 30.0


@runtime_checkable
class WorkspaceMembershipResolver(Protocol):
    """Resolve org-scoped membership for a user_id."""

    async def is_active_member(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> bool:
        """Return ``True`` iff ``user_id`` is an active member of ``org_id``.

        Implementations should treat any uncertainty (5xx from the
        identity backend, malformed response) as a transient failure and
        raise :class:`MembershipResolverUnavailable`. Callers translate
        that to a 503 with ``retryable=True`` rather than a 422 — we want
        a flapping backend to stop forwards cleanly, not to silently let
        unauthorized targets through.
        """


class MembershipResolverUnavailable(RuntimeError):
    """Raised when the resolver cannot reach the identity backend.

    Distinct from a definitive ``False`` so the API layer can map it to
    503 (retryable) rather than 422 (target invalid).
    """


@dataclass(frozen=True)
class _CachedAnswer:
    is_active: bool
    expires_at_monotonic: float


class _MembershipCache:
    """Per-(org, user) TTL cache with separate positive/negative windows.

    Bounded by ``max_entries`` to keep the resolver's footprint tiny on a
    multi-tenant deployment with many distinct callers. Eviction is
    expiry-first; if no entries are expired we drop the oldest.
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
        key = (org_id, user_id)
        cached = self._entries.get(key)
        if cached is None:
            return None
        if cached.expires_at_monotonic <= self._clock():
            self._entries.pop(key, None)
            return None
        return cached.is_active

    def put(self, *, org_id: str, user_id: str, is_active: bool) -> None:
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
        """Drop a cache entry — used by the deactivation cascade so a
        user who was just revoked doesn't keep being treated as active
        until the entry naturally expires.
        """

        self._entries.pop((org_id, user_id), None)

    def _evict_one(self) -> None:
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
        # No expired entries — drop the lexicographically smallest key
        # (deterministic; insertion order isn't load-bearing here).
        oldest_key = next(iter(self._entries))
        self._entries.pop(oldest_key, None)


class InMemoryWorkspaceMembershipResolver:
    """Test/dev resolver backed by an explicit membership dict.

    Tests construct the resolver with the truth they want; the runtime
    treats it identically to the HTTP impl.
    """

    def __init__(
        self,
        active_members: dict[tuple[str, str], bool] | None = None,
    ) -> None:
        self._members: dict[tuple[str, str], bool] = dict(active_members or {})

    def set(self, *, org_id: str, user_id: str, is_active: bool) -> None:
        self._members[(org_id, user_id)] = is_active

    def remove(self, *, org_id: str, user_id: str) -> None:
        self._members.pop((org_id, user_id), None)

    async def is_active_member(self, *, org_id: str, user_id: str) -> bool:
        return self._members.get((org_id, user_id), False)


HttpFetcher = Callable[[str, dict[str, str]], Awaitable[tuple[int, dict[str, object]]]]
"""HTTP fetcher signature: (url, headers) -> (status_code, json_body).

Production injects a thin httpx wrapper; tests inject a fake. Keeping
this as a callable instead of an httpx dependency on the resolver class
keeps the test surface minimal — no real httpx in unit tests.
"""


class HttpWorkspaceMembershipResolver:
    """Production resolver: calls services/backend over the trusted lane.

    The HTTP fetcher is injected so this class stays testable without an
    httpx dependency in the test path. The TTL cache is per-instance —
    one resolver per ai-backend process — and is bounded.
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
        cached = self._cache.get(org_id=org_id, user_id=user_id)
        if cached is not None:
            return cached
        is_active = await self._lookup(org_id=org_id, user_id=user_id)
        self._cache.put(org_id=org_id, user_id=user_id, is_active=is_active)
        return is_active

    def invalidate(self, *, org_id: str, user_id: str) -> None:
        """Drop the cache entry. Called by the membership-cascade
        sweeper when it observes a user transition to inactive.
        """

        self._cache.invalidate(org_id=org_id, user_id=user_id)

    async def _lookup(self, *, org_id: str, user_id: str) -> bool:
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
            raise MembershipResolverUnavailable(
                f"Identity backend returned {status_code}."
            )
        if status_code != 200:
            # 4xx other than 404 — treat as definitive negative; the
            # backend rejected the call (e.g. the service token was
            # missing). Logging surfaces the misconfiguration without
            # leaking internal detail.
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
        return (
            body_org_id == org_id
            and body_status == "active"
            and removed_at in (None, "")
        )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default
