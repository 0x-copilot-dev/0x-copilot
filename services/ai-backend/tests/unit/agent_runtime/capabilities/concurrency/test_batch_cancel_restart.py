"""F6.6 cancellation and restart: what the system says when it does not know.

The assertions here are mostly about *refusals to claim things*. A cancelled
write must not be reported succeeded, a drained-past child must not be reported
rolled back, and a child that started before a crash must not be run again. Each
of those is a sentence the system could easily emit and must not, so each gets a
test that fails if it ever does.

Nothing sleeps on the wall clock. Children are held open by
:class:`_Gate`, which is an ``asyncio.Event`` rather than a delay, and the one
test that proves the drain has a bound proves it with a zero bound — a real
deadline, reached in zero elapsed time.

Every durability test runs against both canonical adapters. The file store is
the desktop's, and a restart story that only works in memory is not a restart
story.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.concurrency import (
    BatchAdmissionOutcome,
    BatchCancellationReason,
    BatchChildDisposition,
    BatchChildPhase,
    BatchChildStatus,
    BatchChildTransitionRecord,
    BatchChildTransitionRecorder,
    BatchChildTransitionWrite,
    BatchEvidence,
    BatchExecutionCoordinator,
    BatchExecutionStatus,
    BatchJournalConflict,
    BatchJournalCorruption,
    BatchOperation,
    BatchPlanRecorder,
    BatchPlanRequest,
    BatchRestartPlanner,
    BatchRunBinding,
    ChildCancellationState,
    ChildEffectCertainty,
    ChildRestartDisposition,
    ChildRestartEvidence,
    ConcurrencyAllowance,
    ConcurrencyKillSwitchGate,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyScope,
    IdempotencyKind,
    OrderingRequirement,
    PermitCapacityPolicy,
    PermitScope,
    PlannedOperation,
    PolicySource,
    ProviderSessionConstraint,
    RunPermitManager,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.batch_journal_store import (
    EventJournalBatchPlanStore,
)
from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlSnapshot,
    RunControlSnapshotWrite,
    RunPolicyRevisions,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)

_ORG = "org-f66"
_USER = "user-f66"
_PROFILE = "single_user_desktop"
_SUBJECT = "c" * 64
_CONNECTOR = "acme-crm"
_SECRET_PROMPT = "private prompt that must not enter the F6 journal"
_CREATED_AT = datetime(2026, 7, 29, 11, 0, tzinfo=timezone.utc)
_BATCH = "batch-f66"
_CAP = "cap_" + "4" * 32

_READ_DONE = "op-read-done"
_WRITE_LOST = "op-write-lost"
_READ_PENDING = "op-read-pending"
_WRITE_PENDING = "op-write-pending"


class _CounterClock:
    """A deterministic, strictly increasing timezone-aware clock."""

    def __init__(self, base: datetime = _CREATED_AT) -> None:
        self._base = base
        self.ticks = 0

    def __call__(self) -> datetime:
        self.ticks += 1
        return self._base + timedelta(microseconds=self.ticks)


class _Gate:
    """A child body held open by an event rather than by elapsed time."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.finished = False

    async def occupy(self, value: str = "value") -> str:
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True
        return value


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


def write_policy() -> ConcurrencyPolicy:
    """A declared irreversible write — never cancellable, never replayed."""

    return ConcurrencyPolicy(
        mode=ConcurrencyMode.SERIAL,
        side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )


def undeclared_policy() -> ConcurrencyPolicy:
    """The conservative floor a capability that declared nothing receives."""

    return ConcurrencyPolicy()


def planned(operation_id: str, policy: ConcurrencyPolicy) -> PlannedOperation:
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


def mixed_operations() -> tuple[PlannedOperation, ...]:
    """Four children, one per serial segment, covering every restart verdict."""

    return (
        planned(_READ_DONE, read_policy()),
        planned(_WRITE_LOST, write_policy()),
        planned(_READ_PENDING, read_policy()),
        planned(_WRITE_PENDING, write_policy()),
    )


def permit_scopes(identity) -> tuple[PermitScope, ...]:
    return (
        PermitScope.for_global(),
        PermitScope.for_capability(
            profile_id=_PROFILE,
            subject_fingerprint=_SUBJECT,
            capability_name=str(identity.capability_ref),
        ),
    )


