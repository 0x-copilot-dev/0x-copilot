"""Bounded, planning-only D12 repair/reconciliation worker loop.

This job can observe existing durable effect claims and runtime event history,
then persist *only* the redacted candidate/withheld output of ``RepairPlanner``.
It deliberately has no cleanup, deletion, approval, queue, apply, resend, or
executor dependency.  A future, separately authorized reconciliation executor
may consume a candidate; this runner never does.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import logging
import os
from time import perf_counter

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimScanCursor,
    EffectClaimStore,
    EffectClaimState,
)
from agent_runtime.observability.lifecycle_metrics import (
    LifecycleOperationalMetrics,
    LifecyclePlanOutcomeLabel,
    LifecyclePlannerLabel,
    get_lifecycle_operational_metrics,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind, LedgerEventType
from agent_runtime.surfaces_v2.lifecycle_refs import (
    LifecycleReferenceEnumerator,
    LifecycleReferenceRegistry,
    LifecycleReferenceScheme,
)
from agent_runtime.surfaces_v2.repair_planning import (
    RepairLegalHoldLookup,
    RepairPlanningSnapshot,
    RepairPlanningSnapshotStore,
    RepairPlanningStateError,
    build_repair_planning_snapshot,
)
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairCandidateKind,
    RepairDecisionState,
    RepairEffectState,
    RepairEvidenceState,
    RepairGraphCoverage,
    RepairLegalHoldState,
    RepairOwnerState,
    RepairPlanner,
    RepairPlanningRequest,
    RepairSnapshotRecord,
)
from runtime_api.schemas import ACTIVE_RUN_STATUSES


_LOGGER = logging.getLogger(__name__)
_TERMINAL_STATUS_VALUES = frozenset({"cancelled", "completed", "failed", "timed_out"})
_MAX_PAGE_ADVANCES_PER_CYCLE = 8
_MAX_CAS_CONFLICTS_PER_CYCLE = 8


class RepairPlanningLoopEnv:
    """Explicit, disabled-by-default configuration for the D12 planner."""

    ENABLED = "REPAIR_PLANNING_ENABLED"
    INTERVAL_SECONDS = "REPAIR_PLANNING_INTERVAL_SECONDS"
    MAX_CLAIMS = "REPAIR_PLANNING_MAX_CLAIMS"
    PAGE_SIZE = "REPAIR_PLANNING_PAGE_SIZE"
    MAX_EVENTS_PER_RUN = "REPAIR_PLANNING_MAX_EVENTS_PER_RUN"
    QUIET_SECONDS = "REPAIR_PLANNING_QUIET_SECONDS"

    DEFAULT_INTERVAL_SECONDS = 600.0
    DEFAULT_MAX_CLAIMS = 100
    DEFAULT_PAGE_SIZE = 100
    DEFAULT_MAX_EVENTS_PER_RUN = 2_000
    DEFAULT_QUIET_SECONDS = 120

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    @classmethod
    def env_int(cls, name: str, default: int, *, maximum: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if 1 <= value <= maximum else default


@dataclass(frozen=True, slots=True)
class RepairPlanningCycleResult:
    """Aggregate, identifier-free outcome returned by one bounded cycle."""

    snapshots: int = 0
    candidates: int = 0
    withheld: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class RepairPlanningSourcePage:
    """One trusted bounded claim page and its durable scan-cursor transition."""

    snapshots: tuple[RepairPlanningSnapshot, ...]
    expected_cursor: EffectClaimScanCursor | None
    next_cursor: EffectClaimScanCursor | None
    advance_cursor: bool


class EffectClaimRepairSnapshotCollector:
    """Collect only trusted, redacted effect-reconciliation snapshot facts."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        claims: EffectClaimStore,
        legal_holds: RepairLegalHoldLookup,
        supported_reconcile_executors: frozenset[EffectExecutorKind] = frozenset(),
        max_events_per_run: int = RepairPlanningLoopEnv.DEFAULT_MAX_EVENTS_PER_RUN,
        quiet_period: timedelta = timedelta(
            seconds=RepairPlanningLoopEnv.DEFAULT_QUIET_SECONDS
        ),
    ) -> None:
        if max_events_per_run < 1 or quiet_period < timedelta(0):
            raise ValueError("repair planning collector bounds are invalid")
        self._persistence = persistence
        self._event_store = event_store
        self._claims = claims
        self._legal_holds = legal_holds
        self._supported_reconcile_executors = supported_reconcile_executors
        self._max_events_per_run = max_events_per_run
        self._quiet_period = quiet_period
        self._enumerator = LifecycleReferenceEnumerator()
        self._references = LifecycleReferenceRegistry.default()

    async def collect(
        self,
        *,
        limit: int,
        cursor: EffectClaimScanCursor | None = None,
        now: datetime | None = None,
    ) -> RepairPlanningSourcePage:
        """Read one keyset page and produce tenant-scoped plan snapshots.

        Seeing one extra row proves there is a later page; that is a normal,
        complete keyset boundary rather than incomplete enumeration. A broken
        adapter response (overlong, out of order, duplicate, or before the
        supplied cursor) is held in place and every retained record is marked
        incomplete, so a planner cannot convert uncertain source traversal
        into a candidate.
        """

        if limit < 1 or limit > 500:
            raise ValueError("repair planning claim limit is invalid")
        reference_now = _as_utc(now or datetime.now(UTC))
        listed_claims = await self._claims.list_incomplete_after(
            cursor=cursor,
            limit=limit + 1,
        )
        # The port promises a Sequence, but retain an explicit local bound as
        # well: a buggy adapter must not turn this periodic job into an
        # unbounded allocation.  An overlong response is still observable as
        # incomplete and thus produces withheld rows only.
        source_complete = len(listed_claims) <= limit + 1
        claims = tuple(listed_claims[: limit + 1])
        try:
            claim_cursors = tuple(_scan_cursor_for(claim) for claim in claims)
            cursor_keys = tuple(_scan_cursor_key(item) for item in claim_cursors)
            supplied_key = _scan_cursor_key(cursor) if cursor is not None else None
            source_complete = source_complete and (
                cursor_keys == tuple(sorted(cursor_keys))
                and len(cursor_keys) == len(set(cursor_keys))
                and (
                    supplied_key is None
                    or all(key > supplied_key for key in cursor_keys)
                )
            )
        except (TypeError, ValueError):
            source_complete = False
            claim_cursors = ()
        bounded_claims = claims[:limit]
        grouped: dict[str, list[EffectClaim]] = defaultdict(list)
        for claim in bounded_claims:
            grouped[claim.org_id].append(claim)
        snapshots: list[RepairPlanningSnapshot] = []
        for tenant_id in sorted(grouped):
            # Keep the I/O fan-out explicitly bounded. Sequential collection
            # is intentional here: this is a low-frequency repair planner,
            # and a large global scan must not issue an unbounded burst of
            # event/hold reads to the durable stores.
            records: list[RepairSnapshotRecord] = []
            for claim in grouped[tenant_id]:
                records.append(
                    await self._record_for_claim(
                        claim=claim,
                        now=reference_now,
                        force_incomplete=not source_complete,
                    )
                )
            snapshots.append(
                build_repair_planning_snapshot(
                    tenant_id=tenant_id,
                    records=tuple(records),
                    source_complete=source_complete,
                    as_of=reference_now,
                )
            )
        has_more = len(claims) > limit
        next_cursor = (
            claim_cursors[len(bounded_claims) - 1]
            if source_complete and has_more and bounded_claims
            else None
        )
        # A malformed page deliberately does not advance. An exhausted
        # non-initial page resets to ``None`` so the next cycle safely begins a
        # fresh full scan and can notice a previously scanned claim changing.
        advance_cursor = source_complete and next_cursor != cursor
        return RepairPlanningSourcePage(
            snapshots=tuple(snapshots),
            expected_cursor=cursor,
            next_cursor=next_cursor,
            advance_cursor=advance_cursor,
        )

    async def _record_for_claim(
        self,
        *,
        claim: EffectClaim,
        now: datetime,
        force_incomplete: bool,
    ) -> RepairSnapshotRecord:
        # Claim IDs are already validated opaque ledger identifiers. Retaining
        # that stable identifier lets a future, separately authorized
        # reconciliation consumer re-load the exact durable claim without
        # storing a second raw-reference mapping in this planning state.
        candidate_id = claim.claim_id
        reference_scheme = self._reference_scheme(claim)
        effect_state = _effect_state(claim)
        graph_coverage = RepairGraphCoverage.INCOMPLETE
        legal_hold = RepairLegalHoldState.UNKNOWN
        owner_state = RepairOwnerState.UNKNOWN
        evidence_state = RepairEvidenceState.MISSING
        evidence_id: str | None = None
        quiet_elapsed = _quiet_period_elapsed(
            updated_at=claim.updated_at,
            now=now,
            quiet_period=self._quiet_period,
        )
        try:
            run = await self._persistence.get_run(
                org_id=claim.org_id,
                run_id=claim.run_id,
            )
            if run is None or run.org_id != claim.org_id or run.run_id != claim.run_id:
                return self._record(
                    claim=claim,
                    candidate_id=candidate_id,
                    reference_scheme=reference_scheme,
                    graph_coverage=graph_coverage,
                    legal_hold=legal_hold,
                    evidence_state=evidence_state,
                    evidence_id=evidence_id,
                    owner_state=owner_state,
                    effect_state=effect_state,
                    quiet_elapsed=quiet_elapsed,
                )
            owner_state = (
                RepairOwnerState.TERMINAL
                if run.status.value in _TERMINAL_STATUS_VALUES
                else RepairOwnerState.ACTIVE
                if run.status in ACTIVE_RUN_STATUSES
                else RepairOwnerState.UNKNOWN
            )
            legal_hold = await self._legal_holds.resolve(
                org_id=claim.org_id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
            )
            graph_coverage, claim_evidenced = await self._verify_run_graph(
                org_id=claim.org_id,
                run_id=claim.run_id,
                latest_sequence_no=run.latest_sequence_no,
                conversation_id=run.conversation_id,
                claim_id=claim.claim_id,
                stage_id=claim.stage_id,
                revision=claim.revision,
                executor=claim.executor,
            )
            if graph_coverage is RepairGraphCoverage.COMPLETE and claim_evidenced:
                evidence_state = RepairEvidenceState.VERIFIED
                evidence_id = _opaque_digest(
                    "rev",
                    claim.org_id,
                    claim.claim_id,
                    claim.state.value,
                    claim.updated_at,
                    str(run.latest_sequence_no),
                )
        except Exception:
            # Source errors may contain an adapter path or remote reference.  Do
            # not log them; the persisted safe decision below makes the failure
            # observable without disclosing source data.
            graph_coverage = RepairGraphCoverage.INCOMPLETE
            legal_hold = RepairLegalHoldState.UNKNOWN
            owner_state = RepairOwnerState.UNKNOWN
            evidence_state = RepairEvidenceState.MISSING
            evidence_id = None
        if force_incomplete:
            graph_coverage = RepairGraphCoverage.INCOMPLETE
        return self._record(
            claim=claim,
            candidate_id=candidate_id,
            reference_scheme=reference_scheme,
            graph_coverage=graph_coverage,
            legal_hold=legal_hold,
            evidence_state=evidence_state,
            evidence_id=evidence_id,
            owner_state=owner_state,
            effect_state=effect_state,
            quiet_elapsed=quiet_elapsed,
        )

    def _reference_scheme(self, claim: EffectClaim) -> str:
        """Validate every durable claim reference before it can be planned.

        Effect claims intentionally allow new server-owned reference schemes at
        their write boundary.  A repair candidate is stricter: an unknown,
        malformed, or legacy-only reference must be withheld until it has an
        explicit lifecycle owner.  No raw reference is retained or reported.
        """

        try:
            proposal = self._references.parse(claim.proposal_ref)
            target = self._references.parse(claim.target_ref)
            if claim.proposal_content_ref is None:
                return "unknown"
            self._references.parse(claim.proposal_content_ref)
        except Exception:
            return "unknown"
        # The proposal reference is always canonical for an EffectClaim.  Use
        # the target's registered scheme for the D12 row so a candidate tells
        # operators which ownership lane would need a future reconciler.
        if proposal.scheme is not LifecycleReferenceScheme.PROPOSAL:
            return "unknown"
        return target.scheme.value

    def _record(
        self,
        *,
        claim: EffectClaim,
        candidate_id: str,
        reference_scheme: str,
        graph_coverage: RepairGraphCoverage,
        legal_hold: RepairLegalHoldState,
        evidence_state: RepairEvidenceState,
        evidence_id: str | None,
        owner_state: RepairOwnerState,
        effect_state: RepairEffectState,
        quiet_elapsed: bool,
    ) -> RepairSnapshotRecord:
        return RepairSnapshotRecord(
            candidate_id=candidate_id,
            tenant_id=claim.org_id,
            kind=RepairCandidateKind.EFFECT_RECONCILIATION,
            reference_scheme=reference_scheme,
            graph_coverage=graph_coverage,
            legal_hold=legal_hold,
            evidence_state=evidence_state,
            evidence_id=evidence_id,
            owner_state=owner_state,
            effect_state=effect_state,
            reconcile_supported=(claim.executor in self._supported_reconcile_executors),
            quiet_period_elapsed=quiet_elapsed,
        )

    async def _verify_run_graph(
        self,
        *,
        org_id: str,
        run_id: str,
        conversation_id: str,
        latest_sequence_no: int,
        claim_id: str,
        stage_id: str,
        revision: int,
        executor: EffectExecutorKind,
    ) -> tuple[RepairGraphCoverage, bool]:
        if latest_sequence_no < 0 or latest_sequence_no > self._max_events_per_run:
            return RepairGraphCoverage.INCOMPLETE, False
        envelopes = tuple(
            await self._event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=0,
            )
        )
        if any(
            envelope.run_id != run_id or envelope.conversation_id != conversation_id
            for envelope in envelopes
        ):
            return RepairGraphCoverage.INCOMPLETE, False
        sequences = tuple(envelope.sequence_no for envelope in envelopes)
        if sequences != tuple(range(1, latest_sequence_no + 1)):
            return RepairGraphCoverage.INCOMPLETE, False
        ledger_events: list[dict[str, object]] = []
        claim_evidenced = False
        for envelope in envelopes:
            try:
                event_type = LedgerEventType(envelope.event_type.value)
            except ValueError:
                continue
            payload = envelope.payload
            if not isinstance(payload, Mapping):
                return RepairGraphCoverage.INCOMPLETE, False
            copied_payload = dict(payload)
            ledger_events.append(
                {
                    "event_type": event_type.value,
                    "sequence_no": envelope.sequence_no,
                    "payload": copied_payload,
                }
            )
            if (
                event_type is LedgerEventType.EFFECT_CLAIMED
                and copied_payload.get("claim_id") == claim_id
                and copied_payload.get("stage_id") == stage_id
                and copied_payload.get("revision") == revision
                and copied_payload.get("executor") == executor.value
            ):
                claim_evidenced = True
        try:
            self._enumerator.enumerate(run_id=run_id, events=ledger_events)
        except Exception:
            return RepairGraphCoverage.INCOMPLETE, False
        return RepairGraphCoverage.COMPLETE, claim_evidenced


