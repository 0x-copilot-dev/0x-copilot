"""Bounded, fair, run-scoped concurrency permits for the F6 executor.

Step 2 installed one conservative serial permit
(``agent_runtime.control_plane.context.RunSerialAdmission``) shared by every
graph-visible tool call in a run. These permits compose *inside* that permit:
they are acquired by children that the batch planner already declared
independent, and they can only ever narrow what runs at once. Nothing in this
module can widen the Step-2 admission.

Design commitments:

- **Digested keys.** A :class:`PermitScope` is a typed component tuple; its
  :class:`PermitScopeKey` is a domain-separated SHA-256 of the canonical
  component payload. Keys carry a kind plus lowercase hexadecimal only. Raw
  paths, connector URLs, user content, and credentials cannot reach a key,
  and the component fields are pattern-constrained so a URL or filesystem path
  cannot even be supplied.
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
  closed :class:`PermitOutcome` on a :class:`PermitLease`; it never raises.
  Queueing requires an explicit deadline. Only genuine programming faults
  (double release, releasing a refusal, cross-event-loop reuse) raise, and they
  raise typed :class:`PermitError` subclasses with safe public text.
- **Run-scoped and bounded.** One :class:`RunPermitManager` per run, on one
  event loop. Held-count entries are pruned to zero, waiters and active leases
  are capped, and :meth:`RunPermitManager.dispose` drops everything so no state
  survives a run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import ClassVar, Final, Self

from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class PermitBounds:
    """Hard, content-free bounds shared by every permit contract."""

    SERIAL_CAPACITY: Final[int] = 1
    MAX_CAPACITY: Final[int] = 16
    MAX_SCOPES_PER_REQUEST: Final[int] = 6
    MAX_WAITERS: Final[int] = 64
    MAX_ACTIVE_LEASES: Final[int] = 128
    MAX_TIMEOUT_SECONDS: Final[float] = 300.0
    IDENTIFIER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    DIGEST_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
    SCOPE_KEY_DOMAIN: Final[str] = "agent_runtime.capabilities.concurrency.permit.v1"
    LEASE_ID_PREFIX: Final[str] = "permit_lease_"


class PermitScopeKind(StrEnum):
    """Closed set of scopes a permit may be bounded at.

    Ordering is broad to narrow. ``GLOBAL`` bounds the whole run process,
    ``PROFILE`` bounds one deployment profile, and every scope narrower than
    ``PROFILE`` is additionally qualified by the verified subject so one
    subject can never consume another subject's capacity.
    """

    GLOBAL = "global"
    PROFILE = "profile"
    USER = "user"
    INSTALLATION = "installation"
    CONNECTOR = "connector"
    CAPABILITY = "capability"


class PermitErrorCode(StrEnum):
    """Stable, content-free permit failure codes."""

    DOUBLE_RELEASE = "permit_double_release"
    RELEASE_NOT_ADMITTED = "permit_release_not_admitted"
    EVENT_LOOP_MISMATCH = "permit_event_loop_mismatch"


class PermitError(RuntimeError):
    """Base permit fault carrying only a stable code and safe public text."""

    def __init__(self, code: PermitErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class PermitDoubleReleaseError(PermitError):
    """Raised when an admitted permit is released more than once."""

    def __init__(self) -> None:
        super().__init__(
            PermitErrorCode.DOUBLE_RELEASE,
            "Concurrency permit was already released.",
        )


class PermitNotAdmittedError(PermitError):
    """Raised when a refused permit is released as if it were held."""

    def __init__(self) -> None:
        super().__init__(
            PermitErrorCode.RELEASE_NOT_ADMITTED,
            "Concurrency permit was never admitted and cannot be released.",
        )


class PermitEventLoopMismatchError(PermitError):
    """Raised when one run's permit table is reused from another event loop."""

    def __init__(self) -> None:
        super().__init__(
            PermitErrorCode.EVENT_LOOP_MISMATCH,
            "Concurrency permits are bound to a single run event loop.",
        )


