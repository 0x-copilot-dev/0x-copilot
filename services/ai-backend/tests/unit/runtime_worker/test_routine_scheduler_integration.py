"""Integration tests for P5-A2's scheduler loop calling P5-A4's pre-fire gate.

DW-3 wiring: ``RoutineSchedulerLoop`` evaluates each due claim through the
``RoutinePreFireGate`` before submitting a run. Coverage:

- gate ALLOWS  -> fire goes through (submit + record_fire + advance).
- gate PAUSES  -> scheduler skips the fire AND the routine is paused via
  the gate's pause_port AND the inbox item lands AND the audit row is
  emitted AND ``advance_next_fire`` is NOT called (so the resume flow
  remains owner-driven).
- resolver returns ``None`` -> hard pause; no fire, no advance, no
  side-effects from the gate (the resolver couldn't build the context).
- legacy path (no gate wired) preserves prior behaviour.

LLM provider imports: none. Plain fakes only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import pytest

from agent_runtime.api.routine_backend_client import (
    ClaimDueOutcome,
    RecordFireOutcome,
    RoutineFireClaim,
    RoutineTrigger,
)
from agent_runtime.api.routine_permission_check import (
    RoutinePauseReason,
    RoutinePermissionContext,
)
from runtime_worker.jobs.routine_pre_fire_gate import (
    RoutineInboxItemKind,
    RoutineInboxItemSpec,
    RoutineInboxLinkKind,
    RoutinePreFireGate,
    RoutineToPauseSummary,
    build_permission_context_from_scope_lists,
)
from runtime_worker.jobs.routine_scheduler import (
    FireStatus,
    ResolvedRoutinePermissionInput,
    RoutineSchedulerLoop,
    SkipReason,
)


# ---------------------------------------------------------------------------
# Fakes — copy/parity with the existing scheduler + pause-gate test fakes,
# but bundled here so the integration test stays self-contained (the
# scheduler test fakes use the underscore-private symbol style that
# pytest discourages importing across modules).
# ---------------------------------------------------------------------------


class FakeBackendClient:
    """Minimal ``RoutineBackendClient`` for one tick.

    ``claim_due_routines`` returns the constructor's claims exactly once;
    subsequent calls return an empty batch (mirrors the real claim
    contract that locks a row until the TTL expires).
    """

    def __init__(self, *, claims: Sequence[RoutineFireClaim]) -> None:
        self._claims = list(claims)
        self.fires_recorded: list[tuple[RoutineFireClaim, str, str, str | None]] = []
        self.advances: list[tuple[str, str, datetime]] = []
        self._fired_keys: set[tuple[str, str]] = set()
        self._returned = False

    async def claim_due_routines(self, *, now: datetime, limit: int) -> ClaimDueOutcome:
        if self._returned:
            return ClaimDueOutcome()
        self._returned = True
        return ClaimDueOutcome(claims=tuple(self._claims[:limit]))

    async def record_fire(
        self,
        *,
        claim: RoutineFireClaim,
        run_id: str,
        status: str,
        skip_reason: str | None = None,
    ) -> RecordFireOutcome:
        key = (claim.routine_id, claim.fire_at.isoformat())
        if key in self._fired_keys:
            return RecordFireOutcome(accepted=False, duplicate=True)
        self._fired_keys.add(key)
        self.fires_recorded.append((claim, run_id, status, skip_reason))
        return RecordFireOutcome(
            accepted=True, fire_id=f"fire-{claim.routine_id}-{len(self.fires_recorded)}"
        )

    async def advance_next_fire(
        self,
        *,
        routine_id: str,
        tenant_id: str,
        next_fire_at: datetime,
    ) -> bool:
        self.advances.append((routine_id, tenant_id, next_fire_at))
        return True


class FakeRunSubmitter:
    """Captures every routine fire that reached the run pipeline."""

    def __init__(self) -> None:
        self.requests: list = []
        self._counter = 0

    async def submit_run(self, *, request) -> str:
        self.requests.append(request)
        self._counter += 1
        return f"run-{self._counter}"


class FakePausePort:
    """Records gate-driven pause writes."""

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
    """Captures every Inbox item the gate publishes."""

    def __init__(self) -> None:
        self.published: list[RoutineInboxItemSpec] = []

    async def publish_routine_paused_item(self, spec: RoutineInboxItemSpec) -> None:
        self.published.append(spec)


class FakeAuditEmitter:
    """Captures every audit row the gate emits."""

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
    INSTANT = datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.INSTANT


class StaticPermissionResolver:
    """Resolver that returns a pre-built context per ``routine_id``.

    The scheduler does not know how to look up routine scopes -- the
    runtime container wires a concrete resolver. Tests inject a static
    lookup table so the gate sees deterministic input.
    """

    def __init__(
        self,
        *,
        table: dict[str, ResolvedRoutinePermissionInput | None],
    ) -> None:
        self._table = dict(table)
        self.calls: list[str] = []

    async def resolve(
        self, *, claim: RoutineFireClaim
    ) -> ResolvedRoutinePermissionInput | None:
        self.calls.append(claim.routine_id)
        return self._table.get(claim.routine_id)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


class IntegrationFixtureMixin:
    """One-call builder for the loop + every wired port."""

    DEFAULT_ROUTINE_ID = "rt_briefing"
    DEFAULT_TENANT_ID = "tenant-acme"
    DEFAULT_OWNER_ID = "user_sarah"
    DEFAULT_AGENT_ID = "agent_briefing"

    @classmethod
    def make_claim(
        cls,
        *,
        routine_id: str | None = None,
        fire_at: datetime | None = None,
    ) -> RoutineFireClaim:
        return RoutineFireClaim(
            routine_id=routine_id or cls.DEFAULT_ROUTINE_ID,
            tenant_id=cls.DEFAULT_TENANT_ID,
            owner_user_id=cls.DEFAULT_OWNER_ID,
            base_agent_id=cls.DEFAULT_AGENT_ID,
            fire_at=fire_at or datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
            triggers=(
                RoutineTrigger(
                    trigger_id="t1",
                    kind="schedule",
                    cron="0 18 * * *",
                    tz="UTC",
                ),
            ),
        )

    @classmethod
    def make_routine_summary(
        cls, *, routine_id: str | None = None
    ) -> RoutineToPauseSummary:
        return RoutineToPauseSummary(
            routine_id=routine_id or cls.DEFAULT_ROUTINE_ID,
            org_id=cls.DEFAULT_TENANT_ID,
            owner_user_id=cls.DEFAULT_OWNER_ID,
            agent_id=cls.DEFAULT_AGENT_ID,
        )

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

    @classmethod
    def make_loop(
        cls,
        *,
        claim: RoutineFireClaim,
        resolved: ResolvedRoutinePermissionInput | None,
    ) -> tuple[
        RoutineSchedulerLoop,
        FakeBackendClient,
        FakeRunSubmitter,
        FakePausePort,
        FakeInboxProducer,
        FakeAuditEmitter,
        StaticPermissionResolver,
    ]:
        backend = FakeBackendClient(claims=[claim])
        submitter = FakeRunSubmitter()
        pause = FakePausePort()
        inbox = FakeInboxProducer()
        audit = FakeAuditEmitter()
        gate = RoutinePreFireGate(
            pause_port=pause,
            inbox_port=inbox,
            audit_port=audit,
            clock=FrozenClock(),
        )
        resolver = StaticPermissionResolver(table={claim.routine_id: resolved})
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
            pre_fire_gate=gate,
            permission_resolver=resolver,
        )
        return loop, backend, submitter, pause, inbox, audit, resolver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPreFireGateAllowsFireGoesThrough(IntegrationFixtureMixin):
    @pytest.mark.asyncio
    async def test_satisfied_context_submits_run_and_advances(self) -> None:
        claim = self.make_claim()
        resolved = ResolvedRoutinePermissionInput(
            routine=self.make_routine_summary(),
            permission_context=self.make_satisfied_context(claim.routine_id),
        )
        loop, backend, submitter, pause, inbox, audit, resolver = self.make_loop(
            claim=claim, resolved=resolved
        )

        outcome = await loop.tick_once()

        # Fire counters reflect a normal happy-path submit.
        assert outcome.fired == 1
        assert outcome.skipped == 0
        assert outcome.auto_paused == 0
        assert outcome.duplicate == 0

        # The gate WAS asked (the resolver was called).
        assert resolver.calls == [claim.routine_id]
        # The submitter received the request.
        assert len(submitter.requests) == 1
        assert submitter.requests[0].routine_id == claim.routine_id
        # The fire was recorded as queued, not skipped.
        assert len(backend.fires_recorded) == 1
        (_, recorded_run_id, status, skip_reason) = backend.fires_recorded[0]
        assert recorded_run_id == "run-1"
        assert status == FireStatus.QUEUED
        assert skip_reason is None
        # Advance was called -- normal next-fire propagation.
        assert len(backend.advances) == 1
        # No auto-pause side effects.
        assert pause.calls == []
        assert inbox.published == []
        assert audit.rows == []


class TestPreFireGatePausesSkipsFireAndCascades(IntegrationFixtureMixin):
    @pytest.mark.asyncio
    async def test_permission_miss_pauses_routine_and_lands_inbox_item(
        self,
    ) -> None:
        claim = self.make_claim()
        resolved = ResolvedRoutinePermissionInput(
            routine=self.make_routine_summary(),
            permission_context=self.make_shrunk_context(claim.routine_id),
        )
        loop, backend, submitter, pause, inbox, audit, resolver = self.make_loop(
            claim=claim, resolved=resolved
        )

        outcome = await loop.tick_once()

        # Scheduler counters reflect a refused fire.
        assert outcome.fired == 0
        assert outcome.auto_paused == 1
        assert outcome.skipped == 0
        # No double-fire on retry: submitter never saw the request.
        assert submitter.requests == []

        # Routine is paused via the gate's pause_port.
        assert len(pause.calls) == 1
        pause_call = pause.calls[0]
        assert pause_call["routine_id"] == claim.routine_id
        assert pause_call["org_id"] == claim.tenant_id
        assert pause_call["pause_reason"] is RoutinePauseReason.PERMISSION_SHRINKAGE
        assert pause_call["missing_scopes"] == ("connector:slack:write",)

        # Inbox item landed via the gate's inbox_port.
        assert len(inbox.published) == 1
        item = inbox.published[0]
        assert item.kind == RoutineInboxItemKind.ROUTINE_PAUSED
        assert item.org_id == claim.tenant_id
        assert item.owner_user_id == claim.owner_user_id
        link_kinds = {link.kind for link in item.links}
        assert RoutineInboxLinkKind.ROUTINE in link_kinds

        # Audit row matches the canonical ``routine.auto_paused`` action.
        assert len(audit.rows) == 1
        row = audit.rows[0]
        assert row["event_type"] == "routine.auto_paused"
        assert row["resource_id"] == claim.routine_id
        assert row["actor_type"] == "system"
        assert row["outcome"] == "denied"
        assert row["context"]["reason"] == "permission_shrinkage"
        assert row["context"]["missing"] == ["connector:slack:write"]

        # Scheduler recorded an explicit skipped fire row carrying the
        # permission-failure reason -- so auditors see the slot we
        # acknowledged + refused.
        assert len(backend.fires_recorded) == 1
        (_, run_id, status, skip_reason) = backend.fires_recorded[0]
        assert run_id == ""
        assert status == FireStatus.SKIPPED
        assert skip_reason == SkipReason.PERMISSION_INTERSECTION_FAILED

        # Critical: ``advance_next_fire`` was NOT called. The routine is
        # paused -- the resume flow is owner-driven, not scheduler-driven.
        assert backend.advances == []


class TestUnresolvedContextHardPauses(IntegrationFixtureMixin):
    @pytest.mark.asyncio
    async def test_resolver_none_skips_fire_without_advancing(self) -> None:
        # Resolver returns None (e.g. owner record missing) -- the
        # scheduler must NOT silently fire a routine whose permission
        # state it can't verify. No fire, no advance, no gate side
        # effects (the gate is never reached when the context is
        # unresolved).
        claim = self.make_claim()
        loop, backend, submitter, pause, inbox, audit, resolver = self.make_loop(
            claim=claim, resolved=None
        )

        outcome = await loop.tick_once()

        assert outcome.fired == 0
        assert outcome.auto_paused == 1
        assert submitter.requests == []
        # Gate side effects are not triggered because we never reach the
        # gate -- the resolver short-circuited the path.
        assert pause.calls == []
        assert inbox.published == []
        assert audit.rows == []
        # But we still record the skipped slot so it's visible in the
        # fire history, and we do NOT advance.
        assert len(backend.fires_recorded) == 1
        (_, _, status, skip_reason) = backend.fires_recorded[0]
        assert status == FireStatus.SKIPPED
        assert skip_reason == SkipReason.PERMISSION_INTERSECTION_FAILED
        assert backend.advances == []


class TestLegacyPathWithoutGateUnchanged(IntegrationFixtureMixin):
    """The pre-fire gate is opt-in via dependency injection.

    A loop constructed WITHOUT a ``pre_fire_gate`` must preserve the
    existing P5-A2 fire behaviour (otherwise the existing P5-A2 test
    suite would have to be rewritten -- it isn't owned by DW-3).
    """

    @pytest.mark.asyncio
    async def test_no_gate_wired_falls_through_to_legacy_submit(self) -> None:
        claim = self.make_claim()
        backend = FakeBackendClient(claims=[claim])
        submitter = FakeRunSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
            # No pre_fire_gate / permission_resolver -- legacy path.
        )

        outcome = await loop.tick_once()

        assert outcome.fired == 1
        assert outcome.auto_paused == 0
        assert len(submitter.requests) == 1
        assert len(backend.fires_recorded) == 1
        assert len(backend.advances) == 1


class TestNoDoubleFireOnRetryAfterPause(IntegrationFixtureMixin):
    """When the gate auto-pauses, re-claiming next tick must not re-fire.

    In production, the gate's pause_port flips the routine to ``paused``
    so the backend's claim query (which filters on ``status='active'``)
    won't return it. Even if a buggy backend re-returned the same claim,
    the scheduler must NOT submit a run.
    """

    @pytest.mark.asyncio
    async def test_paused_routine_re_claimed_does_not_fire(self) -> None:
        # Fixture has the routine in shrunk state. First tick pauses.
        claim = self.make_claim()
        resolved = ResolvedRoutinePermissionInput(
            routine=self.make_routine_summary(),
            permission_context=self.make_shrunk_context(claim.routine_id),
        )
        loop, backend, submitter, pause, inbox, audit, _ = self.make_loop(
            claim=claim, resolved=resolved
        )

        await loop.tick_once()
        assert submitter.requests == []
        assert len(pause.calls) == 1

        # Simulate the backend mis-returning the same claim again (defence
        # in depth). Build a second tick on the same fakes.
        # FakeBackendClient is one-shot, so we re-inject.
        backend._claims = [claim]
        backend._returned = False

        outcome_2 = await loop.tick_once()
        # No fire. Either the gate trips again (pause is idempotent) or
        # the resolver shows the same shrunk state -- either way: no run.
        assert outcome_2.fired == 0
        assert submitter.requests == []
