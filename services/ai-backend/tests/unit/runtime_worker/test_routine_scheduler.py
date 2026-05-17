"""Tests for ``runtime_worker.jobs.routine_scheduler`` and its backend client.

Coverage matches the P5-A2 dispatch brief:

* ``CronSpecEvaluator`` — 5-field cron + rrule subset (every-day, every-
  weekday, ``FREQ=DAILY``, ``FREQ=WEEKLY+BYDAY``, ``FREQ=WEEKLY+BYDAY+INTERVAL``).
* ``RoutineSchedulerLoop`` — happy-path tick, idempotency (same claim
  twice in one tick records only one fire — surfaced as ``duplicate``),
  tenant isolation in the claim flow, live agent re-resolve (claim hands
  the loose FK ``base_agent_id`` to the submitter, not a snapshot record).
* Missed-fire policy variants — ``fire_once`` (default), ``fire_all``,
  ``skip``.
* ``HttpRoutineBackendClient`` — service-token + identity headers always
  present; 5xx / network errors return empty outcomes; 409 surfaces as
  ``duplicate=True`` for idempotency reuse on the caller side.

P5-A1 dependency: the worker depends on backend's
``/internal/v1/routines/claim`` + ``/internal/v1/routines/{id}/fires``
+ ``/internal/v1/routines/{id}/advance`` endpoints and the UNIQUE
``(routine_id, fire_at)`` constraint. These tests use a fake client so
the worker stands alone.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from agent_runtime.api.routine_backend_client import (
    ClaimDueOutcome,
    HttpRoutineBackendClient,
    NullRoutineBackendClient,
    RecordFireOutcome,
    RoutineFireClaim,
    RoutineTrigger,
)
from runtime_worker.jobs.routine_scheduler import (
    CronSpecError,
    CronSpecEvaluator,
    FireStatus,
    MissedFirePolicy,
    RoutineRunRequest,
    RoutineRunSubmitter,
    RoutineSchedulerLoop,
)


# ---------------------------------------------------------------------------
# Cron evaluator — 5-field cron
# ---------------------------------------------------------------------------


class TestCronSpecEvaluatorUnixCron:
    def test_every_minute_advances_one_minute(self) -> None:
        evaluator = CronSpecEvaluator()
        result = evaluator.next_fire(
            spec="* * * * *",
            after=datetime(2026, 5, 18, 12, 0, 30, tzinfo=timezone.utc),
        )
        # 1-minute granularity floor — seconds rounded up to next whole min.
        assert result == datetime(2026, 5, 18, 12, 1, tzinfo=timezone.utc)

    def test_daily_at_18_00(self) -> None:
        evaluator = CronSpecEvaluator()
        # cron "0 18 * * *" — daily at 18:00.
        result = evaluator.next_fire(
            spec="0 18 * * *",
            after=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc)

    def test_weekdays_only_skips_to_monday(self) -> None:
        evaluator = CronSpecEvaluator()
        # cron weekday: Sun=0..Sat=6 → 1-5 = Mon..Fri.
        # 2026-05-16 is Saturday.
        result = evaluator.next_fire(
            spec="0 18 * * 1-5",
            after=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc)

    def test_comma_list_of_hours(self) -> None:
        evaluator = CronSpecEvaluator()
        result = evaluator.next_fire(
            spec="30 8,12,16 * * *",
            after=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 5, 18, 12, 30, tzinfo=timezone.utc)

    def test_step_syntax_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="*/5 * * * *",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )

    def test_at_reboot_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="@reboot",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )

    def test_wrong_field_count_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="0 18 * *",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )

    def test_out_of_range_field_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="0 25 * * *",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )


# ---------------------------------------------------------------------------
# Cron evaluator — rrule subset (parity with todo materializer)
# ---------------------------------------------------------------------------


class TestCronSpecEvaluatorRRule:
    def test_freq_daily(self) -> None:
        evaluator = CronSpecEvaluator()
        result = evaluator.next_fire(
            spec="FREQ=DAILY",
            after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)

    def test_freq_daily_with_interval(self) -> None:
        evaluator = CronSpecEvaluator()
        result = evaluator.next_fire(
            spec="FREQ=DAILY;INTERVAL=4",
            after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)

    def test_freq_weekly_byday_mwf(self) -> None:
        evaluator = CronSpecEvaluator()
        # 2026-05-18 = Mon → next BYDAY in MO/WE/FR is Wed 2026-05-20.
        result = evaluator.next_fire(
            spec="FREQ=WEEKLY;BYDAY=MO,WE,FR",
            after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    def test_freq_weekly_byday_interval_2(self) -> None:
        evaluator = CronSpecEvaluator()
        # Every other week, Tuesday only. From Tue 2026-05-19 → Tue 2026-06-02.
        result = evaluator.next_fire(
            spec="FREQ=WEEKLY;BYDAY=TU;INTERVAL=2",
            after=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        )
        assert result == datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)

    def test_missing_freq_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="INTERVAL=2",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )

    def test_unknown_byday_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="FREQ=WEEKLY;BYDAY=MO,XX",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )

    def test_unsupported_freq_rejected(self) -> None:
        evaluator = CronSpecEvaluator()
        with pytest.raises(CronSpecError):
            evaluator.next_fire(
                spec="FREQ=MONTHLY",
                after=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            )


# ---------------------------------------------------------------------------
# Fakes — backend client + run submitter
# ---------------------------------------------------------------------------


class _FakeRoutineBackendClient:
    """In-memory client that models claim/fire/advance + UNIQUE(routine_id, fire_at).

    A "claim" here is just a queued ``RoutineFireClaim``. ``record_fire``
    rejects duplicates (UNIQUE constraint stand-in). ``advance_next_fire``
    is recorded for assertions.
    """

    def __init__(
        self,
        *,
        claims: list[RoutineFireClaim] | None = None,
    ) -> None:
        self._claims = list(claims or [])
        # idempotency key set: (routine_id, fire_at-iso)
        self._fired_keys: set[tuple[str, str]] = set()
        self.fires_recorded: list[tuple[RoutineFireClaim, str, str, str | None]] = []
        self.advances: list[tuple[str, str, datetime]] = []

    async def claim_due_routines(self, *, now: datetime, limit: int) -> ClaimDueOutcome:
        ready: list[RoutineFireClaim] = []
        keep: list[RoutineFireClaim] = []
        for claim in self._claims:
            if claim.fire_at <= now and len(ready) < limit:
                ready.append(claim)
            else:
                keep.append(claim)
        # Once claimed, the backend would put the row in "claimed" state and
        # not return it again until claim TTL expires. Mirror that by
        # removing returned rows from the queue.
        self._claims = keep
        return ClaimDueOutcome(claims=tuple(ready))

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


class _CapturingSubmitter(RoutineRunSubmitter):
    """Records every run-submit request and returns sequential run ids.

    Lets tests assert (a) the submitter received the right shape,
    (b) the loose-FK ``base_agent_id`` was passed through (live re-resolve),
    and (c) the submitter received the right ``tenant_id`` (tenant isolation).
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[RoutineRunRequest] = []
        self._counter = 0
        self._fail = fail

    async def submit_run(self, *, request: RoutineRunRequest) -> str:
        self.requests.append(request)
        if self._fail:
            return ""
        self._counter += 1
        return f"run-{self._counter}"