def wide_permits() -> RunPermitManager:
    """Permits broad enough that the plan, not the pool, is the bound."""

    return RunPermitManager(
        policy=PermitCapacityPolicy.from_limits(
            {kind: 16 for kind in ConcurrencyScope.permit_pool_kinds()}
        )
    )


def settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def snapshot_for(run, conversation) -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-f66",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=16,
    )
    return RunControlSnapshot.create(
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
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
        snapshot_id="snapshot-f66",
        created_at=_CREATED_AT,
    )


async def new_run(store):
    runtime_settings = settings()
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=None,
        ),
        settings=runtime_settings,
        model_resolver=ModelConfigResolver(runtime_settings),
    )
    conversation_coordinator = ConversationCoordinator(
        persistence=store,
        settings=runtime_settings,
        run_coordinator=run_coordinator,
    )
    conversation = await conversation_coordinator.create_conversation(
        CreateConversationRequest(
            org_id=_ORG,
            user_id=_USER,
            assistant_id="assistant",
        )
    )
    run = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input=_SECRET_PROMPT,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


class _DurableRun:
    """One live run plus the ability to lose the process that was driving it.

    :meth:`restart` is the whole point of the class. For the file store it
    genuinely closes and reopens the store, which is the desktop's real crash
    path. For the in-memory store the durable substrate is the store object and
    the *process* is the reader, so a fresh journal over the same store is the
    faithful analogue — in both cases nothing but the journal crosses the gap.
    """

    def __init__(self, *, param: str, root, store, run, conversation, snapshot) -> None:
        self.param = param
        self.root = root
        self.store = store
        self.run = run
        self.conversation = conversation
        self.snapshot = snapshot

    def journal(self) -> EventJournalBatchPlanStore:
        """Return a journal bound to the currently open store."""

        return EventJournalBatchPlanStore(
            events=self.store,
            snapshots=EventJournalRunControlStore(self.store),
        )

    def binding(self) -> BatchRunBinding:
        return BatchRunBinding.of(
            org_id=_ORG,
            trace_id=self.run.trace_id,
            snapshot=self.snapshot,
        )

    def recorder(self) -> BatchChildTransitionRecorder:
        return BatchChildTransitionRecorder(
            journal=self.journal(),
            binding=self.binding(),
        )

    async def restart(self) -> EventJournalBatchPlanStore:
        """Lose the running process and return the journal a new one reads."""

        if self.param == "file":
            await self.store.close()
            self.store = FileRuntimeApiStore(self.root)
            await self.store.open()
        return self.journal()

    async def recovery_view(self):
        return await self.journal().load_recovery_view(
            org_id=_ORG,
            run_id=self.run.run_id,
            subject_fingerprint=_SUBJECT,
        )

    async def record_plan(self, *operations: PlannedOperation, batch_id: str = _BATCH):
        """Bind one durable plan the way ``aafter_model`` would."""

        recorder = BatchPlanRecorder(
            journal=self.journal(),
            gate=ConcurrencyKillSwitchGate(
                snapshot_allowance=ConcurrencyAllowance.enforcing(4)
            ),
        )
        return await recorder.record(
            BatchPlanRequest(
                org_id=_ORG,
                trace_id=self.run.trace_id,
                subject_fingerprint=_SUBJECT,
                run_id=self.run.run_id,
                batch_id=batch_id,
                turn_ordinal=1,
                operations=operations or mixed_operations(),
                connector_id=_CONNECTOR,
            ),
            snapshot=self.snapshot,
        )

    def coordinator(self, **overrides) -> BatchExecutionCoordinator:
        options: dict[str, object] = {
            "permits": wide_permits(),
            "permit_scopes": permit_scopes,
            "clock": _CounterClock(),
            "child_journal": self.recorder(),
        }
        options.update(overrides)
        return BatchExecutionCoordinator(**options)  # type: ignore[arg-type]

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.store.close()


@pytest.fixture(params=("in_memory", "file"))
async def durable_run(request: pytest.FixtureRequest, tmp_path):
    """A live run with a bound control snapshot on each canonical adapter."""

    root = tmp_path / "runtime"
    store = (
        InMemoryRuntimeApiStore()
        if request.param == "in_memory"
        else FileRuntimeApiStore(root)
    )
    await store.open()
    conversation, run = await new_run(store)
    controls = EventJournalRunControlStore(store)
    snapshot = await controls.get_or_create(
        RunControlSnapshotWrite(
            org_id=_ORG,
            trace_id=run.trace_id,
            snapshot=snapshot_for(run, conversation),
        )
    )
    holder = _DurableRun(
        param=request.param,
        root=root,
        store=store,
        run=run,
        conversation=conversation,
        snapshot=snapshot,
    )
    try:
        yield holder
    finally:
        await holder.close()


