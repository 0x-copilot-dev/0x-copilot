"""F6.8 gate — the Step 10 exit criteria that no existing test established.

Everything already covered is cited rather than duplicated: the planner's
barriers (``test_planner.py``), the coordinator's segment gate and permit
narrowing (``test_batch_coordinator.py``), sibling isolation
(``test_child_execution.py``), cancel and restart (``test_batch_cancel_restart.py``),
and W3's A/B overlap proof through the real graph
(``test_runtime_tool_control_batch.py``).

What was missing, and what this file establishes:

- **Latency, not merely overlap.** W3 proved three reads are *simultaneously
  active*. The Step 10 criterion is about *p95 latency*, and simultaneity is not
  the same claim. The first test here measures elapsed cost in **release waves**
  — a unit of latency that never touches the wall clock — and shows a parallel
  segment costs one wave where a serial one costs three. That is §17's
  "a parallel segment approaches max child latency", stated as an assertion.
- **The effect classes that may overlap, over the closed enum.** Existing tests
  parametrize three interesting cases. This iterates every
  :class:`SideEffectKind` member, so a member added later is serial-by-default
  or the test fails.
- **Resource subjects through the *wired* seam.** The planner has a
  ``RESOURCE_CONFLICT`` barrier, but the graph seam never emits a non-empty
  fingerprint — it answers *unknown* for any capability with a resource-key
  template, which is strictly more conservative. That substitution is what
  actually carries the criterion in production, and nothing asserted it.
- **Sibling survival through the real graph**, rather than against a coordinator
  constructed in a fixture.
- **Cancellation through the real graph** when it arrives as a task
  cancellation rather than as the queued cancel command — the case in which no
  coordinator route runs, and the turn must still invent nothing.
- **Run-wide width when two execution scopes are live**, the open question W3
  recorded and no test answers.

Nothing here sleeps on the wall clock.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.concurrency import (
    ApprovalRequirement,
    BatchSegmentMode,
    BatchSegmentReason,
    ConcurrencyMode,
    PolicySource,
    ResourceKeyTemplate,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    CapabilityConcurrencyDeclaration,
    RunBatchAdmission,
    graph_capability_ref,
)
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.execution.contracts import RuntimeBatchAdmissionContext

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _FANOUT,
    _TOOL,
    _admission,
    _binding,
    _declarations,
    _FanoutModel,
    _InMemoryBatchJournal,
)

#: Scheduler turns a child waits before concluding no sibling will join it.
#: Identical in purpose and value to ``_ConcurrencyProbe.YIELDS``: comfortably
#: more turns than the admission path spends between the gate and the tool body,
#: so every child that *was* admitted alongside this one has arrived. Widening
#: it can never merge two waves that genuinely did not overlap, because under an
#: exclusive permit the next child is not merely late — it is not running.
_QUIESCENCE_YIELDS = 32


class _WaveClock:
    """Elapsed latency measured in release waves, never in elapsed time.

    Each child's body stands for one unit of external latency: it arrives, waits
    for the wave it belongs to to close, and returns. A wave closes once no
    further child can join it.

    Total latency is therefore ``len(waves)``:

    - three children in one wave is ``max`` of the child latencies — the
      parallel case, and the whole point of F6;
    - three children in three waves is their ``sum`` — the serial case.

    This is what makes the claim a *latency* claim rather than a simultaneity
    claim, without a single wall-clock read.
    """

    def __init__(self) -> None:
        self.waves: list[tuple[int, ...]] = []
        self._arrived: list[int] = []

    async def spend_one_unit_of_latency(self, value: int) -> str:
        my_wave = len(self.waves)
        self._arrived.append(value)
        for _ in range(_QUIESCENCE_YIELDS):
            await asyncio.sleep(0)
        if len(self.waves) == my_wave:
            # First of this wave to finish quiescing: everyone who could join
            # has joined, so the wave closes here and later arrivals open a new
            # one.
            self.waves.append(tuple(self._arrived))
            self._arrived.clear()
        return str(value)


async def _run_graph_with(body: Any, *, tool_name: str = _TOOL) -> dict[str, Any]:
    """Drive the same real graph W3 uses, with a caller-supplied tool body."""

    graph = create_agent(
        model=_FanoutModel(),
        tools=[
            StructuredTool.from_function(
                name=tool_name,
                description="One observed operation.",
                coroutine=body,
            )
        ],
        middleware=[RuntimeToolControlMiddleware()],
    )
    return await graph.ainvoke(
        {"messages": [HumanMessage(content="Run all observations.")]}
    )


async def _run_configured(
    body: Any,
    *,
    declarations: Any = None,
    ceiling: int = _FANOUT,
) -> tuple[dict[str, Any], _InMemoryBatchJournal, RunBatchAdmission]:
    """Run the graph with F6 installed exactly as the composition root does."""

    journal = _InMemoryBatchJournal()
    token = RunControlContext.bind_for_run(_binding())
    admission = _admission(
        journal,
        declarations=_declarations() if declarations is None else declarations,
        ceiling=ceiling,
    )
    serial_admission = RunControlContext.serial_admission()
    assert serial_admission is not None
    serial_admission.install_parallel_admission(admission)
    batch_token = RuntimeBatchAdmissionContext.install(admission)
    try:
        result = await _run_graph_with(body)
    finally:
        RuntimeBatchAdmissionContext.reset(batch_token)
        RunControlContext.unbind(token)
    return result, journal, admission


async def _run_unconfigured(body: Any) -> dict[str, Any]:
    """Run the identical graph with no F6 admission installed."""

    token = RunControlContext.bind_for_run(_binding())
    try:
        return await _run_graph_with(body)
    finally:
        RunControlContext.unbind(token)


class TestIndependentReadsCostOneLatencyUnitNotThree:
    """The p95 criterion, reduced to the mechanism that produces it.

    A p95 number over real connectors belongs to the §18.5 performance gate and
    cannot be produced by a unit test. What a unit test *can* establish — and
    what a p95 improvement on independent curated reads entirely rests on — is
    that a parallel segment's elapsed cost is the maximum of its children rather
    than their sum. If that holds, the p95 claim follows from the connector
    latency distribution; if it does not, no amount of load testing rescues it.
    """

    async def test_a_parallel_segment_costs_one_wave(self) -> None:
        """Three independent reads are released together: latency == max."""

        clock = _WaveClock()
        result, journal, _ = await _run_configured(clock.spend_one_unit_of_latency)

        assert result["messages"][-1].content == "done"
        # Membership, not sequence. The claim is that all three reads shared one
        # wave, and a wave records the order its members happened to arrive in —
        # which is the scheduler's choice among children the plan declared
        # unordered. Asserting the arrival sequence would assert a guarantee a
        # parallel segment does not make and was never meant to make.
        assert [sorted(wave) for wave in clock.waves] == [[0, 1, 2]], (
            "the three reads did not share one release wave, so their latency "
            f"was not max-of-children: {clock.waves}"
        )
        assert len(journal.plans) == 1
        record = journal.plans[0].record
        assert len(record.segments) == 1
        assert record.segments[0].mode is BatchSegmentMode.PARALLEL

    async def test_the_identical_serial_work_costs_three_waves(self) -> None:
        """The A/B half: same graph, same tool, no F6 — latency == sum."""

        clock = _WaveClock()
        result = await _run_unconfigured(clock.spend_one_unit_of_latency)

        assert result["messages"][-1].content == "done"
        # Three waves of one child each, and every child accounted for. *Which*
        # child gets which wave is not asserted: with no F6 install the only gate
        # is the Step-2 exclusive lock, whose queue is arrival order at the lock,
        # and arrival order is the framework's scheduling of coroutines it started
        # together. Under load that is observably not input order, and it never
        # promised to be — what feature-off parity claims is that nothing
        # overlapped, which is exactly one child per wave.
        assert [len(wave) for wave in clock.waves] == [1, 1, 1], (
            "an unconfigured run overlapped, which would break feature-off "
            f"parity: {clock.waves}"
        )
        assert sorted(value for wave in clock.waves for value in wave) == [0, 1, 2]

    async def test_the_speedup_is_the_segment_width(self) -> None:
        """Stated as the ratio the performance budget is written in.

        §17 says a parallel segment "approaches max child latency". Comparing
        the two runs directly is what turns that from a design note into a
        measured property of this composition.
        """

        parallel_clock = _WaveClock()
        await _run_configured(parallel_clock.spend_one_unit_of_latency)
        serial_clock = _WaveClock()
        await _run_unconfigured(serial_clock.spend_one_unit_of_latency)

        assert len(serial_clock.waves) == _FANOUT
        assert len(parallel_clock.waves) == 1
        assert len(serial_clock.waves) / len(parallel_clock.waves) == _FANOUT


class TestOnlyDeclaredReadsCanEverOverlap:
    """Criterion: writes, effects, and unknown effects never overlap.

    Existing coverage parametrizes three cases. This iterates the closed enum,
    so a member added later cannot default into the overlapping set.
    """

    @pytest.mark.parametrize("side_effect", list(SideEffectKind))
    async def test_every_effect_class_other_than_read_and_none_is_serial(
        self,
        side_effect: SideEffectKind,
    ) -> None:
        clock = _WaveClock()
        _, journal, _ = await _run_configured(
            clock.spend_one_unit_of_latency,
            declarations=_declarations(
                mode=ConcurrencyMode.PARALLEL_SAFE,
                side_effect=side_effect,
            ),
        )

        overlapping = side_effect in (SideEffectKind.NONE, SideEffectKind.READ)
        expected_waves = 1 if overlapping else _FANOUT
        assert len(clock.waves) == expected_waves, (
            f"{side_effect} produced {len(clock.waves)} waves; "
            f"only NONE and READ may ever overlap"
        )

        record = journal.plans[0].record
        if overlapping:
            assert len(record.segments) == 1
            assert record.segments[0].mode is BatchSegmentMode.PARALLEL
        else:
            assert {segment.mode for segment in record.segments} == {
                BatchSegmentMode.SERIAL
            }

    async def test_a_serial_mode_declaration_never_overlaps_a_read(self) -> None:
        """Mode narrows independently of the effect class."""

        clock = _WaveClock()
        await _run_configured(
            clock.spend_one_unit_of_latency,
            declarations=_declarations(
                mode=ConcurrencyMode.SERIAL,
                side_effect=SideEffectKind.READ,
            ),
        )

        assert len(clock.waves) == _FANOUT


class TestAPlannedChildIsAdmittedWhateverOrderItArrivesIn:
    """The regression that made ``ci-ai-backend`` hang for eleven 300s stretches.

    A serial plan orders its children by segment, and the coordinator's cursor can
    only order the children that have *arrived*. Nothing gives them a
    plan-ordered arrival: the framework starts a turn's tool coroutines together
    and the scheduler picks who reaches the run's tool gate first. So an
    admission that serialized planned siblings against each other could park a
    later segment's child inside the coordinator while it held the one lock its
    blocker needed to arrive at all, and neither moved until the whole 300s
    admission budget expired and the child was refused ``NOT_ADMITTED``.

    That is why these arrival orders are parametrized rather than assumed. The
    tests drive the real ``RuntimeToolControlMiddleware.awrap_tool_call`` — the
    seam that owns the gate/coordinator nesting — so nothing here can pass while
    the composition it is protecting has drifted.

    Wall clock: a passing run never waits, because ``asyncio.wait`` returns as
    soon as the last child settles. The bound exists so a *reintroduced* deadlock
    fails this test in a second instead of parking CI for five minutes per case.
    """

    #: Long enough that no admission path plausibly needs it, short enough that a
    #: regression is a fast red rather than another hour-long job.
    _SETTLE_SECONDS = 5.0

    @staticmethod
    def _tool_call_request(value: int) -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={
                "name": _TOOL,
                "args": {"value": value},
                "id": f"call-{value}",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=cast(Any, object()),
        )

    @pytest.mark.parametrize(
        "arrival_order",
        [[0, 1, 2], [1, 0, 2], [2, 1, 0], [1, 2, 0]],
        ids=["plan-order", "middle-first", "reversed", "last-first"],
    )
    async def test_every_child_of_a_serial_plan_runs_in_plan_order(
        self,
        arrival_order: list[int],
    ) -> None:
        """Arrival order chooses nothing: the plan does, and every child runs."""

        journal = _InMemoryBatchJournal()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(
            journal,
            # A write is the ordinary case that plans serially, and the case the
            # hang was found on.
            declarations=_declarations(side_effect=SideEffectKind.IRREVERSIBLE_WRITE),
        )
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        middleware = RuntimeToolControlMiddleware()

        entered: list[int] = []
        active = 0
        maximum_active = 0

        async def handler(request: ToolCallRequest) -> ToolMessage:
            nonlocal active, maximum_active
            value = int(request.tool_call["args"]["value"])
            entered.append(value)
            active += 1
            maximum_active = max(maximum_active, active)
            for _ in range(_QUIESCENCE_YIELDS):
                await asyncio.sleep(0)
                maximum_active = max(maximum_active, active)
            active -= 1
            return ToolMessage(content=str(value), tool_call_id=f"call-{value}")

        try:
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=[
                    dict(self._tool_call_request(value).tool_call)
                    for value in range(_FANOUT)
                ],
            )
            assert len(journal.plans[0].record.segments) == _FANOUT, (
                "this test only says anything while the plan is segmented"
            )

            tasks: list[asyncio.Task[object]] = []
            for value in arrival_order:
                tasks.append(
                    asyncio.create_task(
                        middleware.awrap_tool_call(
                            self._tool_call_request(value),
                            handler,
                        )
                    )
                )
                # One scheduler turn apart, so arrival order at the gate is this
                # test's choice rather than the scheduler's.
                await asyncio.sleep(0)

            _, pending = await asyncio.wait(tasks, timeout=self._SETTLE_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert not pending, (
            f"{len(pending)} planned child(ren) never settled after arriving in "
            f"{arrival_order} — a child is parked behind a lock its blocker "
            "needs, which is the 300s admission hang"
        )
        assert entered == [0, 1, 2], (
            "a serial plan must run its children in plan order whatever order "
            f"they arrived in, but arriving in {arrival_order} produced {entered}"
        )
        assert maximum_active == 1, (
            f"a serial plan overlapped {maximum_active} children"
        )


class TestAResourceSubjectIsSerialAtTheGraphSeam:
    """Criterion: resource conflicts never overlap improperly.

    The planner distinguishes disjoint resource fingerprints from conflicting
    ones, but the **wired** seam never produces a non-empty fingerprint: it
    cannot render a connector-specific resource key, so it answers *unknown* for
    any capability that declares a resource-key template, and unknown is a
    serial barrier.

    That means production satisfies this criterion by a strictly stronger rule
    than the planner's — a capability with a resource subject does not overlap
    *at all*, rather than overlapping only across distinct subjects. It also
    means the planner's ``RESOURCE_CONFLICT`` branch is unreachable from the
    graph today. Both facts are asserted here so the substitution is a recorded
    property rather than an accident.
    """

    @staticmethod
    def _with_resource_template() -> dict[
        str, tuple[CapabilityConcurrencyDeclaration, ...]
    ]:
        return {
            _TOOL: (
                CapabilityConcurrencyDeclaration(
                    capability_ref=graph_capability_ref(_TOOL),
                    source=PolicySource.PRODUCT_CATALOG,
                    mode=ConcurrencyMode.PARALLEL_SAFE,
                    side_effect=SideEffectKind.READ,
                    # Declared so this fixture keeps testing the *resource*
                    # barrier. Without it the approval floor would narrow the
                    # mode first and the assertions below would be passing for
                    # the wrong reason.
                    approval_requirement=ApprovalRequirement.NEVER,
                    max_parallelism=_FANOUT,
                    resource_key_template=ResourceKeyTemplate.from_template(
                        "{connector}/{object}"
                    ),
                ),
            )
        }

    async def test_a_capability_with_a_resource_subject_never_overlaps(self) -> None:
        clock = _WaveClock()
        _, journal, _ = await _run_configured(
            clock.spend_one_unit_of_latency,
            declarations=self._with_resource_template(),
        )

        assert len(clock.waves) == _FANOUT, (
            "a capability whose subject the seam cannot render overlapped"
        )
        record = journal.plans[0].record
        assert {segment.reason for segment in record.segments} == {
            BatchSegmentReason.UNKNOWN_RESOURCES
        }

    async def test_the_seam_never_emits_a_resource_fingerprint(self) -> None:
        """The mechanism: unknown, never a rendered key.

        Pinning this is what makes the stronger rule above durable. If the seam
        ever learns to render a real subject key, this test fails and whoever
        taught it must then prove the planner's per-subject conflict branch end
        to end — which nothing does today.
        """

        _, journal, _ = await _run_configured(
            _WaveClock().spend_one_unit_of_latency,
            declarations=self._with_resource_template(),
        )
        record = journal.plans[0].record

        assert record.segments, "the turn was never planned"
        assert {segment.reason for segment in record.segments} == {
            BatchSegmentReason.UNKNOWN_RESOURCES
        }
        assert not any(
            segment.reason is BatchSegmentReason.RESOURCE_CONFLICT
            for segment in record.segments
        )


class TestChildSuccessesSurviveSiblingFailureThroughTheGraph:
    """Criterion: a sibling's failure never erases a completed child's result.

    ``test_child_execution.py`` proves this against a coordinator built in a
    fixture. This proves the same property survives the wired path, where the
    failure also has to pass through the framework's own tool-error handling.
    """

    async def test_two_reads_still_complete_when_their_sibling_raises(self) -> None:
        """Both healthy children reach their connector and return a value.

        The graph itself surfaces the raising child's error, which is the
        framework's business. What F6 owes is that the failure neither cancels
        the siblings mid-flight nor discards work they already finished, and the
        bodies record that themselves rather than inferring it from a message.
        """

        entered: list[int] = []
        returned: dict[int, str] = {}

        async def body(value: int) -> str:
            entered.append(value)
            for _ in range(_QUIESCENCE_YIELDS):
                await asyncio.sleep(0)
            if value == 1:
                raise RuntimeError("connector refused")
            returned[value] = f"value-{value}"
            return returned[value]

        with pytest.raises(RuntimeError, match="connector refused"):
            await _run_configured(body)

        assert sorted(entered) == [0, 1, 2], "a sibling was skipped or re-run"
        assert returned == {0: "value-0", 2: "value-2"}, (
            "a sibling's completed work was lost when its neighbour failed"
        )

    async def test_the_failing_child_never_produces_a_result(self) -> None:
        """No value is invented for the child that raised."""

        returned: dict[int, str] = {}

        async def body(value: int) -> str:
            if value == 1:
                raise RuntimeError("connector refused")
            returned[value] = f"value-{value}"
            return returned[value]

        with pytest.raises(RuntimeError, match="connector refused"):
            await _run_configured(body)

        assert 1 not in returned

    async def test_a_sibling_failure_still_leaves_one_durable_plan(self) -> None:
        """The batch journal is not rewritten or erased by a child failure."""

        journal = _InMemoryBatchJournal()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(journal, declarations=_declarations())
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)

        async def body(value: int) -> str:
            if value == 1:
                raise RuntimeError("connector refused")
            return f"value-{value}"

        try:
            with pytest.raises(RuntimeError, match="connector refused"):
                await _run_graph_with(body)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert len(journal.plans) == 1
        record = journal.plans[0].record
        assert len(record.segments) == 1
        assert record.segments[0].mode is BatchSegmentMode.PARALLEL


class TestCancellationThroughTheWiredPathInventsNothing:
    """Criterion: cancel never invents rollback or success.

    This covers the case where cancellation arrives as a *task* cancellation
    rather than as the queued cancel command — worker shutdown, a timeout, a
    host tearing the loop down — and no coordinator route runs at all. What
    production does then is propagate ``CancelledError`` through the graph, and
    what this asserts is that doing so still invents nothing: a cancelled turn
    produces no tool result for work that did not finish, so nothing downstream
    can read a success that never happened.

    The queued-command path, where W4's wiring reaches
    ``BatchExecutionCoordinator.cancel`` and *records* the indeterminate work,
    is proven in ``tests/unit/runtime_worker/test_batch_cancel_restart_wiring.py``
    through :class:`RuntimeCancelHandler`. The two are complementary: this one
    says an unrecorded cancellation still lies about nothing, that one says the
    recorded cancellation happens where a run is actually cancelled.
    """

    async def test_a_cancelled_turn_yields_no_result_for_unfinished_work(
        self,
    ) -> None:
        started = asyncio.Event()
        completed: list[int] = []

        async def body(value: int) -> str:
            started.set()
            await asyncio.Event().wait()  # never completes
            completed.append(value)  # pragma: no cover - unreachable
            return str(value)

        journal = _InMemoryBatchJournal()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(journal, declarations=_declarations())
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            task = asyncio.create_task(_run_graph_with(body))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert completed == [], "a cancelled child reported completing"
        assert task.cancelled()
        # The plan is still durable — cancellation erases no evidence — and no
        # child transition claims a success.
        assert len(journal.plans) == 1
        assert not any(
            "succeeded" in str(write.record.model_dump(mode="json")).lower()
            for write in journal.transitions
        )


class TestRestartAndCancelRecoveryAreOnTheWiredPath:
    """The gap this gate found, now closed — and pinned open in that direction.

    These two tests were written as *negative* controls. F6.6 built
    ``BatchExecutionCoordinator.cancel`` and ``BatchRestartPlanner`` and
    mutation-checked them, and neither was reachable from any composition root,
    so the Step 10 work items "on cancel … mark uncertain work ``in_flight``/
    ``indeterminate``" and "on restart, resume only never-started safe reads"
    had no production route. Each test asserted that no source file outside
    ``capabilities/concurrency/`` mentioned the machinery, and each was designed
    to fail the moment somebody wired it.

    W4 wired it, so their premise is gone and they are **inverted rather than
    deleted**: the same scan, the same file set, the opposite expectation. A
    lane that later unwires either route fails here exactly as loudly as this
    lane failed while it was still unwired, which is the property the original
    controls were protecting and the reason they are still worth their keep.

    The behavioural proofs live in
    ``tests/unit/runtime_worker/test_batch_cancel_restart_wiring.py``, which
    enters through :class:`RuntimeCancelHandler` and ``activate_batch_admission``
    rather than through the package. These stay reachability checks: they answer
    "is there a route", not "does the route do the right thing".
    """

    @staticmethod
    def _source_outside_the_package() -> dict[str, str]:
        from pathlib import Path

        import agent_runtime
        import runtime_worker

        roots = (
            Path(agent_runtime.__file__).parent,
            Path(runtime_worker.__file__).parent,
        )
        excluded = Path(agent_runtime.__file__).parent / "capabilities" / "concurrency"
        sources: dict[str, str] = {}
        for root in roots:
            for path in root.rglob("*.py"):
                if excluded in path.parents:
                    continue
                sources[str(path)] = path.read_text(encoding="utf-8")
        return sources

    def test_the_scan_sees_the_parts_of_f6_that_are_wired(self) -> None:
        """Positive control, so the two negatives below cannot pass vacuously.

        The coordinator *is* reachable from a composition root — W3 wired it.
        Finding it proves this scan reads real source, which is what makes
        "cancel and restart are not there" a finding rather than an empty glob.
        """

        sources = self._source_outside_the_package()

        assert len(sources) > 100, f"the scan read only {len(sources)} files"
        wired = [
            path
            for path, text in sources.items()
            if "BatchExecutionCoordinator" in text
        ]
        assert wired, "the coordinator is not referenced outside the package at all"

    def test_a_composition_root_reaches_the_restart_planner(self) -> None:
        """Inverted from ``test_no_composition_root_references_the_restart_planner``.

        The worker's composer builds the recovery service and both run handlers
        adopt its verdict, so the planner is reachable from a claimed run.
        """

        reached = [
            path
            for path, text in self._source_outside_the_package().items()
            if "batch_run_recovery" in text or "BatchRunRecovery" in text
        ]

        assert reached, (
            "no composition root reaches F6 restart recovery any more — the "
            "Step 10 restart criterion has lost its production route, and the "
            "wired-path proofs in test_batch_cancel_restart_wiring.py are now "
            "testing something production does not do"
        )

    def test_a_composition_root_reaches_the_batch_cancellation_route(self) -> None:
        """Inverted from ``test_no_composition_root_calls_the_batch_cancellation_route``.

        Scanned by *route* rather than by contract name, because the wiring
        deliberately does not name ``BatchCancellationReport`` outside the
        package: the cancel handler calls ``acancel`` and lets the report stay
        an F6-internal contract. Asserting on the type name would therefore have
        gone on passing while the route existed, which is the failure mode an
        inverted control has to avoid.
        """

        sources = self._source_outside_the_package()
        reached = [path for path, text in sources.items() if ".acancel(" in text]

        assert reached, (
            "no composition root calls the F6 batch cancellation route any "
            "more — a cancelled run no longer records indeterminate work, and "
            "Step 10 work item 8 has lost its production route"
        )
        assert any("_cancel_live_batches" in text for text in sources.values()), (
            "the cancel *handler* no longer reaches a live coordinator"
        )


class TestRunWideWidthWhenTwoExecutionScopesAreLive:
    """W3's open question, answered.

    ``graph_admission`` derives a batch id from the execution scope, so a
    supervisor turn and a local-subagent turn are different batches and
    therefore different cohorts at the Step-2 gate. Per-cohort width is the
    plan's width; run-wide width across two live cohorts is their **sum**.

    That does not breach any Step 10 criterion — a cohort only ever contains
    declared independent reads, and every child still narrows again against the
    one run-scoped :class:`RunPermitManager` before it reaches a connector — but
    it is a real property of the composition and it was not asserted anywhere.
    """

    async def test_two_live_cohorts_widen_the_gate_to_their_sum(self) -> None:
        from agent_runtime.control_plane.context import RunSerialAdmission
        from agent_runtime.control_plane.parallel_admission import (
            ParallelAdmissionGrant,
            ToolAdmissionRequest,
        )

        width = 2
        cohorts = ("supervisor", "subagent:researcher")

        class _TwoCohortPort:
            """Stands in for two batches of one run, both live at once."""

            def grant_for(
                self,
                request: ToolAdmissionRequest,
            ) -> ParallelAdmissionGrant | None:
                return ParallelAdmissionGrant(
                    tool_call_id=request.tool_call_id,
                    cohort_id=request.execution_scope,
                    max_parallelism=width,
                )

        admission = RunSerialAdmission(parallel_admission=_TwoCohortPort())
        active = 0
        maximum = 0

        async def call(scope: str, index: int) -> None:
            nonlocal active, maximum
            request = ToolAdmissionRequest(
                tool_call_id=f"{scope}-{index}",
                execution_scope=scope,
                tool_name=_TOOL,
            )
            async with admission.async_permit(request):
                active += 1
                maximum = max(maximum, active)
                for _ in range(_QUIESCENCE_YIELDS):
                    await asyncio.sleep(0)
                    maximum = max(maximum, active)
                active -= 1

        await asyncio.gather(
            *(call(scope, index) for scope in cohorts for index in range(width))
        )

        assert maximum == width * len(cohorts), (
            "run-wide width across two live execution scopes was not the sum "
            f"of their cohort widths: {maximum}"
        )