# ---------------------------------------------------------------------------
# Loop — happy path
# ---------------------------------------------------------------------------


def _make_claim(
    *,
    routine_id: str = "r1",
    tenant_id: str = "tenant-a",
    owner_user_id: str = "user-1",
    base_agent_id: str | None = "agent-X",
    fire_at: datetime | None = None,
    missed_fire_policy: str = "fire_once",
    triggers: tuple[RoutineTrigger, ...] = (),
    last_fire_at: datetime | None = None,
) -> RoutineFireClaim:
    """Build a claim with sensible defaults for tests."""
    if fire_at is None:
        fire_at = datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc)
    if not triggers:
        triggers = (
            RoutineTrigger(
                trigger_id="t1",
                kind="schedule",
                cron="0 18 * * *",
                tz="UTC",
            ),
        )
    return RoutineFireClaim(
        routine_id=routine_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        base_agent_id=base_agent_id,
        fire_at=fire_at,
        last_fire_at=last_fire_at,
        missed_fire_policy=missed_fire_policy,
        triggers=triggers,
    )


class TestRoutineSchedulerLoopHappyPath:
    @pytest.mark.asyncio
    async def test_fires_due_routine_and_advances(self) -> None:
        claim = _make_claim()
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 1
        assert outcome.skipped == 0
        assert outcome.duplicate == 0
        # Submitter received the right shape.
        assert len(submitter.requests) == 1
        req = submitter.requests[0]
        assert req.routine_id == "r1"
        assert req.tenant_id == "tenant-a"
        # Loose-FK agent id passes through unchanged (live re-resolve at
        # submit time — cross-audit §9.7 Q11).
        assert req.base_agent_id == "agent-X"
        assert req.trigger_kind == "cron"
        # Run-source payload (for run.source attribution per §9.7).
        assert req.as_run_source() == {
            "kind": "routine",
            "routine_id": "r1",
            "trigger_kind": "cron",
        }
        # One fire recorded with the assigned run_id.
        assert len(backend.fires_recorded) == 1
        (_, recorded_run_id, status, _) = backend.fires_recorded[0]
        assert recorded_run_id == "run-1"
        assert status == FireStatus.QUEUED
        # Advance computed next fire = next day at 18:00 UTC.
        assert len(backend.advances) == 1
        (routine_id, tenant_id, next_fire) = backend.advances[0]
        assert routine_id == "r1"
        assert tenant_id == "tenant-a"
        assert next_fire == datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_no_due_routines_is_a_noop_tick(self) -> None:
        backend = _FakeRoutineBackendClient(claims=[])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 0
        assert outcome.skipped == 0
        assert outcome.duplicate == 0
        assert submitter.requests == []
        assert backend.fires_recorded == []
        assert backend.advances == []


