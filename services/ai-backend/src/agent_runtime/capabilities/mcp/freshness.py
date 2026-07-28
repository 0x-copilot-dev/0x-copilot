"""Revision-aware freshness control for MCP descriptor discovery.

The existing :class:`McpDiscoveryCache` owns process-local TTL, LRU, and
thundering-herd behavior.  This module adds the control-plane information that
the cache deliberately does not know about:

* which org/user subject authorized a descriptor view;
* which opaque control-plane revision produced that view; and
* the maximum age at which a descriptor may still be reused.

It is an opt-in wrapper rather than a second descriptor cache.  Records remain
stored in ``McpDiscoveryCache`` and every lookup uses its existing
``(server_name, org_id, user_id)`` key.  Missing revision metadata fails closed:
an entry inserted by a legacy caller is never silently treated as revision
compatible.

Since Step RB the subject, revision, tamper, and generation-barrier comparisons
are not implemented here at all.  They are delegated to the one shared
``RevisionBindingRevalidator`` through
:class:`~agent_runtime.capabilities.mcp.descriptor_revision_binding.McpDescriptorRevisionBinder`,
and this module keeps only what is genuinely local to a descriptor cache:
bounded staleness (age), base-cache liveness, and the projection of the
primitive's closed outcome onto :class:`McpDescriptorFreshnessState`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

from pydantic import Field, field_validator

from agent_runtime.capabilities.mcp.cards import LoadedMcpServer
from agent_runtime.capabilities.mcp.control_plane_metrics import (
    McpControlPlaneEvent,
    McpControlPlaneMetricsPort,
    McpControlPlaneOutcome,
    NoopMcpControlPlaneMetrics,
)
from agent_runtime.capabilities.mcp.descriptor_revision_binding import (
    McpDescriptorRevisionBinder,
)
from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCache,
    McpDiscoveryCacheKey,
)
from agent_runtime.capabilities.mcp.revision_feed import (
    ActiveMcpRevisionSubjectRegistry,
    McpRevisionSubject,
)
from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolverPort,
    RevisionResolveState,
)
from agent_runtime.control_plane.revision_binding import RevalidationReason
from agent_runtime.execution.contracts import RuntimeContract


class McpDescriptorRevision(RuntimeContract):
    """Opaque revision emitted by the MCP control plane.

    The runtime compares revisions for equality only.  It must not infer
    ordering from provider-specific values such as ETags, hashes, or database
    sequence identifiers.
    """

    value: str = Field(min_length=1, max_length=512)

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "descriptor revision must not be blank"
            raise ValueError(msg)
        return normalized


class McpDescriptorSubject(RuntimeContract):
    """The complete authorization subject for one descriptor view."""

    org_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class McpDescriptorFreshnessRequest(RuntimeContract):
    """Revision expectation for one subject-scoped MCP server."""

    server_name: str = Field(min_length=1)
    subject: McpDescriptorSubject
    revision: McpDescriptorRevision

    def cache_key(self) -> McpDiscoveryCacheKey:
        """Project the request to the existing cache's isolation key."""
        return McpDiscoveryCacheKey(
            server_name=self.server_name,
            org_id=self.subject.org_id,
            user_id=self.subject.user_id,
        )


class McpDescriptorFreshnessState(StrEnum):
    """Deterministic reasons a cached descriptor may or may not be reused."""

    FRESH = "fresh"
    NOT_TRACKED = "not_tracked"
    REVISION_CHANGED = "revision_changed"
    MAX_STALENESS_EXCEEDED = "max_staleness_exceeded"
    VALUE_EVICTED = "value_evicted"
    INVALIDATION_RACED = "invalidation_raced"