async def _start(coordinator, plan, operation_id, gate) -> asyncio.Task:
    """Start one child in its own task and wait until its body is inside."""

    task = asyncio.create_task(
        coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=operation_id,
            runner=lambda _admission: gate.occupy(),
        )
    )
    await gate.entered.wait()
    return task


async def _abandon(*tasks: asyncio.Task) -> None:
    """Tear down tasks a crash would simply have lost."""

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


class TestCancelStopsAdmission:
    """New work stops the instant cancellation is asked for, not eventually."""

    async def test_cancel_refuses_every_child_that_had_not_started(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan()
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        started: list[str] = []

        async def runner(admission):
            started.append(admission.identity.operation_id)
            return "value"

        first = await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=runner,
        )
        assert first.succeeded
        held = await _start(coordinator, plan, _WRITE_LOST, gate)

        report = await coordinator.cancel(drain_seconds=0)

        # Segments two and three were never admitted, so their bodies never ran.
        assert set(started) == {_READ_DONE}
        outcomes = {
            outcome.identity.operation_id: outcome
            for outcome in coordinator.report(plan.batch_id).outcomes
        }
        for operation_id in (_READ_PENDING, _WRITE_PENDING):
            assert outcomes[operation_id].status is BatchChildStatus.REFUSED
            assert outcomes[operation_id].admission is (
                BatchAdmissionOutcome.REFUSED_RUN_CANCELLED
            )
        states = {child.operation_id: child.state for child in report.children}
        assert states[_READ_PENDING] is ChildCancellationState.NOT_STARTED
        assert states[_WRITE_PENDING] is ChildCancellationState.NOT_STARTED
        await _abandon(held)

    async def test_a_child_offered_after_cancel_is_refused_without_running(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan()
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        await coordinator.cancel(drain_seconds=0)
        entered = False

        async def runner(_admission):
            nonlocal entered
            entered = True
            return "value"

        result = await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=runner,
        )

        assert entered is False
        assert coordinator.cancelled is True
        assert result.outcome.status is BatchChildStatus.REFUSED

    async def test_a_cancelled_batch_reports_cancelled_not_failed(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan()
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)

        await coordinator.cancel(drain_seconds=0)

        assert coordinator.report(plan.batch_id).status is (
            BatchExecutionStatus.CANCELLED
        )