# ---------------------------------------------------------------------------
# Loop — idempotency
# ---------------------------------------------------------------------------


class TestRoutineSchedulerLoopIdempotency:
    @pytest.mark.asyncio
    async def test_double_claim_records_only_one_fire(self) -> None:
        """Critical invariant: UNIQUE(routine_id, fire_at)."""
        claim = _make_claim()
        backend = _FakeRoutineBackendClient(claims=[])
        # Inject the same claim twice — simulates two workers racing on
        # the same row before the backend's claim TTL kicks in. The
        # backend's UNIQUE(routine_id, fire_at) is the second wall.
        backend._claims = [claim, claim]
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            batch_limit=10,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        # One row recorded, the second surfaced as duplicate.
        assert outcome.fired == 1
        assert outcome.duplicate == 1
        # Both submissions hit the run pipeline (it's the backend's job to
        # reject the second one via the UNIQUE constraint), but only one
        # fire row landed.
        assert len(submitter.requests) == 2
        assert len(backend.fires_recorded) == 1


# ---------------------------------------------------------------------------
# Loop — tenant isolation
# ---------------------------------------------------------------------------


class TestRoutineSchedulerLoopTenantIsolation:
    @pytest.mark.asyncio
    async def test_each_claim_carries_its_own_tenant(self) -> None:
        """Tenant id flows through every write — never substituted."""
        claim_a = _make_claim(
            routine_id="r1", tenant_id="tenant-a", owner_user_id="user-a"
        )
        claim_b = _make_claim(
            routine_id="r2", tenant_id="tenant-b", owner_user_id="user-b"
        )
        backend = _FakeRoutineBackendClient(claims=[claim_a, claim_b])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
        )
        await loop.tick_once()
        # Submit requests carry the original tenant ids — no leak.
        tenants_seen = {r.tenant_id for r in submitter.requests}
        assert tenants_seen == {"tenant-a", "tenant-b"}
        # record_fire calls forward the claim's tenant; advance receives it.
        recorded_tenants = {
            recorded[0].tenant_id for recorded in backend.fires_recorded
        }
        assert recorded_tenants == {"tenant-a", "tenant-b"}
        advance_tenants = {tenant for (_, tenant, _) in backend.advances}
        assert advance_tenants == {"tenant-a", "tenant-b"}


# ---------------------------------------------------------------------------
# Loop — missed-fire policy variants
# ---------------------------------------------------------------------------