class McpDescriptorBindingStates:
    """Total projection of RB revalidation reasons onto F8 freshness states.

    Two refusals are reachable on the shipped F8 path: the trusted current
    revision no longer equals the revision a cached view was admitted under,
    and the generation barrier moved under an in-flight load.  Every other
    reason is a structural refusal the shipped path cannot produce -- a forged
    binding, a foreign feature, an unscoped reference, a cross-subject replay,
    or an authority that cannot answer.  All of them project onto
    ``NOT_TRACKED``, F8's existing "this entry cannot be vouched for" state, so
    an unusable authority can never be read as permission to reuse.
    """

    BY_REASON: ClassVar[Mapping[RevalidationReason, McpDescriptorFreshnessState]] = (
        MappingProxyType(
            {
                RevalidationReason.REVISION_MATCHES: McpDescriptorFreshnessState.FRESH,
                RevalidationReason.REVISION_CHANGED: (
                    McpDescriptorFreshnessState.REVISION_CHANGED
                ),
                RevalidationReason.CATALOG_GENERATION_MISMATCH: (
                    McpDescriptorFreshnessState.INVALIDATION_RACED
                ),
                RevalidationReason.AUTHORITY_REVOKED: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.BINDING_DIGEST_MISMATCH: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.FEATURE_MISMATCH: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.SCOPE_DIMENSION_MISSING: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.SUBJECT_MISMATCH: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.RUN_MISMATCH: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.UNKNOWN_REFERENCE: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.AUTHORITY_UNAVAILABLE: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.AUTHORITY_ERROR: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
                RevalidationReason.AUTHORITY_CONTRACT_VIOLATION: (
                    McpDescriptorFreshnessState.NOT_TRACKED
                ),
            }
        )
    )


class McpDescriptorFreshnessDecision(RuntimeContract):
    """Content-free decision produced before descriptor reuse or reload."""

    state: McpDescriptorFreshnessState
    requested_revision: McpDescriptorRevision
    cached_revision: McpDescriptorRevision | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    max_staleness_seconds: float = Field(gt=0)

    @property
    def reuse_allowed(self) -> bool:
        """Whether a cached descriptor is eligible for this request."""
        return self.state is McpDescriptorFreshnessState.FRESH


class McpDescriptorCacheResult(RuntimeContract):
    """Result of a revision-aware cache read or cache-aside load."""

    decision: McpDescriptorFreshnessDecision
    record: LoadedMcpServer | None = None
    loaded: bool = False


class McpDescriptorInvalidationResult(RuntimeContract):
    """Counts returned by an explicit subject-scoped invalidation hook."""

    cached_records_removed: int = Field(ge=0)
    revision_records_removed: int = Field(ge=0)
    generation_barriers_advanced: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class _RevisionRecord:
    revision: McpDescriptorRevision
    admitted_at: float
    generation: int


@dataclass(slots=True)
class _KeyLock:
    lock: asyncio.Lock
    users: int = 0


