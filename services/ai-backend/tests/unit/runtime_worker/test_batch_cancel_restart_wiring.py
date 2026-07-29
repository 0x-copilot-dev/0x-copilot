"""W4 — F6.6's cancellation and restart, proven where production runs them.

F6.6 established these properties against the coordinator and the planner
directly, and mutation-checked them there.  What no test could establish is that
production ever *reaches* either one, because until this lane nothing outside
``capabilities/concurrency/`` did.  The F6.8 gate pinned that as a finding.

So every proof here enters through a composition root rather than through the
package:

- cancellation is driven by :class:`RuntimeCancelHandler`, the handler the queued
  ``run_cancel_requested`` command actually dispatches to — not by calling
  ``BatchExecutionCoordinator.cancel``;
- restart is driven by :func:`activate_batch_admission`, the function both the
  run handler and the approval handler call, over the real
  :class:`BatchRunRecovery` and the real ``BatchRestartPlanner``;
- the effect of both is observed by asking whether a tool body actually ran,
  which is the only question a replayed write cares about.

The one double is the journal's storage adapter.  ``EventJournalBatchPlanStore``
has its own tests over the real event stream; substituting a recording store here
keeps these tests about the wiring rather than about serialisation, and the
records it hands back are the same contracts the real adapter returns.

Nothing here sleeps on the wall clock.  Cancellation is observed by driving a
child to a barrier with :class:`asyncio.Event` and asserting the state that
results; the drain never runs to its bound because every child under test is a
declared read, which the coordinator may cancel in place.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from functools import partial
from typing import Any

from agent_runtime.capabilities.concurrency import (
    BatchChildDisposition,
    BatchChildPhase,
    BatchChildTransitionRecorder,
    BatchChildTransitionWrite,
    BatchExecutionCoordinator,
    BatchJournalWrite,
    BatchPlanRecorder,
    BatchRecoveryView,
    BatchRunBinding,
    BatchRunRecovery,
    CapabilityConcurrencyDeclaration,
    ConcurrencyMode,
    ConcurrencyAllowance,
    ConcurrencyKillSwitchAllowanceSupplier,
    ConcurrencyKillSwitchGate,
    ConcurrencyScope,
    DurableBatchPlan,
    DurableChildTransition,
    PermitAcquisitionRequest,
    PermitCapacityPolicy,
    RunPermitManager,
    SequencedBatchJournalRecord,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.batch_coordinator import (
    BatchAdmissionOutcome,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    DeclaredConcurrencyPolicySource,
    RunBatchAdmission,
)
from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.control_plane.contracts import (
    BudgetEnvelope,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.execution.contracts import RuntimeBatchAdmissionContext
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RuntimeCancelCommand
from runtime_worker.batch_concurrency_composition import (
    BatchConcurrencyComposer,
    LiveBatchAdmissionRegistry,
    activate_batch_admission,
)
from runtime_worker.handlers.cancel import RuntimeCancelHandler

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _FANOUT,
    _ORG,
    _PROFILE,
    _SUBJECT,
    _TOOL,
    _TRACE,
    _InMemoryBatchJournal,
    _declarations,
)
from tests.unit.agent_runtime.capabilities.concurrency.test_step10_gate import (
    _run_graph_with,
)
from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers, _TestSettings

_SNAPSHOT = "snapshot-w4"
#: The execution scope one supervisor turn plans under. Any stable value works;
#: what matters is that the first attempt and the restart use the *same* one, so
#: the operation ids a replayed turn derives match the ones the journal holds.
_SCOPE = "supervisor"


class _RecoverableJournal(_InMemoryBatchJournal):
    """A recording journal that can also be *read back*, which recovery needs.

    ``_InMemoryBatchJournal`` refuses ``load_recovery_view`` because nothing in
    W3 recovered anything.  W4 does, and the view it needs is a pure function of
    the appends already recorded — so this adds the read side without inventing a
    second notion of what is durable.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[SequencedBatchJournalRecord] = []
        self.recovery_calls: list[tuple[str, str, str]] = []

    async def put_plan(self, write: BatchJournalWrite) -> DurableBatchPlan:
        durable = await super().put_plan(write)
        self._remember(durable.sequence_no, write.record)
        return durable

    async def append_child_transition(
        self,
        write: BatchChildTransitionWrite,
    ) -> DurableChildTransition:
        durable = await super().append_child_transition(write)
        self._remember(durable.sequence_no, write.record)
        return durable

    def _remember(self, sequence_no: int, record: Any) -> None:
        self.records.append(
            SequencedBatchJournalRecord(sequence_no=sequence_no, record=record)
        )

    async def load_recovery_view(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> BatchRecoveryView:
        self.recovery_calls.append((org_id, run_id, subject_fingerprint))
        return BatchRecoveryView(
            run_id=run_id,
            snapshot_id=_SNAPSHOT,
            records=tuple(
                item for item in self.records if item.sequence_no > after_sequence
            ),
        )


class _RecoveringComposer(BatchConcurrencyComposer):
    """The real composer, reading recovery from the test's journal.

    Only :meth:`_recovery`'s *storage* is substituted.  ``aplan_restart`` — its
    error handling, its "no plans means no decision" rule — and the planner it
    drives are the production ones, which is what makes the restart proofs below
    proofs about the wiring rather than about a fixture.
    """

    def __init__(self, journal: _RecoverableJournal) -> None:
        super().__init__(events=object(), snapshots=object())  # type: ignore[arg-type]
        self._journal = journal

    def _recovery(self) -> Any:
        return BatchRunRecovery(store=self._journal)


def _snapshot(run_id: str) -> RunControlSnapshot:
    """Return a frozen control snapshot bound to a real run id."""

    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-w4",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=16,
    )
    return RunControlSnapshot.create(
        run_id=run_id,
        conversation_id="conversation-w4",
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
        snapshot_id=_SNAPSHOT,
    )