class TestRoutineSchedulerLoopMissedFirePolicy:
    @pytest.mark.asyncio
    async def test_fire_once_default_fires_current_slot(self) -> None:
        """``fire_once`` is the cross-audit §9.7 Q7 default."""
        # Even when the slot is *backlogged* (5 minutes late), fire_once
        # still fires *this* slot — the backend's claim-set algorithm
        # decides which slot is "the latest pending"; the worker fires
        # whatever it was handed.
        claim = _make_claim(
            fire_at=datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
            missed_fire_policy=MissedFirePolicy.FIRE_ONCE,
        )
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 5, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 1
        assert outcome.skipped == 0

    @pytest.mark.asyncio
    async def test_fire_all_fires_even_backlog(self) -> None:
        claim = _make_claim(
            fire_at=datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
            missed_fire_policy=MissedFirePolicy.FIRE_ALL,
        )
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            # Severely backlogged: 3 hours late.
            clock=lambda: datetime(2026, 5, 18, 21, 0, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 1
        assert outcome.skipped == 0

    @pytest.mark.asyncio
    async def test_skip_policy_skips_backlogged_slot(self) -> None:
        # 3-hour-late claim with ``skip`` policy → records a skipped fire
        # and advances, but does NOT submit a run.
        claim = _make_claim(
            fire_at=datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
            missed_fire_policy=MissedFirePolicy.SKIP,
        )
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 21, 0, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 0
        assert outcome.skipped == 1
        # No submit, but a skip fire row + advance happened.
        assert submitter.requests == []
        assert len(backend.fires_recorded) == 1
        (_, run_id, status, skip_reason) = backend.fires_recorded[0]
        assert run_id == ""
        assert status == FireStatus.SKIPPED
        assert skip_reason == "policy:skip_backlog"
        assert len(backend.advances) == 1

    @pytest.mark.asyncio
    async def test_skip_policy_still_fires_current_non_backlog_slot(self) -> None:
        # A claim that is barely behind (well within the tick window) is
        # NOT considered backlog, so ``skip`` policy still fires it.
        claim = _make_claim(
            fire_at=datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
            missed_fire_policy=MissedFirePolicy.SKIP,
        )
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 1
        assert outcome.skipped == 0


# ---------------------------------------------------------------------------
# Loop — submission failure semantics
# ---------------------------------------------------------------------------


class TestRoutineSchedulerLoopSubmissionFailure:
    @pytest.mark.asyncio
    async def test_submit_failure_leaves_fire_unrecorded_and_unadvanced(
        self,
    ) -> None:
        """Submit failure must not advance ``next_fire_at`` — re-claim next tick."""
        claim = _make_claim()
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter(fail=True)
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.fired == 0
        assert backend.fires_recorded == []
        # Critical: ``advance`` not called on a failed submit — otherwise
        # we'd silently swallow a fire window.
        assert backend.advances == []


# ---------------------------------------------------------------------------
# Loop — live agent re-resolve (cross-audit §9.7 Q11)
# ---------------------------------------------------------------------------


class TestRoutineSchedulerLoopLiveAgentReResolve:
    @pytest.mark.asyncio
    async def test_passes_base_agent_id_not_a_snapshot(self) -> None:
        """The scheduler must NOT snapshot the agent record into the claim.

        The claim carries ``base_agent_id`` (loose FK). The submitter is
        responsible for re-resolving the live agent at fire time — the
        scheduler's job is just to pass the id through unchanged.
        """
        claim = _make_claim(base_agent_id="agent-XYZ")
        backend = _FakeRoutineBackendClient(claims=[claim])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 18, 0, 30, tzinfo=timezone.utc),
        )
        await loop.tick_once()
        # The submitter got the loose FK, not a snapshotted Agent record.
        # The submit pipeline (P5-A1 territory) re-resolves at fire time.
        assert submitter.requests[0].base_agent_id == "agent-XYZ"
        # And the claim itself has no embedded agent snapshot — only the id.
        assert not hasattr(claim, "agent_snapshot")
        assert not hasattr(claim, "agent_system_prompt")
        assert not hasattr(claim, "agent_skills")


# ---------------------------------------------------------------------------
# Loop — start/stop lifecycle
# ---------------------------------------------------------------------------


class TestRoutineSchedulerLoopLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_is_idempotent(self) -> None:
        backend = _FakeRoutineBackendClient(claims=[])
        submitter = _CapturingSubmitter()
        loop = RoutineSchedulerLoop(
            client=backend,
            run_submitter=submitter,
            tick_seconds=0.01,
        )
        await loop.start()
        await loop.start()  # no-op
        await loop.stop()
        await loop.stop()  # no-op on already-stopped


# ---------------------------------------------------------------------------
# HTTP client — header + error handling
# ---------------------------------------------------------------------------


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Replays queued responses; records every request."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(500)
        return self._responses.pop(0)