class RepairPlanningRunner:
    """Persist bounded planner pages without an execution capability."""

    def __init__(
        self,
        *,
        collector: EffectClaimRepairSnapshotCollector,
        snapshots: RepairPlanningSnapshotStore,
        planner: RepairPlanner | None = None,
        page_size: int = RepairPlanningLoopEnv.DEFAULT_PAGE_SIZE,
        max_claims: int = RepairPlanningLoopEnv.DEFAULT_MAX_CLAIMS,
        metrics: LifecycleOperationalMetrics | None = None,
    ) -> None:
        if not 1 <= page_size <= 500 or not 1 <= max_claims <= 500:
            raise ValueError("repair planning runner bounds are invalid")
        self._collector = collector
        self._snapshots = snapshots
        self._metrics = (
            metrics if metrics is not None else get_lifecycle_operational_metrics()
        )
        self._planner = (
            planner if planner is not None else RepairPlanner(metrics=self._metrics)
        )
        self._page_size = page_size
        self._max_claims = max_claims

    async def run_once(
        self, *, now: datetime | None = None
    ) -> RepairPlanningCycleResult:
        """Collect and persist one bounded planning pass; never execute a repair."""

        started_at = perf_counter()
        try:
            source_cursor = await self._snapshots.load_effect_claim_scan_cursor()
            source_page = await self._collector.collect(
                limit=self._max_claims,
                cursor=source_cursor,
                now=now,
            )
        except Exception:
            self._record_failure(elapsed_seconds=perf_counter() - started_at)
            return RepairPlanningCycleResult(failed=1)
        result = RepairPlanningCycleResult(snapshots=len(source_page.snapshots))
        snapshots_completed = True
        for snapshot in source_page.snapshots:
            try:
                page_result = await self._plan_snapshot(snapshot)
                result = RepairPlanningCycleResult(
                    snapshots=result.snapshots,
                    candidates=result.candidates + page_result.candidates,
                    withheld=result.withheld + page_result.withheld,
                    failed=result.failed + page_result.failed,
                )
                state = await self._snapshots.load(
                    tenant_id=snapshot.tenant_id,
                    snapshot_id=snapshot.snapshot_id,
                )
                if state is None or not state.completed:
                    snapshots_completed = False
            except Exception:
                snapshots_completed = False
                self._record_failure(elapsed_seconds=perf_counter() - started_at)
                result = RepairPlanningCycleResult(
                    snapshots=result.snapshots,
                    candidates=result.candidates,
                    withheld=result.withheld,
                    failed=result.failed + 1,
                )
        if snapshots_completed and source_page.advance_cursor:
            try:
                await self._snapshots.advance_effect_claim_scan_cursor(
                    expected=source_page.expected_cursor,
                    next_cursor=source_page.next_cursor,
                )
            except Exception:
                self._record_failure(elapsed_seconds=perf_counter() - started_at)
                result = RepairPlanningCycleResult(
                    snapshots=result.snapshots,
                    candidates=result.candidates,
                    withheld=result.withheld,
                    failed=result.failed + 1,
                )
        return result

    async def _plan_snapshot(
        self, snapshot: RepairPlanningSnapshot
    ) -> RepairPlanningCycleResult:
        state = await self._snapshots.load_or_create(snapshot=snapshot)
        if state.completed:
            return RepairPlanningCycleResult()
        candidates = 0
        withheld = 0
        page_advances = 0
        cas_conflicts = 0
        while page_advances < _MAX_PAGE_ADVANCES_PER_CYCLE:
            if state.completed:
                return RepairPlanningCycleResult(
                    candidates=candidates,
                    withheld=withheld,
                )
            plan = self._planner.plan(
                RepairPlanningRequest(
                    tenant_id=state.snapshot.tenant_id,
                    snapshot_id=state.snapshot.snapshot_id,
                    as_of=state.snapshot.as_of,
                    records=state.snapshot.records,
                    cursor=state.cursor(),
                    limit=self._page_size,
                )
            )
            advanced = await self._snapshots.advance(
                tenant_id=state.snapshot.tenant_id,
                snapshot_id=state.snapshot.snapshot_id,
                expected_after_candidate_id=state.after_candidate_id,
                plan=plan,
            )
            if not advanced:
                cas_conflicts += 1
                if cas_conflicts >= _MAX_CAS_CONFLICTS_PER_CYCLE:
                    raise RepairPlanningStateError()
                loaded = await self._snapshots.load(
                    tenant_id=state.snapshot.tenant_id,
                    snapshot_id=state.snapshot.snapshot_id,
                )
                if loaded is None:
                    raise RepairPlanningStateError()
                state = loaded
                continue
            cas_conflicts = 0
            page_advances += 1
            candidates += sum(
                decision.state is RepairDecisionState.CANDIDATE
                for decision in plan.decisions
            )
            withheld += sum(
                decision.state is RepairDecisionState.WITHHELD
                for decision in plan.decisions
            )
            loaded = await self._snapshots.load(
                tenant_id=state.snapshot.tenant_id,
                snapshot_id=state.snapshot.snapshot_id,
            )
            if loaded is None:
                raise RepairPlanningStateError()
            state = loaded
        # A larger snapshot resumes from its durable cursor next interval. A
        # normal bounded page budget is progress, not a planning failure.
        return RepairPlanningCycleResult(candidates=candidates, withheld=withheld)

    def _record_failure(self, *, elapsed_seconds: float) -> None:
        try:
            self._metrics.record_plan_failure(
                planner=LifecyclePlannerLabel.REPAIR,
                outcome=LifecyclePlanOutcomeLabel.FAILED,
                elapsed_seconds=elapsed_seconds,
            )
        except Exception:
            return


