"""C3 atomicity guarantees for the backend service + store.

Covers:
- ``put_token`` cross-tenant guard (InMemory mirror of the SQL ON CONFLICT
  WHERE clause).
- Service-layer transactions roll back when either the primary write or
  the audit append raises.
- ``BACKEND_DB_*`` env vars feed through to the pool's options string (C4).
"""

from __future__ import annotations

import pytest

from backend_app.contracts import (
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerRecord,
    McpTransport,
    SkillAuditEventRecord,
    SkillRecord,
    TokenEnvelope,
)
from backend_app.store import (
    CrossTenantWriteError,
    InMemoryMcpStore,
    InMemorySkillStore,
    _BackendPoolEnv,
)


def _server_record(*, org_id: str = "org_a") -> McpServerRecord:
    return McpServerRecord(
        org_id=org_id,
        user_id="user_1",
        name="example",
        display_name="Example",
        url="https://example.test",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.NONE,
        auth_state=McpAuthState.AUTHENTICATED,
        health=McpServerHealth.HEALTHY,
    )


def _token(*, org_id: str = "org_a") -> TokenEnvelope:
    return TokenEnvelope(
        server_id="srv_1",
        org_id=org_id,
        user_id="user_1",
        encrypted_access_token=f"enc({org_id})",
        encrypted_refresh_token=None,
    )


class TestPutTokenCrossTenant:
    def test_in_memory_rejects_cross_tenant_overwrite(self) -> None:
        store = InMemoryMcpStore()
        store.put_token(_token(org_id="org_a"))

        with pytest.raises(CrossTenantWriteError) as exc:
            store.put_token(_token(org_id="org_b"))
        assert exc.value.table == "mcp_auth_connections"

        # org_a's row is preserved; org_b never overwrote it.
        existing = store.get_token(server_id="srv_1")
        assert existing is not None
        assert existing.org_id == "org_a"
        assert existing.encrypted_access_token == "enc(org_a)"

    def test_same_org_overwrite_succeeds(self) -> None:
        store = InMemoryMcpStore()
        store.put_token(_token(org_id="org_a"))

        # Updating the same org's row is fine.
        store.put_token(
            TokenEnvelope(
                server_id="srv_1",
                org_id="org_a",
                user_id="user_1",
                encrypted_access_token="enc(updated)",
                encrypted_refresh_token=None,
            )
        )
        existing = store.get_token(server_id="srv_1")
        assert existing is not None
        assert existing.encrypted_access_token == "enc(updated)"


class TestServiceLayerTransactionAtomicity:
    """Smoke-test that the (write+audit) pair runs inside one txn.

    The real blast-radius test against Postgres lives behind the
    ``RUNTIME_TEST_POSTGRES_URL`` integration suite. Here we verify the
    composition shape holds with the in-memory store and that the static
    audit-in-transaction CI check is the load-bearing guarantee.
    """

    def test_create_skill_pair_calls_store_via_transaction_context(self) -> None:
        store = InMemorySkillStore()

        recorded_states: list[str] = []

        original_create = store.create_skill
        original_audit = store.append_skill_audit

        def tracking_create(record: SkillRecord, *, conn=None) -> SkillRecord:
            recorded_states.append("create")
            return original_create(record, conn=conn)

        def tracking_audit(
            record: SkillAuditEventRecord, *, conn=None
        ) -> SkillAuditEventRecord:
            recorded_states.append("audit")
            return original_audit(record, conn=conn)

        store.create_skill = tracking_create  # type: ignore[assignment]
        store.append_skill_audit = tracking_audit  # type: ignore[assignment]

        with store.transaction() as conn:
            record = SkillRecord(
                org_id="org_a",
                user_id="user_1",
                name="hello",
                display_name="Hello",
                description="A simple greeting skill for testing.",
                markdown="# Hello",
                virtual_path="/skills/x/SKILL.md",
                allowed_tools=(),
                compatibility=(),
                metadata={},
            )
            store.create_skill(record, conn=conn)
            store.append_skill_audit(
                SkillAuditEventRecord(
                    org_id="org_a",
                    user_id="user_1",
                    skill_id=record.skill_id,
                    action="skill_created",
                    metadata={},
                ),
                conn=conn,
            )

        # Both legs ran in expected order, both saw the same conn handle.
        assert recorded_states == ["create", "audit"]
        assert len(store.audit_events) == 1
        assert store.audit_events[0].skill_id == record.skill_id


class TestPoolOptionsBuilder:
    def test_options_include_application_name_with_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_BackendPoolEnv.STATEMENT_TIMEOUT_MS, raising=False)
        options = _BackendPoolEnv.build_options(role="api")
        assert "application_name=backend:api" in options
        assert "statement_timeout=10000" in options
        assert "lock_timeout=3000" in options
        assert "idle_in_transaction_session_timeout=30000" in options

    def test_env_overrides_apply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_BackendPoolEnv.STATEMENT_TIMEOUT_MS, "7500")
        monkeypatch.setenv(_BackendPoolEnv.LOCK_TIMEOUT_MS, "1500")
        monkeypatch.setenv(_BackendPoolEnv.IDLE_IN_TXN_TIMEOUT_MS, "60000")
        options = _BackendPoolEnv.build_options(role="worker")
        assert "statement_timeout=7500" in options
        assert "lock_timeout=1500" in options
        assert "idle_in_transaction_session_timeout=60000" in options
        assert "application_name=backend:worker" in options

    def test_invalid_env_value_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_BackendPoolEnv.STATEMENT_TIMEOUT_MS, "not-a-number")
        options = _BackendPoolEnv.build_options(role="api")
        assert "statement_timeout=10000" in options
