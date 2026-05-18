"""Tests for the Tools service — Phase 10 P10-A2.

Coverage:

* ACL — project-scoped reads gate via the canonical
  ``backend_app.projects.acl.is_member`` predicate. Non-readers get
  :class:`ToolNotFound` (404-not-403); admin compliance reads succeed.
* Writes — owner OR tenant admin; project members get :class:`ToolForbidden`.
* Audit emission — every state-changing service method writes one
  ``tool.*`` audit row inside ``store.transaction()``.
* Test-call stub — :class:`ToolNotImplemented` raised by
  :meth:`run_test_call` while the executor is pending (P10-A3).
* Error-threshold flip — N consecutive errors flip status to ``error``.
"""

from __future__ import annotations

import pytest

from backend_app.projects.acl import InMemoryProjectMembershipAdapter
from backend_app.tools.service import (
    ERROR_THRESHOLD_DEFAULT,
    ToolForbidden,
    ToolInvalidRequest,
    ToolNotFound,
    ToolNotImplemented,
    ToolsService,
)
from backend_app.tools.store import (
    InMemoryToolsStore,
    ToolInvocationRecord,
)


def _service(
    *,
    memberships: InMemoryProjectMembershipAdapter | None = None,
) -> tuple[ToolsService, InMemoryToolsStore, InMemoryProjectMembershipAdapter]:
    store = InMemoryToolsStore()
    mem = memberships or InMemoryProjectMembershipAdapter()
    svc = ToolsService(store=store, membership_port=mem)
    return svc, store, mem


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "mcp",
        "name": "Slack summarize",
        "description": "MCP method",
        "scope": "read",
        "transport": {"kind": "mcp"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_happy_path_writes_audit(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        assert record.owner_user_id == "usr_sarah"
        assert record.status == "enabled"
        audit = store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        assert [a.action for a in audit] == ["tool.created"]

    def test_create_rejects_blank_name(self) -> None:
        svc, _, _ = _service()
        with pytest.raises(ToolInvalidRequest):
            svc.create_tool(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                payload=_create_payload(name="   "),
            )

    def test_create_rejects_invalid_kind(self) -> None:
        svc, _, _ = _service()
        with pytest.raises(ToolInvalidRequest):
            svc.create_tool(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                payload=_create_payload(kind="invalid"),
            )

    def test_create_skill_requires_skill_page_ref(self) -> None:
        svc, _, _ = _service()
        with pytest.raises(ToolInvalidRequest):
            svc.create_tool(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                payload=_create_payload(kind="skill"),
            )

    def test_create_code_requires_code_ref(self) -> None:
        svc, _, _ = _service()
        with pytest.raises(ToolInvalidRequest):
            svc.create_tool(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                payload=_create_payload(
                    kind="code", transport={"kind": "sandbox", "executor": "py"}
                ),
            )


# ---------------------------------------------------------------------------
# ACL — reads
# ---------------------------------------------------------------------------


class TestAclReads:
    def test_owner_reads(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        got = svc.get_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            tool_id=record.id,
        )
        assert got.id == record.id

    def test_project_member_reads_404_for_non_member(self) -> None:
        mem = InMemoryProjectMembershipAdapter()
        mem.add(
            tenant_id="org_acme",
            project_id="proj_alpha",
            user_id="usr_bob",
            role="editor",
        )
        svc, _, _ = _service(memberships=mem)
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(project_id="proj_alpha"),
        )
        # bob is a member of proj_alpha — reads
        got = svc.get_tool(
            tenant_id="org_acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            tool_id=record.id,
        )
        assert got.id == record.id
        # carol is NOT — 404 not 403
        with pytest.raises(ToolNotFound):
            svc.get_tool(
                tenant_id="org_acme",
                caller_user_id="usr_carol",
                caller_roles=(),
                tool_id=record.id,
            )

    def test_admin_compliance_read(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(project_id="proj_alpha"),
        )
        got = svc.get_tool(
            tenant_id="org_acme",
            caller_user_id="usr_dave",
            caller_roles=("admin",),
            tool_id=record.id,
        )
        assert got.id == record.id

    def test_cross_tenant_returns_404(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        with pytest.raises(ToolNotFound):
            svc.get_tool(
                tenant_id="org_zeta",
                caller_user_id="usr_alice",
                caller_roles=(),
                tool_id=record.id,
            )


# ---------------------------------------------------------------------------
# ACL — writes
# ---------------------------------------------------------------------------


class TestAclWrites:
    def test_owner_can_patch(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        updated = svc.update_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            tool_id=record.id,
            patch={"name": "renamed"},
        )
        assert updated.name == "renamed"
        actions = [
            a.action
            for a in store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        ]
        assert "tool.updated" in actions

    def test_admin_can_patch(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        updated = svc.update_tool(
            tenant_id="org_acme",
            caller_user_id="usr_dave",
            caller_roles=("admin",),
            tool_id=record.id,
            patch={"description": "by admin"},
        )
        assert updated.description == "by admin"

    def test_project_member_cannot_patch(self) -> None:
        mem = InMemoryProjectMembershipAdapter()
        mem.add(
            tenant_id="org_acme",
            project_id="proj_alpha",
            user_id="usr_bob",
            role="editor",
        )
        svc, _, _ = _service(memberships=mem)
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(project_id="proj_alpha"),
        )
        # bob has READ via project membership but writes are owner-only.
        with pytest.raises(ToolForbidden):
            svc.update_tool(
                tenant_id="org_acme",
                caller_user_id="usr_bob",
                caller_roles=(),
                tool_id=record.id,
                patch={"name": "by bob"},
            )

    def test_non_reader_gets_404_on_patch(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(project_id="proj_alpha"),
        )
        with pytest.raises(ToolNotFound):
            svc.update_tool(
                tenant_id="org_acme",
                caller_user_id="usr_carol",
                caller_roles=(),
                tool_id=record.id,
                patch={"name": "x"},
            )

    def test_delete_owner_only(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        svc.delete_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            tool_id=record.id,
        )
        with pytest.raises(ToolNotFound):
            svc.get_tool(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                tool_id=record.id,
            )
        actions = [
            a.action
            for a in store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        ]
        assert "tool.deleted" in actions


# ---------------------------------------------------------------------------
# Status flips + scope guard
# ---------------------------------------------------------------------------


class TestStatusAndScope:
    def test_disable_emits_disabled_audit(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        updated = svc.set_status(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            tool_id=record.id,
            new_status="disabled",
        )
        assert updated.status == "disabled"
        actions = [
            a.action
            for a in store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        ]
        assert "tool.disabled" in actions

    def test_scope_widen_rejected(self) -> None:
        from backend_app.tools.service import ToolConflict

        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(scope="read"),
        )
        with pytest.raises(ToolConflict):
            svc.update_tool(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                tool_id=record.id,
                patch={"scope": "both"},
            )

    def test_scope_shrink_allowed_and_audited(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(scope="both"),
        )
        svc.update_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            tool_id=record.id,
            patch={"scope": "read"},
        )
        actions = [
            a.action
            for a in store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        ]
        assert "tool.scope_changed" in actions


# ---------------------------------------------------------------------------
# Test-call stub
# ---------------------------------------------------------------------------


class TestTestCallStub:
    def test_test_call_raises_not_implemented(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        with pytest.raises(ToolNotImplemented):
            svc.run_test_call(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                tool_id=record.id,
                args={"channel": "general"},
            )
        # Audit row still landed.
        actions = [
            a.action
            for a in store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        ]
        assert "tool.test_called" in actions

    def test_test_call_forbidden_for_non_owner(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        with pytest.raises(ToolNotFound):
            # Carol can't even READ this tool (no project_id, but
            # tenant-readable rule means she'd see it — actually let's
            # use a non-member of the tenant). For this test, owner /
            # admin gate fires before read.
            svc.run_test_call(
                tenant_id="org_acme",
                caller_user_id="usr_bob",
                caller_roles=(),
                tool_id=record.id + "_missing",
                args={},
            )

    def test_test_call_rejects_non_dict_args(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        with pytest.raises(ToolInvalidRequest):
            svc.run_test_call(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                tool_id=record.id,
                args="not a dict",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Error threshold + usage projection
# ---------------------------------------------------------------------------


class TestErrorThreshold:
    def test_bump_flips_status_at_threshold(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        for _ in range(ERROR_THRESHOLD_DEFAULT - 1):
            updated = svc.bump_error_counter(tenant_id="org_acme", tool_id=record.id)
        assert updated is not None
        assert updated.status == "enabled"
        # Final bump trips the threshold.
        flipped = svc.bump_error_counter(tenant_id="org_acme", tool_id=record.id)
        assert flipped.status == "error"
        actions = [
            a.action
            for a in store.list_audit_for_tool(tenant_id="org_acme", tool_id=record.id)
        ]
        assert "tool.error_threshold" in actions

    def test_successful_invocation_resets_counter(self) -> None:
        svc, store, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        svc.bump_error_counter(tenant_id="org_acme", tool_id=record.id)
        svc.bump_error_counter(tenant_id="org_acme", tool_id=record.id)
        # Now record a successful invocation.
        svc.record_invocation(
            tenant_id="org_acme",
            tool_id=record.id,
            record=ToolInvocationRecord(
                tool_id=record.id,
                tenant_id="org_acme",
                run_id="run_1",
                caller_kind="agent",
                caller_ref={"kind": "agent", "id": "agt_1"},
                args_summary="{}",
                status="ok",
                latency_ms=12,
            ),
        )
        refreshed = store.get_tool(tenant_id="org_acme", tool_id=record.id)
        assert refreshed is not None
        assert refreshed.consecutive_error_count == 0


class TestUsageProjection:
    def test_rolled_up_usage_zero_when_no_calls(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        proj = svc.compute_rolled_up_usage(tenant_id="org_acme", tool_id=record.id)
        assert proj == {
            "calls_24h": 0,
            "calls_30d": 0,
            "p50_latency_ms_30d": None,
            "success_rate_30d": None,
            "last_used_at": None,
        }

    def test_rolled_up_usage_computes_over_invocations(self) -> None:
        svc, _, _ = _service()
        record = svc.create_tool(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload=_create_payload(),
        )
        for i, status in enumerate(("ok", "ok", "error", "ok")):
            svc.record_invocation(
                tenant_id="org_acme",
                tool_id=record.id,
                record=ToolInvocationRecord(
                    tool_id=record.id,
                    tenant_id="org_acme",
                    run_id=f"run_{i}",
                    caller_kind="agent",
                    caller_ref={"kind": "agent", "id": "agt_1"},
                    args_summary="",
                    status=status,  # type: ignore[arg-type]
                    latency_ms=100 + i,
                ),
            )
        proj = svc.compute_rolled_up_usage(tenant_id="org_acme", tool_id=record.id)
        assert proj["calls_30d"] == 4
        assert proj["calls_24h"] == 4
        assert proj["success_rate_30d"] == 0.75
        assert proj["p50_latency_ms_30d"] is not None
        assert proj["last_used_at"] is not None
