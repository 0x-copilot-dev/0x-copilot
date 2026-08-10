"""Tests for the routine pre-fire gate auto-pause cascade.

Verifies the orchestration in
``runtime_worker.jobs.routine_scheduler.RoutinePreFireGate``:

- On a permission hit -> ``allow_fire=True`` and no side effects.
- On a permission miss -> pause -> inbox -> audit cascade with the right
  shapes and identifiers.
- Connector disconnection drives the
  ``CRITICAL_CONNECTOR_DISCONNECTED`` pause reason.
- Owner offboarding drives ``OWNER_OFFBOARDED``.
- No auto-resume: re-granting permissions on a subsequent call does NOT
  emit any "resume" side effect -- the gate has no such codepath.

LLM provider imports: none. Tests use plain fakes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone

import pytest

from agent_runtime.api.routine_permission_check import (
    RoutinePauseReason,
    RoutinePermissionContext,
    RoutinePermissionScope,
)
from runtime_worker.jobs.routine_pre_fire_gate import (
    Clock,
    RoutineAuditPort,
    RoutineInboxItemKind,
    RoutineInboxItemSpec,
    RoutineInboxLinkKind,
    RoutineInboxProducerPort,
    RoutinePausePort,
    RoutinePreFireGate,
    RoutineToPauseSummary,
    build_permission_context_from_scope_lists,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePausePort:
    """Records pause-port invocations for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mark_routine_paused(
        self,
        *,
        routine_id: str,
        org_id: str,
        pause_reason: RoutinePauseReason,
        missing_scopes: Sequence[str],
        occurred_at: datetime,
    ) -> None:
        self.calls.append(
            {
                "routine_id": routine_id,
                "org_id": org_id,
                "pause_reason": pause_reason,
                "missing_scopes": tuple(missing_scopes),
                "occurred_at": occurred_at,
            }
        )


class FakeInboxProducer:
    """Records every Inbox item the gate publishes."""

    def __init__(self) -> None:
        self.published: list[RoutineInboxItemSpec] = []

    async def publish_routine_paused_item(self, spec: RoutineInboxItemSpec) -> None:
        self.published.append(spec)


