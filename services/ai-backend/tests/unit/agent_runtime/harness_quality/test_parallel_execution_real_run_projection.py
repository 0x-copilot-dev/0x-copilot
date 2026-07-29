"""F6 — how much of the F1 parallel-execution criterion survives a real run.

``test_parallel_execution_cases.py`` proves each of the six families bites, but
every one of those proofs runs through ``FixtureOnlyCaseExecutor`` over a corpus
that *authors* its own observations. Lane BUG-14b established that the
distinction is load-bearing: a case can be perfectly written and still be
grading a fixture rather than a system. This module answers the question that is
left — scored over a trajectory projected from **real** ``operation_batch``
journal events, which assertions are still doing work?

The records below are not hand-written mappings that happen to have the right
keys. Each is a real :class:`BatchPlanBoundRecord`, and that record's own
validator **re-plans its stored inputs with the real** :class:`BatchPlanner`
**and refuses to exist unless it reproduces the stored segments exactly**. So a
segmentation asserted here is the segmentation the production planner actually
produces, not one the test chose. That is what caught the corpus originally
claiming a mode-serial write reports ``effectful_operation``; the real planner
reports ``policy_requires_serial``, because the mode check precedes the
side-effect check.

What holds:

* **All six families are gradeable on real events.** Plan records and child
  transitions both ride ``operation_batch.journal.v1``, and both project into
  the F6 columns.
* Each family **fails** on a real journal broken in the way it exists to catch.
* A width ceiling over a real record that carried no segment list fails closed.

One limit is worth stating plainly rather than leaving to be discovered:

* ``parallel_cancel_restart_no_invention`` grades a **property**, not a journal
  shape, and that is load-bearing. A cancel path that unwinds the coroutine
  records nothing for the interrupted child; one that records its uncertainty
  leaves a durable ``indeterminate``. F6 has had both. Neither claims anything
  about the world, so the case is written over *determinate* settlements —
  ``succeeded`` and ``failed`` only — and passes on either journal.
  :class:`TestTheCancelCaseGradesEitherCancelImplementation` builds both from
  real records and pins that. Had the case been written around "a child never
  settled", it would have scored the durable implementation as a failure the
  moment cancellation started recording — the BUG-17 shape of grading a working
  run as broken.

  What the case still cannot see is the *decision* a restart makes.
  ``ChildRestartDisposition`` is a returned value, not a journal record, so a
  trajectory carries the evidence a restart reads and never the verdict it
  reached. Asserting "a started write was not replayed" would need a record F6
  does not write.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.capabilities.concurrency.batch_journal import (
    BatchChildDisposition,
    BatchChildPhase,
    BatchChildTransitionRecord,
    BatchPlanBoundRecord,
    PlannedOperation,
)
from agent_runtime.capabilities.concurrency.contracts import (
    BatchFailurePolicy,
    BatchOperation,
    ConcurrencyAllowance,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyScope,
    IdempotencyKind,
    OperationBatch,
    OrderingRequirement,
    PolicySource,
    ProviderSessionConstraint,
    ResourceKeyTemplate,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.kill_switches import (
    ConcurrencyKillSwitchReason,
)
from agent_runtime.capabilities.concurrency.planner import BatchPlanner
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.harness_quality.evaluation import TrajectoryProjector
from agent_runtime.harness_quality.evaluation_contracts import TrajectoryManifest
from agent_runtime.harness_quality.operational_corpus import operational_corpus
from agent_runtime.harness_quality.scoring import ParallelExecutionTrajectoryScorer
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)
from runtime_api.schemas.events import OperationBatchJournalPayload


_NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
_RUN = "run_f6"
_SNAPSHOT = "snapshot_f6"
_OVERLAP = "parallel_independent_reads_overlap"
_UNKNOWN = "parallel_unknown_capability_serialized"
_WRITE = "parallel_write_after_planned_reads"
_UNPLANNABLE = "parallel_approval_gated_unplannable"
_SIBLING = "parallel_sibling_failure_isolated"
_NO_INVENTION = "parallel_cancel_restart_no_invention"


def _capability_ref(ordinal: int) -> str:
    return f"cap_{ordinal:032x}"


def _resource(seed: str) -> str:
    return f"hmac-sha256:{seed * 64}"


def _operation(
    operation_id: str,
    *,
    resource_fingerprints: tuple[str, ...] = (),
) -> BatchOperation:
    return BatchOperation(
        operation_id=operation_id,
        authorization_epoch="auth_1",
        dependency_ids=(),
        resource_fingerprints=resource_fingerprints,
    )


def _read_policy() -> ConcurrencyPolicy:
    return ConcurrencyPolicy(
        mode=ConcurrencyMode.PARALLEL_SAFE,
        side_effect=SideEffectKind.READ,
        idempotency=IdempotencyKind.NATURAL,
        resource_key_template=ResourceKeyTemplate.from_template("{connector}/{object}"),
        rate_limit_scope=ConcurrencyScope.CONNECTOR,
        ordering_requirement=OrderingRequirement.NONE,
        provider_session_constraint=ProviderSessionConstraint.SESSION_PARALLEL_SAFE,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )


def _mislabelled_write_policy() -> ConcurrencyPolicy:
    """A write an operator declared ``parallel_safe``.

    The interesting write: nothing about its *mode* forbids the overlap, so the
    only thing that keeps it out of the read segment is its effect class.
    """

    return ConcurrencyPolicy(
        mode=ConcurrencyMode.PARALLEL_SAFE,
        side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )


def _serial_write_policy() -> ConcurrencyPolicy:
    return ConcurrencyPolicy(
        mode=ConcurrencyMode.SERIAL,
        side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )


def _unknown_effect_policy() -> ConcurrencyPolicy:
    return ConcurrencyPolicy(
        mode=ConcurrencyMode.PARALLEL_SAFE,
        side_effect=SideEffectKind.UNKNOWN,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )


def _reads(count: int) -> tuple[PlannedOperation, ...]:
    return tuple(
        PlannedOperation.of(
            operation=_operation(
                f"op-read-{index}",
                resource_fingerprints=(_resource(chr(97 + index)),),
            ),
            capability_ref=_capability_ref(index),
            policy=_read_policy(),
        )
        for index in range(count)
    )


def _plan_record(
    *,
    batch_id: str,
    planned: tuple[PlannedOperation, ...],
    allowance: ConcurrencyAllowance = ConcurrencyAllowance.enforcing(4),
) -> BatchPlanBoundRecord:
    """Build one real plan record, segmented by the production planner.

    The record's validator re-plans ``operations`` and refuses the record unless
    the result equals ``segments``, so this cannot encode a segmentation the
    planner would not have made.
    """

    batch = OperationBatch(
        batch_id=batch_id,
        operations=tuple(item.operation for item in planned),
        allowance=allowance,
        failure_policy=BatchFailurePolicy.COLLECT_ALL,
    )
    plan = BatchPlanner().plan(
        batch,
        {item.operation.operation_id: item.policy for item in planned},
    )
    return BatchPlanBoundRecord.create(
        record_id=BatchPlanBoundRecord.stable_record_id(batch_id),
        run_id=_RUN,
        snapshot_id=_SNAPSHOT,
        batch_id=batch_id,
        turn_ordinal=1,
        concurrency_policy_revision="concurrency-r1",
        snapshot_allowance=allowance,
        effective_allowance=allowance,
        kill_switch_reason=ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS,
        failure_policy=BatchFailurePolicy.COLLECT_ALL,
        operations=planned,
        segments=plan.segments,
        plan_digest=BatchPlanBoundRecord.digest_of_plan(plan),
        created_at=_NOW,
    )


def _child_record(
    *,
    batch_id: str,
    operation_id: str,
    phase: BatchChildPhase,
    disposition: BatchChildDisposition | None = None,
) -> BatchChildTransitionRecord:
    return BatchChildTransitionRecord.create(
        record_id=BatchChildTransitionRecord.stable_record_id(
            batch_id=batch_id,
            operation_id=operation_id,
            phase=phase,
        ),
        run_id=_RUN,
        snapshot_id=_SNAPSHOT,
        batch_id=batch_id,
        operation_id=operation_id,
        phase=phase,
        disposition=disposition,
    )


def _envelope(record: object, *, sequence_no: int) -> RuntimeEventEnvelope:
    """Wrap one real record exactly as ``BatchPlanJournalStore`` wraps it."""

    payload = OperationBatchJournalPayload(record=record).model_dump(mode="json")
    return RuntimeEventEnvelope(
        run_id=_RUN,
        conversation_id="conv_f6",
        trace_id="trace_f6",
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.OPERATION_BATCH_JOURNAL,
        activity_kind=RuntimeActivityKind.EVENT,
        sequence_no=sequence_no,
        payload=payload,
    )


def _tool_envelope(*, sequence_no: int, ordinal: int) -> RuntimeEventEnvelope:
    """One ordinary tool result, so an absence assertion has a floor to clear."""

    return RuntimeEventEnvelope(
        run_id=_RUN,
        conversation_id="conv_f6",
        trace_id="trace_f6",
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_RESULT,
        activity_kind=RuntimeActivityKind.TOOL,
        sequence_no=sequence_no,
        payload={"tool_name": f"connector.read.{ordinal}"},
    )


def _projected(*events: RuntimeEventEnvelope) -> TrajectoryManifest:
    """Project real runtime envelopes through the production F1 projector."""

    return TrajectoryProjector(redaction_policy_revision="redaction-v1").project(
        run_id=_RUN,
        variant_id="candidate",
        events=events,
    )


def _case(family: str):  # type: ignore[no-untyped-def]
    return next(item for item in operational_corpus() if item.family == family).case


def _score(family: str, trajectory: TrajectoryManifest):  # type: ignore[no-untyped-def]
    return ParallelExecutionTrajectoryScorer().score(
        case=_case(family),
        trajectory=trajectory,
    )


def _healthy_overlap_run() -> TrajectoryManifest:
    return _projected(
        _envelope(
            _plan_record(batch_id="batch-reads", planned=_reads(3)),
            sequence_no=1,
        )
    )


def _healthy_unknown_run() -> TrajectoryManifest:
    return _projected(
        _envelope(
            _plan_record(
                batch_id="batch-unknown",
                planned=(
                    # No policy at all: the undeclared case, which falls to the
                    # conservative floor.
                    PlannedOperation.of(
                        operation=_operation("op-undeclared"),
                        capability_ref=_capability_ref(11),
                    ),
                    PlannedOperation.of(
                        operation=_operation("op-unknown-effect"),
                        capability_ref=_capability_ref(12),
                        policy=_unknown_effect_policy(),
                    ),
                ),
            ),
            sequence_no=1,
        )
    )


def _healthy_write_run() -> TrajectoryManifest:
    return _projected(
        _envelope(
            _plan_record(
                batch_id="batch-write",
                planned=(
                    *_reads(2),
                    PlannedOperation.of(
                        operation=_operation("op-write-effect"),
                        capability_ref=_capability_ref(9),
                        policy=_mislabelled_write_policy(),
                    ),
                    PlannedOperation.of(
                        operation=_operation("op-write-mode"),
                        capability_ref=_capability_ref(10),
                        policy=_serial_write_policy(),
                    ),
                ),
            ),
            sequence_no=1,
        )
    )


def _child_events(
    *,
    batch_id: str,
    settled: tuple[tuple[str, BatchChildDisposition], ...],
    intents: tuple[str, ...],
    first_sequence_no: int,
) -> tuple[RuntimeEventEnvelope, ...]:
    records = [
        _child_record(
            batch_id=batch_id,
            operation_id=operation_id,
            phase=BatchChildPhase.DISPATCH_INTENT,
        )
        for operation_id in intents
    ]
    records.extend(
        _child_record(
            batch_id=batch_id,
            operation_id=operation_id,
            phase=BatchChildPhase.SETTLED,
            disposition=disposition,
        )
        for operation_id, disposition in settled
    )
    return tuple(
        _envelope(record, sequence_no=first_sequence_no + index)
        for index, record in enumerate(records)
    )


def _healthy_sibling_run() -> TrajectoryManifest:
    """One read succeeded, its sibling failed, and neither erased the other."""

    batch_id = "batch-sibling"
    return _projected(
        _envelope(
            _plan_record(batch_id=batch_id, planned=_reads(2)),
            sequence_no=1,
        ),
        *_child_events(
            batch_id=batch_id,
            intents=("op-read-0", "op-read-1"),
            settled=(
                ("op-read-0", BatchChildDisposition.SUCCEEDED),
                ("op-read-1", BatchChildDisposition.FAILED),
            ),
            first_sequence_no=2,
        ),
    )


def _healthy_cancelled_run() -> TrajectoryManifest:
    """What a real cancel leaves: one child settled, one begun and unaccounted.

    No ``indeterminate`` record, because a production cancel writes none — the
    coroutine is unwound and ``cancel()`` is never called. The durable evidence
    is the intent with no settle beside it.
    """

    batch_id = "batch-cancelled"
    return _projected(
        _envelope(
            _plan_record(batch_id=batch_id, planned=_reads(2)),
            sequence_no=1,
        ),
        *_child_events(
            batch_id=batch_id,
            intents=("op-read-0", "op-read-1"),
            settled=(("op-read-0", BatchChildDisposition.SUCCEEDED),),
            first_sequence_no=2,
        ),
    )


def _healthy_unplannable_run() -> TrajectoryManifest:
    """An approval-gated turn: two tool calls, and no batch journal at all."""

    return _projected(
        _tool_envelope(sequence_no=1, ordinal=1),
        _tool_envelope(sequence_no=2, ordinal=2),
    )


class TestTheProjectionReadsARealBatchJournal:
    """Before any case is scored: the columns are populated from real records."""

    def test_a_real_plan_record_projects_its_closed_segment_vocabulary(self) -> None:
        trajectory = _healthy_overlap_run()

        step = trajectory.ordered_steps[0]

        assert step.event_type == "operation_batch.journal.v1"
        assert step.parallel_record_kind == "plan_bound"
        assert step.parallel_segment_modes == ("parallel",)
        assert step.parallel_parallel_segment_reasons == ("independent_reads",)
        assert step.parallel_serial_segment_reasons == ()
        assert step.parallel_kill_switch_reason == "snapshot_governs"
        assert step.parallel_planned_operations == 3
        assert step.parallel_overlapping_operations == 3
        assert step.parallel_maximum_segment_width == 3
        assert step.parallel_counts_observed is True

    def test_a_real_child_transition_projects_its_phase_and_disposition(self) -> None:
        trajectory = _healthy_sibling_run()

        settled = tuple(
            step
            for step in trajectory.ordered_steps
            if step.parallel_child_phase == "settled"
        )

        assert {step.parallel_child_disposition for step in settled} == {
            "succeeded",
            "failed",
        }
        assert all(step.parallel_record_kind == "child_transition" for step in settled)
        # A transition measures no width, so its zeroes must stay
        # distinguishable from an observed zero.
        assert all(step.parallel_counts_observed is False for step in settled)

    def test_the_projection_carries_no_operation_identity(self) -> None:
        """Body-free means counts and closed enums, and nothing that names work.

        The plan record itself holds operation ids and capability refs; the
        evaluation projection must reduce them to a width.
        """

        step = _healthy_write_run().ordered_steps[0]

        projected = step.model_dump(mode="json")
        parallel = {
            key: value
            for key, value in projected.items()
            if key.startswith("parallel_")
        }
        rendered = repr(parallel)

        assert "op-read" not in rendered
        assert "op-write" not in rendered
        assert "cap_" not in rendered
        assert step.capability_id is None

    def test_a_non_batch_event_populates_no_parallel_column(self) -> None:
        """The event-type gate.

        ``plan_bound`` is a record kind F4 uses too, so a projection that read
        the nested record without checking the event type would let an F4
        controller row masquerade as an F6 plan.
        """

        step = _projected(_tool_envelope(sequence_no=1, ordinal=1)).ordered_steps[0]

        assert step.parallel_record_kind is None
        assert step.parallel_segment_modes == ()
        assert step.parallel_counts_observed is False


class TestEveryFamilyPassesOnAWorkingRealRun:
    """A working system must go green through the production projector."""

    @pytest.mark.parametrize(
        ("family", "run"),
        [
            (_OVERLAP, _healthy_overlap_run),
            (_UNKNOWN, _healthy_unknown_run),
            (_WRITE, _healthy_write_run),
            (_UNPLANNABLE, _healthy_unplannable_run),
            (_SIBLING, _healthy_sibling_run),
            (_NO_INVENTION, _healthy_cancelled_run),
        ],
    )
    def test_the_case_passes_over_real_events(self, family: str, run) -> None:  # type: ignore[no-untyped-def]
        result = _score(family, run())

        assert result.passed, result.reason_code
        assert result.reason_code == "parallel_execution_trajectory_passed"


class TestTheCorpusDescribesWhatThePlannerActuallyDoes:
    """The authored fixture and the real record must agree, segment for segment.

    This is the check that keeps the corpus from drifting into plausible-sounding
    behaviour. It already earned its place: the write family originally claimed a
    mode-serial write reports ``effectful_operation``, and the real planner
    reports ``policy_requires_serial`` because the mode check runs first.
    """

    @pytest.mark.parametrize(
        ("family", "run"),
        [
            (_OVERLAP, _healthy_overlap_run),
            (_UNKNOWN, _healthy_unknown_run),
            (_WRITE, _healthy_write_run),
        ],
    )
    def test_the_authored_segments_match_the_planner(self, family: str, run) -> None:  # type: ignore[no-untyped-def]
        entry = next(item for item in operational_corpus() if item.family == family)
        authored = tuple(
            (
                step.parallel_segment_modes,
                step.parallel_parallel_segment_reasons,
                step.parallel_serial_segment_reasons,
                step.parallel_maximum_segment_width,
            )
            for call in entry.calls
            for step in call.after_observations
            if step.parallel_record_kind == "plan_bound"
        )
        real = tuple(
            (
                step.parallel_segment_modes,
                step.parallel_parallel_segment_reasons,
                step.parallel_serial_segment_reasons,
                step.parallel_maximum_segment_width,
            )
            for step in run().ordered_steps
            if step.parallel_record_kind == "plan_bound"
        )

        assert authored == real


class TestEveryFamilyFailsOnARealRunThatBroke:
    """The half that matters: a broken real journal must fail its own case."""

    def test_a_real_plan_that_stopped_overlapping_fails_the_overlap_case(
        self,
    ) -> None:
        """Reads whose resources are unknown are serialized by the planner.

        Nothing is mutated here — the *inputs* are changed, and the planner
        makes a different, genuinely worse decision from them.
        """

        broken = _projected(
            _envelope(
                _plan_record(
                    batch_id="batch-reads",
                    planned=tuple(
                        PlannedOperation.of(
                            operation=BatchOperation(
                                operation_id=f"op-read-{index}",
                                authorization_epoch="auth_1",
                                dependency_ids=(),
                                resource_fingerprints=None,
                            ),
                            capability_ref=_capability_ref(index),
                            policy=_read_policy(),
                        )
                        for index in range(3)
                    ),
                ),
                sequence_no=1,
            )
        )

        result = _score(_OVERLAP, broken)

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_mode_observed"

    def test_a_real_batch_capped_to_serial_fails_the_overlap_case(self) -> None:
        """A kill switch or an unadmitted run produces exactly this plan."""

        broken = _projected(
            _envelope(
                _plan_record(
                    batch_id="batch-reads",
                    planned=_reads(3),
                    allowance=ConcurrencyAllowance.serial(),
                ),
                sequence_no=1,
            )
        )

        result = _score(_OVERLAP, broken)

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_mode_observed"

    def test_a_real_plan_that_overlapped_an_unknown_capability_fails_its_case(
        self,
    ) -> None:
        """The widening F6.1's conservative floor exists to prevent.

        Declaring the two operations parallel-safe reads is precisely the
        mislabelling the unknown family guards against, and the planner
        obligingly overlaps them.
        """

        broken = _projected(
            _envelope(
                _plan_record(batch_id="batch-unknown", planned=_reads(2)),
                sequence_no=1,
            )
        )

        result = _score(_UNKNOWN, broken)

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_mode_observed"

    def test_a_real_write_admitted_into_the_read_segment_fails_its_case(self) -> None:
        """The safety failure, produced by the real planner from real inputs.

        The write is declared a parallel-safe *read* — the mislabelling that
        would let an effect join an overlap — and the planner puts all three
        operations in one parallel segment.

        The refusal comes from the serial-reason gate rather than the ordering
        one, because a plan that folded the write in has no serial segment left
        to be out of order. Both gates are the same claim seen from two sides:
        the effect must have a segment of its own, after the reads.
        """

        broken = _projected(
            _envelope(
                _plan_record(
                    batch_id="batch-write",
                    planned=(
                        *_reads(2),
                        PlannedOperation.of(
                            operation=_operation(
                                "op-write-effect",
                                resource_fingerprints=(_resource("f"),),
                            ),
                            capability_ref=_capability_ref(9),
                            policy=_read_policy(),
                        ),
                    ),
                ),
                sequence_no=1,
            )
        )

        result = _score(_WRITE, broken)
        step = next(
            item
            for item in broken.ordered_steps
            if item.parallel_record_kind == "plan_bound"
        )

        assert step.parallel_segment_modes == ("parallel",)
        assert step.parallel_overlapping_operations == 3
        assert not result.passed
        assert result.reason_code == "parallel_execution_serial_reason_missing"

    def test_a_real_plan_recorded_for_an_approval_turn_fails_its_case(self) -> None:
        """Approval-gated work reaching the planner at all is the failure."""

        broken = _projected(
            _tool_envelope(sequence_no=1, ordinal=1),
            _tool_envelope(sequence_no=2, ordinal=2),
            _envelope(
                _plan_record(batch_id="batch-approval", planned=_reads(2)),
                sequence_no=3,
            ),
        )

        result = _score(_UNPLANNABLE, broken)

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_record_observed"

    def test_a_real_run_that_abandoned_a_completed_sibling_fails_its_case(
        self,
    ) -> None:
        """What a ``fail_fast`` regression writes: the survivor loses its result."""

        batch_id = "batch-sibling"
        broken = _projected(
            _envelope(
                _plan_record(batch_id=batch_id, planned=_reads(2)),
                sequence_no=1,
            ),
            *_child_events(
                batch_id=batch_id,
                intents=("op-read-0", "op-read-1"),
                settled=(
                    ("op-read-0", BatchChildDisposition.INDETERMINATE),
                    ("op-read-1", BatchChildDisposition.FAILED),
                ),
                first_sequence_no=2,
            ),
        )

        result = _score(_SIBLING, broken)

        assert not result.passed
        assert result.reason_code == "parallel_execution_child_disposition_missing"

    @pytest.mark.parametrize(
        "disposition",
        [BatchChildDisposition.SUCCEEDED, BatchChildDisposition.FAILED],
    )
    def test_a_real_journal_that_invented_an_outcome_fails_its_case(
        self,
        disposition: BatchChildDisposition,
    ) -> None:
        """Both comfortable lies, written as real records.

        ``succeeded`` invents a result nobody saw. ``failed`` invents the claim
        that nothing reached the connector, which is the implicit rollback the
        F6 cancellation vocabulary refuses to make.
        """

        batch_id = "batch-cancelled"
        broken = _projected(
            _envelope(
                _plan_record(batch_id=batch_id, planned=_reads(2)),
                sequence_no=1,
            ),
            *_child_events(
                batch_id=batch_id,
                intents=("op-read-0", "op-read-1"),
                settled=(
                    ("op-read-0", BatchChildDisposition.SUCCEEDED),
                    ("op-read-1", disposition),
                ),
                first_sequence_no=2,
            ),
        )

        result = _score(_NO_INVENTION, broken)

        assert not result.passed
        assert result.reason_code == "parallel_execution_invented_outcome_observed"


class TestARealRecordWithoutSegmentsFailsClosed:
    """BUG-14's refusal, proved over the real projection rather than a fixture.

    A width ceiling is satisfied by an unpopulated field, so a real record that
    carried no segment list must not read as a plan that overlapped nothing.
    """

    def test_a_child_transition_alone_cannot_answer_a_width_question(self) -> None:
        batch_id = "batch-unknown"
        without_a_plan = _projected(
            *_child_events(
                batch_id=batch_id,
                intents=("op-a",),
                settled=(("op-a", BatchChildDisposition.SUCCEEDED),),
                first_sequence_no=1,
            )
        )

        result = _score(_UNKNOWN, without_a_plan)

        assert not result.passed
        # The record gate fires before the width gate, and both are refusals.
        # What must not happen is a pass.
        assert result.reason_code == "parallel_execution_record_missing"

    def test_a_plan_record_stripped_of_its_segments_fails_closed(self) -> None:
        """The exact shape BUG-14 found: a ceiling met by missing evidence.

        A reader that saw ``segments`` absent and reported width ``0`` would
        satisfy ``maximum_overlapping_operations: 0`` — the assertion by which
        the unknown-capability family says nothing overlapped — while observing
        nothing at all.

        The assertion is narrowed to that single numeric expectation on purpose.
        The shipped case also demands serial *reasons*, which an empty segment
        list fails first; that is a correct refusal but it proves the wrong
        thing. Isolating the ceiling is what shows the numeric gate itself
        refuses rather than being carried by its neighbours.
        """

        record = _plan_record(batch_id="batch-unknown", planned=_reads(2))
        payload = OperationBatchJournalPayload(record=record).model_dump(mode="json")
        payload["record"].pop("segments")
        stripped = _projected(
            RuntimeEventEnvelope(
                run_id=_RUN,
                conversation_id="conv_f6",
                trace_id="trace_f6",
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.OPERATION_BATCH_JOURNAL,
                activity_kind=RuntimeActivityKind.EVENT,
                sequence_no=1,
                payload=payload,
            )
        )
        ceiling_only = _case(_UNKNOWN).model_copy(
            update={
                "expected_assertions": tuple(
                    item.model_copy(
                        update={"expected": {"maximum_overlapping_operations": 0}}
                    )
                    if item.scorer_id == "parallel_execution_trajectory"
                    else item
                    for item in _case(_UNKNOWN).expected_assertions
                )
            }
        )

        step = stripped.ordered_steps[0]
        result = ParallelExecutionTrajectoryScorer().score(
            case=ceiling_only,
            trajectory=stripped,
        )

        assert step.parallel_record_kind == "plan_bound"
        assert step.parallel_counts_observed is False
        assert not result.passed
        assert result.reason_code == "parallel_execution_counts_unobserved"

    def test_the_shipped_case_also_refuses_that_record(self) -> None:
        """Whatever gate fires first, a segment-less plan must never pass."""

        record = _plan_record(batch_id="batch-unknown", planned=_reads(2))
        payload = OperationBatchJournalPayload(record=record).model_dump(mode="json")
        payload["record"].pop("segments")
        stripped = _projected(
            RuntimeEventEnvelope(
                run_id=_RUN,
                conversation_id="conv_f6",
                trace_id="trace_f6",
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.OPERATION_BATCH_JOURNAL,
                activity_kind=RuntimeActivityKind.EVENT,
                sequence_no=1,
                payload=payload,
            )
        )

        result = _score(_UNKNOWN, stripped)

        assert not result.passed
        assert result.reason_code == "parallel_execution_serial_reason_missing"


class TestTheCancelCaseGradesEitherCancelImplementation:
    """The family must not depend on which shape the cancel path records.

    There are two honest journals for a cancelled batch, and F6 has had both:

    * a cancel that unwinds the coroutine records nothing for the interrupted
      child, leaving a dispatch intent with no settle beside it;
    * a cancel that records its uncertainty leaves a durable ``indeterminate``
      settlement.

    Neither claims anything about the world, so both must pass. A case written
    around the *absence* of a settle would grade the first as correct and the
    second as a failure — scoring a working, strictly better implementation as
    broken, which is the BUG-17 shape. Writing the assertion over *determinate*
    settlements is what makes the property, rather than the shape, the thing
    being graded. Both journals are built from real records below.
    """

    def test_a_cancel_that_recorded_nothing_passes(self) -> None:
        result = _score(_NO_INVENTION, _healthy_cancelled_run())

        assert result.passed
        assert result.reason_code == "parallel_execution_trajectory_passed"

    def test_a_cancel_that_durably_recorded_its_uncertainty_passes(self) -> None:
        batch_id = "batch-cancelled"
        durable = _projected(
            _envelope(
                _plan_record(batch_id=batch_id, planned=_reads(2)),
                sequence_no=1,
            ),
            *_child_events(
                batch_id=batch_id,
                intents=("op-read-0", "op-read-1"),
                settled=(
                    ("op-read-0", BatchChildDisposition.SUCCEEDED),
                    ("op-read-1", BatchChildDisposition.INDETERMINATE),
                ),
                first_sequence_no=2,
            ),
        )

        result = _score(_NO_INVENTION, durable)

        assert result.passed
        assert result.reason_code == "parallel_execution_trajectory_passed"

    def test_the_case_never_demands_one_shape_over_the_other(self) -> None:
        """Guard the property framing itself against a later "tightening"."""

        expected = next(
            item.expected
            for item in _case(_NO_INVENTION).expected_assertions
            if item.scorer_id == "parallel_execution_trajectory"
        )

        assert "indeterminate" not in str(expected.get("required_child_dispositions"))
        assert "maximum_settled_children" not in expected
        assert expected.get("maximum_determinate_settlements") == 1
        assert expected.get("require_unresolved_child") is True

    def test_the_case_still_fails_a_run_that_finished_nothing(self) -> None:
        """The family is not vacuous just because it asserts an absence.

        A batch whose children were all begun and none settled satisfies
        ``require_unresolved_child`` trivially, so the settled-success
        requirement is what keeps it honest.
        """

        batch_id = "batch-cancelled"
        nothing_settled = _projected(
            _envelope(
                _plan_record(batch_id=batch_id, planned=_reads(2)),
                sequence_no=1,
            ),
            *_child_events(
                batch_id=batch_id,
                intents=("op-read-0", "op-read-1"),
                settled=(),
                first_sequence_no=2,
            ),
        )

        result = _score(_NO_INVENTION, nothing_settled)

        assert not result.passed
        assert result.reason_code == "parallel_execution_child_phase_missing"