class PermitScopeKey(RuntimeContract):
    """Content-free, collision-resistant identity for one permit scope.

    The key exposes the scope kind and a digest only. It is safe to log, meter,
    and persist.
    """

    kind: PermitScopeKind
    digest: str = Field(pattern=PermitBounds.DIGEST_PATTERN)

    @property
    def token(self) -> str:
        """Return the stable ``kind:digest`` string used for internal tables."""

        return f"{self.kind.value}:{self.digest}"


class PermitScope(RuntimeContract):
    """One typed, pattern-constrained scope a permit may be bounded at.

    Component values are opaque identifiers, never bodies. ``subject_fingerprint``
    must already be a keyed SHA-256 digest produced by the control plane; the
    remaining components must be plain identifiers, which structurally excludes
    URLs, filesystem paths, and free text.
    """

    class Keys:
        """Canonical digest payload keys."""

        DOMAIN = "domain"
        KIND = "kind"
        PROFILE_ID = "profile_id"
        SUBJECT_FINGERPRINT = "subject_fingerprint"
        INSTALLATION_ID = "installation_id"
        CONNECTOR_ID = "connector_id"
        CAPABILITY_NAME = "capability_name"

    _COMPONENT_NAMES: ClassVar[tuple[str, ...]] = (
        Keys.PROFILE_ID,
        Keys.SUBJECT_FINGERPRINT,
        Keys.INSTALLATION_ID,
        Keys.CONNECTOR_ID,
        Keys.CAPABILITY_NAME,
    )
    _REQUIRED_COMPONENTS: ClassVar[dict[PermitScopeKind, tuple[str, ...]]] = {
        PermitScopeKind.GLOBAL: (),
        PermitScopeKind.PROFILE: (Keys.PROFILE_ID,),
        PermitScopeKind.USER: (Keys.PROFILE_ID, Keys.SUBJECT_FINGERPRINT),
        PermitScopeKind.INSTALLATION: (
            Keys.PROFILE_ID,
            Keys.SUBJECT_FINGERPRINT,
            Keys.INSTALLATION_ID,
        ),
        PermitScopeKind.CONNECTOR: (
            Keys.PROFILE_ID,
            Keys.SUBJECT_FINGERPRINT,
            Keys.CONNECTOR_ID,
        ),
        PermitScopeKind.CAPABILITY: (
            Keys.PROFILE_ID,
            Keys.SUBJECT_FINGERPRINT,
            Keys.CAPABILITY_NAME,
        ),
    }

    kind: PermitScopeKind
    profile_id: str | None = Field(
        default=None, pattern=PermitBounds.IDENTIFIER_PATTERN
    )
    subject_fingerprint: str | None = Field(
        default=None,
        pattern=PermitBounds.DIGEST_PATTERN,
    )
    installation_id: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )
    connector_id: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )
    capability_name: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )

    @model_validator(mode="after")
    def _components_match_kind(self) -> Self:
        required = self._REQUIRED_COMPONENTS[self.kind]
        for name in self._COMPONENT_NAMES:
            value = getattr(self, name)
            if name in required and value is None:
                raise ValueError(f"{self.kind.value} permit scope requires {name}")
            if name not in required and value is not None:
                raise ValueError(f"{self.kind.value} permit scope must not set {name}")
        return self

    def digest_payload(self) -> dict[str, str]:
        """Return the transient canonical body hashed into the scope key."""

        payload: dict[str, str] = {
            self.Keys.DOMAIN: PermitBounds.SCOPE_KEY_DOMAIN,
            self.Keys.KIND: self.kind.value,
        }
        for name in self._REQUIRED_COMPONENTS[self.kind]:
            component = getattr(self, name)
            payload[name] = str(component)
        return payload

    def key(self) -> PermitScopeKey:
        """Return the stable digested key for this scope."""

        return PermitScopeKey(
            kind=self.kind,
            digest=canonical_json_sha256(self.digest_payload()),
        )

    @classmethod
    def for_global(cls) -> Self:
        """Return the process-wide scope for this run."""

        return cls(kind=PermitScopeKind.GLOBAL)

    @classmethod
    def for_profile(cls, *, profile_id: str) -> Self:
        """Return the deployment-profile scope."""

        return cls(kind=PermitScopeKind.PROFILE, profile_id=profile_id)

    @classmethod
    def for_user(cls, *, profile_id: str, subject_fingerprint: str) -> Self:
        """Return the verified-subject scope."""

        return cls(
            kind=PermitScopeKind.USER,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
        )

    @classmethod
    def for_installation(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        installation_id: str,
    ) -> Self:
        """Return the subject-qualified installed-capability-source scope."""

        return cls(
            kind=PermitScopeKind.INSTALLATION,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            installation_id=installation_id,
        )

    @classmethod
    def for_connector(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        connector_id: str,
    ) -> Self:
        """Return the subject-qualified connector scope."""

        return cls(
            kind=PermitScopeKind.CONNECTOR,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            connector_id=connector_id,
        )

    @classmethod
    def for_capability(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        capability_name: str,
    ) -> Self:
        """Return the subject-qualified capability scope."""

        return cls(
            kind=PermitScopeKind.CAPABILITY,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            capability_name=capability_name,
        )


