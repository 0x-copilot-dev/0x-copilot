"""Regression coverage for bounded pooled-MCP transport behavior."""

from __future__ import annotations

from dataclasses import dataclass

from backend_app.mcp_session_pool import (
    McpSessionPool,
    McpSessionPoolConfig,
    McpSessionPoolOutcome,
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


class _Factory:
    def __init__(self) -> None:
        self.transports: list[_Transport] = []

    def connect(self, _scope: VerifiedMcpSessionScopeKey) -> _Transport:
        transport = _Transport()
        self.transports.append(transport)
        return transport


def _scope(user: str = "user") -> VerifiedMcpSessionScopeKey:
    return VerifiedMcpSessionScopeKey.from_verified_credential_reference(
        org_id="org",
        profile_partition="backend-registry-compat-v1",
        user_id=user,
        server_id="server",
        credential_reference="vault-ref",
        auth_epoch="auth-epoch",
        transport_revision="transport-revision",
        session_scope="internal-rpc",
    )


def test_pool_metrics_distinguish_open_reuse_saturation_and_keepalive() -> None:
    factory = _Factory()
    pool = McpSessionPool(
        factory=factory,
        config=McpSessionPoolConfig(max_total_sessions=1, max_sessions_per_key=1),
    )
    scope = _scope()
    first = pool.acquire(scope)
    assert first.lease is not None
    assert pool.acquire(scope).outcome is McpSessionPoolOutcome.SATURATED
    assert pool.release(first.lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    second = pool.acquire(scope)
    assert second.lease is not None
    assert pool.release(second.lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    assert pool.keepalive_idle() is McpSessionPoolOutcome.RELEASED

    diagnostics = pool.diagnostics()
    assert diagnostics.opened_sessions == 1
    assert diagnostics.reused_sessions == 1
    assert diagnostics.saturated_acquires == 1
    assert diagnostics.keepalive_attempts == 1