def _admission(
    journal: _RecoverableJournal,
    *,
    run_id: str,
    declarations: Mapping[str, tuple[CapabilityConcurrencyDeclaration, ...]]
    | None = None,
) -> RunBatchAdmission:
    """Compose what ``BatchConcurrencyComposer._compose`` composes.

    Including ``child_journal``, which W3's helper omits and which is the whole
    reason cancellation has anything durable to write: without it the coordinator
    still cancels correctly and records nothing, which is precisely the state
    ``BatchRestartPlanner`` reads as "unknown".
    """

    snapshot = _snapshot(run_id)
    gate = ConcurrencyKillSwitchGate(
        snapshot_allowance=ConcurrencyAllowance(
            mode=FeatureMode.ENFORCE,
            max_parallelism=_FANOUT,
        )
    )
    coordinator = BatchExecutionCoordinator(
        permits=RunPermitManager(
            policy=PermitCapacityPolicy.from_limits(
                {kind: _FANOUT for kind in ConcurrencyScope.permit_pool_kinds()}
            )
        ),
        permit_scopes=lambda identity: (
            PermitAcquisitionRequest.for_operation(
                profile_id=_PROFILE,
                subject_fingerprint=_SUBJECT,
                capability_name=identity.capability_ref or identity.operation_id,
            ).scopes
        ),
        live_allowance=ConcurrencyKillSwitchAllowanceSupplier(gate),
        child_journal=BatchChildTransitionRecorder(
            journal=journal,  # type: ignore[arg-type]
            binding=BatchRunBinding.of(
                org_id=_ORG,
                trace_id=_TRACE,
                snapshot=snapshot,
            ),
        ),
    )
    return RunBatchAdmission(
        recorder=BatchPlanRecorder(journal=journal, gate=gate),  # type: ignore[arg-type]
        coordinator=coordinator,
        policies=DeclaredConcurrencyPolicySource(
            _declarations() if declarations is None else declarations
        ),
        snapshot=snapshot,
        org_id=_ORG,
        trace_id=_TRACE,
    )


