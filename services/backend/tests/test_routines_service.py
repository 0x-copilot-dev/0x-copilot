"""Unit tests for the routines service layer (Phase 5 P5-A1).

Coverage:

* State transition allowlist enforcement.
* Quota counting (per-USER cap).
* Trigger validation (cron / event / webhook).
* Permissions validation (manual_fire scope + project_id pairing).
* Audit redaction of the ``instructions`` field (routines-prd §7.5).
"""

from __future__ import annotations

import pytest

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.routines.service import (
    ACTIVE_ROUTINES_PER_USER_LIMIT,
    RoutineForbidden,
    RoutineInvalidRequest,
    RoutineInvalidTransition,
    RoutineNotFound,
    RoutineQuotaExceeded,
    RoutinesService,
)
from backend_app.routines.store import InMemoryRoutinesStore


def _svc(active_quota: int | None = None) -> RoutinesService:
    return RoutinesService(
        store=InMemoryRoutinesStore(),
        identity_store=InMemoryIdentityStore(),
        active_quota_per_user=active_quota or ACTIVE_ROUTINES_PER_USER_LIMIT,
    )


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "test routine",
        "instructions": "hello",
        "agent_id": "agent_x",
    }
    base.update(overrides)
    return base


class TestCreateValidation:
    def test_name_required(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1", caller_user_id="u1", payload=_payload(name="")
            )

    def test_name_too_long(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(name="x" * 81),
            )

    def test_instructions_too_long(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(instructions="x" * 16385),
            )

    def test_agent_id_required(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1", caller_user_id="u1", payload=_payload(agent_id="")
            )

    def test_manual_fire_invalid_scope(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(permissions={"manual_fire": "bogus"}),
            )

    def test_project_members_scope_requires_project_id(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(
                    permissions={"manual_fire": "project_members"},
                ),
            )

    def test_trigger_kind_validation(self) -> None:
        svc = _svc()
        # cron missing spec
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(triggers=[{"kind": "cron"}]),
            )
        # event missing source
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(triggers=[{"kind": "event", "event_name": "x"}]),
            )
        # webhook missing trigger_id
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(triggers=[{"kind": "webhook"}]),
            )
        # unknown kind
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(triggers=[{"kind": "telegram"}]),
            )

    def test_missed_fire_policy_validation(self) -> None:
        svc = _svc()
        with pytest.raises(RoutineInvalidRequest):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload=_payload(missed_fire_policy="hourly"),
            )

    def test_default_status_is_draft(self) -> None:
        svc = _svc()
        record = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload()
        )
        assert record.status == "draft"
        assert record.permissions == {"manual_fire": "owner"}
        assert record.missed_fire_policy == "fire_once"


class TestStateMachine:
    def test_draft_to_paused_rejected(self) -> None:
        svc = _svc()
        record = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload()
        )
        with pytest.raises(RoutineInvalidTransition):
            svc.update_routine(
                tenant_id="t1",
                caller_user_id="u1",
                caller_roles=(),
                routine_id=record.id,
                patch={"status": "paused"},
            )

    def test_active_to_errored_allowed(self) -> None:
        svc = _svc()
        record = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload()
        )
        active = svc.update_routine(
            tenant_id="t1",
            caller_user_id="u1",
            caller_roles=(),
            routine_id=record.id,
            patch={"status": "active"},
        )
        assert active.status == "active"
        errored = svc.update_routine(
            tenant_id="t1",
            caller_user_id="u1",
            caller_roles=(),
            routine_id=record.id,
            patch={"status": "errored", "pause_reason": "error"},
        )
        assert errored.status == "errored"
        assert errored.pause_reason == "error"

    def test_errored_to_active_rejected_must_go_through_draft(self) -> None:
        svc = _svc()
        record = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload()
        )
        svc.update_routine(
            tenant_id="t1",
            caller_user_id="u1",
            caller_roles=(),
            routine_id=record.id,
            patch={"status": "active"},
        )
        svc.update_routine(
            tenant_id="t1",
            caller_user_id="u1",
            caller_roles=(),
            routine_id=record.id,
            patch={"status": "errored", "pause_reason": "error"},
        )
        with pytest.raises(RoutineInvalidTransition):
            svc.update_routine(
                tenant_id="t1",
                caller_user_id="u1",
                caller_roles=(),
                routine_id=record.id,
                patch={"status": "active"},
            )