class PermitCapacity(RuntimeContract):
    """Configured concurrency ceiling for one scope kind."""

    kind: PermitScopeKind
    max_concurrency: int = Field(
        ge=PermitBounds.SERIAL_CAPACITY,
        le=PermitBounds.MAX_CAPACITY,
    )


class PermitCapacityPolicy(RuntimeContract):
    """Configuration-driven capacities with a conservative serial default.

    An empty policy is fully serial. Any scope kind without an explicit entry
    is serial, so unknown metadata can never authorize overlap.
    """

    capacities: tuple[PermitCapacity, ...] = Field(
        default=(),
        max_length=len(PermitScopeKind),
    )

    @model_validator(mode="after")
    def _kinds_are_unique(self) -> Self:
        kinds = tuple(entry.kind for entry in self.capacities)
        if len(set(kinds)) != len(kinds):
            raise ValueError("permit capacity kinds must be unique")
        return self

    def capacity_for(self, kind: PermitScopeKind) -> int:
        """Return the configured ceiling, or serial when unknown or absent."""

        for entry in self.capacities:
            if entry.kind is kind:
                return entry.max_concurrency
        return PermitBounds.SERIAL_CAPACITY

    @classmethod
    def serial(cls) -> Self:
        """Return the fully conservative policy."""

        return cls()

    @classmethod
    def from_limits(cls, limits: Mapping[PermitScopeKind, int]) -> Self:
        """Build a deterministic policy from a configuration mapping."""

        return cls(
            capacities=tuple(
                PermitCapacity(kind=kind, max_concurrency=limits[kind])
                for kind in sorted(limits, key=lambda entry: entry.value)
            )
        )


class PermitWaitMode(StrEnum):
    """Closed set of saturation behaviors a caller may request."""

    REFUSE_IF_SATURATED = "refuse_if_saturated"
    QUEUE = "queue"


class PermitOutcome(StrEnum):
    """Closed, deterministic result of one acquisition attempt."""

    ADMITTED = "admitted"
    QUEUED_ADMITTED = "queued_admitted"
    REFUSED_SATURATED = "refused_saturated"
    REFUSED_DEADLINE = "refused_deadline"
    REFUSED_QUEUE_FULL = "refused_queue_full"
    REFUSED_DISPOSED = "refused_disposed"

    @property
    def admitted(self) -> bool:
        """Return whether this outcome holds capacity."""

        return self in (PermitOutcome.ADMITTED, PermitOutcome.QUEUED_ADMITTED)