def _binding(run_id: str) -> Any:
    from agent_runtime.control_plane.context import RunControlBinding

    return RunControlBinding(
        snapshot=_snapshot(run_id),
        effective_modes=FeatureModeSet(f6=FeatureMode.ENFORCE),
        decisions=(),
    )


def _settled_dispositions(
    journal: _RecoverableJournal,
) -> list[BatchChildDisposition]:
    """Return every durable child outcome the journal actually holds."""

    return [
        write.record.disposition
        for write in journal.transitions
        if write.record.phase is BatchChildPhase.SETTLED
        and write.record.disposition is not None
    ]


async def _cancel_through_the_handler(
    *,
    store: InMemoryRuntimeApiStore,
    registry: LiveBatchAdmissionRegistry | None,
    run_id: str,
) -> None:
    """Cancel one run the way the queue cancels it, and no other way."""

    await RuntimeCancelHandler(
        persistence=store,
        event_store=store,
        live_batch_admissions=registry,
    ).handle(
        RuntimeCancelCommand(
            run_id=run_id,
            org_id="org_123",
            requested_by_user_id="user_123",
            reason="user_requested",
        )
    )


class TestCancellingARunRecordsIndeterminateThroughTheWiredPath:
    """Step 10 work item 8, established on the path a run is cancelled by.

    The F6.8 gate could only show that a cancelled turn *invents* nothing. That
    was true and weaker than the criterion: a run that records nothing has also
    not told anybody its work is in doubt. These tests are about the recording.
    """

    async def test_the_cancel_handler_records_indeterminate_for_work_in_flight(
        self,
    ) -> None:
        """The headline: cancelling a run durably marks its in-flight children.

        The cancel command is handled by the same handler the worker dispatches
        to, holding only what that handler holds — a run id and a registry. It
        reaches a coordinator it never saw constructed and leaves an
        ``indeterminate`` fact behind in the one journal F6 writes.
        """

        store = InMemoryRuntimeApiStore()
        settings = _TestSettings.create()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.RUNNING)

        journal = _RecoverableJournal()
        admission = _admission(journal, run_id=run_id)
        registry = LiveBatchAdmissionRegistry()
        registry.register(run_id=run_id, admission=admission)

        in_flight = asyncio.Event()

        async def body(value: int) -> str:
            in_flight.set()
            await asyncio.Event().wait()  # never completes on its own
            return str(value)  # pragma: no cover - unreachable

        token = RunControlContext.bind_for_run(_binding(run_id))
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            turn = asyncio.create_task(_run_graph_with(body))
            await in_flight.wait()
            await _cancel_through_the_handler(
                store=store,
                registry=registry,
                run_id=run_id,
            )
        finally:
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        dispositions = _settled_dispositions(journal)
        assert BatchChildDisposition.INDETERMINATE in dispositions, (
            "cancelling a run with an in-flight batch recorded no indeterminate "
            f"child; the journal settled {dispositions}"
        )

    async def test_the_recorded_outcome_never_claims_success_or_rollback(
        self,
    ) -> None:
        """Cancellation invents nothing — asserted over what was *written*.

        The gate asserted this of a report nothing built. Here it is asserted of
        the durable rows a real cancellation left, which is where a false claim
        would actually do damage.
        """

        store = InMemoryRuntimeApiStore()
        settings = _TestSettings.create()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.RUNNING)

        journal = _RecoverableJournal()
        admission = _admission(journal, run_id=run_id)
        registry = LiveBatchAdmissionRegistry()
        registry.register(run_id=run_id, admission=admission)

        in_flight = asyncio.Event()

        async def body(value: int) -> str:
            in_flight.set()
            await asyncio.Event().wait()
            return str(value)  # pragma: no cover - unreachable

        token = RunControlContext.bind_for_run(_binding(run_id))
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            turn = asyncio.create_task(_run_graph_with(body))
            await in_flight.wait()
            await _cancel_through_the_handler(
                store=store,
                registry=registry,
                run_id=run_id,
            )
        finally:
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert BatchChildDisposition.SUCCEEDED not in _settled_dispositions(journal)

    async def test_the_run_still_becomes_terminal(self) -> None:
        """The cancellation the user asked for is not held hostage by F6.

        Whatever the coordinator does or fails to do, the handler's own job —
        making the run terminal — still happens. Anything else would let a
        concurrency subsystem turn "stop this" into "this cannot be stopped".
        """

        store = InMemoryRuntimeApiStore()
        settings = _TestSettings.create()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.RUNNING)

        journal = _RecoverableJournal()
        admission = _admission(journal, run_id=run_id)
        registry = LiveBatchAdmissionRegistry()
        registry.register(run_id=run_id, admission=admission)

        await _cancel_through_the_handler(
            store=store,
            registry=registry,
            run_id=run_id,
        )

        assert store.runs[run_id].status is AgentRunStatus.CANCELLED

    async def test_a_run_this_process_is_not_executing_is_simply_missed(
        self,
    ) -> None:
        """A registry miss degrades to the pre-W4 behaviour, not to a failure.

        This is the multi-worker case stated as a test rather than as a comment:
        the cancel claim can land on a process that never held the coordinator,
        and when it does the handler must still cancel the run.
        """

        store = InMemoryRuntimeApiStore()
        settings = _TestSettings.create()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.RUNNING)

        registry = LiveBatchAdmissionRegistry()
        assert registry.admission_for(run_id) is None

        await _cancel_through_the_handler(
            store=store,
            registry=registry,
            run_id=run_id,
        )

        assert store.runs[run_id].status is AgentRunStatus.CANCELLED