class TestCancelInterruptsOnlySafeReads:
    """Interrupting a write abandons it; interrupting a read costs nothing."""

    async def test_a_cancellable_read_is_actually_cancelled(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _READ_DONE, gate)

        report = await coordinator.cancel()

        assert gate.cancelled is True
        assert gate.finished is False
        assert report.drained is True
        child = report.children[0]
        assert child.state is ChildCancellationState.CANCELLED_IN_PLACE
        assert child.effect_certainty is ChildEffectCertainty.NO_EXTERNAL_EFFECT
        assert child.cancel_requested is True
        await _abandon(task)

    async def test_a_write_is_never_interrupted_only_waited_for(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)

        report = await coordinator.cancel(drain_seconds=0)

        assert gate.cancelled is False
        child = report.children[0]
        assert child.cancel_requested is False
        assert report.cancelled_in_place == ()
        await _abandon(task)

    async def test_an_undeclared_effect_class_is_not_cancellable(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned("op-unknown", undeclared_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, "op-unknown", gate)

        report = await coordinator.cancel(drain_seconds=0)

        assert gate.cancelled is False
        assert report.children[0].effect_certainty is ChildEffectCertainty.UNKNOWN
        await _abandon(task)


class TestBoundedDrain:
    """The drain stops watching; it never stops the work or guesses at it."""

    async def test_the_drain_is_bounded_and_says_so(self, durable_run) -> None:
        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)

        report = await coordinator.cancel(drain_seconds=0)

        assert report.drained is False
        child = report.children[0]
        assert child.state is ChildCancellationState.IN_FLIGHT_INDETERMINATE
        assert child.indeterminate is True
        assert child.effect_certainty is ChildEffectCertainty.UNKNOWN
        assert report.unknown_effects == (child,)
        await _abandon(task)

    async def test_a_completed_drain_leaves_nothing_indeterminate(
        self,
        durable_run,
    ) -> None:
        """A write is never interrupted, so the drain is what actually ends it.

        Using a write here is not incidental: a read would have been cancelled
        in place before the drain began, so only an uncancellable child can
        demonstrate that waiting — rather than interrupting — is what settled
        it.
        """

        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)
        gate.release.set()

        report = await coordinator.cancel()

        assert report.drained is True
        assert report.in_flight == ()
        assert report.indeterminate == ()
        assert gate.finished is True
        assert report.children[0].state is ChildCancellationState.SETTLED_DURING_DRAIN
        assert (await task).succeeded is True

    async def test_a_child_already_unknowable_is_not_blamed_on_the_drain(
        self,
        durable_run,
    ) -> None:
        """A drain cannot resolve what was already indeterminate before it ran.

        The framework cancelled this child on its own, so by the time
        cancellation is requested its outcome is already unknown. Calling that
        "still running" would blame the drain for something it never had a
        chance to finish — and would make the report refuse to describe a
        perfectly ordinary run.
        """

        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)
        await _abandon(task)

        report = await coordinator.cancel()

        assert report.drained is True
        assert report.in_flight == ()
        child = report.children[0]
        assert child.state is ChildCancellationState.INDETERMINATE_BEFORE_CANCEL
        assert child.cancel_requested is False
        assert child.indeterminate is True
        assert report.unknown_effects == (child,)

    async def test_a_late_completion_never_overwrites_indeterminate(
        self,
        durable_run,
    ) -> None:
        """The single most tempting lie: the work finished, so call it done.

        It did finish — after the run stopped waiting and after the answer was
        already reported as unknown. Promoting it now would mean the same child
        was reported two different ways, and the later report is the one nobody
        was listening for.
        """

        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)
        await coordinator.cancel(drain_seconds=0)

        gate.release.set()
        result = await task

        assert gate.finished is True
        assert result.outcome.status is BatchChildStatus.INDETERMINATE
        assert result.succeeded is False
        assert coordinator.report(plan.batch_id).status is (
            BatchExecutionStatus.CANCELLED
        )

    async def test_cancellation_reports_no_rollback_and_no_success(
        self,
        durable_run,
    ) -> None:
        """The report has no field through which either claim could be made."""

        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)

        report = await coordinator.cancel(drain_seconds=0)

        serialized = report.model_dump(mode="json")
        assert "rolled_back" not in str(serialized)
        assert "succeeded" not in str(serialized)
        assert report.reason is BatchCancellationReason.RUN_CANCELLED
        await _abandon(task)


