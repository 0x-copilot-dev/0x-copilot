"""F6.3 batch execution: segment gates, permits, ordering, and disposal.

Every concurrency assertion here observes *real* overlap through a probe that
counts how many child bodies are inside at once. Nothing sleeps on the wall
clock, and no test infers concurrency from elapsed time: the program has already
been bitten once by a timing-based assertion, so overlap is measured, not timed.

Timestamps come from an injected counter clock, which makes "results follow
input order while timestamps follow completion order" two independently
checkable facts rather than one lucky coincidence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.capabilities.concurrency import (
    BatchAdmissionOutcome,
    BatchChildIdentity,
    BatchChildStatus,
    BatchCoordinatorError,
    BatchCoordinatorMessages,
    BatchExecutionCoordinator,
    BatchExecutionStatus,
    BatchFailurePolicy,
    BatchOperation,
    BatchPlanRecorder,
    BatchPlanRequest,
    BatchSegmentMode,
    ConcurrencyAllowance,
    ConcurrencyKillSwitchGate,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyScope,
    DurableBatchPlan,
    IdempotencyKind,
    OrderingRequirement,
    PermitCapacityPolicy,
    PermitOutcome,
    PermitScope,
    PlannedOperation,
    PolicySource,
    ProviderSessionConstraint,
    RunPermitManager,
    SideEffectKind,
)
from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlSnapshot,
    RunPolicyRevisions,
)

_PROFILE = "single_user_desktop"
_SUBJECT = "a" * 64
_RUN = "run-f63"
_TRACE = "trace-f63"
_ORG = "org-f63"
_CREATED_AT = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
_CAP = "cap_" + "0" * 32


class _CounterClock:
    """A deterministic, strictly increasing timezone-aware clock."""

    def __init__(self, base: datetime = _CREATED_AT) -> None:
        self._base = base
        self.ticks = 0

    def __call__(self) -> datetime:
        self.ticks += 1
        return self._base + timedelta(microseconds=self.ticks)


class _NullJournal:
    """A store that refuses to persist; plans are built, not written, here."""

    async def put_plan(self, write):  # pragma: no cover - never invoked
        raise AssertionError("coordinator tests must not persist")

    async def load_recovery_view(self, **kwargs):  # pragma: no cover
        raise AssertionError("coordinator tests must not read")


class CoordinatorFixtureMixin:
    """Durable plans, permit tables, and coordinators for every F6.3 test."""

    @staticmethod
    def snapshot() -> RunControlSnapshot:
        budget = BudgetEnvelope.create(
            budget_envelope_id="budget-f63",
            revision="budget-r1",
            max_model_turns=8,
            max_tool_calls=16,
        )
        return RunControlSnapshot.create(
            run_id=_RUN,
            conversation_id="conversation-f63",
            subject_fingerprint=_SUBJECT,
            deployment_profile=_PROFILE,
            harness_variant_ref="harness://stable/r1",
            task_policy_selection_ref="task-policy://unknown.general/r1",
            policy_revisions=RunPolicyRevisions(
                prompt="prompt-r1",
                capability="capability-r1",
                context="context-r1",
                tool_controller="tool-r1",
                concurrency="concurrency-r1",
                dataflow="dataflow-r1",
                mcp_freshness="mcp-r1",
                delegation="delegation-r1",
                model_route="model-r1",
                workspace_edit="workspace-r1",
                answer_verification="answer-r1",
            ),
            feature_modes=FeatureModeSet(f6=FeatureMode.ENFORCE),
            budget_envelope_ref=budget.revision_ref,
            assignment_revision="assignment-r1",
            snapshot_id="snapshot-f63",
            created_at=_CREATED_AT,
        )

    @staticmethod
    def read_policy() -> ConcurrencyPolicy:
        """A curated, explicitly parallel-safe read."""

        return ConcurrencyPolicy(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            idempotency=IdempotencyKind.NATURAL,
            rate_limit_scope=ConcurrencyScope.CONNECTOR,
            ordering_requirement=OrderingRequirement.NONE,
            provider_session_constraint=ProviderSessionConstraint.SESSION_PARALLEL_SAFE,
            policy_source=PolicySource.PRODUCT_CATALOG,
        )

    @staticmethod
    def write_policy() -> ConcurrencyPolicy:
        return ConcurrencyPolicy(
            mode=ConcurrencyMode.SERIAL,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            policy_source=PolicySource.PRODUCT_CATALOG,
        )

    @classmethod
    def planned(
        cls,
        operation_id: str,
        *,
        policy: ConcurrencyPolicy | None = None,
    ) -> PlannedOperation:
        return PlannedOperation.of(
            operation=BatchOperation(
                operation_id=operation_id,
                authorization_epoch="auth_1",
                dependency_ids=(),
                resource_fingerprints=(),
            ),
            capability_ref=_CAP,
            policy=policy,
        )

    @classmethod
    def durable_plan(
        cls,
        *operations: PlannedOperation,
        max_parallelism: int = 4,
        batch_id: str = "batch-f63",
        failure_policy: BatchFailurePolicy = BatchFailurePolicy.STOP_NEW,
        deadline_at: datetime | None = None,
    ) -> DurableBatchPlan:
        """Return the handle a completed journal append would have produced."""

        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=ConcurrencyAllowance.enforcing(max_parallelism)
        )
        request = BatchPlanRequest(
            org_id=_ORG,
            trace_id=_TRACE,
            subject_fingerprint=_SUBJECT,
            run_id=_RUN,
            batch_id=batch_id,
            turn_ordinal=1,
            operations=operations,
            failure_policy=failure_policy,
            deadline_at=deadline_at,
        )
        record = BatchPlanRecorder(journal=_NullJournal(), gate=gate).build_record(
            request,
            snapshot=cls.snapshot(),
            decision=gate.admit(),
            created_at=_CREATED_AT,
        )
        return DurableBatchPlan(sequence_no=1, record=record)

    @classmethod
    def serial_plan(cls, count: int = 5, **overrides) -> DurableBatchPlan:
        """A plan whose every operation is its own serial segment."""

        return cls.durable_plan(
            *(
                cls.planned(f"op-{index}", policy=cls.write_policy())
                for index in range(count)
            ),
            **overrides,
        )

    @classmethod
    def parallel_plan(cls, count: int = 4, **overrides) -> DurableBatchPlan:
        """A plan whose operations are curated independent reads."""

        return cls.durable_plan(
            *(
                cls.planned(f"op-{index}", policy=cls.read_policy())
                for index in range(count)
            ),
            **overrides,
        )

    @staticmethod
    def permit_scopes(identity: BatchChildIdentity) -> tuple[PermitScope, ...]:
        return (
            PermitScope.for_global(),
            PermitScope.for_capability(
                profile_id=_PROFILE,
                subject_fingerprint=_SUBJECT,
                capability_name=str(identity.capability_ref),
            ),
        )

    @staticmethod
    def permits(limits: dict[ConcurrencyScope, int]) -> RunPermitManager:
        return RunPermitManager(policy=PermitCapacityPolicy.from_limits(limits))

    @classmethod
    def coordinator(
        cls,
        *,
        permits: RunPermitManager | None = None,
        live_allowance=None,
        clock=None,
        max_tracked_batches: int = 32,
    ) -> BatchExecutionCoordinator:
        return BatchExecutionCoordinator(
            permits=permits if permits is not None else cls.wide_permits(),
            permit_scopes=cls.permit_scopes,
            live_allowance=live_allowance,
            clock=clock if clock is not None else _CounterClock(),
            max_tracked_batches=max_tracked_batches,
        )

    @staticmethod
    def wide_permits() -> RunPermitManager:
        """Permits broad enough that the plan, not the pool, is the bound."""

        return RunPermitManager(
            policy=PermitCapacityPolicy.from_limits(
                {kind: 16 for kind in ConcurrencyScope.permit_pool_kinds()}
            )
        )


class OverlapProbeMixin:
    """Observe real overlap instead of inferring it from elapsed time."""

    class Probe:
        def __init__(self) -> None:
            self.current = 0
            self.observed_max = 0
            self.entered: list[str] = []
            self.completed: list[str] = []

        async def occupy(self, operation_id: str, turns: int = 6) -> str:
            self.current += 1
            self.observed_max = max(self.observed_max, self.current)
            self.entered.append(operation_id)
            for _ in range(turns):
                await asyncio.sleep(0)
            self.current -= 1
            self.completed.append(operation_id)
            return f"value:{operation_id}"

    async def drive(
        self,
        coordinator: BatchExecutionCoordinator,
        plan: DurableBatchPlan,
        *,
        probe: Probe | None = None,
    ) -> Probe:
        """Start every child at once, the way the framework would."""

        observed = probe if probe is not None else self.Probe()

        async def child(operation_id: str):
            return await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=operation_id,
                runner=lambda _admission: observed.occupy(operation_id),
            )

        await asyncio.gather(
            *(child(operation_id) for operation_id in plan.plan.operation_ids)
        )
        return observed


class TestDurablePlanBinding(CoordinatorFixtureMixin):
    """Nothing runs that a durable plan does not account for."""

    async def test_a_batch_that_was_never_begun_never_runs_its_child(self) -> None:
        coordinator = self.coordinator()
        started = False

        async def runner(_admission):
            nonlocal started
            started = True

        result = await coordinator.run_child(
            batch_id="batch-never-planned",
            operation_id="op-0",
            runner=runner,
        )

        assert started is False
        assert result.outcome.status is BatchChildStatus.REFUSED
        assert result.outcome.admission is (BatchAdmissionOutcome.REFUSED_UNKNOWN_BATCH)
        assert result.outcome.identity.planned is False

    async def test_an_operation_outside_the_plan_never_runs(self) -> None:
        coordinator = self.coordinator()
        plan = self.parallel_plan(2)
        coordinator.begin(plan)
        started = False

        async def runner(_admission):
            nonlocal started
            started = True

        result = await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id="op-smuggled",
            runner=runner,
        )

        assert started is False
        assert result.outcome.admission is (
            BatchAdmissionOutcome.REFUSED_UNKNOWN_OPERATION
        )

    async def test_a_second_coroutine_for_one_operation_is_refused(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(2)
        coordinator.begin(plan)
        release = asyncio.Event()
        second: list[object] = []

        async def first(_admission):
            await release.wait()
            return "first"

        async def duplicate(_admission):  # pragma: no cover - must never run
            raise AssertionError("a duplicate coroutine must not run the child")

        task = asyncio.create_task(
            coordinator.run_child(
                batch_id=plan.batch_id, operation_id="op-0", runner=first
            )
        )
        await self._drain()
        second.append(
            await coordinator.run_child(
                batch_id=plan.batch_id, operation_id="op-0", runner=duplicate
            )
        )
        release.set()
        await task

        assert second[0].outcome.admission is (
            BatchAdmissionOutcome.REFUSED_ALREADY_SETTLED
        )

    def test_rebinding_the_identical_plan_is_idempotent(self) -> None:
        coordinator = self.coordinator()
        plan = self.parallel_plan(2)

        coordinator.begin(plan)
        coordinator.begin(plan)

        assert coordinator.tracked_batches == 1

    def test_a_different_plan_for_one_batch_id_is_a_typed_fault(self) -> None:
        coordinator = self.coordinator()
        coordinator.begin(self.parallel_plan(2, batch_id="batch-f63"))

        with pytest.raises(BatchCoordinatorError) as excinfo:
            coordinator.begin(self.serial_plan(2, batch_id="batch-f63"))

        assert excinfo.value.safe_message == BatchCoordinatorMessages.PLAN_CONFLICT

    @staticmethod
    async def _drain(turns: int = 4) -> None:
        for _ in range(turns):
            await asyncio.sleep(0)


class TestSegmentGates(CoordinatorFixtureMixin, OverlapProbeMixin):
    """Framework-started coroutines wait on the persisted segment gates."""

    async def test_a_serial_plan_never_exceeds_one_concurrent_child(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.serial_plan(5)
        coordinator.begin(plan)

        assert all(
            segment.mode is BatchSegmentMode.SERIAL for segment in plan.plan.segments
        )
        probe = await self.drive(coordinator, plan)

        assert probe.observed_max == 1
        assert probe.entered == list(plan.plan.operation_ids)
        assert probe.completed == list(plan.plan.operation_ids)

    async def test_undeclared_metadata_yields_no_overlap(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        # No policy at all: the conservative floor, planned fully serial.
        plan = self.durable_plan(*(self.planned(f"op-{index}") for index in range(4)))
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert all(
            segment.mode is BatchSegmentMode.SERIAL for segment in plan.plan.segments
        )
        assert probe.observed_max == 1

    async def test_a_parallel_segment_overlaps_up_to_its_allowance(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert plan.plan.segments[0].mode is BatchSegmentMode.PARALLEL
        assert plan.plan.segments[0].effective_max_parallelism == 4
        assert probe.observed_max == 4

    async def test_a_parallel_segment_never_exceeds_its_allowance(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        # Five curated reads under a batch allowance of two plan as
        # [op-0 op-1] [op-2 op-3] [op-4]; segments are barriers, so five
        # simultaneously started coroutines may still only ever overlap two.
        plan = self.parallel_plan(5, max_parallelism=2)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert [len(segment.operation_ids) for segment in plan.plan.segments] == [
            2,
            2,
            1,
        ]
        assert probe.observed_max == 2

    async def test_the_next_segment_waits_for_the_whole_previous_one(self) -> None:
        """The segment barrier, isolated from every other bound.

        Overlap *counts* cannot prove this. Four reads under an allowance of two
        plan as two parallel segments of two, so the observed maximum is two
        whether or not the barrier between them holds. What only the gate can
        deliver is that segment 1 waits for *both* members of segment 0. So the
        first read is released while the second is still held open: a
        coordinator that admitted on freed capacity rather than on a settled
        segment would let the third read in right here, and the checkpoint below
        would see it.
        """

        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(4, max_parallelism=2)
        coordinator.begin(plan)
        gates = {
            operation_id: asyncio.Event() for operation_id in plan.plan.operation_ids
        }
        entered: list[str] = []

        async def child(operation_id: str):
            async def runner(_admission):
                entered.append(operation_id)
                await gates[operation_id].wait()
                return operation_id

            return await coordinator.run_child(
                batch_id=plan.batch_id, operation_id=operation_id, runner=runner
            )

        tasks = [
            asyncio.create_task(child(operation_id))
            for operation_id in plan.plan.operation_ids
        ]
        await self._drain()
        assert entered == ["op-0", "op-1"]

        gates["op-0"].set()
        await self._drain()
        assert entered == ["op-0", "op-1"], "segment 1 started before segment 0 settled"

        gates["op-1"].set()
        await self._drain()
        assert entered == ["op-0", "op-1", "op-2", "op-3"]

        gates["op-2"].set()
        gates["op-3"].set()
        await asyncio.gather(*tasks)

        assert [len(segment.operation_ids) for segment in plan.plan.segments] == [2, 2]

    async def test_the_segment_gate_alone_bounds_a_narrowed_segment(self) -> None:
        """Width enforcement that no other layer can be doing.

        Every child here takes a permit in its *own* pool, so the permit table
        admits all four regardless of width, and the plan's own segment is four
        wide. The only thing holding the segment at two is the coordinator's own
        gate honouring the live narrowing. Releasing exactly one child must let
        in exactly one more: a gate that stopped counting would let in the rest.
        """

        coordinator = BatchExecutionCoordinator(
            permits=self.wide_permits(),
            permit_scopes=lambda identity: (
                PermitScope.for_capability(
                    profile_id=_PROFILE,
                    subject_fingerprint=_SUBJECT,
                    capability_name=identity.operation_id,
                ),
            ),
            live_allowance=lambda: ConcurrencyAllowance.enforcing(2),
            clock=_CounterClock(),
        )
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)
        gates = {
            operation_id: asyncio.Event() for operation_id in plan.plan.operation_ids
        }
        entered: list[str] = []

        async def child(operation_id: str):
            async def runner(_admission):
                entered.append(operation_id)
                await gates[operation_id].wait()
                return operation_id

            return await coordinator.run_child(
                batch_id=plan.batch_id, operation_id=operation_id, runner=runner
            )

        tasks = [
            asyncio.create_task(child(operation_id))
            for operation_id in plan.plan.operation_ids
        ]
        await self._drain()
        assert len(plan.plan.segments) == 1
        assert plan.plan.segments[0].effective_max_parallelism == 4
        assert entered == ["op-0", "op-1"]

        gates["op-0"].set()
        await self._drain()
        assert entered == ["op-0", "op-1", "op-2"], "one release admitted more than one"

        for gate in gates.values():
            gate.set()
        await asyncio.gather(*tasks)

        assert entered == ["op-0", "op-1", "op-2", "op-3"]

    @staticmethod
    async def _drain(turns: int = 8) -> None:
        for _ in range(turns):
            await asyncio.sleep(0)

    async def test_a_write_never_overlaps_the_reads_planned_before_it(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.durable_plan(
            self.planned("op-read-a", policy=self.read_policy()),
            self.planned("op-read-b", policy=self.read_policy()),
            self.planned("op-write", policy=self.write_policy()),
        )
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)
        outcomes = {
            outcome.identity.operation_id: outcome
            for outcome in coordinator.report(plan.batch_id).outcomes
        }

        assert probe.observed_max == 2
        assert probe.entered[:2] == ["op-read-a", "op-read-b"]
        assert probe.entered[2] == "op-write"
        assert outcomes["op-write"].admitted_at > outcomes["op-read-a"].completed_at
        assert outcomes["op-write"].admitted_at > outcomes["op-read-b"].completed_at


class TestPermitNarrowing(CoordinatorFixtureMixin, OverlapProbeMixin):
    """Permits may narrow the plan; they can never widen it."""

    async def test_a_default_permit_table_makes_a_parallel_plan_serial(self) -> None:
        coordinator = self.coordinator(permits=RunPermitManager())
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert plan.plan.segments[0].effective_max_parallelism == 4
        assert probe.observed_max == 1

    async def test_permit_capacity_bounds_a_wider_segment(self) -> None:
        coordinator = self.coordinator(
            permits=self.permits(
                {
                    ConcurrencyScope.GLOBAL: 2,
                    ConcurrencyScope.CAPABILITY: 16,
                }
            )
        )
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert probe.observed_max == 2

    async def test_a_refused_permit_never_runs_the_child(self) -> None:
        permits = self.wide_permits()
        coordinator = self.coordinator(permits=permits)
        plan = self.parallel_plan(2)
        coordinator.begin(plan)
        permits.dispose()
        started = False

        async def runner(_admission):
            nonlocal started
            started = True

        result = await coordinator.run_child(
            batch_id=plan.batch_id, operation_id="op-0", runner=runner
        )

        assert started is False
        assert result.outcome.status is BatchChildStatus.REFUSED
        assert result.outcome.admission is BatchAdmissionOutcome.REFUSED_PERMIT
        assert result.outcome.permit_outcome is PermitOutcome.REFUSED_DISPOSED

    async def test_a_permit_is_released_on_the_exception_path(self) -> None:
        permits = self.wide_permits()
        coordinator = self.coordinator(permits=permits)
        plan = self.parallel_plan(2)
        coordinator.begin(plan)

        async def explode(_admission):
            raise RuntimeError("child failed")

        result = await coordinator.run_child(
            batch_id=plan.batch_id, operation_id="op-0", runner=explode
        )

        assert result.outcome.status is BatchChildStatus.FAILED
        assert isinstance(result.error, RuntimeError)
        assert permits.active_leases == 0
        assert permits.tracked_scopes == 0

    async def test_a_permit_is_released_on_the_success_path(self) -> None:
        permits = self.wide_permits()
        coordinator = self.coordinator(permits=permits)
        plan = self.parallel_plan(3)
        coordinator.begin(plan)

        await self.drive(coordinator, plan)

        assert permits.active_leases == 0
        assert permits.tracked_scopes == 0
        assert permits.pending_waiters == 0

    async def test_a_permit_is_released_when_the_child_is_cancelled(self) -> None:
        permits = self.wide_permits()
        coordinator = self.coordinator(permits=permits)
        plan = self.parallel_plan(2)
        coordinator.begin(plan)
        entered = asyncio.Event()

        async def blocked(_admission):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            coordinator.run_child(
                batch_id=plan.batch_id, operation_id="op-0", runner=blocked
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert permits.active_leases == 0
        outcome = coordinator.report(plan.batch_id).outcomes[0]
        # The body started, so whether the external effect happened is unknown.
        assert outcome.status is BatchChildStatus.INDETERMINATE


class TestNoWidening(CoordinatorFixtureMixin, OverlapProbeMixin):
    """The effective width is the minimum of every ceiling that applies."""

    async def test_a_live_serial_control_forces_a_parallel_segment_serial(
        self,
    ) -> None:
        coordinator = self.coordinator(
            permits=self.wide_permits(),
            live_allowance=ConcurrencyAllowance.serial,
        )
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert probe.observed_max == 1

    async def test_a_live_control_narrower_than_the_plan_wins(self) -> None:
        coordinator = self.coordinator(
            permits=self.wide_permits(),
            live_allowance=lambda: ConcurrencyAllowance.enforcing(2),
        )
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert probe.observed_max == 2

    async def test_a_live_control_wider_than_the_plan_changes_nothing(self) -> None:
        coordinator = self.coordinator(
            permits=self.wide_permits(),
            live_allowance=lambda: ConcurrencyAllowance.enforcing(16),
        )
        plan = self.parallel_plan(5, max_parallelism=2)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert probe.observed_max == 2

    async def test_a_wide_live_control_cannot_widen_a_serial_plan(self) -> None:
        coordinator = self.coordinator(
            permits=self.wide_permits(),
            live_allowance=lambda: ConcurrencyAllowance.enforcing(16),
        )
        plan = self.serial_plan(4)
        coordinator.begin(plan)

        probe = await self.drive(coordinator, plan)

        assert probe.observed_max == 1

    async def test_the_permit_request_can_never_ask_for_more_than_the_segment(
        self,
    ) -> None:
        requested: list[int] = []
        permits = self.wide_permits()
        original = permits.acquire

        def observe(request):
            requested.append(request.max_parallelism)
            return original(request)

        permits.acquire = observe  # type: ignore[method-assign]
        coordinator = self.coordinator(
            permits=permits,
            live_allowance=lambda: ConcurrencyAllowance.enforcing(2),
        )
        plan = self.parallel_plan(4, max_parallelism=4)
        coordinator.begin(plan)

        await self.drive(coordinator, plan)

        assert requested == [2, 2, 2, 2]


class TestOrderingAndTimestamps(CoordinatorFixtureMixin):
    """Input order and completion order are both true at the same time."""

    async def test_results_follow_input_order_while_time_follows_completion(
        self,
    ) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(3, max_parallelism=4)
        coordinator.begin(plan)
        gates = {
            operation_id: asyncio.Event() for operation_id in plan.plan.operation_ids
        }

        async def child(operation_id: str):
            async def runner(_admission):
                await gates[operation_id].wait()
                return f"value:{operation_id}"

            return await coordinator.run_child(
                batch_id=plan.batch_id, operation_id=operation_id, runner=runner
            )

        tasks = [
            asyncio.create_task(child(operation_id))
            for operation_id in plan.plan.operation_ids
        ]
        # Finish in the reverse of input order, one completion at a time.
        for operation_id in ("op-2", "op-0", "op-1"):
            gates[operation_id].set()
            await self._drain()
        await asyncio.gather(*tasks)

        results = coordinator.results(plan.batch_id)
        assert [result.operation_id for result in results] == ["op-0", "op-1", "op-2"]
        assert [result.value for result in results] == [
            "value:op-0",
            "value:op-1",
            "value:op-2",
        ]
        by_completion = sorted(results, key=lambda result: result.outcome.completed_at)
        assert [result.operation_id for result in by_completion] == [
            "op-2",
            "op-0",
            "op-1",
        ]

    async def test_every_recorded_moment_is_real_and_ordered(self) -> None:
        clock = _CounterClock()
        coordinator = self.coordinator(permits=self.wide_permits(), clock=clock)
        plan = self.serial_plan(3)
        coordinator.begin(plan)

        async def child(operation_id: str):
            return await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=operation_id,
                runner=lambda _admission: self._yield_once(),
            )

        await asyncio.gather(
            *(child(operation_id) for operation_id in plan.plan.operation_ids)
        )

        report = coordinator.report(plan.batch_id)
        for outcome in report.outcomes:
            assert outcome.admitted_at is not None
            assert outcome.completed_at > outcome.admitted_at
            assert outcome.completed_at.tzinfo is not None
        assert report.completed_at == max(
            outcome.completed_at for outcome in report.outcomes
        )
        assert report.started_at < report.completed_at

    async def test_a_completed_batch_reports_completed(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(3)
        coordinator.begin(plan)

        async def child(operation_id: str):
            return await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=operation_id,
                runner=lambda _admission: self._yield_once(),
            )

        await asyncio.gather(
            *(child(operation_id) for operation_id in plan.plan.operation_ids)
        )

        assert coordinator.report(plan.batch_id).status is (
            BatchExecutionStatus.COMPLETED
        )

    async def test_an_unstarted_batch_reports_in_progress(self) -> None:
        coordinator = self.coordinator()
        plan = self.parallel_plan(2)
        coordinator.begin(plan)

        report = coordinator.report(plan.batch_id)

        assert report.status is BatchExecutionStatus.IN_PROGRESS
        assert report.completed_at is None
        assert all(
            outcome.status is BatchChildStatus.PENDING for outcome in report.outcomes
        )

    @staticmethod
    async def _yield_once() -> str:
        await asyncio.sleep(0)
        return "done"

    @staticmethod
    async def _drain(turns: int = 6) -> None:
        for _ in range(turns):
            await asyncio.sleep(0)


class TestFailurePolicy(CoordinatorFixtureMixin, OverlapProbeMixin):
    """A failure stops new admission without erasing a sibling's success."""

    async def test_stop_new_refuses_every_child_planned_after_a_failure(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.serial_plan(4, failure_policy=BatchFailurePolicy.STOP_NEW)
        coordinator.begin(plan)
        ran: list[str] = []

        async def child(operation_id: str):
            async def runner(_admission):
                ran.append(operation_id)
                if operation_id == "op-1":
                    raise RuntimeError("child failed")
                return operation_id

            return await coordinator.run_child(
                batch_id=plan.batch_id, operation_id=operation_id, runner=runner
            )

        await asyncio.gather(
            *(child(operation_id) for operation_id in plan.plan.operation_ids)
        )

        assert ran == ["op-0", "op-1"]
        report = coordinator.report(plan.batch_id)
        statuses = [outcome.status for outcome in report.outcomes]
        assert statuses == [
            BatchChildStatus.SUCCEEDED,
            BatchChildStatus.FAILED,
            BatchChildStatus.REFUSED,
            BatchChildStatus.REFUSED,
        ]
        assert report.status is BatchExecutionStatus.PARTIAL
        assert report.outcomes[2].admission is (
            BatchAdmissionOutcome.REFUSED_BATCH_STOPPED
        )
        # The sibling that already finished keeps its result.
        assert coordinator.results(plan.batch_id)[0].value == "op-0"

    async def test_collect_all_keeps_admitting_after_a_failure(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.serial_plan(4, failure_policy=BatchFailurePolicy.COLLECT_ALL)
        coordinator.begin(plan)
        ran: list[str] = []

        async def child(operation_id: str):
            async def runner(_admission):
                ran.append(operation_id)
                if operation_id == "op-1":
                    raise RuntimeError("child failed")
                return operation_id

            return await coordinator.run_child(
                batch_id=plan.batch_id, operation_id=operation_id, runner=runner
            )

        await asyncio.gather(
            *(child(operation_id) for operation_id in plan.plan.operation_ids)
        )

        assert ran == ["op-0", "op-1", "op-2", "op-3"]
        assert coordinator.report(plan.batch_id).status is (
            BatchExecutionStatus.PARTIAL
        )

    async def test_a_batch_where_nothing_succeeded_reports_failed(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.serial_plan(2, failure_policy=BatchFailurePolicy.COLLECT_ALL)
        coordinator.begin(plan)

        async def child(operation_id: str):
            async def runner(_admission):
                raise RuntimeError("child failed")

            return await coordinator.run_child(
                batch_id=plan.batch_id, operation_id=operation_id, runner=runner
            )

        await asyncio.gather(
            *(child(operation_id) for operation_id in plan.plan.operation_ids)
        )

        assert coordinator.report(plan.batch_id).status is BatchExecutionStatus.FAILED


class TestDeadlines(CoordinatorFixtureMixin):
    """An expired batch never admits, and a stalled one never hangs forever."""

    async def test_a_passed_deadline_refuses_admission_before_any_work(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(
            2,
            deadline_at=_CREATED_AT - timedelta(seconds=1),
        )
        coordinator.begin(plan)
        started = False

        async def runner(_admission):
            nonlocal started
            started = True

        result = await coordinator.run_child(
            batch_id=plan.batch_id, operation_id="op-0", runner=runner
        )

        assert started is False
        assert result.outcome.admission is BatchAdmissionOutcome.REFUSED_DEADLINE

    async def test_a_child_parked_behind_a_stalled_segment_is_bounded(self) -> None:
        # op-0's coroutine never arrives, so op-1 can only be released by the
        # bounded admission budget the deadline supplies.
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.serial_plan(2, deadline_at=_CREATED_AT + timedelta(seconds=30))
        coordinator.begin(plan)

        async def runner(_admission):  # pragma: no cover - must never run
            raise AssertionError("a parked child must not run out of order")

        task = asyncio.create_task(
            coordinator.run_child(
                batch_id=plan.batch_id, operation_id="op-1", runner=runner
            )
        )
        for _ in range(6):
            await asyncio.sleep(0)
        assert task.done() is False

        coordinator.dispose()
        result = await task

        assert result.outcome.admission is BatchAdmissionOutcome.REFUSED_DISPOSED


class TestRunLifecycle(CoordinatorFixtureMixin, OverlapProbeMixin):
    """Coordinator state is bounded inside a run and gone after it."""

    async def test_dispose_refuses_new_work_and_clears_state(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(2)
        coordinator.begin(plan)

        coordinator.dispose()
        result = await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id="op-0",
            runner=lambda _admission: asyncio.sleep(0),
        )

        assert coordinator.disposed is True
        assert coordinator.tracked_batches == 0
        assert result.outcome.admission is BatchAdmissionOutcome.REFUSED_UNKNOWN_BATCH

    def test_begin_after_dispose_registers_nothing(self) -> None:
        coordinator = self.coordinator()
        coordinator.dispose()

        coordinator.begin(self.parallel_plan(2))

        assert coordinator.tracked_batches == 0

    async def test_settled_batches_do_not_accumulate_across_a_run(self) -> None:
        coordinator = self.coordinator(
            permits=self.wide_permits(),
            max_tracked_batches=4,
        )

        for turn in range(12):
            plan = self.parallel_plan(2, batch_id=f"batch-turn-{turn}")
            coordinator.begin(plan)
            await self.drive(coordinator, plan)
            assert coordinator.tracked_batches <= 4

        assert coordinator.tracked_batches == 1

    async def test_an_exhausted_tracking_bound_is_a_typed_fault(self) -> None:
        coordinator = self.coordinator(max_tracked_batches=2)
        coordinator.begin(self.parallel_plan(2, batch_id="batch-a"))
        coordinator.begin(self.parallel_plan(2, batch_id="batch-b"))

        with pytest.raises(BatchCoordinatorError) as excinfo:
            coordinator.begin(self.parallel_plan(2, batch_id="batch-c"))

        assert excinfo.value.safe_message == (
            BatchCoordinatorMessages.TRACKING_EXHAUSTED
        )

    async def test_release_drops_one_batch(self) -> None:
        coordinator = self.coordinator(permits=self.wide_permits())
        plan = self.parallel_plan(2)
        coordinator.begin(plan)
        await self.drive(coordinator, plan)

        coordinator.release(plan.batch_id)

        assert coordinator.tracked_batches == 0
        with pytest.raises(BatchCoordinatorError) as excinfo:
            coordinator.report(plan.batch_id)
        assert excinfo.value.safe_message == BatchCoordinatorMessages.UNKNOWN_BATCH

    def test_a_naive_clock_is_a_typed_fault(self) -> None:
        coordinator = self.coordinator(clock=lambda: datetime(2026, 7, 29, 9, 0))

        with pytest.raises(BatchCoordinatorError) as excinfo:
            coordinator.begin(self.parallel_plan(2))

        assert excinfo.value.safe_message == BatchCoordinatorMessages.NAIVE_CLOCK

    async def test_unusable_permit_scopes_settle_the_child_and_raise(self) -> None:
        coordinator = BatchExecutionCoordinator(
            permits=self.wide_permits(),
            permit_scopes=lambda _identity: (),
            clock=_CounterClock(),
        )
        plan = self.parallel_plan(2)
        coordinator.begin(plan)

        with pytest.raises(BatchCoordinatorError) as excinfo:
            await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id="op-0",
                runner=lambda _admission: asyncio.sleep(0),
            )

        assert excinfo.value.safe_message == (
            BatchCoordinatorMessages.PERMIT_SCOPES_REJECTED
        )
        # The child settled, so the batch cannot stall behind it.
        assert coordinator.report(plan.batch_id).outcomes[0].status is (
            BatchChildStatus.REFUSED
        )