class TestFeatureOffParityForCancellation:
    """With F6 unconfigured, cancellation is byte-for-byte what it always was."""

    @staticmethod
    async def _cancel_with(registry: LiveBatchAdmissionRegistry | None) -> str:
        store = InMemoryRuntimeApiStore()
        settings = _TestSettings.create()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.RUNNING)
        await _cancel_through_the_handler(
            store=store,
            registry=registry,
            run_id=run_id,
        )
        return store.runs[run_id].status.value

    async def test_an_unconfigured_worker_passes_no_registry_at_all(self) -> None:
        """The composition root builds no registry unless it built a composer."""

        from runtime_worker.batch_concurrency_composition import (
            build_batch_concurrency_composer,
        )

        assert (
            build_batch_concurrency_composer(
                events=object(),  # type: ignore[arg-type]
                snapshots=object(),  # type: ignore[arg-type]
                environ={},
            )
            is None
        )

    async def test_cancellation_is_identical_with_and_without_the_registry(
        self,
    ) -> None:
        """The feature-off proof: same terminal status, same handler, no F6.

        ``None`` is what an unconfigured deployment passes, and an empty registry
        is what a configured one passes for a run it is not executing. Neither
        may differ from the other, or from what the handler did before W4.
        """

        assert await self._cancel_with(None) == AgentRunStatus.CANCELLED.value
        assert (
            await self._cancel_with(LiveBatchAdmissionRegistry())
            == AgentRunStatus.CANCELLED.value
        )