class TestHttpRoutineBackendClientHeaders:
    @pytest.mark.asyncio
    async def test_claim_sends_system_headers(self) -> None:
        transport = _CapturingTransport([httpx.Response(200, json={"claims": []})])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpRoutineBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            outcome = await client.claim_due_routines(
                now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc), limit=10
            )
        assert outcome.claims == ()
        req = transport.requests[0]
        # Claim is system-scoped (fan-out across tenants).
        assert req.headers["x-enterprise-service-token"] == "secret-token"
        assert req.headers["x-enterprise-org-id"] == "system"
        assert req.headers["x-enterprise-user-id"] == "system"
        assert req.url.path == "/internal/v1/routines/claim"

    @pytest.mark.asyncio
    async def test_record_fire_sends_tenant_scoped_headers(self) -> None:
        transport = _CapturingTransport(
            [httpx.Response(200, json={"fire_id": "fire-1"})]
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpRoutineBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            claim = _make_claim(tenant_id="tenant-acme", owner_user_id="user-7")
            outcome = await client.record_fire(
                claim=claim,
                run_id="run-1",
                status=FireStatus.QUEUED,
            )
        assert outcome.accepted is True
        assert outcome.fire_id == "fire-1"
        req = transport.requests[0]
        # Per-row writes forward the tenant + owner so audit attribution
        # lands on the routine's owner.
        assert req.headers["x-enterprise-org-id"] == "tenant-acme"
        assert req.headers["x-enterprise-user-id"] == "user-7"
        assert req.url.path == f"/internal/v1/routines/{claim.routine_id}/fires"

    @pytest.mark.asyncio
    async def test_record_fire_409_surfaces_duplicate(self) -> None:
        """The UNIQUE(routine_id, fire_at) duplicate path must be visible.

        The caller relies on ``duplicate=True`` to advance ``next_fire_at``
        without double-counting.
        """
        transport = _CapturingTransport([httpx.Response(409, json={})])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpRoutineBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            claim = _make_claim()
            outcome = await client.record_fire(
                claim=claim,
                run_id="run-1",
                status=FireStatus.QUEUED,
            )
        assert outcome.accepted is False
        assert outcome.duplicate is True

    @pytest.mark.asyncio
    async def test_5xx_returns_empty_outcome(self) -> None:
        transport = _CapturingTransport([httpx.Response(503)])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpRoutineBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            outcome = await client.claim_due_routines(
                now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc), limit=10
            )
        assert outcome == ClaimDueOutcome()

    @pytest.mark.asyncio
    async def test_non_dict_body_returns_empty_claim(self) -> None:
        transport = _CapturingTransport([httpx.Response(200, json=["unexpected"])])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpRoutineBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            outcome = await client.claim_due_routines(
                now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc), limit=10
            )
        assert outcome == ClaimDueOutcome()

    @pytest.mark.asyncio
    async def test_advance_sends_tenant_org_header(self) -> None:
        transport = _CapturingTransport([httpx.Response(200, json={})])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpRoutineBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            ok = await client.advance_next_fire(
                routine_id="r1",
                tenant_id="tenant-acme",
                next_fire_at=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
            )
        assert ok is True
        req = transport.requests[0]
        assert req.headers["x-enterprise-org-id"] == "tenant-acme"
        assert req.url.path == "/internal/v1/routines/r1/advance"


class TestNullRoutineBackendClient:
    @pytest.mark.asyncio
    async def test_claim_returns_empty(self) -> None:
        client = NullRoutineBackendClient()
        outcome = await client.claim_due_routines(
            now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc), limit=10
        )
        assert outcome == ClaimDueOutcome()

    @pytest.mark.asyncio
    async def test_record_fire_returns_unaccepted(self) -> None:
        client = NullRoutineBackendClient()
        claim = _make_claim()
        outcome = await client.record_fire(
            claim=claim, run_id="run-x", status=FireStatus.QUEUED
        )
        assert outcome.accepted is False

    @pytest.mark.asyncio
    async def test_advance_returns_false(self) -> None:
        client = NullRoutineBackendClient()
        ok = await client.advance_next_fire(
            routine_id="r1",
            tenant_id="t1",
            next_fire_at=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
        )
        assert ok is False


# ---------------------------------------------------------------------------
# Claim validation — missed_fire_policy enum
# ---------------------------------------------------------------------------


class TestRoutineFireClaimValidation:
    def test_unknown_policy_rejected(self) -> None:
        with pytest.raises(Exception):
            RoutineFireClaim(
                routine_id="r1",
                tenant_id="t1",
                owner_user_id="u1",
                fire_at=datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
                missed_fire_policy="explode",
            )

    def test_accepted_policies(self) -> None:
        for policy in ("fire_once", "fire_all", "skip"):
            claim = RoutineFireClaim(
                routine_id="r1",
                tenant_id="t1",
                owner_user_id="u1",
                fire_at=datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc),
                missed_fire_policy=policy,
            )
            assert claim.missed_fire_policy == policy