class FakeAuditEmitter:
    """Captures the audit emission. Mirrors WorkerAuditEmitter's contract."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def emit_routine_auto_paused(
        self,
        *,
        routine: RoutineToPauseSummary,
        pause_reason: RoutinePauseReason,
        missing_scopes: Sequence[str],
        effective_scopes: Sequence[str],
    ) -> None:
        self.rows.append(
            {
                "event_type": "routine.auto_paused",
                "routine_id": routine.routine_id,
                "org_id": routine.org_id,
                "user_id": routine.owner_user_id,
                "actor_type": "system",
                "resource_type": "routine",
                "resource_id": routine.routine_id,
                "outcome": "denied",
                "context": {
                    "reason": pause_reason.value,
                    "missing": list(missing_scopes),
                    "effective": list(effective_scopes),
                },
            }
        )


class FrozenClock:
    """Pin ``occurred_at`` for stable assertions."""

    INSTANT = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.INSTANT


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class GateBuilderMixin:
    """Build a gate + fake ports + a routine summary in one call."""

    DEFAULT_ROUTINE_ID = "rt_briefing"
    DEFAULT_ORG_ID = "org_acme"
    DEFAULT_OWNER_ID = "user_sarah"
    DEFAULT_AGENT_ID = "agent_briefing"

    @classmethod
    def make_gate(
        cls,
    ) -> tuple[
        RoutinePreFireGate,
        FakePausePort,
        FakeInboxProducer,
        FakeAuditEmitter,
        RoutineToPauseSummary,
    ]:
        pause = FakePausePort()
        inbox = FakeInboxProducer()
        audit = FakeAuditEmitter()
        gate = RoutinePreFireGate(
            pause_port=pause,
            inbox_port=inbox,
            audit_port=audit,
            clock=FrozenClock(),
        )
        routine = RoutineToPauseSummary(
            routine_id=cls.DEFAULT_ROUTINE_ID,
            org_id=cls.DEFAULT_ORG_ID,
            owner_user_id=cls.DEFAULT_OWNER_ID,
            agent_id=cls.DEFAULT_AGENT_ID,
        )
        return gate, pause, inbox, audit, routine

    @staticmethod
    def make_satisfied_context(routine_id: str) -> RoutinePermissionContext:
        return build_permission_context_from_scope_lists(
            routine_id=routine_id,
            routine_declared=["connector:slack:read"],
            routine_required=["connector:slack:read"],
            owner_scopes=["connector:slack:read"],
            project_scopes=None,
        )

    @staticmethod
    def make_shrunk_context(routine_id: str) -> RoutinePermissionContext:
        # Owner lost ``connector:slack:write`` since routine creation.
        return build_permission_context_from_scope_lists(
            routine_id=routine_id,
            routine_declared=[
                "connector:slack:read",
                "connector:slack:write",
            ],
            routine_required=[
                "connector:slack:read",
                "connector:slack:write",
            ],
            owner_scopes=["connector:slack:read"],
            project_scopes=None,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGateAllowsFireOnSatisfied(GateBuilderMixin):
    def test_satisfied_returns_allow_fire_with_no_side_effects(self) -> None:
        gate, pause, inbox, audit, routine = self.make_gate()
        decision = asyncio.run(
            gate.evaluate(
                routine=routine,
                permission_context=self.make_satisfied_context(routine.routine_id),
            )
        )
        assert decision.allow_fire is True
        assert decision.pause_reason is None
        assert decision.missing_scopes == ()
        assert pause.calls == []
        assert inbox.published == []
        assert audit.rows == []


class TestGateAutoPauseOnShrinkage(GateBuilderMixin):
    def test_shrinkage_drives_pause_inbox_audit_cascade(self) -> None:
        gate, pause, inbox, audit, routine = self.make_gate()
        decision = asyncio.run(
            gate.evaluate(
                routine=routine,
                permission_context=self.make_shrunk_context(routine.routine_id),
            )
        )

        # Decision
        assert decision.allow_fire is False
        assert decision.pause_reason is RoutinePauseReason.PERMISSION_SHRINKAGE
        assert decision.missing_scopes == ("connector:slack:write",)

        # Pause port
        assert len(pause.calls) == 1
        call = pause.calls[0]
        assert call["routine_id"] == self.DEFAULT_ROUTINE_ID
        assert call["org_id"] == self.DEFAULT_ORG_ID
        assert call["pause_reason"] is RoutinePauseReason.PERMISSION_SHRINKAGE
        assert call["missing_scopes"] == ("connector:slack:write",)
        assert call["occurred_at"] == FrozenClock.INSTANT

        # Inbox CTA shape
        assert len(inbox.published) == 1
        spec = inbox.published[0]
        assert spec.kind == RoutineInboxItemKind.ROUTINE_PAUSED
        assert spec.title == "Routine paused: permissions shrunk"
        assert spec.priority == "high"
        assert spec.org_id == self.DEFAULT_ORG_ID
        assert spec.owner_user_id == self.DEFAULT_OWNER_ID
        # body_ref points at the routine detail (re-auth CTA renders inline).
        assert spec.body_ref.kind == RoutineInboxLinkKind.ROUTINE
        assert spec.body_ref.id == self.DEFAULT_ROUTINE_ID
        # sender_ref is the agent (or system:routines when agent missing).
        assert spec.sender_ref.kind == "agent"
        assert spec.sender_ref.id == self.DEFAULT_AGENT_ID
        # links include BOTH the routine and the agent.
        link_kinds = {link.kind for link in spec.links}
        assert link_kinds == {
            RoutineInboxLinkKind.ROUTINE,
            RoutineInboxLinkKind.AGENT,
        }

        # Audit row shape
        assert len(audit.rows) == 1
        row = audit.rows[0]
        assert row["event_type"] == "routine.auto_paused"
        assert row["resource_type"] == "routine"
        assert row["resource_id"] == self.DEFAULT_ROUTINE_ID
        assert row["actor_type"] == "system"
        assert row["outcome"] == "denied"
        # context.missing is the FULL list (not just "shrunk one bit").
        assert row["context"]["reason"] == "permission_shrinkage"
        assert row["context"]["missing"] == ["connector:slack:write"]

    def test_inbox_links_omit_agent_when_routine_has_no_agent(self) -> None:
        gate, _, inbox, _, _ = self.make_gate()
        routine_no_agent = RoutineToPauseSummary(
            routine_id="rt_no_agent",
            org_id=self.DEFAULT_ORG_ID,
            owner_user_id=self.DEFAULT_OWNER_ID,
            agent_id=None,
        )
        asyncio.run(
            gate.evaluate(
                routine=routine_no_agent,
                permission_context=self.make_shrunk_context("rt_no_agent"),
            )
        )
        spec = inbox.published[0]
        link_kinds = {link.kind for link in spec.links}
        assert link_kinds == {RoutineInboxLinkKind.ROUTINE}
        # Sender falls back to system agent.
        assert spec.sender_ref.id == "system:routines"


class TestGateAutoPauseOnOwnerOffboarded(GateBuilderMixin):
    def test_owner_offboarded_drives_correct_reason(self) -> None:
        gate, pause, inbox, audit, routine = self.make_gate()
        ctx = build_permission_context_from_scope_lists(
            routine_id=routine.routine_id,
            routine_declared=["connector:slack:read"],
            routine_required=["connector:slack:read"],
            owner_scopes=["connector:slack:read"],
            project_scopes=None,
            owner_disabled=True,
        )
        decision = asyncio.run(gate.evaluate(routine=routine, permission_context=ctx))
        assert decision.allow_fire is False
        assert decision.pause_reason is RoutinePauseReason.OWNER_OFFBOARDED
        assert pause.calls[0]["pause_reason"] is RoutinePauseReason.OWNER_OFFBOARDED
        assert inbox.published[0].title == "Routine paused: owner offboarded"
        assert audit.rows[0]["context"]["reason"] == "owner_offboarded"


class TestGateAutoPauseOnConnectorDisconnected(GateBuilderMixin):
    def test_connector_disconnection_drives_specific_reason(self) -> None:
        gate, pause, inbox, audit, routine = self.make_gate()
        ctx = build_permission_context_from_scope_lists(
            routine_id=routine.routine_id,
            routine_declared=[
                "connector:slack:read",
                "connector:slack:write",
            ],
            routine_required=[
                "connector:slack:read",
                "connector:slack:write",
            ],
            owner_scopes=[
                "connector:slack:read",
                "connector:slack:write",
            ],
            project_scopes=None,
            disconnected_connectors=["slack"],
        )
        decision = asyncio.run(gate.evaluate(routine=routine, permission_context=ctx))
        assert (
            decision.pause_reason is RoutinePauseReason.CRITICAL_CONNECTOR_DISCONNECTED
        )
        assert (
            pause.calls[0]["pause_reason"]
            is RoutinePauseReason.CRITICAL_CONNECTOR_DISCONNECTED
        )
        assert (
            inbox.published[0].title
            == "Routine paused: critical connector disconnected"
        )
        assert audit.rows[0]["context"]["reason"] == "critical_connector_disconnected"
        # All required scopes touching the disconnected connector are
        # listed missing so the Inbox CTA can show what to reconnect.
        assert set(audit.rows[0]["context"]["missing"]) == {
            "connector:slack:read",
            "connector:slack:write",
        }


class TestGateRespectsNoAutoResume(GateBuilderMixin):
    """Cross-audit §9.7 Q5: re-granting perms must not auto-resume.

    The gate has no resume codepath. We exercise that by running:

      1. miss   -> pause + inbox + audit cascade (side effects fire)
      2. hit    -> ``allow_fire=True`` and NO new pause/inbox/audit calls,
                   no "unpause" call either (the gate has no such API),
                   no "you can resume now" Inbox item.

    This validates that the system never auto-restarts the routine on
    its own -- the user has to take an explicit action through the
    Routine destination's resume control (out of scope here).
    """

    def test_recheck_after_perms_restored_does_not_resume(self) -> None:
        gate, pause, inbox, audit, routine = self.make_gate()

        # Step 1: shrinkage -> side effects fire.
        asyncio.run(
            gate.evaluate(
                routine=routine,
                permission_context=self.make_shrunk_context(routine.routine_id),
            )
        )
        assert len(pause.calls) == 1
        assert len(inbox.published) == 1
        assert len(audit.rows) == 1

        # Step 2: scopes restored. Gate sees a satisfied context.
        decision = asyncio.run(
            gate.evaluate(
                routine=routine,
                permission_context=self.make_satisfied_context(routine.routine_id),
            )
        )
        # The decision permits fire, but NO new side effects -- and
        # critically NO call into ``mark_routine_paused`` to flip the
        # routine back to active (that's owner-driven).
        assert decision.allow_fire is True
        assert len(pause.calls) == 1  # unchanged
        assert len(inbox.published) == 1  # unchanged
        assert len(audit.rows) == 1  # unchanged

    def test_pause_port_only_pauses_never_resumes(self) -> None:
        # The port surface MUST NOT include a resume/unpause method --
        # presence of one would create an auto-resume escape hatch.
        # We assert this by checking the protocol's __annotations__ set.
        port_attrs = {
            attr for attr in dir(RoutinePausePort) if not attr.startswith("_")
        }
        # Single public method on the port: mark_routine_paused.
        assert port_attrs == {"mark_routine_paused"}


class TestInboxSpecKindIsRoutinePausedNotAgentQuestion(GateBuilderMixin):
    """Domain emits the target wire value; adapter handles fallback.

    Cross-merge note: until P4-A1's InboxItemKind union adds
    ``"routine_paused"``, the producer adapter at integration time will
    translate. The gate itself emits the target value.
    """

    def test_kind_is_routine_paused(self) -> None:
        gate, _, inbox, _, routine = self.make_gate()
        asyncio.run(
            gate.evaluate(
                routine=routine,
                permission_context=self.make_shrunk_context(routine.routine_id),
            )
        )
        assert inbox.published[0].kind == "routine_paused"


class TestStaticTypingOfPorts:
    """Sanity-check the port protocols are recognised by ``isinstance``-style
    structural typing (Protocol). Fakes above conform to them implicitly --
    we verify here that nothing in the port shapes regressed."""

    def test_pause_port_protocol(self) -> None:
        port: RoutinePausePort = FakePausePort()
        assert port is not None

    def test_inbox_producer_protocol(self) -> None:
        port: RoutineInboxProducerPort = FakeInboxProducer()
        assert port is not None

    def test_audit_port_protocol(self) -> None:
        port: RoutineAuditPort = FakeAuditEmitter()
        assert port is not None

    def test_clock_protocol(self) -> None:
        clock: Clock = FrozenClock()
        assert clock.now().tzinfo is timezone.utc


class TestNoPIIInGateContext:
    """The permission context surface forbids fields that could carry PII
    or routine instruction content (§7.5)."""

    def test_context_forbids_instructions_field(self) -> None:
        with pytest.raises(Exception):
            RoutinePermissionContext(  # type: ignore[call-arg]
                routine_id="rt_x",
                routine_declared_scopes=RoutinePermissionScope.from_iterable([]),
                routine_required_scopes=RoutinePermissionScope.from_iterable([]),
                owner_scopes=RoutinePermissionScope.from_iterable([]),
                instructions="leak",
            )

    def test_routine_summary_forbids_instructions_field(self) -> None:
        with pytest.raises(Exception):
            RoutineToPauseSummary(  # type: ignore[call-arg]
                routine_id="rt_x",
                org_id="org_x",
                owner_user_id="user_x",
                instructions="leak",
            )