class TestRestartResumesOnlyNeverStartedSafeReads:
    """Step 10 work item 9, established through ``activate_batch_admission``.

    Every test drives the function both composition roots call, over the real
    :class:`BatchRunRecovery` and the real ``BatchRestartPlanner``, and then
    observes the verdict the only way that matters: by replaying the turn
    through the real graph and asking which tool bodies executed.

    The first attempt runs its capability **serially** so that some children
    provably never start.  That is not a convenience — it is the only shape in
    which the journal can hold the two facts this criterion is about at once: a
    child with a durable dispatch intent, and siblings with durable proof of
    absence.  Under a parallel declaration every child starts together and every
    child is indeterminate, which exercises the withhold and nothing else.

    Both halves drive ``aplan_model_batch`` and ``arun_tool_body`` — the exact
    pair ``RuntimeToolControlMiddleware`` calls — rather than ``graph.ainvoke``.
    That is a deliberate step *down* one layer, and only for these tests. A
    withheld child refuses by raising, the graph's tool node propagates the
    first exception and cancels its siblings, and whether a resumable sibling
    got to run first is then a scheduling race rather than a decision. Driving
    the seam with ``return_exceptions=True`` isolates the siblings the way
    ``BatchFailurePolicy.COLLECT_ALL`` already intends, so what these tests
    observe is the restart verdict and nothing else. The layer above is covered
    by the graph-driven withhold tests below, which do not race because in them
    every child is withheld.
    """

    @staticmethod
    def _catalog(side_effect: SideEffectKind) -> Any:
        """Declare one serial capability of the given effect class."""

        return _declarations(mode=ConcurrencyMode.SERIAL, side_effect=side_effect)

    @staticmethod
    def _tool_calls() -> list[dict[str, Any]]:
        """The three sibling calls one model turn emits, replayed identically."""

        return [
            {"name": _TOOL, "args": {"value": value}, "id": f"call-{value}"}
            for value in range(_FANOUT)
        ]

    @classmethod
    async def _drive(
        cls,
        admission: RunBatchAdmission,
        body: Any,
    ) -> None:
        """Plan one turn and run every child through the production seam."""

        await admission.aplan_model_batch(
            execution_scope=_SCOPE,
            model_turn=1,
            tool_calls=cls._tool_calls(),
        )
        await asyncio.gather(
            *(
                admission.arun_tool_body(
                    tool_call_id=f"call-{value}",
                    body=partial(body, value),
                )
                for value in range(_FANOUT)
            ),
            return_exceptions=True,
        )

    @classmethod
    async def _first_attempt(
        cls,
        *,
        run_id: str,
        side_effect: SideEffectKind,
        journal: _RecoverableJournal,
    ) -> set[int]:
        """Run one turn that plans three children, starts one, and dies.

        Returns the children that actually started.  Which children those are is
        read off the run rather than asserted into it, so the tests below argue
        from what the journal recorded rather than from what this helper hoped.
        """

        started: set[int] = set()
        admission = _admission(
            journal,
            run_id=run_id,
            declarations=cls._catalog(side_effect),
        )
        first = asyncio.Event()

        async def body(value: int) -> str:
            started.add(value)
            first.set()
            await asyncio.Event().wait()  # the process dies here
            return str(value)  # pragma: no cover - unreachable

        token = RunControlContext.bind_for_run(_binding(run_id))
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            turn = asyncio.create_task(cls._drive(admission, body))
            await first.wait()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            # The run really is over, so close its admission the way production
            # closes it. A zero drain reports what is true now and never spends
            # wall-clock time; without it the children still parked on the
            # serial gate would sit on the admission timeout, which is 300
            # seconds and is not a thing a unit test may wait for.
            await admission.acancel(drain_seconds=0.0)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)
        return started

    @classmethod
    async def _restart(
        cls,
        *,
        run_id: str,
        side_effect: SideEffectKind,
        journal: _RecoverableJournal,
    ) -> tuple[set[int], RunBatchAdmission]:
        """Claim the run again and replay the identical turn.

        A withheld child refuses by raising ``BatchChildExecutionError`` — the
        contract ``arun_tool_body`` has always had for a child the coordinator
        did not admit, and it raises rather than returns because no connector
        ever saw the call.  What is measured is which bodies ran.
        """

        replayed: set[int] = set()

        async def body(value: int) -> str:
            replayed.add(value)
            return str(value)

        admission = _admission(
            journal,
            run_id=run_id,
            declarations=cls._catalog(side_effect),
        )
        await activate_batch_admission(
            composer=_RecoveringComposer(journal),
            registry=None,
            admission=admission,
            org_id=_ORG,
            run_id=run_id,
            subject_fingerprint=_SUBJECT,
        )
        token = RunControlContext.bind_for_run(_binding(run_id))
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            await cls._drive(admission, body)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)
        return replayed, admission

    async def test_a_started_write_is_never_replayed(self) -> None:
        """The property F6.6 defends twice; the wiring adds no third way round.

        A write that got as far as a durable dispatch intent is withheld on
        restart even though the replayed turn asks for it by name, and the proof
        is that its body does not run a second time.
        """

        run_id = "run_w4_write"
        journal = _RecoverableJournal()
        started = await self._first_attempt(
            run_id=run_id,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            journal=journal,
        )
        assert started, "the first attempt started nothing, so nothing is at risk"

        replayed, _ = await self._restart(
            run_id=run_id,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            journal=journal,
        )

        assert not (replayed & started), (
            "a write that had already started was replayed on restart: "
            f"{sorted(replayed & started)}"
        )

    async def test_a_never_started_write_is_withheld_too(self) -> None:
        """Proof of absence is not a licence to write.

        F6.6's second rule, carried through the wiring: a write with durable
        proof it never started is still not resumed, because "very probably
        correct" is not the standard for repeating something that changes the
        world.
        """

        run_id = "run_w4_write"
        journal = _RecoverableJournal()
        started = await self._first_attempt(
            run_id=run_id,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            journal=journal,
        )
        never_started = set(range(_FANOUT)) - started
        assert never_started, "every child started, so nothing proves this rule"

        replayed, _ = await self._restart(
            run_id=run_id,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            journal=journal,
        )

        assert not replayed, (
            f"a never-started write was replayed on restart: {sorted(replayed)}"
        )

    async def test_a_never_started_safe_read_runs_again_after_restart(self) -> None:
        """The other half: withholding must not become withholding everything.

        A lane that resumed nothing would pass every safety test above and be
        useless.  Reads with durable proof they never started are resumed, and
        the same run's started child is not — one turn, both rules.
        """

        run_id = "run_w4_read"
        journal = _RecoverableJournal()
        started = await self._first_attempt(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )
        never_started = set(range(_FANOUT)) - started
        assert never_started, "every child started, so nothing proves a resume"

        replayed, admission = await self._restart(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )

        assert never_started <= replayed, (
            "a safe read with durable proof it never started was not resumed: "
            f"{sorted(never_started - replayed)}"
        )
        assert not (replayed & started), (
            "the same restart replayed a child whose outcome is unknown: "
            f"{sorted(replayed & started)}"
        )
        assert admission.restart_plan is not None

    async def test_a_started_read_whose_outcome_was_lost_is_not_replayed(
        self,
    ) -> None:
        """Indeterminate is withheld too, for a different reason than a write.

        The planner rules a started-but-unsettled child ``INDETERMINATE``
        whatever its effect class.  The wiring has to carry that verdict as a
        withhold rather than treat "it was only a read" as licence to repeat it.
        """

        run_id = "run_w4_read"
        journal = _RecoverableJournal()
        started = await self._first_attempt(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )

        replayed, admission = await self._restart(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )

        assert not (replayed & started), (
            "a child whose outcome the journal cannot establish was replayed: "
            f"{sorted(replayed & started)}"
        )
        assert admission.restart_plan is not None
        assert admission.restart_plan.indeterminate, (
            "the plan recorded no indeterminate child for a run that had one"
        )

    async def test_a_first_attempt_withholds_nothing(self) -> None:
        """Feature-on parity for the common case: a fresh run is unaffected.

        Every run's first attempt reaches ``activate_batch_admission`` too.  If
        recovery could withhold anything there, W4 would have broken every run
        in the deployment in order to protect the restarts.
        """

        run_id = "run_w4_fresh"
        journal = _RecoverableJournal()

        replayed, admission = await self._restart(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )

        assert admission.restart_plan is None
        assert replayed == set(range(_FANOUT))

    async def test_recovery_is_consulted_with_the_run_it_is_recovering(self) -> None:
        """The lookup uses verified run identity, not anything a caller invented."""

        run_id = "run_w4_lookup"
        journal = _RecoverableJournal()

        await self._restart(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )

        assert journal.recovery_calls == [(_ORG, run_id, _SUBJECT)]