class TestQuota:
    def test_active_count_per_user_isolated(self) -> None:
        svc = _svc(active_quota=3)
        for i in range(3):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload={**_payload(name=f"r-{i}"), "status": "active"},
            )
        # u1's fourth → quota exceeded.
        with pytest.raises(RoutineQuotaExceeded):
            svc.create_routine(
                tenant_id="t1",
                caller_user_id="u1",
                payload={**_payload(name="r-4"), "status": "active"},
            )
        # u2 still has the full quota.
        record = svc.create_routine(
            tenant_id="t1",
            caller_user_id="u2",
            payload={**_payload(name="r-u2"), "status": "active"},
        )
        assert record.status == "active"

    def test_activating_via_patch_counts_against_quota(self) -> None:
        svc = _svc(active_quota=1)
        a = svc.create_routine(
            tenant_id="t1",
            caller_user_id="u1",
            payload={**_payload(name="a"), "status": "active"},
        )
        assert a.status == "active"
        b = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload(name="b")
        )
        with pytest.raises(RoutineQuotaExceeded):
            svc.update_routine(
                tenant_id="t1",
                caller_user_id="u1",
                caller_roles=(),
                routine_id=b.id,
                patch={"status": "active"},
            )

    def test_keeping_active_doesnt_recount(self) -> None:
        svc = _svc(active_quota=1)
        a = svc.create_routine(
            tenant_id="t1",
            caller_user_id="u1",
            payload={**_payload(name="a"), "status": "active"},
        )
        # Re-saving without changing status is fine.
        renamed = svc.update_routine(
            tenant_id="t1",
            caller_user_id="u1",
            caller_roles=(),
            routine_id=a.id,
            patch={"name": "renamed"},
        )
        assert renamed.status == "active"
        assert renamed.name == "renamed"


class TestAcl:
    def test_get_404_for_non_owner(self) -> None:
        svc = _svc()
        record = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload()
        )
        with pytest.raises(RoutineNotFound):
            svc.get_routine(
                tenant_id="t1",
                caller_user_id="u2",
                caller_roles=(),
                routine_id=record.id,
            )

    def test_update_403_for_admin_who_can_read(self) -> None:
        svc = _svc()
        record = svc.create_routine(
            tenant_id="t1", caller_user_id="u1", payload=_payload()
        )
        # Admin can READ, but writes are owner-only.
        with pytest.raises(RoutineForbidden):
            svc.update_routine(
                tenant_id="t1",
                caller_user_id="u_admin",
                caller_roles=("admin",),
                routine_id=record.id,
                patch={"status": "active"},
            )


class TestAuditRedaction:
    def test_instructions_redacted_in_audit_rows(self) -> None:
        store = InMemoryRoutinesStore()
        svc = RoutinesService(store=store, identity_store=InMemoryIdentityStore())
        record = svc.create_routine(
            tenant_id="t1",
            caller_user_id="u1",
            payload={**_payload(instructions="secret prompt content")},
        )
        audit = store.list_audit_for_routine(tenant_id="t1", routine_id=record.id)
        assert audit
        after = audit[0].after_state
        assert isinstance(after, dict)
        # routines-prd §7.5 — raw instructions never land in audit rows.
        assert after["instructions"] != "secret prompt content"
        assert isinstance(after["instructions"], dict)
        assert after["instructions"]["redacted"] is True
        assert after["instructions"]["length"] == len("secret prompt content")
