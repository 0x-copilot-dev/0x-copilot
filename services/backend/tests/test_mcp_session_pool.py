"""Conformance tests for the backend-owned MCP remote-session pool."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading

import pytest

from backend_app.mcp_session_pool import (
    McpDiscoveryPage,
    McpSessionAcquireResult,
    McpSessionPool,
    McpSessionPoolConfig,
    McpSessionPoolOutcome,
    McpSessionPoolRejected,
    VerifiedMcpSessionScopeKey,
)


@dataclass
class _Transport:
    closed: int = 0
    keepalives: int = 0

    def close(self) -> None:
        self.closed += 1

    def keepalive(self) -> None:
        self.keepalives += 1


@dataclass
class _Factory:
    transports: list[_Transport] = field(default_factory=list)
    unavailable: bool = False

    def connect(self, _scope: VerifiedMcpSessionScopeKey) -> _Transport:
        if self.unavailable:
            raise OSError("unavailable")
        transport = _Transport()
        self.transports.append(transport)
        return transport


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _BlockingFactory(_Factory):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release_connect = threading.Event()
        self._start_lock = threading.Lock()
        self.start_count = 0

    def connect(self, scope: VerifiedMcpSessionScopeKey) -> _Transport:
        with self._start_lock:
            self.start_count += 1
            self.started.set()
        assert self.release_connect.wait(timeout=1)
        return super().connect(scope)


def _scope(
    *,
    user: str = "user-1",
    server: str = "server-1",
    credential_ref: str = "vault-ref-1",
    auth_epoch: str = "auth-1",
    revision: str = "transport-v1",
) -> VerifiedMcpSessionScopeKey:
    return VerifiedMcpSessionScopeKey.from_verified_credential_reference(
        org_id="org-1",
        profile_partition="prod-compatible",
        user_id=user,
        server_id=server,
        credential_reference=credential_ref,
        auth_epoch=auth_epoch,
        transport_revision=revision,
        session_scope="internal-rpc",
    )


def _acquire(pool: McpSessionPool, scope: VerifiedMcpSessionScopeKey):
    result = pool.acquire(scope)
    assert result.outcome is McpSessionPoolOutcome.ACQUIRED
    assert result.lease is not None
    return result.lease


def test_verified_scope_fingerprint_never_contains_credential_reference() -> None:
    scope = _scope(credential_ref="ciphertext-or-connection-reference")

    assert len(scope.credential_subject) == 64
    assert "ciphertext" not in scope.credential_subject
    assert "connection" not in scope.fingerprint


def test_reuses_only_the_exact_verified_scope_and_hides_transport_handle() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)

    assert pool.release(lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    reused = _acquire(pool, scope)
    assert len(factory.transports) == 1
    assert "transport" not in repr(reused).lower()
    assert "vault-ref-1" not in repr(reused)
    assert pool.release(reused, scope=scope) is McpSessionPoolOutcome.RELEASED

    different_scope = _scope(auth_epoch="auth-2")
    different = _acquire(pool, different_scope)
    assert len(factory.transports) == 2
    assert (
        pool.release(different, scope=different_scope) is McpSessionPoolOutcome.RELEASED
    )


def test_lease_serializes_as_an_opaque_token_without_scope_fingerprint() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)

    token = pool.export_lease_token(lease)
    restored = pool.import_lease_token(token)
    assert scope.fingerprint not in token
    assert scope.credential_subject not in token
    assert "fingerprint" not in repr(restored)
    assert pool.release(restored, scope=scope) is McpSessionPoolOutcome.RELEASED


def test_global_and_per_key_capacity_have_deterministic_saturation() -> None:
    factory = _Factory()
    pool = McpSessionPool(
        factory=factory,
        config=McpSessionPoolConfig(max_total_sessions=2, max_sessions_per_key=1),
    )
    first_scope = _scope()
    first = _acquire(pool, first_scope)

    assert pool.acquire(first_scope).outcome is McpSessionPoolOutcome.SATURATED
    second_scope = _scope(server="server-2")
    second = _acquire(pool, second_scope)
    assert (
        pool.acquire(_scope(server="server-3")).outcome
        is McpSessionPoolOutcome.SATURATED
    )
    assert pool.diagnostics().active_leases == 2

    assert pool.release(first, scope=first_scope) is McpSessionPoolOutcome.RELEASED
    assert pool.release(second, scope=second_scope) is McpSessionPoolOutcome.RELEASED


def test_opening_reservation_is_thread_safe_and_counts_against_capacity() -> None:
    factory = _BlockingFactory()
    pool = McpSessionPool(
        factory=factory,
        config=McpSessionPoolConfig(max_total_sessions=1, max_sessions_per_key=1),
    )
    scope = _scope()
    result: list[McpSessionAcquireResult] = []

    def first_acquire() -> None:
        result.append(pool.acquire(scope))

    worker = threading.Thread(target=first_acquire)
    worker.start()
    assert factory.started.wait(timeout=1)
    assert pool.acquire(scope).outcome is McpSessionPoolOutcome.SATURATED
    factory.release_connect.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert len(result) == 1
    first = result[0]
    assert first.lease is not None
    assert pool.release(first.lease, scope=scope) is McpSessionPoolOutcome.RELEASED


def test_simultaneous_opens_never_oversubscribe_global_or_per_key_capacity() -> None:
    factory = _BlockingFactory()
    pool = McpSessionPool(
        factory=factory,
        config=McpSessionPoolConfig(max_total_sessions=2, max_sessions_per_key=1),
    )
    scopes = (
        _scope(server="server-a"),
        _scope(server="server-a"),
        _scope(server="server-b"),
        _scope(server="server-c"),
    )
    barrier = threading.Barrier(len(scopes))
    results: list[tuple[VerifiedMcpSessionScopeKey, McpSessionAcquireResult]] = []
    results_lock = threading.Lock()

    def acquire(scope: VerifiedMcpSessionScopeKey) -> None:
        barrier.wait(timeout=1)
        result = pool.acquire(scope)
        with results_lock:
            results.append((scope, result))

    workers = [threading.Thread(target=acquire, args=(scope,)) for scope in scopes]
    for worker in workers:
        worker.start()
    assert factory.started.wait(timeout=1)
    factory.release_connect.set()
    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive()

    acquired = [
        (scope, result)
        for scope, result in results
        if result.outcome is McpSessionPoolOutcome.ACQUIRED
    ]
    assert len(acquired) == 2
    assert factory.start_count == 2
    assert (
        sum(result.outcome is McpSessionPoolOutcome.SATURATED for _, result in results)
        == 2
    )
    assert sum(scope.server_id == "server-a" for scope, _ in acquired) <= 1
    for scope, result in acquired:
        assert result.lease is not None
        assert pool.release(result.lease, scope=scope) is McpSessionPoolOutcome.RELEASED


def test_idle_and_absolute_ttl_close_stale_transports() -> None:
    clock = _Clock()
    factory = _Factory()
    pool = McpSessionPool(
        factory=factory,
        clock=clock,
        config=McpSessionPoolConfig(
            idle_ttl_seconds=5,
            absolute_ttl_seconds=10,
        ),
    )
    scope = _scope()
    lease = _acquire(pool, scope)
    pool.release(lease, scope=scope)
    clock.advance(5)

    replacement = _acquire(pool, scope)
    assert len(factory.transports) == 2
    assert factory.transports[0].closed == 1
    pool.release(replacement, scope=scope)
    clock.advance(10)
    newest = _acquire(pool, scope)
    assert len(factory.transports) == 3
    assert factory.transports[1].closed == 1
    pool.release(newest, scope=scope)


def test_reconnects_once_only_before_backend_dispatch_fence() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)
    attempts = 0

    def operation(_transport: _Transport, fence) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("local pre-dispatch construction failed")
        fence.commit()
        return "ok"

    assert pool.invoke(lease, scope=scope, operation=operation) == "ok"
    assert attempts == 2
    assert len(factory.transports) == 2
    assert factory.transports[0].closed == 1
    pool.release(lease, scope=scope)


def test_never_reconnects_after_backend_dispatch_fence() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)

    def operation(_transport: _Transport, fence) -> None:
        fence.commit()
        raise ConnectionError("missing HTTP response is ambiguous")

    with pytest.raises(ConnectionError):
        pool.invoke(lease, scope=scope, operation=operation)
    assert len(factory.transports) == 1
    pool.release(lease, scope=scope)


def test_scope_mismatch_cancellation_and_invalidation_fail_closed() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    other = _scope(user="user-2")
    lease = _acquire(pool, scope)

    assert pool.release(lease, scope=other) is McpSessionPoolOutcome.SCOPE_MISMATCH
    assert pool.cancel(lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    assert factory.transports[0].closed == 1
    assert pool.release(lease, scope=scope) is McpSessionPoolOutcome.STALE

    active = _acquire(pool, scope)
    assert pool.invalidate_scope(scope) == 1
    with pytest.raises(McpSessionPoolRejected) as caught:
        pool.invoke(active, scope=scope, operation=lambda _transport, _fence: None)
    assert caught.value.outcome is McpSessionPoolOutcome.STALE
    assert pool.release(active, scope=scope) is McpSessionPoolOutcome.RELEASED
    assert pool.acquire(scope).outcome is McpSessionPoolOutcome.STALE


def test_invalidation_tombstones_are_ttl_bounded() -> None:
    clock = _Clock()
    factory = _Factory()
    pool = McpSessionPool(
        factory=factory,
        clock=clock,
        config=McpSessionPoolConfig(invalidation_ttl_seconds=2),
    )
    scope = _scope()
    lease = _acquire(pool, scope)
    pool.release(lease, scope=scope)
    pool.invalidate_scope(scope)
    assert pool.acquire(scope).outcome is McpSessionPoolOutcome.STALE

    clock.advance(2)
    replacement = _acquire(pool, scope)
    assert pool.release(replacement, scope=scope) is McpSessionPoolOutcome.RELEASED


def test_keepalive_uses_only_transport_ping_contract_and_retires_failures() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)
    pool.release(lease, scope=scope)

    assert pool.keepalive_idle() is McpSessionPoolOutcome.RELEASED
    assert factory.transports[0].keepalives == 1
    assert not hasattr(factory.transports[0], "tools_list")


def test_shutdown_drains_active_leases_and_rejects_new_work() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    active = _acquire(pool, scope)

    assert not pool.shutdown(timeout_seconds=0)
    assert (
        pool.acquire(_scope(server="server-2")).outcome
        is McpSessionPoolOutcome.SHUTTING_DOWN
    )
    assert pool.release(active, scope=scope) is McpSessionPoolOutcome.RELEASED
    assert pool.shutdown(timeout_seconds=0)
    assert factory.transports[0].closed == 1


def test_reaper_revokes_abandoned_active_leases_at_absolute_ttl() -> None:
    clock = _Clock()
    factory = _Factory()
    pool = McpSessionPool(
        factory=factory,
        clock=clock,
        config=McpSessionPoolConfig(absolute_ttl_seconds=3),
    )
    scope = _scope()
    leaked = _acquire(pool, scope)
    clock.advance(3)

    assert pool.reap_expired() == 1
    assert pool.diagnostics().active_leases == 0
    assert factory.transports[0].closed == 1
    assert pool.release(leaked, scope=scope) is McpSessionPoolOutcome.STALE
    replacement = _acquire(pool, scope)
    assert pool.release(replacement, scope=scope) is McpSessionPoolOutcome.RELEASED


def test_complete_paginated_discovery_observes_only_body_free_facts() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)
    seen: list[str | None] = []

    def fetch(_transport: _Transport, cursor: str | None) -> McpDiscoveryPage:
        seen.append(cursor)
        if cursor is None:
            return McpDiscoveryPage(cursor=None, next_cursor="next", item_count=2)
        return McpDiscoveryPage(cursor="next", next_cursor=None, item_count=1)

    observation = pool.observe_paginated_discovery(
        lease,
        scope=scope,
        fetch_page=fetch,
    )
    assert seen == [None, "next"]
    assert observation.page_count == 2
    assert observation.item_count == 3
    assert observation.complete
    pool.release(lease, scope=scope)


def test_discovery_cycle_is_stale_not_partially_complete() -> None:
    factory = _Factory()
    pool = McpSessionPool(factory=factory)
    scope = _scope()
    lease = _acquire(pool, scope)

    def fetch(_transport: _Transport, cursor: str | None) -> McpDiscoveryPage:
        return McpDiscoveryPage(cursor=cursor, next_cursor="loop", item_count=1)

    with pytest.raises(McpSessionPoolRejected) as caught:
        pool.observe_paginated_discovery(lease, scope=scope, fetch_page=fetch)
    assert caught.value.outcome is McpSessionPoolOutcome.STALE
    pool.release(lease, scope=scope)