class RevisionAwareMcpDiscoveryCache:
    """Subject-scoped revision and bounded-staleness guard.

    ``max_staleness_seconds`` is an upper bound independent of the underlying
    cache TTL.  The effective lifetime is therefore the smaller of the two:
    the wrapper rejects an over-age record even if the base TTL is longer, and
    reports ``VALUE_EVICTED`` if the base cache expires or evicts it first.

    Revision state is process-local, matching ``McpDiscoveryCache``.  A later
    shared-cache adapter can persist both values behind this same contract.
    """

    def __init__(
        self,
        cache: McpDiscoveryCache,
        *,
        max_staleness_seconds: float,
        revision_resolver: McpDescriptorRevisionResolverPort | None = None,
        active_subjects: ActiveMcpRevisionSubjectRegistry | None = None,
        revision_checks_enabled: bool = False,
        clock: Callable[[], float] = time.monotonic,
        metrics: McpControlPlaneMetricsPort | None = None,
        revision_binder: McpDescriptorRevisionBinder | None = None,
    ) -> None:
        if max_staleness_seconds <= 0:
            msg = "max_staleness_seconds must be positive"
            raise ValueError(msg)
        if revision_checks_enabled and revision_resolver is None:
            raise ValueError(
                "revision_resolver is required when revision checks are enabled"
            )
        self._cache = cache
        self._binder = revision_binder or McpDescriptorRevisionBinder()
        self._revision_resolver = revision_resolver
        self._active_subjects = active_subjects
        self._subject_registration_declined = 0
        self._revision_checks_enabled = revision_checks_enabled
        self._max_staleness_seconds = float(max_staleness_seconds)
        self._clock = clock
        self._metrics = metrics or NoopMcpControlPlaneMetrics()
        self._revisions: dict[McpDiscoveryCacheKey, _RevisionRecord] = {}
        self._key_locks: dict[McpDiscoveryCacheKey, _KeyLock] = {}
        self._generations: dict[McpDiscoveryCacheKey, int] = {}
        self._state_lock = asyncio.Lock()

    async def get(
        self,
        request: McpDescriptorFreshnessRequest,
    ) -> McpDescriptorCacheResult:
        """Return a record only when subject, revision, and age all match."""
        key = request.cache_key()
        async with self._lock_for(key):
            return await self._get_locked(key=key, request=request)

    async def put(
        self,
        request: McpDescriptorFreshnessRequest,
        record: LoadedMcpServer,
    ) -> None:
        """Admit a descriptor under the request's exact subject and revision."""
        key = request.cache_key()
        async with self._lock_for(key):
            generation = await self._generation_for(key)
            await self._put_locked(
                key=key,
                request=request,
                record=record,
                expected_generation=generation,
            )

    async def get_or_load(
        self,
        request: McpDescriptorFreshnessRequest,
        load: Callable[[], Awaitable[LoadedMcpServer | None]],
    ) -> McpDescriptorCacheResult:
        """Load once per exact subject/server key when reuse is not allowed.

        ``None`` and exceptions are not admitted.  The key lock spans the load
        so concurrent callers for the same subject and revision share the
        result while different subjects and servers may still load in parallel.
        """
        key = request.cache_key()
        async with self._lock_for(key):
            return await self._get_or_load_locked(
                key=key,
                request=request,
                load=load,
            )

    async def get_or_load_cache_entry(
        self,
        key: McpDiscoveryCacheKey,
        *,
        source_id: str | None,
        load: Callable[[], Awaitable[LoadedMcpServer | None]],
    ) -> LoadedMcpServer | None:
        """Resolve one trusted revision and compose it over the base cache.

        The feature-off path delegates byte-for-byte to the existing cache.
        When enabled, source registration, exact revision resolution, and
        descriptor lookup share this wrapper's per-key cohort. Missing revision
        authority falls back to a generation-fenced live load that is never
        admitted under a fabricated revision.
        """

        if not self._revision_checks_enabled:
            return await self._cache.get_or_load_cache_entry(
                key,
                source_id=source_id,
                load=load,
            )

        async with self._lock_for(key):
            # McpLoader calls this cache only after it has resolved the card and
            # performed its live permission check.  Never move this touch into a
            # registry/provider path: cache keys are derived from that verified
            # runtime identity, and an unauthorised card must not activate a
            # background feed subject.  A full registry changes only polling;
            # exact resolution and the normal live-load fallback still run.
            if self._active_subjects is not None:
                admitted = await self._active_subjects.touch_verified(
                    McpRevisionSubject(org_id=key.org_id, user_id=key.user_id)
                )
                if not admitted:
                    self._subject_registration_declined += 1
            resolver = self._revision_resolver
            if resolver is not None and source_id is not None:
                await resolver.register(
                    org_id=key.org_id,
                    user_id=key.user_id,
                    server_name=key.server_name,
                    server_id=source_id,
                )
                resolved = await resolver.resolve(
                    org_id=key.org_id,
                    user_id=key.user_id,
                    server_name=key.server_name,
                )
                if (
                    resolved.state is RevisionResolveState.FRESH
                    and resolved.revision is not None
                    and resolved.revision.server_id == source_id
                ):
                    request = McpDescriptorFreshnessRequest(
                        server_name=key.server_name,
                        subject=McpDescriptorSubject(
                            org_id=key.org_id,
                            user_id=key.user_id,
                        ),
                        revision=McpDescriptorRevision(
                            value=resolved.revision.revision
                        ),
                    )
                    result = await self._get_or_load_locked(
                        key=key,
                        request=request,
                        load=load,
                    )
                    self._metrics.event(
                        event=McpControlPlaneEvent.CACHE,
                        outcome={
                            McpDescriptorFreshnessState.FRESH: McpControlPlaneOutcome.FRESH,
                            McpDescriptorFreshnessState.NOT_TRACKED: McpControlPlaneOutcome.NOT_TRACKED,
                            McpDescriptorFreshnessState.REVISION_CHANGED: McpControlPlaneOutcome.REVISION_CHANGED,
                            McpDescriptorFreshnessState.MAX_STALENESS_EXCEEDED: McpControlPlaneOutcome.EXPIRED,
                            McpDescriptorFreshnessState.VALUE_EVICTED: McpControlPlaneOutcome.EVICTED,
                            McpDescriptorFreshnessState.INVALIDATION_RACED: McpControlPlaneOutcome.RACE,
                        }[result.decision.state],
                    )
                    return result.record

            await self._invalidate_exact(key, advance_generation=True)
            self._metrics.event(
                event=McpControlPlaneEvent.CACHE,
                outcome=McpControlPlaneOutcome.UNTRACKED,
            )
            return await self._load_untracked_locked(key=key, load=load)

    async def invalidate(
        self,
        *,
        server_name: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> int:
        """Preserve base invalidation semantics and advance wrapper barriers."""

        if not self._revision_checks_enabled:
            return await self._cache.invalidate(
                server_name=server_name,
                org_id=org_id,
                user_id=user_id,
            )
        matching_keys, _revision_records_removed = await self._invalidate_metadata(
            server_name=server_name,
            org_id=org_id,
            user_id=user_id,
        )
        resolver = self._revision_resolver
        if resolver is not None:
            resolver_keys = list(matching_keys)
            if server_name is not None and org_id is not None and user_id is not None:
                explicit_key = McpDiscoveryCacheKey(
                    server_name=server_name,
                    org_id=org_id,
                    user_id=user_id,
                )
                if explicit_key not in resolver_keys:
                    resolver_keys.append(explicit_key)
            for key in resolver_keys:
                await resolver.invalidate(
                    org_id=key.org_id,
                    user_id=key.user_id,
                    server_name=key.server_name,
                )
        return await self._cache.invalidate(
            server_name=server_name,
            org_id=org_id,
            user_id=user_id,
        )

    async def invalidate_subject(
        self,
        subject: McpDescriptorSubject,
        *,
        server_name: str | None = None,
    ) -> McpDescriptorInvalidationResult:
        """Invalidate only one org/user subject, optionally one server.

        Both subject fields are mandatory by construction.  This intentionally
        does not expose the wildcard org/user surface of the base cache.
        """
        matching_keys, revision_records_removed = await self._invalidate_metadata(
            server_name=server_name,
            org_id=subject.org_id,
            user_id=subject.user_id,
        )
        resolver = self._revision_resolver
        if resolver is not None:
            for key in matching_keys:
                await resolver.invalidate(
                    org_id=key.org_id,
                    user_id=key.user_id,
                    server_name=key.server_name,
                )
        cached_removed = await self._cache.invalidate(
            server_name=server_name,
            org_id=subject.org_id,
            user_id=subject.user_id,
        )
        return McpDescriptorInvalidationResult(
            cached_records_removed=cached_removed,
            revision_records_removed=revision_records_removed,
            generation_barriers_advanced=len(matching_keys),
        )

    async def invalidate_descriptor_subject(
        self,
        subject: McpDescriptorSubject,
        *,
        server_name: str | None = None,
    ) -> McpDescriptorInvalidationResult:
        """Evict descriptor material without changing resolver state.

        Feed notices first update the resolver.  The production feed adapter
        uses this narrow operation immediately afterwards so the notice cannot
        be overwritten by a second resolver invalidation before catalog
        generation advances.
        """

        matching_keys, revision_records_removed = await self._invalidate_metadata(
            server_name=server_name,
            org_id=subject.org_id,
            user_id=subject.user_id,
        )
        cached_removed = await self._cache.invalidate(
            server_name=server_name,
            org_id=subject.org_id,
            user_id=subject.user_id,
        )
        return McpDescriptorInvalidationResult(
            cached_records_removed=cached_removed,
            revision_records_removed=revision_records_removed,
            generation_barriers_advanced=len(matching_keys),
        )

    def subject_registration_diagnostics(self) -> dict[str, int]:
        """Bounded, content-free diagnostics for declined feed activation."""

        return {"subject_registration_declined": self._subject_registration_declined}

    async def _get_or_load_locked(
        self,
        *,
        key: McpDiscoveryCacheKey,
        request: McpDescriptorFreshnessRequest,
        load: Callable[[], Awaitable[LoadedMcpServer | None]],
    ) -> McpDescriptorCacheResult:
        cached = await self._get_locked(key=key, request=request)
        if cached.record is not None:
            return cached

        generation = await self._generation_for(key)
        loaded = await load()
        if loaded is None:
            return cached

        admitted_for_generation = await self._put_locked(
            key=key,
            request=request,
            record=loaded,
            expected_generation=generation,
        )
        if not admitted_for_generation:
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.INVALIDATION_RACED,
                ),
            )
        admitted = await self._cache.get(key)
        if admitted is None:  # Defensive: an adapter may reject admission.
            async with self._state_lock:
                self._revisions.pop(key, None)
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.VALUE_EVICTED,
                ),
            )
        return McpDescriptorCacheResult(
            decision=cached.decision,
            record=admitted,
            loaded=True,
        )

    async def _load_untracked_locked(
        self,
        *,
        key: McpDiscoveryCacheKey,
        load: Callable[[], Awaitable[LoadedMcpServer | None]],
    ) -> LoadedMcpServer | None:
        # The one barrier that stays local.  This path exists precisely because
        # no trusted revision could be resolved, so there is nothing to bind a
        # reference to: ``RevisionBoundRef`` requires a revision, and inventing
        # one to reach the shared revalidator would fabricate exactly the
        # authority the primitive forbids.  The material is never admitted to
        # the cache either way; only the fence below decides whether an
        # in-flight live load may still be returned to its caller.
        generation = await self._generation_for(key)
        loaded = await load()
        if loaded is None:
            return None
        async with self._state_lock:
            if self._generations.get(key, 0) != generation:
                return None
        return loaded.model_copy(deep=True)

    async def _invalidate_metadata(
        self,
        *,
        server_name: str | None,
        org_id: str | None,
        user_id: str | None,
    ) -> tuple[tuple[McpDiscoveryCacheKey, ...], int]:
        async with self._state_lock:
            matching_keys = tuple(
                key
                for key in set(self._revisions).union(self._key_locks)
                if (server_name is None or key.server_name == server_name)
                and (org_id is None or key.org_id == org_id)
                and (user_id is None or key.user_id == user_id)
            )
            revision_records_removed = 0
            for key in matching_keys:
                self._generations[key] = self._generations.get(key, 0) + 1
                if key in self._revisions:
                    revision_records_removed += 1
                self._revisions.pop(key, None)
                # The projection of the backend authority is no longer trusted
                # once the key is invalidated.  Every revalidation republishes
                # the request's trusted revision first, so dropping it here
                # only bounds memory; it can neither admit nor refuse a view.
                self._binder.forget(key)
                if key not in self._key_locks:
                    self._generations.pop(key, None)
        return matching_keys, revision_records_removed

    async def _get_locked(
        self,
        *,
        key: McpDiscoveryCacheKey,
        request: McpDescriptorFreshnessRequest,
    ) -> McpDescriptorCacheResult:
        async with self._state_lock:
            revision_record = self._revisions.get(key)
            generation = self._generations.get(key, 0)

        if revision_record is None:
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.NOT_TRACKED,
                ),
            )

        age_seconds = max(0.0, self._clock() - revision_record.admitted_at)
        # Revision equality, subject isolation, binding integrity, and the
        # generation barrier are the shared primitive's job.  The snapshot
        # above was taken in one critical section, so the barrier value the
        # record was admitted under is the barrier value observed here; the
        # post-I/O re-check below is the one that can actually move.
        binding = await self._binder.revalidate(
            key=key,
            bound_revision=revision_record.revision.value,
            bound_generation=revision_record.generation,
            trusted_revision=request.revision.value,
            observed_generation=generation,
        )
        if not binding.is_current:
            state = McpDescriptorBindingStates.BY_REASON[binding.reason]
            decision = self._decision(
                request=request,
                state=state,
                cached_revision=revision_record.revision,
                age_seconds=age_seconds,
            )
            if state is not McpDescriptorFreshnessState.INVALIDATION_RACED:
                # A raced barrier means an invalidation already evicted this
                # key; every other refusal must bust the entry it distrusts.
                await self._invalidate_exact(key)
            return McpDescriptorCacheResult(decision=decision)

        if age_seconds >= self._max_staleness_seconds:
            decision = self._decision(
                request=request,
                state=McpDescriptorFreshnessState.MAX_STALENESS_EXCEEDED,
                cached_revision=revision_record.revision,
                age_seconds=age_seconds,
            )
            await self._invalidate_exact(key)
            return McpDescriptorCacheResult(decision=decision)

        record = await self._cache.get(key)
        if record is None:
            async with self._state_lock:
                self._revisions.pop(key, None)
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.VALUE_EVICTED,
                    cached_revision=revision_record.revision,
                    age_seconds=age_seconds,
                ),
            )

        async with self._state_lock:
            observed_generation = self._generations.get(key, 0)
            record_unchanged = self._revisions.get(key) == revision_record
        # The generation barrier again, now against post-I/O reality: a load or
        # read that began before an invalidation must not be returned across it.
        barrier = await self._binder.revalidate(
            key=key,
            bound_revision=revision_record.revision.value,
            bound_generation=revision_record.generation,
            trusted_revision=request.revision.value,
            observed_generation=observed_generation,
        )
        if not barrier.is_current or not record_unchanged:
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.INVALIDATION_RACED,
                    cached_revision=revision_record.revision,
                    age_seconds=age_seconds,
                ),
            )

        return McpDescriptorCacheResult(
            decision=self._decision(
                request=request,
                state=McpDescriptorFreshnessState.FRESH,
                cached_revision=revision_record.revision,
                age_seconds=age_seconds,
            ),
            record=record,
        )

    async def _put_locked(
        self,
        *,
        key: McpDiscoveryCacheKey,
        request: McpDescriptorFreshnessRequest,
        record: LoadedMcpServer,
        expected_generation: int,
    ) -> bool:
        await self._cache.put(key, record)
        async with self._state_lock:
            # The publication half of the generation barrier.  Every mutation
            # of ``_generations`` is made under this lock and the authority
            # projection takes no lock of its own, so revalidating here keeps
            # the barrier check atomic with the admission without any
            # lock-ordering hazard.  ``bound_revision`` is the request's own
            # trusted revision, so only a moved barrier can refuse admission.
            admission = await self._binder.revalidate(
                key=key,
                bound_revision=request.revision.value,
                bound_generation=expected_generation,
                trusted_revision=request.revision.value,
                observed_generation=self._generations.get(key, 0),
            )
            admitted = admission.is_current
            if admitted:
                self._revisions[key] = _RevisionRecord(
                    revision=request.revision,
                    admitted_at=self._clock(),
                    generation=expected_generation,
                )
        if not admitted:
            await self._cache.invalidate(
                server_name=key.server_name,
                org_id=key.org_id,
                user_id=key.user_id,
            )
            return False
        return True

    async def _generation_for(self, key: McpDiscoveryCacheKey) -> int:
        async with self._state_lock:
            return self._generations.get(key, 0)

    async def _invalidate_exact(
        self,
        key: McpDiscoveryCacheKey,
        *,
        advance_generation: bool = False,
    ) -> None:
        if advance_generation:
            async with self._state_lock:
                self._generations[key] = self._generations.get(key, 0) + 1
                self._revisions.pop(key, None)
        await self._cache.invalidate(
            server_name=key.server_name,
            org_id=key.org_id,
            user_id=key.user_id,
        )
        if not advance_generation:
            async with self._state_lock:
                self._revisions.pop(key, None)

    def _decision(
        self,
        *,
        request: McpDescriptorFreshnessRequest,
        state: McpDescriptorFreshnessState,
        cached_revision: McpDescriptorRevision | None = None,
        age_seconds: float | None = None,
    ) -> McpDescriptorFreshnessDecision:
        return McpDescriptorFreshnessDecision(
            state=state,
            requested_revision=request.revision,
            cached_revision=cached_revision,
            age_seconds=age_seconds,
            max_staleness_seconds=self._max_staleness_seconds,
        )

    @asynccontextmanager
    async def _lock_for(
        self,
        key: McpDiscoveryCacheKey,
    ) -> AsyncIterator[None]:
        """Acquire a temporary per-key lock without an unbounded lock table."""
        async with self._state_lock:
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = _KeyLock(lock=asyncio.Lock())
                self._key_locks[key] = key_lock
            contended = key_lock.lock.locked()
            key_lock.users += 1
        acquired = False
        try:
            await key_lock.lock.acquire()
            acquired = True
            if contended:
                self._metrics.event(
                    event=McpControlPlaneEvent.CACHE,
                    outcome=McpControlPlaneOutcome.COALESCED,
                )
            yield
        finally:
            if acquired:
                key_lock.lock.release()
            async with self._state_lock:
                key_lock.users -= 1
                if key_lock.users == 0:
                    self._key_locks.pop(key, None)
                    if key not in self._revisions:
                        self._generations.pop(key, None)
                        # Keep the authority projection bounded by exactly the
                        # same invariant as the generation state it fences.
                        self._binder.forget(key)