class TestDurableDispatchEvidence:
    """A child that ran always left a record; a child that could not, did not."""

    async def test_dispatch_intent_is_durable_before_the_body_runs(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        observed: list[str] = []

        async def runner(_admission):
            view = await durable_run.recovery_view()
            observed.extend(
                transition.phase.value for transition in view.transitions_for(_BATCH)
            )
            return "value"

        await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=runner,
        )

        assert observed == [BatchChildPhase.DISPATCH_INTENT.value]

    async def test_a_child_whose_intent_cannot_be_recorded_never_runs(
        self,
        durable_run,
    ) -> None:
        """Fail closed: an unrecordable dispatch is a refusal, not a silent run.

        Running anyway would create precisely the child a restart wrongly
        believes never started.
        """

        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator(
            child_journal=BatchChildTransitionRecorder(
                journal=_BrokenJournal(),
                binding=durable_run.binding(),
            )
        )
        coordinator.begin(plan)
        entered = False

        async def runner(_admission):
            nonlocal entered
            entered = True
            return "value"

        result = await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=runner,
        )

        assert entered is False
        assert result.outcome.status is BatchChildStatus.REFUSED
        assert result.outcome.admission is BatchAdmissionOutcome.REFUSED_UNDURABLE

    async def test_a_refused_child_leaves_no_transition_at_all(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan()
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)

        await coordinator.cancel(drain_seconds=0)

        view = await durable_run.recovery_view()
        assert view.transitions_for(_BATCH) == ()

    async def test_a_completed_child_records_both_phases(self, durable_run) -> None:
        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)

        await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=lambda _admission: _value(),
        )

        view = await durable_run.recovery_view()
        transitions = view.transitions_for(_BATCH)
        assert [transition.phase for transition in transitions] == [
            BatchChildPhase.DISPATCH_INTENT,
            BatchChildPhase.SETTLED,
        ]
        assert transitions[1].record.disposition is BatchChildDisposition.SUCCEEDED

    async def test_cancellation_records_the_indeterminate_verdict(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned(_WRITE_LOST, write_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        gate = _Gate()
        task = await _start(coordinator, plan, _WRITE_LOST, gate)

        await coordinator.cancel(drain_seconds=0)

        view = await durable_run.recovery_view()
        settled = [
            transition
            for transition in view.transitions_for(_BATCH)
            if transition.phase is BatchChildPhase.SETTLED
        ]
        assert len(settled) == 1
        assert settled[0].record.disposition is BatchChildDisposition.INDETERMINATE
        await _abandon(task)


class TestRestartDecision:
    """What a restarted run may do, decided from durable facts alone."""

    async def test_restart_classifies_every_child_from_the_journal(
        self,
        durable_run,
    ) -> None:
        """One crashed batch, four children, four different honest verdicts."""

        restart_plan, task = await _crash_mid_batch(durable_run)
        batch = restart_plan.batch_for(_BATCH)

        assert batch is not None
        assert batch.evidence is BatchEvidence.CHILD_RECORDS_PRESENT
        assert {child.operation_id: child.disposition for child in batch.children} == {
            _READ_DONE: ChildRestartDisposition.WITHHELD_ALREADY_SUCCEEDED,
            _WRITE_LOST: ChildRestartDisposition.INDETERMINATE,
            _READ_PENDING: ChildRestartDisposition.RESUME_SAFE_READ,
            _WRITE_PENDING: ChildRestartDisposition.WITHHELD_UNSAFE_TO_REPLAY,
        }
        await _abandon(task)

    async def test_restart_resumes_a_never_started_safe_read(
        self,
        durable_run,
    ) -> None:
        restart_plan, task = await _crash_mid_batch(durable_run)

        resumable = restart_plan.resumable

        assert [child.operation_id for child in resumable] == [_READ_PENDING]
        assert resumable[0].evidence is ChildRestartEvidence.NO_DISPATCH_INTENT
        assert resumable[0].side_effect is SideEffectKind.READ
        await _abandon(task)

    async def test_a_started_write_is_never_replayed_after_restart(
        self,
        durable_run,
    ) -> None:
        """The rule the whole lane exists to hold.

        ``op-write-lost`` recorded a dispatch intent and never recorded an
        outcome — the exact shape of "we asked a connector to change something
        and never heard back". It is reported indeterminate and it is not
        resumed. Not "usually not": there is no input to this planner that makes
        a started write resumable, and
        :class:`ChildRestartDecision` refuses to be constructed as one.
        """

        restart_plan, task = await _crash_mid_batch(durable_run)
        decision = restart_plan.decision_for(_WRITE_LOST)

        assert decision is not None
        assert decision.evidence is ChildRestartEvidence.DISPATCH_INTENT_ONLY
        assert decision.disposition is ChildRestartDisposition.INDETERMINATE
        assert decision.resumable is False
        assert decision not in restart_plan.resumable
        assert all(not child.resumable for child in restart_plan.indeterminate)
        await _abandon(task)

    async def test_a_child_whose_outcome_was_lost_is_indeterminate_not_complete(
        self,
        durable_run,
    ) -> None:
        """Started, unheard-from work is never reported done and never retried."""

        restart_plan, task = await _crash_mid_batch(durable_run)

        assert [child.operation_id for child in restart_plan.indeterminate] == [
            _WRITE_LOST
        ]
        decision = restart_plan.decision_for(_WRITE_LOST)
        assert decision is not None
        assert decision.disposition is not (
            ChildRestartDisposition.WITHHELD_ALREADY_SUCCEEDED
        )
        assert decision.disposition is not (
            ChildRestartDisposition.WITHHELD_ALREADY_FAILED
        )
        await _abandon(task)

    async def test_a_batch_with_no_child_records_resumes_nothing(
        self,
        durable_run,
    ) -> None:
        """Silence is only evidence when the writer is known to have spoken.

        A batch whose journal holds no child records is indistinguishable from a
        batch whose coordinator was never journaling at all, so nothing in it is
        resumed. That is the failure mode of a miswiring: lost throughput, never
        a duplicated effect.
        """

        plan = await durable_run.record_plan()
        del plan
        restarted = await durable_run.restart()
        view = await restarted.load_recovery_view(
            org_id=_ORG,
            run_id=durable_run.run.run_id,
            subject_fingerprint=_SUBJECT,
        )

        restart_plan = BatchRestartPlanner().plan(view)
        batch = restart_plan.batch_for(_BATCH)

        assert batch is not None
        assert batch.evidence is BatchEvidence.NO_CHILD_RECORDS
        assert restart_plan.resumable == ()
        assert all(
            child.evidence is ChildRestartEvidence.NO_BATCH_EVIDENCE
            for child in batch.children
        )
        assert len(restart_plan.indeterminate) == 4

    async def test_a_failed_child_is_not_retried_either(self, durable_run) -> None:
        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)

        await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=lambda _admission: _boom(),
        )

        restarted = await durable_run.restart()
        view = await restarted.load_recovery_view(
            org_id=_ORG,
            run_id=durable_run.run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        decision = BatchRestartPlanner().plan(view).decision_for(_READ_DONE)

        assert decision is not None
        assert decision.disposition is ChildRestartDisposition.WITHHELD_ALREADY_FAILED
        assert decision.resumable is False

    async def test_the_restart_plan_is_a_pure_function_of_the_journal(
        self,
        durable_run,
    ) -> None:
        restart_plan, task = await _crash_mid_batch(durable_run)
        view = await durable_run.recovery_view()

        assert BatchRestartPlanner().plan(view) == restart_plan
        await _abandon(task)


class TestRestartDecisionInvariants:
    """The safety rule is a type constraint, not only a code path."""

    def test_a_resumable_decision_cannot_be_built_from_a_started_child(self) -> None:
        from agent_runtime.capabilities.concurrency.batch_recovery import (
            ChildRestartDecision,
        )

        with pytest.raises(ValueError, match="never started"):
            ChildRestartDecision(
                batch_id=_BATCH,
                operation_id=_WRITE_LOST,
                evidence=ChildRestartEvidence.DISPATCH_INTENT_ONLY,
                disposition=ChildRestartDisposition.RESUME_SAFE_READ,
                side_effect=SideEffectKind.READ,
            )

    def test_a_resumable_decision_cannot_be_built_for_a_write(self) -> None:
        from agent_runtime.capabilities.concurrency.batch_recovery import (
            ChildRestartDecision,
        )

        with pytest.raises(ValueError, match="safe reads"):
            ChildRestartDecision(
                batch_id=_BATCH,
                operation_id=_WRITE_PENDING,
                evidence=ChildRestartEvidence.NO_DISPATCH_INTENT,
                disposition=ChildRestartDisposition.RESUME_SAFE_READ,
                side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            )

    def test_an_undeclared_effect_class_is_never_resumable(self) -> None:
        from agent_runtime.capabilities.concurrency.batch_recovery import (
            ChildRestartDecision,
        )

        with pytest.raises(ValueError, match="safe reads"):
            ChildRestartDecision(
                batch_id=_BATCH,
                operation_id="op-unknown",
                evidence=ChildRestartEvidence.NO_DISPATCH_INTENT,
                disposition=ChildRestartDisposition.RESUME_SAFE_READ,
                side_effect=SideEffectKind.UNKNOWN,
            )


class TestChildTransitionDurability:
    """Idempotency, conflict, and replay validation, on both adapters."""

    async def test_an_identical_retry_returns_the_first_durable_record(
        self,
        durable_run,
    ) -> None:
        await durable_run.record_plan()
        recorder = durable_run.recorder()

        first = await recorder.record_dispatch_intent(
            batch_id=_BATCH,
            operation_id=_READ_DONE,
        )
        second = await recorder.record_dispatch_intent(
            batch_id=_BATCH,
            operation_id=_READ_DONE,
        )

        assert first == second
        view = await durable_run.recovery_view()
        assert len(view.transitions_for(_BATCH)) == 1

    async def test_a_different_outcome_under_one_identity_is_a_conflict(
        self,
        durable_run,
    ) -> None:
        """A durable answer is never quietly rewritten by a later one."""

        await durable_run.record_plan()
        recorder = durable_run.recorder()
        await recorder.record_dispatch_intent(
            batch_id=_BATCH,
            operation_id=_READ_DONE,
        )
        await recorder.record_settled(
            batch_id=_BATCH,
            operation_id=_READ_DONE,
            disposition=BatchChildDisposition.INDETERMINATE,
        )

        with pytest.raises(BatchJournalConflict):
            await recorder.record_settled(
                batch_id=_BATCH,
                operation_id=_READ_DONE,
                disposition=BatchChildDisposition.SUCCEEDED,
            )

    async def test_a_transition_before_its_plan_is_corruption(
        self,
        durable_run,
    ) -> None:
        recorder = durable_run.recorder()

        with pytest.raises(BatchJournalCorruption, match="precedes its batch plan"):
            await recorder.record_dispatch_intent(
                batch_id=_BATCH,
                operation_id=_READ_DONE,
            )

    async def test_a_transition_for_an_unplanned_operation_is_corruption(
        self,
        durable_run,
    ) -> None:
        await durable_run.record_plan()
        recorder = durable_run.recorder()

        with pytest.raises(BatchJournalCorruption, match="unplanned operation"):
            await recorder.record_dispatch_intent(
                batch_id=_BATCH,
                operation_id="op-smuggled",
            )

    async def test_a_settled_child_without_an_intent_is_corruption(
        self,
        durable_run,
    ) -> None:
        await durable_run.record_plan()
        recorder = durable_run.recorder()

        with pytest.raises(BatchJournalCorruption, match="without a durable dispatch"):
            await recorder.record_settled(
                batch_id=_BATCH,
                operation_id=_READ_DONE,
                disposition=BatchChildDisposition.SUCCEEDED,
            )

    async def test_a_transition_bound_to_another_run_is_refused(
        self,
        durable_run,
    ) -> None:
        await durable_run.record_plan()
        journal = durable_run.journal()
        record = BatchChildTransitionRecord.create(
            record_id=BatchChildTransitionRecord.stable_record_id(
                batch_id=_BATCH,
                operation_id=_READ_DONE,
                phase=BatchChildPhase.DISPATCH_INTENT,
            ),
            run_id=durable_run.run.run_id,
            snapshot_id="snapshot-someone-else",
            batch_id=_BATCH,
            operation_id=_READ_DONE,
            phase=BatchChildPhase.DISPATCH_INTENT,
            disposition=None,
        )

        with pytest.raises(Exception) as caught:
            await journal.append_child_transition(
                BatchChildTransitionWrite(
                    org_id=_ORG,
                    trace_id=durable_run.run.trace_id,
                    subject_fingerprint=_SUBJECT,
                    record=record,
                )
            )

        assert "snapshot" in str(caught.value).lower()

    async def test_transitions_survive_a_restart_byte_identically(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=lambda _admission: _value(),
        )
        before = (await durable_run.recovery_view()).transitions

        restarted = await durable_run.restart()
        after = (
            await restarted.load_recovery_view(
                org_id=_ORG,
                run_id=durable_run.run.run_id,
                subject_fingerprint=_SUBJECT,
            )
        ).transitions

        assert after == before
        assert len(after) == 2


class TestChildTransitionRecordShape:
    """The record is an identity and a closed vocabulary, and nothing else."""

    def test_a_settled_record_requires_a_disposition(self) -> None:
        with pytest.raises(ValueError, match="requires a disposition"):
            _transition(phase=BatchChildPhase.SETTLED, disposition=None)

    def test_an_intent_record_refuses_a_disposition(self) -> None:
        with pytest.raises(ValueError, match="only a settled"):
            _transition(
                phase=BatchChildPhase.DISPATCH_INTENT,
                disposition=BatchChildDisposition.SUCCEEDED,
            )

    def test_the_record_id_is_derived_and_cannot_be_chosen(self) -> None:
        record = _transition(
            phase=BatchChildPhase.DISPATCH_INTENT,
            disposition=None,
        )
        forged = record.model_dump(mode="json")
        forged["record_id"] = "batch-child:whatever"

        with pytest.raises(ValueError):
            BatchChildTransitionRecord.model_validate(forged)

    def test_two_children_cannot_collide_on_one_identity(self) -> None:
        """Separator-safe identity: joined strings could merge two children."""

        left = BatchChildTransitionRecord.stable_record_id(
            batch_id="batch:a",
            operation_id="b",
            phase=BatchChildPhase.SETTLED,
        )
        right = BatchChildTransitionRecord.stable_record_id(
            batch_id="batch",
            operation_id="a:b",
            phase=BatchChildPhase.SETTLED,
        )

        assert left != right

    def test_an_unsealed_edit_is_caught_by_the_record_digest(self) -> None:
        record = _transition(phase=BatchChildPhase.DISPATCH_INTENT, disposition=None)
        forged = record.model_dump(mode="json")
        forged["operation_id"] = "op-somebody-else"

        with pytest.raises(ValueError):
            BatchChildTransitionRecord.model_validate(forged)

    async def test_persisted_transitions_are_internal_redacted_and_body_free(
        self,
        durable_run,
    ) -> None:
        plan = await durable_run.record_plan(planned(_READ_DONE, read_policy()))
        coordinator = durable_run.coordinator()
        coordinator.begin(plan)
        await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=_READ_DONE,
            runner=lambda _admission: _value("a secret connector result"),
        )

        events = await durable_run.store.list_events_after(
            org_id=_ORG,
            run_id=durable_run.run.run_id,
            after_sequence=0,
        )
        transitions = tuple(
            event
            for event in events
            if event.event_type is RuntimeApiEventType.OPERATION_BATCH_JOURNAL
            and event.payload["record"]["record_kind"] == "child_transition"
        )

        assert len(transitions) == 2
        assert all(
            event.visibility is RuntimeEventVisibility.INTERNAL for event in transitions
        )
        assert all(
            event.redaction_state is RuntimeEventRedactionState.REDACTED
            for event in transitions
        )
        serialized = "".join(event.model_dump_json() for event in transitions)
        for forbidden in (
            _SECRET_PROMPT,
            "a secret connector result",
            "private prompt",
            _CONNECTOR,
            "raw_arguments",
            "raw_result",
            "credential",
            "https://",
            "/Users/",
        ):
            assert forbidden not in serialized

    def test_every_persisted_field_is_an_identity_or_closed_value(self) -> None:
        record = _transition(
            phase=BatchChildPhase.SETTLED,
            disposition=BatchChildDisposition.INDETERMINATE,
        )

        for value in _leaf_strings(record.model_dump(mode="json")):
            assert value != "" and not any(
                token in value for token in (" ", "\t", "\n", "://", "\\")
            ), value