class TestTheWithholdRefusesBeforeItJournals:
    """Why the refusal sits where it does, pinned so a later edit cannot move it.

    Refusing *before* the dispatch intent is what keeps a withheld child's
    evidence intact for the next restart.  Refusing after would rewrite "never
    started" into "started, outcome unknown" — permanently downgrading a
    resumable safe read, purely as a side effect of having declined to run it
    once.
    """

    @staticmethod
    def _dispatch_intents(journal: _RecoverableJournal) -> list[str]:
        return [
            write.record.operation_id
            for write in journal.transitions
            if write.record.phase is BatchChildPhase.DISPATCH_INTENT
        ]

    async def test_a_withheld_child_journals_no_new_dispatch_intent(self) -> None:
        """A second restart must see exactly the evidence the first one saw."""

        run_id = "run_w4_evidence"
        journal = _RecoverableJournal()
        restarts = TestRestartResumesOnlyNeverStartedSafeReads
        await restarts._first_attempt(
            run_id=run_id,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            journal=journal,
        )
        before = sorted(self._dispatch_intents(journal))
        assert before, "the first attempt journalled no dispatch intent"

        await restarts._restart(
            run_id=run_id,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            journal=journal,
        )

        assert sorted(self._dispatch_intents(journal)) == before, (
            "refusing withheld children journalled new dispatch intents, which "
            "degrades the very evidence the refusal was derived from"
        )

    async def test_a_resumed_read_is_not_resumed_a_second_time(self) -> None:
        """Repeated restarts converge on withholding; they never re-run work.

        The verdict is deliberately *not* constant across restarts, and asserting
        that it were would be asserting a bug. A read the first restart resumed
        has, by resuming, journalled its own dispatch intent — so to the second
        restart it is a child that started, which is exactly what it is. The
        property that must hold is the one-directional one: each restart resumes
        a subset of what the last one did, and never the same work twice.
        """

        run_id = "run_w4_stable"
        journal = _RecoverableJournal()
        restarts = TestRestartResumesOnlyNeverStartedSafeReads
        started = await restarts._first_attempt(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )
        never_started = set(range(_FANOUT)) - started
        assert never_started

        first_replay, _ = await restarts._restart(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )
        second_replay, _ = await restarts._restart(
            run_id=run_id,
            side_effect=SideEffectKind.READ,
            journal=journal,
        )

        assert never_started <= first_replay
        assert not (second_replay & first_replay), (
            "a restart re-ran work an earlier restart had already run: "
            f"{sorted(second_replay & first_replay)}"
        )
        assert second_replay <= first_replay, (
            "a later restart resumed work an earlier one withheld, so the "
            f"verdict widened instead of tightening: {sorted(second_replay)}"
        )

    def test_a_withheld_refusal_never_claims_nothing_happened(self) -> None:
        """The one refusal that may not be read as "no external effect".

        Every other refusal describes work no process ever began.  This one
        exists precisely because an earlier process may have begun it, so the
        property that separates them has to answer ``False`` here or the
        vocabulary would launder the finding it carries.
        """

        assert not BatchAdmissionOutcome.REFUSED_WITHHELD_ON_RESTART.admitted
        assert not (
            BatchAdmissionOutcome.REFUSED_WITHHELD_ON_RESTART.introduced_no_effect
        )
        assert BatchAdmissionOutcome.REFUSED_RUN_CANCELLED.introduced_no_effect
        assert not BatchAdmissionOutcome.ADMITTED.introduced_no_effect