class RepairPlanningLoop:
    """Periodic wrapper for the planning-only runner; disabled by composition."""

    def __init__(
        self,
        *,
        runner: RepairPlanningRunner,
        interval_seconds: float | None = None,
    ) -> None:
        self._runner = runner
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else RepairPlanningLoopEnv.env_float(
                RepairPlanningLoopEnv.INTERVAL_SECONDS,
                RepairPlanningLoopEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="repair-planning-loop")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            return

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            result = await self._runner.run_once()
            if result.failed:
                # Counts and the fixed event name are safe; source exceptions,
                # tenant IDs, paths, content, and references never reach logs.
                _LOGGER.warning(
                    "repair_planning_cycle_failed snapshots=%s failures=%s",
                    result.snapshots,
                    result.failed,
                )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _opaque_digest(prefix: str, *parts: str) -> str:
    encoded = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:48]}"


def _scan_cursor_for(claim: EffectClaim) -> EffectClaimScanCursor:
    created_at = datetime.fromisoformat(claim.created_at)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("claim timestamp must be timezone-aware")
    return EffectClaimScanCursor(
        after_created_at=created_at.astimezone(UTC),
        after_org_id=claim.org_id,
        after_claim_id=claim.claim_id,
    )


def _scan_cursor_key(cursor: EffectClaimScanCursor) -> tuple[datetime, str, str]:
    return (
        cursor.after_created_at,
        cursor.after_org_id,
        cursor.after_claim_id,
    )


def _effect_state(claim: EffectClaim) -> RepairEffectState:
    return {
        EffectClaimState.CLAIMED: RepairEffectState.CLAIMED,
        EffectClaimState.INDETERMINATE: RepairEffectState.INDETERMINATE,
        EffectClaimState.COMPLETED: RepairEffectState.COMPLETED,
        EffectClaimState.CANCELLED: RepairEffectState.CANCELLED,
    }.get(claim.state, RepairEffectState.UNKNOWN)


def _quiet_period_elapsed(
    *, updated_at: str, now: datetime, quiet_period: timedelta
) -> bool:
    try:
        updated = _as_utc(datetime.fromisoformat(updated_at))
    except ValueError:
        return False
    return updated <= now - quiet_period


__all__ = (
    "EffectClaimRepairSnapshotCollector",
    "RepairPlanningCycleResult",
    "RepairPlanningLoop",
    "RepairPlanningLoopEnv",
    "RepairPlanningRunner",
    "RepairPlanningSourcePage",
)
