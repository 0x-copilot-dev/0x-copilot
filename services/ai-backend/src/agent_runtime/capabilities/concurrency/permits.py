"""The bounded, fair, run-scoped permit table for the F6 executor.

The permit *vocabulary* — scopes, keys, capacities, requests, and leases — lives
in :mod:`agent_runtime.capabilities.concurrency.contracts` alongside the rest of
the F6 domain, because a permit scope and a declared rate-limit scope are the
same :class:`~agent_runtime.capabilities.concurrency.contracts.ConcurrencyScope`.
This module owns only the mutable table that enforces them.

Step 2 installed one conservative serial permit
(``agent_runtime.control_plane.context.RunSerialAdmission``) shared by every
graph-visible tool call in a run. These permits compose *inside* that permit:
they are acquired by children that the batch planner already declared
independent, and they can only ever narrow what runs at once. Nothing in this
module can widen the Step-2 admission.

Design commitments:

- **Digested keys.** A ``PermitScope`` is a typed component tuple; its
  ``PermitScopeKey`` is a domain-separated SHA-256 of the canonical component
  payload. Keys carry a kind plus lowercase hexadecimal only. Raw paths,
  connector URLs, user content, and credentials cannot reach a key, and the
  component fields are pattern-constrained so a URL or filesystem path cannot
  even be supplied.
- **Conservative capacity.** Capacity is configuration-driven per scope kind.
  An absent or unknown kind means ``1`` (serial). The *minimum* capacity across
  a request's applicable scopes is the reported effective capacity, and each
  individual scope is additionally enforced against its own configured cap.
- **Fairness by construction.** Waiters take a monotonic ticket. A waiter is
  admitted only when it is the lowest-ticket waiter needing each of its scopes
  and every one of those scopes has room. Later arrivals can never overtake an
  existing waiter on a scope they share, so no waiter starves. Waiters on
  disjoint scopes still proceed independently.
- **No unbounded waits, no surprise exceptions.** Saturation produces a typed,
  closed ``PermitOutcome`` on a ``PermitLease``; it never raises. Queueing
  requires an explicit deadline. Only genuine programming faults (double
  release, releasing a refusal, cross-event-loop reuse) raise, and they raise
  typed ``PermitError`` subclasses with safe public text.
- **Run-scoped and bounded.** One :class:`RunPermitManager` per run, on one
  event loop. Held-count entries are pruned to zero, waiters and active leases
  are capped, and :meth:`RunPermitManager.dispose` drops everything so no state
  survives a run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyScope,
    PermitAcquisitionRequest,
    PermitBounds,
    PermitCapacityPolicy,
    PermitLease,
    PermitOutcome,
    PermitScopeKey,
    PermitWaitMode,
)
from agent_runtime.capabilities.concurrency.errors import (
    PermitDoubleReleaseError,
    PermitEventLoopMismatchError,
    PermitNotAdmittedError,
)


class _PermitWaiter:
    """One queued acquisition holding a monotonic fairness ticket."""

    __slots__ = (
        "ceiling",
        "future",
        "lease",
        "requested",
        "scope_keys",
        "ticket",
        "tokens",
    )

    def __init__(
        self,
        *,
        ticket: int,
        scope_keys: tuple[PermitScopeKey, ...],
        requested: int | None,
        ceiling: int,
        future: asyncio.Future[None],
    ) -> None:
        self.ticket = ticket
        self.scope_keys = scope_keys
        self.tokens = frozenset(key.token for key in scope_keys)
        self.requested = requested
        self.ceiling = ceiling
        self.future = future
        self.lease: PermitLease | None = None


class _ActiveLease:
    """Bounded bookkeeping for one admitted lease."""

    __slots__ = ("tokens",)

    def __init__(self, tokens: tuple[str, ...]) -> None:
        self.tokens = tokens


class RunPermitManager:
    """One run-scoped, bounded, starvation-free permit table.

    The manager narrows the Step-2 run-scoped serial permit; it never widens
    it. It is asyncio-only and bound to the first event loop that uses it, which
    matches the desktop's single in-process worker. All admission bookkeeping is
    synchronous, so no lock is needed and no check/act window exists.
    """

    def __init__(
        self,
        *,
        policy: PermitCapacityPolicy | None = None,
        max_waiters: int = PermitBounds.MAX_WAITERS,
        max_active_leases: int = PermitBounds.MAX_ACTIVE_LEASES,
    ) -> None:
        self._policy = policy if policy is not None else PermitCapacityPolicy()
        self._max_waiters = max(1, max_waiters)
        self._max_active_leases = max(1, max_active_leases)
        self._held: dict[str, int] = {}
        self._active: dict[str, _ActiveLease] = {}
        self._waiters: list[_PermitWaiter] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._disposed = False
        self._lease_ordinal = 0
        self._ticket_ordinal = 0

    @property
    def policy(self) -> PermitCapacityPolicy:
        """Return the immutable capacity policy this run was bound to."""

        return self._policy

    @property
    def pending_waiters(self) -> int:
        """Return the bounded number of queued acquisitions."""

        return len(self._waiters)

    @property
    def active_leases(self) -> int:
        """Return the bounded number of currently held leases."""

        return len(self._active)

    @property
    def tracked_scopes(self) -> int:
        """Return how many scope keys currently hold capacity."""

        return len(self._held)

    def in_flight(self, scope_key: PermitScopeKey) -> int:
        """Return the current in-flight count for one digested scope."""

        return self._held.get(scope_key.token, 0)

    def effective_capacity(self, request: PermitAcquisitionRequest) -> int:
        """Return the minimum capacity across the request's applicable scopes."""

        return self._effective_capacity(request.scope_keys(), request.max_parallelism)

    def holds(self, lease: PermitLease) -> bool:
        """Return whether this manager still holds capacity for ``lease``."""

        return lease.lease_id is not None and lease.lease_id in self._active

    @asynccontextmanager
    async def acquire(
        self,
        request: PermitAcquisitionRequest,
    ) -> AsyncIterator[PermitLease]:
        """Acquire permits for ``request`` and release them exactly once.

        This is the required production path. The lease is released on success,
        refusal, exception, and cancellation, so a permit cannot leak. Callers
        must check :attr:`PermitLease.admitted` before doing work.
        """

        lease = await self.acquire_lease(request)
        try:
            yield lease
        finally:
            if self.holds(lease):
                self.release(lease)

    async def acquire_lease(self, request: PermitAcquisitionRequest) -> PermitLease:
        """Return a typed lease without installing a release guard.

        Prefer :meth:`acquire`. Direct callers own release safety and must pair
        every admitted lease with exactly one :meth:`release`.
        """

        self._bind_loop()
        scope_keys = request.scope_keys()
        ceiling = self._effective_capacity(scope_keys, request.max_parallelism)

        if self._disposed:
            return self._refusal(scope_keys, ceiling, PermitOutcome.REFUSED_DISPOSED)
        if self._has_room(scope_keys, request.max_parallelism) and not self._blocked(
            scope_keys
        ):
            return self._admit(
                scope_keys,
                request.max_parallelism,
                ceiling,
                PermitOutcome.ADMITTED,
            )
        if request.wait_mode is PermitWaitMode.REFUSE_IF_SATURATED:
            return self._refusal(scope_keys, ceiling, PermitOutcome.REFUSED_SATURATED)

        timeout_seconds = request.timeout_seconds or 0.0
        if timeout_seconds <= 0.0:
            return self._refusal(scope_keys, ceiling, PermitOutcome.REFUSED_DEADLINE)
        if len(self._waiters) >= self._max_waiters:
            return self._refusal(scope_keys, ceiling, PermitOutcome.REFUSED_QUEUE_FULL)

        waiter = self._enqueue(scope_keys, request.max_parallelism, ceiling)
        try:
            async with asyncio.timeout(timeout_seconds):
                await waiter.future
        except TimeoutError:
            self._discard(waiter)
            return self._refusal(scope_keys, ceiling, PermitOutcome.REFUSED_DEADLINE)
        except asyncio.CancelledError:
            self._discard(waiter)
            raise

        if waiter.lease is None:
            return self._refusal(scope_keys, ceiling, PermitOutcome.REFUSED_DISPOSED)
        return waiter.lease

    def release(self, lease: PermitLease) -> None:
        """Release one admitted lease exactly once.

        Releasing a refusal or releasing twice is a typed fault, never silent
        capacity corruption.
        """

        if lease.lease_id is None:
            raise PermitNotAdmittedError()
        active = self._active.pop(lease.lease_id, None)
        if active is None:
            raise PermitDoubleReleaseError()
        for token in active.tokens:
            remaining = self._held.get(token, 0) - 1
            if remaining > 0:
                self._held[token] = remaining
            else:
                self._held.pop(token, None)
        self._pump()

    def dispose(self) -> None:
        """Drop every permit, waiter, and counter when the run ends.

        Queued waiters resolve to ``REFUSED_DISPOSED``. Later acquisitions are
        refused rather than raised so shutdown races cannot look like tool
        failures. Disposal is idempotent.
        """

        self._disposed = True
        waiters = list(self._waiters)
        self._waiters.clear()
        for waiter in waiters:
            if not waiter.future.done():
                waiter.future.set_result(None)
        self._held.clear()
        self._active.clear()

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise PermitEventLoopMismatchError()

    def _limit_for(self, kind: ConcurrencyScope, requested: int | None) -> int:
        configured = self._policy.capacity_for(kind)
        if requested is None:
            return configured
        return min(configured, requested)

    def _effective_capacity(
        self,
        scope_keys: tuple[PermitScopeKey, ...],
        requested: int | None,
    ) -> int:
        return min(self._limit_for(key.kind, requested) for key in scope_keys)

    def _has_room(
        self,
        scope_keys: tuple[PermitScopeKey, ...],
        requested: int | None,
    ) -> bool:
        if len(self._active) >= self._max_active_leases:
            return False
        return all(
            self._held.get(key.token, 0) < self._limit_for(key.kind, requested)
            for key in scope_keys
        )

    def _blocked(self, scope_keys: tuple[PermitScopeKey, ...]) -> bool:
        """Return whether an existing waiter owns any of these scopes."""

        tokens = {key.token for key in scope_keys}
        return any(not waiter.tokens.isdisjoint(tokens) for waiter in self._waiters)

    def _enqueue(
        self,
        scope_keys: tuple[PermitScopeKey, ...],
        requested: int | None,
        ceiling: int,
    ) -> _PermitWaiter:
        self._ticket_ordinal += 1
        loop = self._loop if self._loop is not None else asyncio.get_running_loop()
        waiter = _PermitWaiter(
            ticket=self._ticket_ordinal,
            scope_keys=scope_keys,
            requested=requested,
            ceiling=ceiling,
            future=loop.create_future(),
        )
        self._waiters.append(waiter)
        return waiter

    def _admit(
        self,
        scope_keys: tuple[PermitScopeKey, ...],
        requested: int | None,
        ceiling: int,
        outcome: PermitOutcome,
    ) -> PermitLease:
        tokens = tuple(key.token for key in scope_keys)
        for token in tokens:
            self._held[token] = self._held.get(token, 0) + 1
        self._lease_ordinal += 1
        lease_id = f"{PermitBounds.LEASE_ID_PREFIX}{self._lease_ordinal}"
        self._active[lease_id] = _ActiveLease(tokens=tokens)
        return PermitLease(
            outcome=outcome,
            scope_keys=scope_keys,
            effective_capacity=ceiling,
            lease_id=lease_id,
        )

    def _pump(self) -> None:
        """Admit waiters in strict ticket order, never overtaking a blocked one."""

        if self._disposed:
            return
        blocked: set[str] = set()
        for waiter in list(self._waiters):
            if blocked.isdisjoint(waiter.tokens) and self._has_room(
                waiter.scope_keys, waiter.requested
            ):
                self._waiters.remove(waiter)
                waiter.lease = self._admit(
                    waiter.scope_keys,
                    waiter.requested,
                    waiter.ceiling,
                    PermitOutcome.QUEUED_ADMITTED,
                )
                if not waiter.future.done():
                    waiter.future.set_result(None)
            else:
                blocked.update(waiter.tokens)

    def _discard(self, waiter: _PermitWaiter) -> None:
        """Remove a timed-out or cancelled waiter without leaking capacity."""

        if waiter in self._waiters:
            self._waiters.remove(waiter)
        if waiter.lease is not None and self.holds(waiter.lease):
            self.release(waiter.lease)
            return
        self._pump()

    @staticmethod
    def _refusal(
        scope_keys: tuple[PermitScopeKey, ...],
        ceiling: int,
        outcome: PermitOutcome,
    ) -> PermitLease:
        return PermitLease(
            outcome=outcome,
            scope_keys=scope_keys,
            effective_capacity=ceiling,
        )


__all__ = ("RunPermitManager",)
