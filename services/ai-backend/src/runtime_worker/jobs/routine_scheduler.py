"""Periodic worker that fires due routine triggers as ai-backend runs.

Implements routines-prd.md §3.7 (Option A: in-process loop on ai-backend
``runtime_worker``). Polls every ``ROUTINE_SCHEDULER_INTERVAL_SECONDS``
(default 60s) and asks ``backend`` to claim all routines whose
``next_fire_at <= now()``. The backend owns the storage transaction, the
``FOR UPDATE SKIP LOCKED`` claim, and the UNIQUE ``(routine_id, fire_at)``
constraint that makes the fire write idempotent.

The cron evaluator (``CronSpecEvaluator``) is a pure-Python class held here
so it can be unit-tested without a backend. Two cron grammars are
supported, matching routines-prd.md §16 ("valid Unix cron") and the
implementation-plan parallel evaluator in
``todo_recurrence_materializer.RecurrenceRuleEvaluator``:

* **Standard 5-field cron** — ``minute hour day month weekday`` with ``*``,
  numeric values, comma lists, and ``L-H`` ranges (no ``/step``, no
  ``@reboot``, no nested ranges — see anti-goals §17 of the PRD). 1-minute
  granularity floor.
* **rrule subset** — ``FREQ=DAILY|WEEKLY`` ± ``BYDAY=...`` ± ``INTERVAL=N``,
  for parity with the same grammar used by the todo recurrence materializer.

Cross-service contract: no direct Postgres access from this worker. All
reads and writes go through ``agent_runtime.api.routine_backend_client``.

Per cross-audit §9.7 Q11, the routine's ``base_agent_id`` is **re-resolved
at fire time** — the run-create path here passes the claim's ``base_agent_id``
through unchanged to whatever run-creation port the worker is wired to.
We do **not** snapshot the agent record into the claim; it stays a loose FK
the run pipeline looks up live.

PII / privacy: routine instruction text never appears in logs. Only
``routine_id`` / ``tenant_id`` / ``fire_at`` / ``status`` are logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.api.routine_backend_client import (
    RoutineBackendClient,
    RoutineFireClaim,
)
from agent_runtime.api.routine_permission_check import RoutinePermissionContext
from runtime_worker.jobs.routine_pre_fire_gate import (
    RoutinePreFireGate,
    RoutineToPauseSummary,
)


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — env + names
# ---------------------------------------------------------------------------


class _Env:
    """Environment variable names + defaults for the routine scheduler."""

    INTERVAL_SECONDS = "ROUTINE_SCHEDULER_INTERVAL_SECONDS"
    ENABLED = "ROUTINE_SCHEDULER_ENABLED"
    BATCH_LIMIT = "ROUTINE_SCHEDULER_BATCH_LIMIT"
    DEFAULT_INTERVAL_SECONDS = 60.0
    DEFAULT_BATCH_LIMIT = 100


class MissedFirePolicy:
    """Allowed values for the per-routine missed-fire policy.

    Cross-audit §9.7 Q7 sets the default to ``fire_once``. The wire shape
    keeps all three variants so product can flip per-routine without a
    schema change.
    """

    FIRE_ONCE = "fire_once"
    FIRE_ALL = "fire_all"
    SKIP = "skip"
    SUPPORTED: tuple[str, ...] = (FIRE_ONCE, FIRE_ALL, SKIP)
    DEFAULT: str = FIRE_ONCE


class FireStatus:
    """Status values written to ``routine_fires.status``."""

    QUEUED = "queued"
    SKIPPED = "skipped"


class SkipReason:
    """Stable wire values for ``routine_fires.skip_reason``.

    Used by callers (auditors / dashboards) to bucket skips. Keep enum-y
    string literals here so callers reference them by name, never inline.
    """

    POLICY_SKIP_BACKLOG = "policy:skip_backlog"
    PERMISSION_INTERSECTION_FAILED = "permission_intersection_failed"


# ---------------------------------------------------------------------------
# Cron evaluator — pure, unit-testable
# ---------------------------------------------------------------------------


class CronSpecError(ValueError):
    """Raised when a cron / rrule spec cannot be parsed."""


class _Weekday:
    """Two-letter RFC 5545 weekday codes mapped to ``date.weekday()`` (Mon=0)."""

    CODES: dict[str, int] = {
        "MO": 0,
        "TU": 1,
        "WE": 2,
        "TH": 3,
        "FR": 4,
        "SA": 5,
        "SU": 6,
    }


class CronSpecEvaluator:
    """Compute the **next** firing instant strictly after a reference instant.

    Stateless. Public method ``next_fire`` returns a tz-aware ``datetime`` in
    UTC. Two cron grammars are supported (see module docstring):

    * 5-field Unix cron (``*`` / int / comma list / ``L-H`` range);
    * rrule subset (``FREQ=DAILY|WEEKLY`` ± ``BYDAY`` ± ``INTERVAL``).

    Selection is by spec syntax: anything with ``=`` is treated as rrule,
    everything else as 5-field cron. 1-minute granularity floor.
    """

    _MAX_SCAN_MINUTES = 366 * 24 * 60  # one full year — safety bound

    def next_fire(
        self,
        *,
        spec: str,
        after: datetime,
    ) -> datetime:
        """Return the next firing instant in UTC strictly after ``after``.

        ``after`` is expected to be tz-aware; naive datetimes are treated as
        UTC. Raises ``CronSpecError`` for unsupported or malformed specs.
        """
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)
        after_utc = after.astimezone(timezone.utc)
        if "=" in spec:
            return self._next_rrule(spec=spec, after=after_utc)
        return self._next_cron(spec=spec, after=after_utc)

    # ---- 5-field cron -----------------------------------------------------

    def _next_cron(self, *, spec: str, after: datetime) -> datetime:
        parsed = self._parse_cron(spec)
        # Round ``after`` up to the next whole minute (granularity floor).
        candidate = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(self._MAX_SCAN_MINUTES):
            if (
                candidate.minute in parsed["minute"]
                and candidate.hour in parsed["hour"]
                and candidate.day in parsed["day"]
                and candidate.month in parsed["month"]
                # Unix cron weekday: Sun=0, Mon=1 ... Sat=6 (we accept both).
                and self._weekday_matches(weekday_field=parsed["weekday"], dt=candidate)
            ):
                return candidate
            candidate = candidate + timedelta(minutes=1)
        raise CronSpecError(
            f"no cron match within {self._MAX_SCAN_MINUTES} minutes for spec '{spec}'"
        )

    @staticmethod
    def _weekday_matches(*, weekday_field: frozenset[int], dt: datetime) -> bool:
        """Return True when ``dt``'s weekday is in the parsed cron weekday set.

        Cron weekday: Sun=0..Sat=6. Python ``date.weekday()``: Mon=0..Sun=6.
        Map by ``(weekday() + 1) % 7``.
        """
        cron_dow = (dt.weekday() + 1) % 7
        return cron_dow in weekday_field

    def _parse_cron(self, spec: str) -> dict[str, frozenset[int]]:
        """Parse a 5-field Unix cron spec to a per-field set of allowed values."""
        fields = spec.strip().split()
        if len(fields) != 5:
            raise CronSpecError(
                f"cron spec must have 5 fields (got {len(fields)}): '{spec}'"
            )
        minute, hour, day, month, weekday = fields
        return {
            "minute": self._parse_field(minute, lo=0, hi=59),
            "hour": self._parse_field(hour, lo=0, hi=23),
            "day": self._parse_field(day, lo=1, hi=31),
            "month": self._parse_field(month, lo=1, hi=12),
            "weekday": self._parse_field(weekday, lo=0, hi=6),
        }

    def _parse_field(self, field: str, *, lo: int, hi: int) -> frozenset[int]:
        """Parse one cron field — ``*``, ints, comma lists, ``L-H`` ranges."""
        # Anti-goal: ``/step`` and ``@reboot`` are NOT supported (PRD §17).
        if "/" in field:
            raise CronSpecError(f"cron step ('/') not supported: '{field}'")
        if field.startswith("@"):
            raise CronSpecError(f"cron alias ('{field}') not supported")
        if field == "*":
            return frozenset(range(lo, hi + 1))
        values: set[int] = set()
        for piece in field.split(","):
            piece = piece.strip()
            if not piece:
                raise CronSpecError(f"empty cron piece in field '{field}'")
            if "-" in piece:
                lo_s, _, hi_s = piece.partition("-")
                try:
                    a = int(lo_s)
                    b = int(hi_s)
                except ValueError as exc:
                    raise CronSpecError(
                        f"cron range must be int-int, got '{piece}'"
                    ) from exc
                if a > b:
                    raise CronSpecError(f"cron range reversed: '{piece}'")
                if a < lo or b > hi:
                    raise CronSpecError(f"cron range '{piece}' outside [{lo},{hi}]")
                values.update(range(a, b + 1))
            else:
                try:
                    v = int(piece)
                except ValueError as exc:
                    raise CronSpecError(
                        f"cron value must be int, got '{piece}'"
                    ) from exc
                if v < lo or v > hi:
                    raise CronSpecError(f"cron value '{piece}' outside [{lo},{hi}]")
                values.add(v)
        return frozenset(values)

    # ---- rrule subset -----------------------------------------------------

    def _next_rrule(self, *, spec: str, after: datetime) -> datetime:
        parsed = self._parse_rrule(spec)
        freq = parsed["FREQ"]
        interval = parsed["INTERVAL"]
        byday = parsed["BYDAY"]
        # rrule shape preserves time-of-day of ``after`` — semantics:
        # "every N days at the same minute" (PRD parity with the Todos
        # recurrence evaluator). 1-minute granularity floor still applies.
        base = after.replace(second=0, microsecond=0)
        if freq == "DAILY":
            return base + timedelta(days=interval)
        if freq != "WEEKLY":
            raise CronSpecError(f"rrule FREQ must be DAILY or WEEKLY, got '{freq}'")
        if not byday:
            return base + timedelta(days=7 * interval)
        # WEEKLY + BYDAY: scan day-by-day from ``base`` for the next allowed
        # weekday whose week-offset is a multiple of ``interval``.
        target = {_Weekday.CODES[code] for code in byday}
        base_week_start = (base - timedelta(days=base.weekday())).date()
        for offset in range(1, 366 + 1):
            candidate = base + timedelta(days=offset)
            if candidate.weekday() not in target:
                continue
            cand_week_start = (candidate - timedelta(days=candidate.weekday())).date()
            week_delta_days = (cand_week_start - base_week_start).days
            if week_delta_days % (7 * interval) != 0:
                continue
            return candidate
        raise CronSpecError(f"no rrule match within 366 days for spec '{spec}'")

    def _parse_rrule(self, spec: str) -> dict[str, object]:
        """Parse the RFC 5545 subset we support (mirrors todo materializer)."""
        out: dict[str, object] = {"INTERVAL": 1, "BYDAY": ()}
        parts = [piece for piece in spec.split(";") if piece]
        for piece in parts:
            if "=" not in piece:
                raise CronSpecError(
                    f"malformed rrule fragment '{piece}' in spec '{spec}'"
                )
            key, value = piece.split("=", 1)
            key = key.strip().upper()
            value = value.strip().upper()
            if key == "FREQ":
                out["FREQ"] = value
            elif key == "INTERVAL":
                try:
                    interval = int(value)
                except ValueError as exc:
                    raise CronSpecError(
                        f"rrule INTERVAL must be int, got '{value}'"
                    ) from exc
                if interval <= 0:
                    raise CronSpecError(f"rrule INTERVAL must be > 0, got {interval}")
                out["INTERVAL"] = interval
            elif key == "BYDAY":
                codes = tuple(code.strip() for code in value.split(",") if code.strip())
                for code in codes:
                    if code not in _Weekday.CODES:
                        raise CronSpecError(
                            f"unknown BYDAY code '{code}' in spec '{spec}'"
                        )
                out["BYDAY"] = codes
            else:
                raise CronSpecError(f"unsupported rrule key '{key}' in spec '{spec}'")
        if "FREQ" not in out:
            raise CronSpecError(f"rrule spec missing required FREQ: '{spec}'")
        return out


# ---------------------------------------------------------------------------
# Run submission port — decouples scheduler from run-creation wiring
# ---------------------------------------------------------------------------


class RoutineRunRequest(BaseModel):
    """Canonical "create a run for a routine fire" request.

    Run-creation in ai-backend will be wired in by the orchestrator. This
    shape is the contract the scheduler emits at fire time — it carries
    everything the run pipeline needs to set ``run.source = {kind: "routine",
    routine_id, trigger_kind: "cron"}`` and to **re-resolve the live agent**
    at run-create time (cross-audit §9.7 Q11).
    """

    model_config = ConfigDict(frozen=True)
    routine_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    project_id: str | None = None
    # Loose FK — the run pipeline re-resolves at fire time. We pass the id,
    # not a snapshot of the agent record.
    base_agent_id: str | None = None
    fire_at: datetime
    trigger_kind: str = "cron"

    def as_run_source(self) -> dict[str, str]:
        """Materialise ``run.source = {...}`` per cross-audit §9.7 attribution."""
        return {
            "kind": "routine",
            "routine_id": self.routine_id,
            "trigger_kind": self.trigger_kind,
        }


@runtime_checkable
class RoutineRunSubmitter(Protocol):
    """Port for the scheduler -> run-creation handoff.

    A successful submission returns the new ``run_id``. The scheduler
    records the fire with this id. An empty string signals "submission
    failed" — the scheduler skips ``record_fire`` for that claim and the
    next tick re-claims (the backend's claim TTL expires).
    """

    async def submit_run(self, *, request: RoutineRunRequest) -> str:
        """Submit a routine fire as an ai-backend run; return ``run_id`` or ``""``."""


class NullRoutineRunSubmitter:
    """No-op submitter used in tests where the run pipeline is mocked.

    Returns a deterministic placeholder id so tests can assert "a fire was
    recorded" without spinning up the real run-coordinator.
    """

    async def submit_run(self, *, request: RoutineRunRequest) -> str:
        """Return a deterministic placeholder run id for the fire."""
        return f"run-null-{request.routine_id}-{request.fire_at.isoformat()}"


# ---------------------------------------------------------------------------
# Pre-fire permission context resolution — port
# ---------------------------------------------------------------------------


class ResolvedRoutinePermissionInput(BaseModel):
    """Bundle of inputs the pre-fire gate consumes for one claim.

    Returned by ``RoutinePermissionContextResolver`` and handed straight to
    :meth:`RoutinePreFireGate.evaluate`. The resolver implementation
    (wired in production by the runtime container) is the only place that
    knows how to look up the routine's declared / required scopes, the
    owner's current grants, the project's grants, and the disconnected
    connector list -- the scheduler stays free of that knowledge.
    """

    model_config = ConfigDict(frozen=True)
    routine: RoutineToPauseSummary
    permission_context: RoutinePermissionContext


@runtime_checkable
class RoutinePermissionContextResolver(Protocol):
    """Port for the scheduler -> permission-context lookup.

    A successful resolve returns the inputs the pre-fire gate needs. A
    ``None`` return signals "could not resolve this claim's context"
    (e.g. owner record missing). The scheduler treats ``None`` the same
    as a gate miss (do NOT submit; do NOT advance) and records the fire
    with skip_reason ``permission_intersection_failed``.
    """

    async def resolve(
        self, *, claim: RoutineFireClaim
    ) -> ResolvedRoutinePermissionInput | None:
        """Build the routine summary + permission context for one claim."""


class NullRoutinePermissionContextResolver:
    """No-op resolver used in tests where the pre-fire gate is disabled.

    Returns ``None`` for every claim so behaviour matches "gate not wired".
    Production wiring MUST inject a real resolver -- this is a safety
    fallback only.
    """

    async def resolve(
        self, *, claim: RoutineFireClaim
    ) -> ResolvedRoutinePermissionInput | None:
        return None


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


class RoutineSchedulerEnv:
    """Env-var helpers mirroring ``RetentionSweeperLoopEnv``."""

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            v = int(raw)
            return v if v > 0 else default
        except ValueError:
            return default


class TickOutcome(BaseModel):
    """Aggregate counts for one ``tick_once`` pass."""

    model_config = ConfigDict(frozen=True)
    fired: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    duplicate: int = Field(default=0, ge=0)
    advance_failures: int = Field(default=0, ge=0)
    auto_paused: int = Field(default=0, ge=0)


class RoutineSchedulerLoop:
    """Polls every ``tick_seconds`` (default 60s) and fires due routines.

    Per-tick algorithm:

    1. Ask backend to claim a batch of due routines (``FOR UPDATE SKIP
       LOCKED`` server-side).
    2. For each claim:
       a. Decide whether to fire or skip based on ``missed_fire_policy``
          (cross-audit §9.7 Q7 — default ``fire_once``).
       b. Submit a run via the injected ``RoutineRunSubmitter``. The submit
          path is responsible for **re-resolving the live agent** at fire
          time (§9.7 Q11) — the scheduler only passes the loose FK
          ``base_agent_id`` through.
       c. Record the fire via ``record_fire`` — the backend's UNIQUE
          ``(routine_id, fire_at)`` makes this idempotent across concurrent
          workers.
       d. Compute the next firing instant from the routine's schedule
          trigger and advance ``next_fire_at``.
    """

    def __init__(
        self,
        *,
        client: RoutineBackendClient,
        run_submitter: RoutineRunSubmitter | None = None,
        evaluator: CronSpecEvaluator | None = None,
        tick_seconds: float | None = None,
        batch_limit: int | None = None,
        clock: object | None = None,
        pre_fire_gate: RoutinePreFireGate | None = None,
        permission_resolver: RoutinePermissionContextResolver | None = None,
    ) -> None:
        self._client = client
        self._submitter = run_submitter or NullRoutineRunSubmitter()
        self._evaluator = evaluator or CronSpecEvaluator()
        self._tick = (
            tick_seconds
            if tick_seconds is not None
            else RoutineSchedulerEnv.env_float(
                _Env.INTERVAL_SECONDS, _Env.DEFAULT_INTERVAL_SECONDS
            )
        )
        self._batch_limit = (
            batch_limit
            if batch_limit is not None
            else RoutineSchedulerEnv.env_int(_Env.BATCH_LIMIT, _Env.DEFAULT_BATCH_LIMIT)
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Pre-fire gate: both ports must be wired together (gate + resolver)
        # or neither. When unwired, the loop runs the legacy submit path --
        # used by tests that exercise pure scheduling / cron behaviour.
        self._pre_fire_gate = pre_fire_gate
        self._permission_resolver = (
            permission_resolver or NullRoutinePermissionContextResolver()
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background loop; idempotent if already running."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="routine-scheduler-loop")

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to finish."""
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
                return
            except TimeoutError:
                pass
            try:
                await self.tick_once()
            except Exception:
                _LOGGER.warning("routine_scheduler.tick_failed", exc_info=True)

    # ---- per-tick driver --------------------------------------------------

    async def tick_once(self) -> TickOutcome:
        """Run one claim → pre-fire gate → fire/skip → advance pass."""
        now = self._now()
        outcome = await self._client.claim_due_routines(
            now=now, limit=self._batch_limit
        )
        fired = 0
        skipped = 0
        duplicate = 0
        advance_failures = 0
        auto_paused = 0
        for claim in outcome.claims:
            decision = self._decide(claim=claim, now=now)
            if decision.action == _Action.SKIP:
                skipped += 1
                # Record an explicit skip on the routine's fire history so
                # auditors can see "we acknowledged this slot and chose not
                # to fire it" — important for ``fire_once`` backlog skips.
                fire_result = await self._client.record_fire(
                    claim=claim,
                    run_id="",
                    status=FireStatus.SKIPPED,
                    skip_reason=decision.reason,
                )
                if fire_result.duplicate:
                    duplicate += 1
                advance_ok = await self._advance(claim=claim)
                if not advance_ok:
                    advance_failures += 1
                continue
            # FIRE path. First, pass through the pre-fire permission gate
            # (routines-prd §7.4). On a permission miss the gate drives
            # the auto-pause cascade (pause -> inbox -> audit) and we
            # MUST NOT submit a run.
            gate_outcome = await self._evaluate_pre_fire_gate(claim=claim)
            if gate_outcome == _PreFireOutcome.PAUSED:
                auto_paused += 1
                # Record an explicit skip row so the routine's fire
                # history reflects the slot we acknowledged + refused to
                # fire. Do NOT advance: the routine is now paused, so the
                # backend won't claim it again until the owner resumes;
                # leaving ``next_fire_at`` untouched keeps the resume flow
                # deterministic.
                fire_result = await self._client.record_fire(
                    claim=claim,
                    run_id="",
                    status=FireStatus.SKIPPED,
                    skip_reason=SkipReason.PERMISSION_INTERSECTION_FAILED,
                )
                if fire_result.duplicate:
                    duplicate += 1
                continue
            # gate_outcome is ALLOWED or SKIPPED (gate not wired); proceed
            # to submit.
            request = RoutineRunRequest(
                routine_id=claim.routine_id,
                tenant_id=claim.tenant_id,
                owner_user_id=claim.owner_user_id,
                project_id=claim.project_id,
                base_agent_id=claim.base_agent_id,
                fire_at=claim.fire_at,
                trigger_kind="cron",
            )
            run_id = ""
            try:
                run_id = await self._submitter.submit_run(request=request)
            except Exception:
                _LOGGER.warning(
                    "routine_scheduler.submit_failed",
                    extra={
                        "metadata": {
                            "routine_id": claim.routine_id,
                            "tenant_id": claim.tenant_id,
                            "fire_at": claim.fire_at.isoformat(),
                        }
                    },
                    exc_info=True,
                )
            if not run_id:
                # Submission failed — leave the claim un-recorded; the
                # backend's claim TTL will expire and the next tick will
                # re-claim. Do NOT advance ``next_fire_at`` — that would
                # silently drop a fire window.
                continue
            fire_result = await self._client.record_fire(
                claim=claim,
                run_id=run_id,
                status=FireStatus.QUEUED,
            )
            if fire_result.duplicate:
                duplicate += 1
                # Concurrent worker already fired this slot. Still advance
                # so we don't claim the same row next tick.
                advance_ok = await self._advance(claim=claim)
                if not advance_ok:
                    advance_failures += 1
                continue
            if not fire_result.accepted:
                # record_fire failed (network); retry next tick via re-claim.
                continue
            fired += 1
            advance_ok = await self._advance(claim=claim)
            if not advance_ok:
                advance_failures += 1
        _LOGGER.info(
            "routine_scheduler.tick",
            extra={
                "metadata": {
                    "fired": fired,
                    "skipped": skipped,
                    "duplicate": duplicate,
                    "advance_failures": advance_failures,
                    "auto_paused": auto_paused,
                }
            },
        )
        return TickOutcome(
            fired=fired,
            skipped=skipped,
            duplicate=duplicate,
            advance_failures=advance_failures,
            auto_paused=auto_paused,
        )

    async def _evaluate_pre_fire_gate(self, *, claim: RoutineFireClaim) -> str:
        """Run the pre-fire permission gate for one claim.

        Returns:
            ``_PreFireOutcome.SKIPPED`` -- gate not wired; legacy submit path.
            ``_PreFireOutcome.ALLOWED`` -- gate evaluated, fire allowed.
            ``_PreFireOutcome.PAUSED``  -- gate evaluated, auto-pause cascade
            executed (pause + inbox + audit); caller MUST NOT submit a run.

        The gate's own ports (pause / inbox / audit) are responsible for
        their own idempotency on retry. The scheduler treats a resolver
        ``None`` the same as a gate miss so a transient resolver outage
        cannot silently fire a routine without the permission check.
        """
        if self._pre_fire_gate is None:
            return _PreFireOutcome.SKIPPED
        try:
            resolved = await self._permission_resolver.resolve(claim=claim)
        except Exception:
            _LOGGER.warning(
                "routine_scheduler.permission_resolver_failed",
                extra={
                    "metadata": {
                        "routine_id": claim.routine_id,
                        "tenant_id": claim.tenant_id,
                    }
                },
                exc_info=True,
            )
            return _PreFireOutcome.PAUSED
        if resolved is None:
            # No context available -- treat as a hard pause so we never
            # fire a routine whose permission state we couldn't verify.
            _LOGGER.warning(
                "routine_scheduler.permission_context_unresolved",
                extra={
                    "metadata": {
                        "routine_id": claim.routine_id,
                        "tenant_id": claim.tenant_id,
                    }
                },
            )
            return _PreFireOutcome.PAUSED
        try:
            decision = await self._pre_fire_gate.evaluate(
                routine=resolved.routine,
                permission_context=resolved.permission_context,
            )
        except Exception:
            _LOGGER.warning(
                "routine_scheduler.pre_fire_gate_failed",
                extra={
                    "metadata": {
                        "routine_id": claim.routine_id,
                        "tenant_id": claim.tenant_id,
                    }
                },
                exc_info=True,
            )
            # Gate failures must NOT silently allow a fire -- treat as a
            # hard pause (the auto-pause side effects already ran, or
            # they didn't and the routine will be re-evaluated next tick
            # once the gate recovers; either way we refuse to fire now).
            return _PreFireOutcome.PAUSED
        if decision.allow_fire:
            return _PreFireOutcome.ALLOWED
        return _PreFireOutcome.PAUSED

    # ---- fire-vs-skip decision -------------------------------------------

    def _decide(self, *, claim: RoutineFireClaim, now: datetime) -> _Decision:
        """Apply the missed-fire policy to decide fire vs skip for this claim.

        Cross-audit §9.7 Q7: default ``fire_once`` — the most-recent missed
        window fires; older missed windows are skipped. ``fire_all`` fires
        every backlog slot in a single tick (rarely desired — only useful
        for replay tooling). ``skip`` skips the backlog and the current
        slot (e.g. paused-during-vacation semantics).
        """
        policy = claim.missed_fire_policy
        # We can't see whether earlier slots existed without the cron + last
        # fire — but a "backlog" condition only matters when fire_at is far
        # in the past relative to ``now``. The scheduler ticks every 60s; a
        # claim more than 2 ticks late is "backlogged".
        backlog_threshold = timedelta(seconds=max(self._tick * 2, 120))
        is_backlogged = (now - claim.fire_at) > backlog_threshold

        if policy == MissedFirePolicy.FIRE_ALL:
            return _Decision(action=_Action.FIRE, reason=None)
        if policy == MissedFirePolicy.SKIP:
            if is_backlogged:
                return _Decision(
                    action=_Action.SKIP, reason=SkipReason.POLICY_SKIP_BACKLOG
                )
            return _Decision(action=_Action.FIRE, reason=None)
        # Default: fire_once — current slot fires; backlog claims (older
        # slots that may surface) are skipped by the backend's claim
        # algorithm (it returns only the latest pending slot per routine).
        # Defensive check: if a stale ``fire_at`` somehow makes it through,
        # still fire the *most recent* one and rely on idempotency for the
        # rest.
        return _Decision(action=_Action.FIRE, reason=None)

    # ---- advance helpers --------------------------------------------------

    async def _advance(self, *, claim: RoutineFireClaim) -> bool:
        """Compute the next firing instant and tell backend to persist it.

        Picks the first ``schedule`` trigger on the claim (a routine may
        have multiple schedule triggers; we advance using the earliest next
        fire across them all).
        """
        cron_specs = [
            t.cron
            for t in claim.triggers
            if t.kind == "schedule" and t.cron is not None
        ]
        if not cron_specs:
            # Webhook-only / event-only / manual-only routine: no schedule
            # to advance. Should not happen in the scheduler's claim list
            # because the backend filters on schedule triggers, but guard
            # anyway.
            return True
        # Compute next fire = MIN over each cron's next instant strictly
        # after the current claim's fire_at (we anchor on fire_at to make
        # the advance deterministic regardless of how late we ran).
        next_candidates: list[datetime] = []
        for spec in cron_specs:
            try:
                next_candidates.append(
                    self._evaluator.next_fire(spec=spec, after=claim.fire_at)
                )
            except CronSpecError:
                _LOGGER.warning(
                    "routine_scheduler.bad_cron",
                    extra={
                        "metadata": {
                            "routine_id": claim.routine_id,
                            "tenant_id": claim.tenant_id,
                        }
                    },
                )
                continue
        if not next_candidates:
            return False
        next_fire_at = min(next_candidates)
        return await self._client.advance_next_fire(
            routine_id=claim.routine_id,
            tenant_id=claim.tenant_id,
            next_fire_at=next_fire_at,
        )

    # ---- clock helper -----------------------------------------------------

    def _now(self) -> datetime:
        value = self._clock() if callable(self._clock) else self._clock
        assert isinstance(value, datetime)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Decision sub-types (private)
# ---------------------------------------------------------------------------


class _Action:
    FIRE = "fire"
    SKIP = "skip"


class _Decision(BaseModel):
    """Internal fire-vs-skip decision for one claim."""

    model_config = ConfigDict(frozen=True)
    action: str
    reason: str | None = None


class _PreFireOutcome:
    """Outcome of the pre-fire permission gate for one claim.

    Three distinct states because the scheduler's branching must
    differentiate "no gate wired" (SKIPPED -- fall through to legacy
    submit) from "gate said no" (PAUSED -- record skip + no advance) from
    "gate said yes" (ALLOWED -- submit).
    """

    SKIPPED: ClassVar[str] = "skipped"
    ALLOWED: ClassVar[str] = "allowed"
    PAUSED: ClassVar[str] = "paused"


__all__ = [
    "CronSpecError",
    "CronSpecEvaluator",
    "FireStatus",
    "MissedFirePolicy",
    "NullRoutinePermissionContextResolver",
    "NullRoutineRunSubmitter",
    "ResolvedRoutinePermissionInput",
    "RoutinePermissionContextResolver",
    "RoutineRunRequest",
    "RoutineRunSubmitter",
    "RoutineSchedulerEnv",
    "RoutineSchedulerLoop",
    "SkipReason",
    "TickOutcome",
]