async def _crash_mid_batch(durable_run):
    """Run one batch to the exact shape a mid-flight crash leaves behind.

    ``op-read-done`` completes, ``op-write-lost`` starts and is never heard from
    again, and the last two segments are never reached — which is what a process
    death in the middle of a tool-call group actually looks like.
    """

    plan = await durable_run.record_plan()
    coordinator = durable_run.coordinator()
    coordinator.begin(plan)
    await coordinator.run_child(
        batch_id=plan.batch_id,
        operation_id=_READ_DONE,
        runner=lambda _admission: _value(),
    )
    gate = _Gate()
    task = await _start(coordinator, plan, _WRITE_LOST, gate)

    restarted = await durable_run.restart()
    view = await restarted.load_recovery_view(
        org_id=_ORG,
        run_id=durable_run.run.run_id,
        subject_fingerprint=_SUBJECT,
    )
    return BatchRestartPlanner().plan(view), task


def _transition(
    *,
    phase: BatchChildPhase,
    disposition: BatchChildDisposition | None,
) -> BatchChildTransitionRecord:
    return BatchChildTransitionRecord.create(
        record_id=BatchChildTransitionRecord.stable_record_id(
            batch_id=_BATCH,
            operation_id=_READ_DONE,
            phase=phase,
        ),
        run_id="run-f66",
        snapshot_id="snapshot-f66",
        batch_id=_BATCH,
        operation_id=_READ_DONE,
        phase=phase,
        disposition=disposition,
    )


async def _value(value: str = "value") -> str:
    return value


async def _boom() -> str:
    raise RuntimeError("child failed")


def _leaf_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            item
            for key, nested in value.items()
            for item in ([key] if isinstance(key, str) else []) + _leaf_strings(nested)
        ]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _leaf_strings(nested)]
    return []


class _BrokenJournal:
    """A journal that cannot make a dispatch durable."""

    async def append_child_transition(self, write):
        raise BatchJournalCorruption(
            run_id=write.record.run_id,
            reason="the journal is unavailable",
        )
