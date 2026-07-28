"""Focused qualification for body-free MCP diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from backend_app.contracts import (
    McpAuthMode,
    McpAuthState,
    McpRevisionReason,
    McpServerHealth,
    McpServerRecord,
    McpTransport,
)
from backend_app.mcp_revisions import McpRevisionAuthority
from backend_app.mcp_session_pool import (
    McpSessionDispatchFence,
    McpSessionPool,
    McpSessionPoolConfig,
    McpSessionPoolOutcome,
    McpSessionPoolRejected,
    VerifiedMcpSessionScopeKey,
)
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore


@dataclass
class _Recorder:
    phases: list[tuple[str, str]] = field(default_factory=list)
    counts: list[tuple[str, int, str]] = field(default_factory=list)
    pool_sizes: list[tuple[str, int]] = field(default_factory=list)

    def record_phase(
        self, *, phase: str, outcome: str, duration_seconds: float
    ) -> None:
        assert duration_seconds >= 0
        self.phases.append((phase, outcome))

    def record_count(self, *, measure: str, value: int, outcome: str) -> None:
        self.counts.append((measure, value, outcome))

    def record_pool_size(self, *, state: str, value: int) -> None:
        self.pool_sizes.append((state, value))


@dataclass
class _Transport:
    def close(self) -> None:
        return

    def keepalive(self) -> None:
        return


class _Factory:
    def connect(self, _scope: VerifiedMcpSessionScopeKey) -> _Transport:
        return _Transport()


class _FailingFactory:
    def connect(self, _scope: VerifiedMcpSessionScopeKey) -> _Transport:
        raise OSError("offline")


def _scope() -> VerifiedMcpSessionScopeKey:
    return VerifiedMcpSessionScopeKey.from_verified_credential_reference(
        org_id="org-safe",
        profile_partition="desktop",
        user_id="user-safe",
        server_id="server-safe",
        credential_reference="credential-reference-must-not-be-an-attribute",
        auth_epoch="epoch-1",
        transport_revision="revision-1",
        session_scope="internal-rpc",
    )


def test_pool_records_acquire_reuse_saturation_and_drain_without_scope_values() -> None:
    recorder = _Recorder()
    pool = McpSessionPool(
        factory=_Factory(),
        config=McpSessionPoolConfig(max_total_sessions=1, max_sessions_per_key=1),
        diagnostics=recorder,
    )
    scope = _scope()
    first = pool.acquire(scope)
    assert first.outcome is McpSessionPoolOutcome.ACQUIRED
    assert first.lease is not None
    assert pool.acquire(scope).outcome is McpSessionPoolOutcome.SATURATED
    assert pool.release(first.lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    second = pool.acquire(scope)
    assert second.lease is not None
    assert pool.shutdown(timeout_seconds=0) is False
    assert pool.release(second.lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    assert pool.shutdown(timeout_seconds=0)

    assert ("lease_acquisition", "acquired") in recorder.phases
    assert ("lease_reuse", "reused") in recorder.phases
    assert ("saturation", "rejected") in recorder.phases
    assert ("shutdown_drain", "timed_out") in recorder.phases
    assert ("shutdown_drain", "drained") in recorder.phases
    assert {state for state, _ in recorder.pool_sizes} >= {
        "active",
        "idle",
        "opening",
        "total",
    }
    flattened = repr((recorder.phases, recorder.counts, recorder.pool_sizes))
    assert "credential-reference" not in flattened
    assert scope.fingerprint not in flattened


def test_pool_records_closed_reuse_disabled_backout_without_scope_values() -> None:
    recorder = _Recorder()
    pool = McpSessionPool(
        factory=_Factory(),
        config=McpSessionPoolConfig(reuse_enabled=False),
        diagnostics=recorder,
    )
    acquired = pool.acquire(_scope())
    assert acquired.lease is not None

    assert (
        pool.release(acquired.lease, scope=_scope()) is McpSessionPoolOutcome.RELEASED
    )

    assert ("lease_reuse", "disabled_closed") in recorder.phases
    assert "credential-reference" not in repr(recorder.phases)


def test_pool_records_connect_failure_and_scope_invalidation() -> None:
    recorder = _Recorder()
    failed = McpSessionPool(factory=_FailingFactory(), diagnostics=recorder)
    assert failed.acquire(_scope()).outcome is McpSessionPoolOutcome.UNAVAILABLE
    assert ("session_initialization", "unavailable") in recorder.phases
    assert ("lease_acquisition", "unavailable") in recorder.phases

    pool = McpSessionPool(factory=_Factory(), diagnostics=recorder)
    scope = _scope()
    acquired = pool.acquire(scope)
    assert acquired.lease is not None
    assert pool.invalidate_scope(scope) == 1
    with pytest.raises(McpSessionPoolRejected):
        pool.invoke(acquired.lease, scope=scope, operation=lambda _t, _f: None)
    assert ("invalidation", "scope") in recorder.phases


def test_pool_distinguishes_safe_reconnect_from_ambiguous_dispatch() -> None:
    recorder = _Recorder()
    pool = McpSessionPool(factory=_Factory(), diagnostics=recorder)
    scope = _scope()
    lease = pool.acquire(scope).lease
    assert lease is not None
    attempts = 0

    def safe_retry(_transport: _Transport, fence: McpSessionDispatchFence) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("local construction failed")
        fence.commit()
        return "ok"

    assert pool.invoke(lease, scope=scope, operation=safe_retry) == "ok"
    assert ("reconnect", "reconnected") in recorder.phases

    def ambiguous_dispatch(
        _transport: _Transport, fence: McpSessionDispatchFence
    ) -> None:
        fence.commit()
        raise ConnectionError("remote response ambiguous")

    with pytest.raises(ConnectionError):
        pool.invoke(lease, scope=scope, operation=ambiguous_dispatch)
    assert ("reconnect", "ambiguous_or_stale") in recorder.phases


def test_registry_records_card_and_feed_counts_without_notice_identity() -> None:
    recorder = _Recorder()
    store = InMemoryMcpStore()
    service = McpRegistryService(store=store, mcp_diagnostics=recorder)
    record = McpServerRecord(
        org_id="org-safe",
        user_id="user-safe",
        name="safe_server",
        display_name="Safe server",
        url="https://mcp.invalid/rpc",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.NONE,
        auth_state=McpAuthState.AUTHENTICATED,
    )
    store.create_server(record)
    service.revision_authority.invalidate(
        org_id="org-safe",
        user_id="user-safe",
        server_id=record.server_id,
        reason=McpRevisionReason.CONFIG_CHANGED,
    )

    assert (
        len(service.list_internal_cards(org_id="org-safe", user_id="user-safe").servers)
        == 1
    )
    assert (
        len(
            service.feed_descriptor_revisions(
                org_id="org-safe", user_id="user-safe", after_cursor=None, limit=10
            ).notices
        )
        == 1
    )

    assert ("card_validation", "completed") in recorder.phases
    assert ("revision_feed", "page_returned") in recorder.phases
    assert ("card_validation", 1, "admitted") in recorder.counts
    assert ("revision_feed_notices", 1, "returned") in recorder.counts
    flattened = repr((recorder.phases, recorder.counts, recorder.pool_sizes))
    assert record.server_id not in flattened
    assert "https://" not in flattened


def test_registry_records_expired_feed_cursor_and_overrides_pool_recorder() -> None:
    service_recorder = _Recorder()
    supplied_recorder = _Recorder()
    pool = McpSessionPool(factory=_Factory(), diagnostics=supplied_recorder)
    store = InMemoryMcpStore()
    service = McpRegistryService(
        store=store,
        session_pool=pool,
        revision_authority=McpRevisionAuthority(retain_max=1),
        mcp_diagnostics=service_recorder,
    )
    authority = service.revision_authority
    authority.invalidate(
        org_id="org-safe",
        user_id="user-safe",
        server_id="server-one",
        reason=McpRevisionReason.CONFIG_CHANGED,
    )
    cursor = authority.feed(
        org_id="org-safe", user_id="user-safe", after_cursor=None, limit=1
    ).next_cursor
    authority.invalidate(
        org_id="org-safe",
        user_id="user-safe",
        server_id="server-two",
        reason=McpRevisionReason.CONFIG_CHANGED,
    )

    with pytest.raises(ValueError):
        service.feed_descriptor_revisions(
            org_id="org-safe", user_id="user-safe", after_cursor=cursor, limit=1
        )
    acquired = pool.acquire(_scope())
    assert acquired.lease is not None
    assert ("revision_feed", "cursor_expired") in service_recorder.phases
    assert ("lease_acquisition", "acquired") in service_recorder.phases
    assert supplied_recorder.phases == []


def test_feature_off_and_default_no_recorder_paths_do_not_enable_sessions() -> None:
    # The default recorder is safe when no collector is configured.
    default_pool = McpSessionPool(factory=_Factory())
    acquired = default_pool.acquire(_scope())
    assert acquired.lease is not None
    assert (
        default_pool.release(acquired.lease, scope=_scope())
        is McpSessionPoolOutcome.RELEASED
    )

    recorder = _Recorder()
    store = InMemoryMcpStore()
    service = McpRegistryService(store=store, mcp_diagnostics=recorder)
    disabled = McpServerRecord(
        org_id="org-safe",
        user_id="user-safe",
        name="feature_off",
        display_name="Feature off",
        url="https://mcp.invalid/rpc",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.NONE,
        auth_state=McpAuthState.AUTHENTICATED,
        health=McpServerHealth.DISABLED,
        enabled=False,
    )
    store.create_server(disabled)
    with pytest.raises(ValueError):
        service.create_internal_client_session(
            org_id="org-safe", user_id="user-safe", server_id=disabled.server_id
        )
    assert recorder.phases == []
