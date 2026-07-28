"""Process-local, backend-owned MCP remote-session pooling.

This module deliberately owns no OAuth state, HTTP route, database row, or
remote protocol implementation. ``McpRegistryService`` verifies the server,
uses ``TokenVault`` internally, and supplies a one-way credential subject to
the scope key. The service then acquires an opaque lease and invokes its
backend-only proxy callback through this pool. Neither a token nor a transport
handle appears in a lease, result, or diagnostic projection.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypeVar


class McpSessionPoolOutcome(StrEnum):
    """Closed, low-cardinality pool outcomes safe for metrics and callers."""

    ACQUIRED = "acquired"
    RELEASED = "released"
    SATURATED = "saturated"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    SCOPE_MISMATCH = "scope_mismatch"
    SHUTTING_DOWN = "shutting_down"
    KEEPALIVE_FAILED = "keepalive_failed"


class McpSessionPoolRejected(RuntimeError):
    """Raised when an opaque lease can no longer safely service an operation."""

    def __init__(self, outcome: McpSessionPoolOutcome) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome


class McpSessionTransport(Protocol):
    """Backend-only transport contract; no inventory/list operation is exposed."""

    def close(self) -> None: ...

    def keepalive(self) -> None: ...


class McpSessionTransportFactory(Protocol):
    """Construct a connected transport for a previously verified scope."""

    def connect(self, scope: "VerifiedMcpSessionScopeKey") -> McpSessionTransport: ...


@dataclass(frozen=True, slots=True)
class VerifiedMcpSessionScopeKey:
    """All compatibility facts that must match before a transport is reused.

    ``credential_subject`` is a SHA-256-like one-way identifier derived inside
    the backend from the vault-owned credential reference/ciphertext. It is not
    a plaintext token, server URL, or externally usable credential.
    """

    org_id: str
    profile_partition: str
    user_id: str
    server_id: str
    credential_subject: str
    auth_epoch: str
    transport_revision: str
    session_scope: str

    def __post_init__(self) -> None:
        for name, value in (
            ("org_id", self.org_id),
            ("profile_partition", self.profile_partition),
            ("user_id", self.user_id),
            ("server_id", self.server_id),
            ("credential_subject", self.credential_subject),
            ("auth_epoch", self.auth_epoch),
            ("transport_revision", self.transport_revision),
            ("session_scope", self.session_scope),
        ):
            if not value.strip():
                raise ValueError(f"MCP session scope {name} must be non-empty")
        if len(self.credential_subject) != 64 or any(
            char not in "0123456789abcdef" for char in self.credential_subject
        ):
            raise ValueError("MCP credential_subject must be a lowercase SHA-256")

    @classmethod
    def from_verified_credential_reference(
        cls,
        *,
        org_id: str,
        profile_partition: str,
        user_id: str,
        server_id: str,
        credential_reference: str,
        auth_epoch: str,
        transport_revision: str,
        session_scope: str,
    ) -> "VerifiedMcpSessionScopeKey":
        """Construct a scope from a vault-owned reference without exposing it."""

        subject = hashlib.sha256(credential_reference.encode("utf-8")).hexdigest()
        return cls(
            org_id=org_id,
            profile_partition=profile_partition,
            user_id=user_id,
            server_id=server_id,
            credential_subject=subject,
            auth_epoch=auth_epoch,
            transport_revision=transport_revision,
            session_scope=session_scope,
        )

    @property
    def fingerprint(self) -> str:
        """Return a one-way compatibility fingerprint for internal indexing."""

        canonical = "\x1f".join(
            (
                self.org_id,
                self.profile_partition,
                self.user_id,
                self.server_id,
                self.credential_subject,
                self.auth_epoch,
                self.transport_revision,
                self.session_scope,
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class McpSessionLease:
    """Opaque process-local capability; it intentionally contains no handle."""

    _lease_id: str

    def __repr__(self) -> str:
        return "McpSessionLease(<opaque>)"


@dataclass(frozen=True, slots=True)
class McpSessionAcquireResult:
    outcome: McpSessionPoolOutcome
    lease: McpSessionLease | None = None

    def __post_init__(self) -> None:
        if (self.outcome is McpSessionPoolOutcome.ACQUIRED) != (self.lease is not None):
            raise ValueError("only an acquired session-pool result carries a lease")


@dataclass(frozen=True, slots=True)
class McpSessionPoolDiagnostics:
    """Low-cardinality snapshot; ids, URLs, tokens, and handles are omitted."""

    total_sessions: int
    active_leases: int
    idle_sessions: int
    opening_sessions: int
    invalidated_sessions: int
    draining: bool


@dataclass(frozen=True, slots=True)
class McpDiscoveryPage:
    """Body-free pagination fact supplied by the internal MCP proxy adapter."""

    cursor: str | None
    next_cursor: str | None
    item_count: int

    def __post_init__(self) -> None:
        if self.item_count < 0:
            raise ValueError("MCP discovery item_count must be non-negative")


@dataclass(frozen=True, slots=True)
class McpDiscoveryObservation:
    """Completion evidence for a paginated discovery run, without page bodies."""

    page_count: int
    item_count: int
    complete: bool


@dataclass(frozen=True, slots=True)
class McpSessionPoolConfig:
    max_total_sessions: int = 64
    max_sessions_per_key: int = 4
    idle_ttl_seconds: float = 60.0
    absolute_ttl_seconds: float = 900.0
    invalidation_ttl_seconds: float = 900.0
    max_pre_dispatch_reconnects: int = 1

    def __post_init__(self) -> None:
        if self.max_total_sessions < 1 or self.max_sessions_per_key < 1:
            raise ValueError("MCP session-pool capacities must be positive")
        if (
            self.idle_ttl_seconds <= 0
            or self.absolute_ttl_seconds <= 0
            or self.invalidation_ttl_seconds <= 0
        ):
            raise ValueError("MCP session-pool TTLs must be positive")
        if self.max_pre_dispatch_reconnects < 0:
            raise ValueError("MCP reconnect budget must be non-negative")


@dataclass(slots=True)
class _SessionEntry:
    session_id: str
    scope: VerifiedMcpSessionScopeKey
    transport: McpSessionTransport
    created_at: float
    last_released_at: float
    active_lease_id: str | None = None
    invalidated: bool = False


@dataclass(slots=True)
class _LeaseEntry:
    lease: McpSessionLease
    session_id: str
    scope_fingerprint: str
    dispatch_committed: bool = False


@dataclass(frozen=True, slots=True)
class McpSessionDispatchFence:
    """One-way fence that must be committed before bytes may leave backend.

    A missing HTTP response cannot prove a remote server rejected the request.
    Callers therefore commit this fence immediately before network dispatch;
    only failures before the fence are local construction failures eligible for
    the bounded reconnect path.
    """

    _mark: Callable[[], None] = field(repr=False, compare=False)

    def commit(self) -> None:
        self._mark()


T = TypeVar("T")


class McpSessionPool:
    """Thread-safe, bounded local pool with explicit backend-only leases."""

    def __init__(
        self,
        *,
        factory: McpSessionTransportFactory,
        config: McpSessionPoolConfig = McpSessionPoolConfig(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._config = config
        self._clock = clock
        self._lock = threading.RLock()
        self._drained = threading.Condition(self._lock)
        self._sessions: dict[str, _SessionEntry] = {}
        self._leases: dict[str, _LeaseEntry] = {}
        self._opening_by_scope: dict[str, int] = {}
        self._opening_scopes: dict[str, VerifiedMcpSessionScopeKey] = {}
        self._invalidated_scope_expirations: dict[str, float] = {}
        self._draining = False

    def acquire(self, scope: VerifiedMcpSessionScopeKey) -> McpSessionAcquireResult:
        """Borrow a compatible idle session or establish one within capacity."""

        now = self._clock()
        with self._lock:
            self._reap_locked(now)
            if self._draining:
                return McpSessionAcquireResult(McpSessionPoolOutcome.SHUTTING_DOWN)
            if not self._scope_is_current_locked(scope):
                return McpSessionAcquireResult(McpSessionPoolOutcome.STALE)
            reusable = self._idle_for_scope_locked(scope)
            if reusable is not None:
                return self._lease_locked(reusable)
            if not self._reserve_open_locked(scope):
                return McpSessionAcquireResult(McpSessionPoolOutcome.SATURATED)

        try:
            transport = self._factory.connect(scope)
        except Exception:
            with self._lock:
                self._release_open_locked(scope)
            return McpSessionAcquireResult(McpSessionPoolOutcome.UNAVAILABLE)

        with self._lock:
            self._release_open_locked(scope)
            now = self._clock()
            if self._draining:
                self._close_transport(transport)
                return McpSessionAcquireResult(McpSessionPoolOutcome.SHUTTING_DOWN)
            if not self._scope_is_current_locked(scope):
                self._close_transport(transport)
                return McpSessionAcquireResult(McpSessionPoolOutcome.STALE)
            entry = _SessionEntry(
                session_id=secrets.token_urlsafe(18),
                scope=scope,
                transport=transport,
                created_at=now,
                last_released_at=now,
            )
            self._sessions[entry.session_id] = entry
            return self._lease_locked(entry)

    def release(
        self,
        lease: McpSessionLease,
        *,
        scope: VerifiedMcpSessionScopeKey,
    ) -> McpSessionPoolOutcome:
        """Return a lease after proving the caller still has the same scope."""

        with self._lock:
            lease_entry = self._leases.get(lease._lease_id)
            if lease_entry is None:
                return McpSessionPoolOutcome.STALE
            if not self._lease_scope_matches(lease_entry, scope):
                return McpSessionPoolOutcome.SCOPE_MISMATCH
            self._leases.pop(lease._lease_id, None)
            session = self._sessions.get(lease_entry.session_id)
            if session is None:
                self._drained.notify_all()
                return McpSessionPoolOutcome.STALE
            session.active_lease_id = None
            session.last_released_at = self._clock()
            if (
                self._draining
                or session.invalidated
                or self._expired(session, self._clock())
            ):
                self._remove_session_locked(session.session_id)
            self._drained.notify_all()
            return McpSessionPoolOutcome.RELEASED

    def cancel(
        self,
        lease: McpSessionLease,
        *,
        scope: VerifiedMcpSessionScopeKey,
    ) -> McpSessionPoolOutcome:
        """Cancel an in-flight lease and retire its transport from reuse."""

        with self._lock:
            lease_entry = self._leases.get(lease._lease_id)
            if lease_entry is None:
                return McpSessionPoolOutcome.STALE
            if not self._lease_scope_matches(lease_entry, scope):
                return McpSessionPoolOutcome.SCOPE_MISMATCH
            self._leases.pop(lease._lease_id, None)
            self._remove_session_locked(lease_entry.session_id)
            self._drained.notify_all()
            return McpSessionPoolOutcome.RELEASED

    def invoke(
        self,
        lease: McpSessionLease,
        *,
        scope: VerifiedMcpSessionScopeKey,
        operation: Callable[[McpSessionTransport, McpSessionDispatchFence], T],
    ) -> T:
        """Run one backend-owned operation with at most one safe reconnect.

        The callback must call ``fence.commit()`` immediately before request
        bytes can leave backend. Only an exception strictly before that fence
        may reconnect, and only within the configured bounded budget.
        """

        reconnects = 0
        while True:
            session = self._session_for_lease(lease, scope)
            fence = McpSessionDispatchFence(
                _mark=lambda: self._mark_dispatch_committed(lease, scope)
            )
            try:
                return operation(session.transport, fence)
            except Exception:
                if reconnects >= self._config.max_pre_dispatch_reconnects:
                    raise
                if self._lease_dispatched_or_stale(lease, scope):
                    raise
                if not self._reconnect_pre_dispatch(lease, scope):
                    raise
                reconnects += 1

    def keepalive_idle(self, *, limit: int = 1) -> McpSessionPoolOutcome:
        """Probe at most ``limit`` idle transports via their cheap keepalive.

        ``McpSessionTransport`` intentionally has no ``tools/list`` method;
        an implementation must use its protocol ping/no-op instead.
        """

        if limit < 1:
            raise ValueError("MCP keepalive limit must be positive")
        with self._lock:
            now = self._clock()
            self._reap_locked(now)
            candidates = sorted(
                (
                    item
                    for item in self._sessions.values()
                    if item.active_lease_id is None and not item.invalidated
                ),
                key=lambda item: (item.last_released_at, item.session_id),
            )[:limit]
        for item in candidates:
            try:
                item.transport.keepalive()
            except Exception:
                with self._lock:
                    self._remove_session_locked(item.session_id)
                return McpSessionPoolOutcome.KEEPALIVE_FAILED
        return McpSessionPoolOutcome.RELEASED

    def reap_expired(self) -> int:
        """Close expired idle *and active* transports and revoke leaked leases.

        Composition may call this from an existing backend lifecycle tick; the
        pool does not create a daemon thread. Active leases share the absolute
        session TTL, so a client that never calls release/cancel cannot retain
        capacity indefinitely.
        """

        with self._lock:
            before = len(self._sessions)
            self._reap_locked(self._clock())
            return before - len(self._sessions)

    def invalidate_scope(self, scope: VerifiedMcpSessionScopeKey) -> int:
        """Invalidate credential/config/auth-epoch compatible transports."""

        fingerprint = scope.fingerprint
        with self._lock:
            self._invalidate_scope_fingerprint_locked(fingerprint)
            invalidated = 0
            for session in tuple(self._sessions.values()):
                if session.scope.fingerprint != fingerprint:
                    continue
                session.invalidated = True
                invalidated += 1
                if session.active_lease_id is None:
                    self._remove_session_locked(session.session_id)
            return invalidated

    def invalidate_credential_subject(self, credential_subject: str) -> int:
        """Drop all sessions for a rotated/revoked credential subject digest."""

        with self._lock:
            invalidated = 0
            for session in tuple(self._sessions.values()):
                if session.scope.credential_subject != credential_subject:
                    continue
                self._invalidate_scope_fingerprint_locked(session.scope.fingerprint)
                session.invalidated = True
                invalidated += 1
                if session.active_lease_id is None:
                    self._remove_session_locked(session.session_id)
            for scope in self._opening_scopes.values():
                if scope.credential_subject == credential_subject:
                    self._invalidate_scope_fingerprint_locked(scope.fingerprint)
            return invalidated

    def export_lease_token(self, lease: McpSessionLease) -> str:
        """Return the only serialization-safe lease value for internal callers."""

        with self._lock:
            if lease._lease_id not in self._leases:
                raise McpSessionPoolRejected(McpSessionPoolOutcome.STALE)
            return lease._lease_id

    @staticmethod
    def import_lease_token(token: str) -> McpSessionLease:
        """Reconstruct an opaque lease capability without scope/handle details."""

        if len(token) < 16 or any(char.isspace() for char in token):
            raise ValueError("MCP session lease token is invalid")
        return McpSessionLease(_lease_id=token)

    def shutdown(self, *, timeout_seconds: float | None = None) -> bool:
        """Stop new leases, close idle sessions, and optionally wait for drain."""

        with self._lock:
            self._draining = True
            for session in tuple(self._sessions.values()):
                if session.active_lease_id is None:
                    self._remove_session_locked(session.session_id)
            if timeout_seconds is None:
                return not self._leases
            deadline = self._clock() + timeout_seconds
            while self._leases:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._drained.wait(timeout=remaining)
            return True

    def diagnostics(self) -> McpSessionPoolDiagnostics:
        """Return counts only; safe for metrics and operational logging."""

        with self._lock:
            self._reap_locked(self._clock())
            active = len(self._leases)
            total = len(self._sessions)
            return McpSessionPoolDiagnostics(
                total_sessions=total,
                active_leases=active,
                idle_sessions=total - active,
                opening_sessions=sum(self._opening_by_scope.values()),
                invalidated_sessions=sum(
                    item.invalidated for item in self._sessions.values()
                ),
                draining=self._draining,
            )

    def observe_paginated_discovery(
        self,
        lease: McpSessionLease,
        *,
        scope: VerifiedMcpSessionScopeKey,
        fetch_page: Callable[[McpSessionTransport, str | None], McpDiscoveryPage],
        max_pages: int = 100,
    ) -> McpDiscoveryObservation:
        """Observe complete discovery pagination without retaining item bodies."""

        if max_pages < 1:
            raise ValueError("MCP discovery max_pages must be positive")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0
        items = 0
        while True:
            page = self.invoke(
                lease,
                scope=scope,
                operation=lambda transport, fence: self._fetch_discovery_page(
                    fetch_page, transport, cursor, fence
                ),
            )
            if page.cursor != cursor:
                raise McpSessionPoolRejected(McpSessionPoolOutcome.STALE)
            pages += 1
            items += page.item_count
            if page.next_cursor is None:
                return McpDiscoveryObservation(
                    page_count=pages,
                    item_count=items,
                    complete=True,
                )
            if pages >= max_pages or page.next_cursor in seen_cursors:
                raise McpSessionPoolRejected(McpSessionPoolOutcome.STALE)
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor

    @staticmethod
    def _fetch_discovery_page(
        fetch_page: Callable[[McpSessionTransport, str | None], McpDiscoveryPage],
        transport: McpSessionTransport,
        cursor: str | None,
        fence: McpSessionDispatchFence,
    ) -> McpDiscoveryPage:
        fence.commit()
        return fetch_page(transport, cursor)

    def _idle_for_scope_locked(
        self, scope: VerifiedMcpSessionScopeKey
    ) -> _SessionEntry | None:
        matches = sorted(
            (
                item
                for item in self._sessions.values()
                if item.scope == scope
                and item.active_lease_id is None
                and not item.invalidated
            ),
            key=lambda item: (item.last_released_at, item.session_id),
        )
        return matches[0] if matches else None

    def _reserve_open_locked(self, scope: VerifiedMcpSessionScopeKey) -> bool:
        fingerprint = scope.fingerprint
        current_total = len(self._sessions) + sum(self._opening_by_scope.values())
        current_key = sum(
            item.scope == scope for item in self._sessions.values()
        ) + self._opening_by_scope.get(fingerprint, 0)
        if current_key >= self._config.max_sessions_per_key:
            return False
        if current_total >= self._config.max_total_sessions:
            self._evict_oldest_idle_locked()
            current_total = len(self._sessions) + sum(self._opening_by_scope.values())
            if current_total >= self._config.max_total_sessions:
                return False
        self._opening_by_scope[fingerprint] = (
            self._opening_by_scope.get(fingerprint, 0) + 1
        )
        self._opening_scopes[fingerprint] = scope
        return True

    def _release_open_locked(self, scope: VerifiedMcpSessionScopeKey) -> None:
        fingerprint = scope.fingerprint
        count = self._opening_by_scope.get(fingerprint, 0)
        if count <= 1:
            self._opening_by_scope.pop(fingerprint, None)
            self._opening_scopes.pop(fingerprint, None)
        else:
            self._opening_by_scope[fingerprint] = count - 1

    def _lease_locked(self, session: _SessionEntry) -> McpSessionAcquireResult:
        lease = McpSessionLease(
            _lease_id=secrets.token_urlsafe(18),
        )
        session.active_lease_id = lease._lease_id
        self._leases[lease._lease_id] = _LeaseEntry(
            lease=lease,
            session_id=session.session_id,
            scope_fingerprint=session.scope.fingerprint,
        )
        return McpSessionAcquireResult(McpSessionPoolOutcome.ACQUIRED, lease)

    def _session_for_lease(
        self,
        lease: McpSessionLease,
        scope: VerifiedMcpSessionScopeKey,
    ) -> _SessionEntry:
        with self._lock:
            lease_entry = self._leases.get(lease._lease_id)
            if lease_entry is None:
                raise McpSessionPoolRejected(McpSessionPoolOutcome.STALE)
            if not self._lease_scope_matches(lease_entry, scope):
                raise McpSessionPoolRejected(McpSessionPoolOutcome.SCOPE_MISMATCH)
            session = self._sessions.get(lease_entry.session_id)
            if (
                session is None
                or session.invalidated
                or self._expired(session, self._clock())
            ):
                raise McpSessionPoolRejected(McpSessionPoolOutcome.STALE)
            return session

    def _mark_dispatch_committed(
        self,
        lease: McpSessionLease,
        scope: VerifiedMcpSessionScopeKey,
    ) -> None:
        with self._lock:
            lease_entry = self._leases.get(lease._lease_id)
            if lease_entry is None or not self._lease_scope_matches(lease_entry, scope):
                raise McpSessionPoolRejected(McpSessionPoolOutcome.STALE)
            lease_entry.dispatch_committed = True

    def _lease_dispatched_or_stale(
        self, lease: McpSessionLease, scope: VerifiedMcpSessionScopeKey
    ) -> bool:
        with self._lock:
            lease_entry = self._leases.get(lease._lease_id)
            return (
                lease_entry is None
                or not self._lease_scope_matches(lease_entry, scope)
                or lease_entry.dispatch_committed
            )

    def _reconnect_pre_dispatch(
        self, lease: McpSessionLease, scope: VerifiedMcpSessionScopeKey
    ) -> bool:
        with self._lock:
            lease_entry = self._leases.get(lease._lease_id)
            if lease_entry is None or not self._lease_scope_matches(lease_entry, scope):
                return False
            old = self._sessions.get(lease_entry.session_id)
            if old is None:
                return False
            self._remove_session_locked(old.session_id)
            if not self._reserve_open_locked(scope):
                return False
        try:
            transport = self._factory.connect(scope)
        except Exception:
            with self._lock:
                self._release_open_locked(scope)
            return False
        with self._lock:
            self._release_open_locked(scope)
            lease_entry = self._leases.get(lease._lease_id)
            if (
                lease_entry is None
                or self._draining
                or not self._lease_scope_matches(lease_entry, scope)
                or not self._scope_is_current_locked(scope)
            ):
                self._close_transport(transport)
                return False
            now = self._clock()
            replacement = _SessionEntry(
                session_id=secrets.token_urlsafe(18),
                scope=scope,
                transport=transport,
                created_at=now,
                last_released_at=now,
                active_lease_id=lease._lease_id,
            )
            self._sessions[replacement.session_id] = replacement
            lease_entry.session_id = replacement.session_id
            lease_entry.dispatch_committed = False
            return True

    def _scope_is_current_locked(self, scope: VerifiedMcpSessionScopeKey) -> bool:
        self._prune_invalidations_locked(self._clock())
        return (
            not self._draining
            and scope.fingerprint not in self._invalidated_scope_expirations
        )

    @staticmethod
    def _lease_scope_matches(
        lease_entry: _LeaseEntry, scope: VerifiedMcpSessionScopeKey
    ) -> bool:
        return lease_entry.scope_fingerprint == scope.fingerprint

    def _invalidate_scope_fingerprint_locked(self, fingerprint: str) -> None:
        self._invalidated_scope_expirations[fingerprint] = (
            self._clock() + self._config.invalidation_ttl_seconds
        )

    def _prune_invalidations_locked(self, now: float) -> None:
        for fingerprint, expires_at in tuple(
            self._invalidated_scope_expirations.items()
        ):
            if expires_at <= now:
                self._invalidated_scope_expirations.pop(fingerprint, None)

    def _reap_locked(self, now: float) -> None:
        self._prune_invalidations_locked(now)
        for session in tuple(self._sessions.values()):
            if self._expired(session, now):
                if session.active_lease_id is not None:
                    self._leases.pop(session.active_lease_id, None)
                    self._drained.notify_all()
                self._remove_session_locked(session.session_id)

    def _expired(self, session: _SessionEntry, now: float) -> bool:
        return now - session.created_at >= self._config.absolute_ttl_seconds or (
            session.active_lease_id is None
            and now - session.last_released_at >= self._config.idle_ttl_seconds
        )

    def _evict_oldest_idle_locked(self) -> None:
        idle = sorted(
            (item for item in self._sessions.values() if item.active_lease_id is None),
            key=lambda item: (item.last_released_at, item.session_id),
        )
        if idle:
            self._remove_session_locked(idle[0].session_id)

    def _remove_session_locked(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            self._close_transport(session.transport)

    @staticmethod
    def _close_transport(transport: McpSessionTransport) -> None:
        try:
            transport.close()
        except Exception:
            pass


__all__ = (
    "McpSessionDispatchFence",
    "McpDiscoveryObservation",
    "McpDiscoveryPage",
    "McpSessionAcquireResult",
    "McpSessionLease",
    "McpSessionPool",
    "McpSessionPoolConfig",
    "McpSessionPoolDiagnostics",
    "McpSessionPoolOutcome",
    "McpSessionPoolRejected",
    "McpSessionTransport",
    "McpSessionTransportFactory",
    "VerifiedMcpSessionScopeKey",
)