class PermitAcquisitionRequest(RuntimeContract):
    """One child's declared permit scopes and saturation policy."""

    scopes: tuple[PermitScope, ...] = Field(
        min_length=1,
        max_length=PermitBounds.MAX_SCOPES_PER_REQUEST,
    )
    wait_mode: PermitWaitMode = PermitWaitMode.REFUSE_IF_SATURATED
    timeout_seconds: float | None = Field(
        default=None,
        ge=0.0,
        le=PermitBounds.MAX_TIMEOUT_SECONDS,
    )
    max_parallelism: int | None = Field(
        default=None,
        ge=PermitBounds.SERIAL_CAPACITY,
        le=PermitBounds.MAX_CAPACITY,
    )

    @model_validator(mode="after")
    def _request_is_well_formed(self) -> Self:
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("permit scopes must be unique")
        if self.wait_mode is PermitWaitMode.QUEUE and self.timeout_seconds is None:
            raise ValueError("queued permit acquisition requires timeout_seconds")
        if (
            self.wait_mode is PermitWaitMode.REFUSE_IF_SATURATED
            and self.timeout_seconds is not None
        ):
            raise ValueError("refuse_if_saturated acquisition must not set a timeout")
        return self

    def scope_keys(self) -> tuple[PermitScopeKey, ...]:
        """Return this request's digested keys in a deterministic order."""

        return tuple(
            sorted(
                (scope.key() for scope in self.scopes),
                key=lambda key: (key.kind.value, key.digest),
            )
        )

    @classmethod
    def for_operation(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        capability_name: str,
        connector_id: str | None = None,
        installation_id: str | None = None,
        wait_mode: PermitWaitMode = PermitWaitMode.REFUSE_IF_SATURATED,
        timeout_seconds: float | None = None,
        max_parallelism: int | None = None,
    ) -> Self:
        """Build the canonical broad-to-narrow scope ladder for one child."""

        scopes: list[PermitScope] = [
            PermitScope.for_global(),
            PermitScope.for_profile(profile_id=profile_id),
            PermitScope.for_user(
                profile_id=profile_id,
                subject_fingerprint=subject_fingerprint,
            ),
        ]
        if installation_id is not None:
            scopes.append(
                PermitScope.for_installation(
                    profile_id=profile_id,
                    subject_fingerprint=subject_fingerprint,
                    installation_id=installation_id,
                )
            )
        if connector_id is not None:
            scopes.append(
                PermitScope.for_connector(
                    profile_id=profile_id,
                    subject_fingerprint=subject_fingerprint,
                    connector_id=connector_id,
                )
            )
        scopes.append(
            PermitScope.for_capability(
                profile_id=profile_id,
                subject_fingerprint=subject_fingerprint,
                capability_name=capability_name,
            )
        )
        return cls(
            scopes=tuple(scopes),
            wait_mode=wait_mode,
            timeout_seconds=timeout_seconds,
            max_parallelism=max_parallelism,
        )


class PermitLease(RuntimeContract):
    """Deterministic outcome of one acquisition, admitted or refused.

    A refused lease has no ``lease_id``. Saturation is reported here, never as
    an exception, so a caller can never mistake it for a tool failure.
    """

    outcome: PermitOutcome
    scope_keys: tuple[PermitScopeKey, ...] = Field(
        min_length=1,
        max_length=PermitBounds.MAX_SCOPES_PER_REQUEST,
    )
    effective_capacity: int = Field(
        ge=PermitBounds.SERIAL_CAPACITY,
        le=PermitBounds.MAX_CAPACITY,
    )
    lease_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _lease_identity_matches_outcome(self) -> Self:
        if self.outcome.admitted and self.lease_id is None:
            raise ValueError("an admitted permit lease requires a lease_id")
        if not self.outcome.admitted and self.lease_id is not None:
            raise ValueError("a refused permit lease must not carry a lease_id")
        return self

    @property
    def admitted(self) -> bool:
        """Return whether this lease holds capacity."""

        return self.outcome.admitted


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

    def _limit_for(self, kind: PermitScopeKind, requested: int | None) -> int:
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


__all__ = (
    "PermitAcquisitionRequest",
    "PermitBounds",
    "PermitCapacity",
    "PermitCapacityPolicy",
    "PermitDoubleReleaseError",
    "PermitError",
    "PermitErrorCode",
    "PermitEventLoopMismatchError",
    "PermitLease",
    "PermitNotAdmittedError",
    "PermitOutcome",
    "PermitScope",
    "PermitScopeKey",
    "PermitScopeKind",
    "PermitWaitMode",
    "RunPermitManager",
)
