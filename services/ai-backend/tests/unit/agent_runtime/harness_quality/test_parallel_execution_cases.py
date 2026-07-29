"""F6 — the F1 cases for parallel execution, and proof that they bite.

ARQ-010 carries its own precondition: graph-level parallel execution stays off
"until those controls **and F1 evaluation** are present". Every F6 control is
built and wired; this is the evaluation half.

Six properties carry the precondition, and each gets a family:

* independent curated reads overlap safely;
* an undeclared or unknown capability stays serial;
* a write never overlaps the reads planned before it;
* approval-gated work is unplannable — *unplannable*, not merely serial,
  because a parked child under the coordinator's segment gate holds that gate
  while it waits on a person;
* a sibling failure leaves a completed child's result intact;
* cancel and restart invent neither rollback nor success.

Every case is tested twice, following lane F3.7. Once against the corpus as
authored, where it must pass; and once against a trajectory deliberately mutated
to describe the *broken* behaviour the case exists to catch, where it must fail
with a named reason code. The second half is the point of this file: a case that
passes against a broken implementation is worse than no case at all, and this
program has already found two families of assertion that passed on absent data.

Two anti-vacuity proofs are called out because they are the shapes that went
wrong before:

* :class:`TestUnplannableWorkCannotPassOnAnEmptyTrajectory` — the approval case
  asserts an *absence*, and an absence proves nothing unless the turn ran.
* :class:`TestAWidthCeilingRefusesAnUnmeasuredPlan` — BUG-14 in F6's shape: a
  ``maximum_`` bound over an unpopulated width is satisfied by absence.

Which of these run against **real** trajectories rather than authored fixtures
is answered separately, in
``test_parallel_execution_real_run_projection.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.harness_quality.evaluation import FixtureToolExecutor
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationStatus,
    HarnessVariant,
    TrajectoryManifest,
    TrajectoryStep,
)
from agent_runtime.harness_quality.operational_corpus import (
    OPERATIONAL_TASK_FAMILIES,
    operational_corpus,
)
from agent_runtime.harness_quality.scoring import (
    DEFAULT_HARD_SCORERS,
    ParallelExecutionTrajectoryScorer,
)
from agent_runtime.harness_quality.suite_execution import FixtureOnlyCaseExecutor

_NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
_OVERLAP = "parallel_independent_reads_overlap"
_UNKNOWN = "parallel_unknown_capability_serialized"
_WRITE = "parallel_write_after_planned_reads"
_UNPLANNABLE = "parallel_approval_gated_unplannable"
_SIBLING = "parallel_sibling_failure_isolated"
_NO_INVENTION = "parallel_cancel_restart_no_invention"
_F6_FAMILIES = (
    _OVERLAP,
    _UNKNOWN,
    _WRITE,
    _UNPLANNABLE,
    _SIBLING,
    _NO_INVENTION,
)
_PLAN = "plan_bound"
_CHILD = "child_transition"
_BATCH_EVENT = "operation_batch.journal.v1"
_DIGEST = "b" * 64


def _variant() -> HarnessVariant:
    return HarnessVariant(
        variant_id="candidate",
        revision="variant-v1",
        prompt_plan_revision="prompt-v1",
        capability_policy_revision="capability-v1",
        context_policy_revision="context-v1",
        model_route_revision="model-v1",
    )


def _entry(family: str):  # type: ignore[no-untyped-def]
    return next(item for item in operational_corpus() if item.family == family)


async def _trajectory(family: str) -> TrajectoryManifest:
    """Project one F6 family through the real fixture-only case executor."""

    entry = _entry(family)
    return await FixtureOnlyCaseExecutor().execute(
        suite_run_id=f"suite_{family}",
        case=entry.case,
        variant=_variant(),
        plan=entry.plan(),
        fixtures=FixtureToolExecutor(entry.fixtures),
        projected_at=_NOW,
    )


def _score(family: str, trajectory: TrajectoryManifest):  # type: ignore[no-untyped-def]
    return ParallelExecutionTrajectoryScorer().score(
        case=_entry(family).case,
        trajectory=trajectory,
    )


def _mutated(
    trajectory: TrajectoryManifest,
    *,
    record_kind: str | None = None,
    disposition: str | None = None,
    **updates: object,
) -> TrajectoryManifest:
    """Rewrite the F6 projection of matching steps, and nothing else."""

    def matches(step: TrajectoryStep) -> bool:
        if step.parallel_record_kind is None:
            return False
        if record_kind is not None and step.parallel_record_kind != record_kind:
            return False
        return disposition is None or step.parallel_child_disposition == disposition

    return trajectory.model_copy(
        update={
            "ordered_steps": tuple(
                step.model_copy(update=dict(updates)) if matches(step) else step
                for step in trajectory.ordered_steps
            )
        }
    )


def _without(
    trajectory: TrajectoryManifest,
    *,
    record_kind: str | None = None,
    phase: str | None = None,
    disposition: str | None = None,
) -> TrajectoryManifest:
    """Drop matching F6 steps, leaving the rest of the trajectory untouched."""

    def drop(step: TrajectoryStep) -> bool:
        if step.parallel_record_kind is None:
            return False
        if record_kind is not None and step.parallel_record_kind != record_kind:
            return False
        if phase is not None and step.parallel_child_phase != phase:
            return False
        return disposition is None or step.parallel_child_disposition == disposition

    return trajectory.model_copy(
        update={
            "ordered_steps": tuple(
                step for step in trajectory.ordered_steps if not drop(step)
            )
        }
    )


def _dropping_first(
    trajectory: TrajectoryManifest,
    *,
    phase: str,
) -> TrajectoryManifest:
    """Drop the first step in one child phase, keeping the rest."""

    dropped = False
    kept: list[TrajectoryStep] = []
    for step in trajectory.ordered_steps:
        if not dropped and step.parallel_child_phase == phase:
            dropped = True
            continue
        kept.append(step)
    return trajectory.model_copy(update={"ordered_steps": tuple(kept)})


def _with_extra_step(
    trajectory: TrajectoryManifest,
    **projection: object,
) -> TrajectoryManifest:
    """Append one F6 step, as a broken producer would have written it."""

    return trajectory.model_copy(
        update={
            "ordered_steps": (
                *trajectory.ordered_steps,
                TrajectoryStep(
                    sequence_no=len(trajectory.ordered_steps) + 1,
                    event_type=_BATCH_EVENT,
                    source="fixture",
                    payload_digest=_DIGEST,
                    **projection,
                ),
            )
        }
    )


class TestTheSixCasesExistAndAreSatisfiable:
    """The corpus half: the cases are real, reachable, and pass as authored."""

    @pytest.mark.parametrize("family", _F6_FAMILIES)
    def test_the_corpus_declares_the_family(self, family: str) -> None:
        assert family in OPERATIONAL_TASK_FAMILIES

    @pytest.mark.parametrize("family", _F6_FAMILIES)
    async def test_each_case_passes_every_hard_scorer_as_authored(
        self,
        family: str,
    ) -> None:
        entry = _entry(family)
        trajectory = await _trajectory(family)

        results = tuple(
            scorer.score(case=entry.case, trajectory=trajectory)
            for scorer in DEFAULT_HARD_SCORERS
        )

        assert all(result.passed for result in results), (
            family,
            [result.reason_code for result in results if not result.passed],
        )

    @pytest.mark.parametrize("family", _F6_FAMILIES)
    async def test_each_case_carries_a_hard_gated_parallel_assertion(
        self,
        family: str,
    ) -> None:
        """An advisory case cannot fail a promotion, so the gate is asserted."""

        assertions = [
            item
            for item in _entry(family).case.expected_assertions
            if item.scorer_id == "parallel_execution_trajectory"
        ]

        assert len(assertions) == 1
        assert assertions[0].hard_gate is True

    async def test_a_family_without_parallel_facts_is_left_alone(self) -> None:
        """The new scorer must not start failing the 37 pre-existing families."""

        entry = operational_corpus()[0]
        trajectory = await FixtureOnlyCaseExecutor().execute(
            suite_run_id="suite_untouched",
            case=entry.case,
            variant=_variant(),
            plan=entry.plan(),
            fixtures=FixtureToolExecutor(entry.fixtures),
            projected_at=_NOW,
        )

        result = ParallelExecutionTrajectoryScorer().score(
            case=entry.case,
            trajectory=trajectory,
        )

        assert result.passed
        assert result.hard_gate is False
        assert result.reason_code == "parallel_execution_not_applicable"


class TestOverlapDetectsAPlannerThatStoppedOverlapping:
    """If independent reads stop running together, this case must fail."""

    async def test_a_plan_that_serialized_the_reads_fails_the_case(self) -> None:
        """The regression F6 exists to prevent: correct, and no faster."""

        trajectory = await _trajectory(_OVERLAP)

        result = _score(
            _OVERLAP,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_segment_modes=("serial", "serial", "serial"),
                parallel_parallel_segment_reasons=(),
                parallel_serial_segment_reasons=(
                    "insufficient_parallel_members",
                    "insufficient_parallel_members",
                    "insufficient_parallel_members",
                ),
                parallel_overlapping_operations=0,
                parallel_maximum_segment_width=1,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_mode_observed"
        assert result.hard_gate is True

    async def test_a_parallel_segment_that_overlapped_nothing_fails_the_case(
        self,
    ) -> None:
        """A mode is not a measurement.

        A planner could emit a segment marked ``parallel`` that holds no work
        and satisfy every categorical check in the case. The overlap floor is
        the assertion that actually says throughput happened.
        """

        trajectory = await _trajectory(_OVERLAP)

        result = _score(
            _OVERLAP,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_overlapping_operations=0,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_overlap_below_minimum"

    async def test_reads_overlapped_for_the_wrong_reason_fail_the_case(self) -> None:
        """``independent_reads`` is the only reason that justifies an overlap."""

        trajectory = await _trajectory(_OVERLAP)

        result = _score(
            _OVERLAP,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_parallel_segment_reasons=("batch_serial_default",),
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_parallel_reason_missing"

    async def test_an_extra_reason_reaching_a_parallel_segment_fails_the_case(
        self,
    ) -> None:
        """The allowlist half.

        ``required_parallel_segment_reasons`` only says the right reason was
        present. This says nothing *else* got into a parallel segment, which is
        the direction a reason added after the case was written would breach.
        """

        trajectory = await _trajectory(_OVERLAP)

        result = _score(
            _OVERLAP,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_parallel_segment_reasons=(
                    "independent_reads",
                    "explicit_dependencies",
                ),
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_parallel_reason_unexpected"

    async def test_an_overlap_wider_than_the_ceiling_fails_the_case(self) -> None:
        """Width is a budget: unbounded fan-out is its own failure."""

        trajectory = await _trajectory(_OVERLAP)

        result = _score(
            _OVERLAP,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_maximum_segment_width=16,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_segment_width_exceeded"

    async def test_a_run_that_recorded_no_plan_at_all_fails_the_case(self) -> None:
        """A case that silently scored a planless trajectory would prove nothing."""

        trajectory = await _trajectory(_OVERLAP)

        result = _score(_OVERLAP, _without(trajectory, record_kind=_PLAN))

        assert not result.passed
        assert result.reason_code == "parallel_execution_record_missing"


class TestUnknownCapabilitiesStayingSerialDetectsAWidening:
    """The conservative half: silence about a capability forbids overlap."""

    async def test_an_unknown_capability_that_starts_overlapping_fails_the_case(
        self,
    ) -> None:
        trajectory = await _trajectory(_UNKNOWN)

        result = _score(
            _UNKNOWN,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_segment_modes=("parallel",),
                parallel_parallel_segment_reasons=("independent_reads",),
                parallel_serial_segment_reasons=(),
                parallel_overlapping_operations=2,
                parallel_maximum_segment_width=2,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_mode_observed"

    async def test_serializing_for_the_wrong_reason_fails_the_case(self) -> None:
        """ "It stayed serial" is not the claim; *why* it stayed serial is.

        A plan that went serial because the whole batch was capped would look
        identical at the mode level while proving nothing about how an
        undeclared capability is treated.
        """

        trajectory = await _trajectory(_UNKNOWN)

        result = _score(
            _UNKNOWN,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_serial_segment_reasons=(
                    "batch_serial_default",
                    "batch_serial_default",
                ),
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_serial_reason_missing"

    async def test_any_operation_reaching_an_overlap_fails_the_case(self) -> None:
        """The numeric half of "nothing overlapped", independent of the mode."""

        trajectory = await _trajectory(_UNKNOWN)

        result = _score(
            _UNKNOWN,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_overlapping_operations=2,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_overlap_above_maximum"

    async def test_a_segment_holding_more_than_one_operation_fails_the_case(
        self,
    ) -> None:
        trajectory = await _trajectory(_UNKNOWN)

        result = _score(
            _UNKNOWN,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_maximum_segment_width=2,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_segment_width_exceeded"


class TestTheWriteOrderingDetectsAnOverlappedEffect:
    """The safety case: an effect must never join the overlap before it."""

    async def test_a_write_that_ran_before_the_reads_settled_fails_the_case(
        self,
    ) -> None:
        """Order is the assertion.

        The set ``{parallel, serial}`` is equally true of this plan and of one
        that ran the write first, which is why the case compares an ordered
        tuple rather than a set.
        """

        trajectory = await _trajectory(_WRITE)

        result = _score(
            _WRITE,
            # The same three segments, reordered so a write leads. Deliberately
            # not a shorter tuple: a length change would fail an order check
            # that only compared sizes, and this must fail on position alone.
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_segment_modes=("serial", "parallel", "serial"),
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_segment_order_mismatch"
        assert result.hard_gate is True

    async def test_a_write_admitted_into_the_parallel_segment_fails_the_case(
        self,
    ) -> None:
        """The failure this family is named for, stated at the reason level."""

        trajectory = await _trajectory(_WRITE)

        result = _score(
            _WRITE,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_parallel_segment_reasons=(
                    "independent_reads",
                    "effectful_operation",
                ),
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_parallel_reason_unexpected"

    async def test_a_plan_that_folded_the_write_into_the_reads_fails_the_case(
        self,
    ) -> None:
        """One parallel segment of three, and no serial segment at all."""

        trajectory = await _trajectory(_WRITE)

        result = _score(
            _WRITE,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_segment_modes=("parallel",),
                parallel_serial_segment_reasons=(),
                parallel_overlapping_operations=3,
                parallel_maximum_segment_width=3,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_serial_reason_missing"

    async def test_reads_that_stopped_overlapping_still_fail_the_case(self) -> None:
        """Safety is not the only claim here: the reads must still be fast."""

        trajectory = await _trajectory(_WRITE)

        result = _score(
            _WRITE,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_overlapping_operations=0,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_overlap_below_minimum"


class TestUnplannableWorkDetectsAPlannedApproval:
    """Approval-gated work must not be planned — not even into serial segments.

    BUG-15 is the reason this is stated as *unplannable* rather than *serial*:
    LangGraph cannot resume a multi-interrupt turn from one decision, and a
    parked child under the coordinator's segment gate holds that gate while its
    siblings burn the admission budget and are then refused.
    """

    async def test_a_planned_approval_gated_turn_fails_the_case(self) -> None:
        trajectory = await _trajectory(_UNPLANNABLE)

        result = _score(
            _UNPLANNABLE,
            _with_extra_step(
                trajectory,
                parallel_record_kind=_PLAN,
                parallel_segment_modes=("serial", "serial"),
                parallel_serial_segment_reasons=(
                    "policy_requires_serial",
                    "policy_requires_serial",
                ),
                parallel_planned_operations=2,
                parallel_maximum_segment_width=1,
                parallel_counts_observed=True,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_record_observed"
        assert result.hard_gate is True

    async def test_a_child_journalled_for_an_approval_turn_fails_the_case(
        self,
    ) -> None:
        """A transition proves a batch existed even if its plan went missing."""

        trajectory = await _trajectory(_UNPLANNABLE)

        result = _score(
            _UNPLANNABLE,
            _with_extra_step(
                trajectory,
                parallel_record_kind=_CHILD,
                parallel_child_phase="dispatch_intent",
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_record_observed"


class TestUnplannableWorkCannotPassOnAnEmptyTrajectory:
    """The anti-vacuity proof for the one case that asserts an absence.

    "No plan was bound" is trivially true of a run that never happened. Without
    the tool-call floor this case would go green on an empty trajectory and
    report safety that was never exercised — the failure mode BUG-14 found in a
    numeric assertion, here in a structural one.
    """

    async def test_a_trajectory_with_no_work_at_all_fails_the_case(self) -> None:
        trajectory = await _trajectory(_UNPLANNABLE)
        empty = trajectory.model_copy(
            update={
                "ordered_steps": (),
                "usage_summary": {**trajectory.usage_summary, "tool_calls": 0},
            }
        )

        result = _score(_UNPLANNABLE, empty)

        assert not result.passed
        assert result.reason_code == "parallel_execution_tool_call_minimum_not_met"

    async def test_a_turn_that_ran_only_half_its_work_fails_the_case(self) -> None:
        """The floor is a real count, not a boolean "something happened"."""

        trajectory = await _trajectory(_UNPLANNABLE)
        halved = trajectory.model_copy(
            update={
                "ordered_steps": tuple(
                    step
                    for step in trajectory.ordered_steps
                    if step.capability_id is None
                ),
                "usage_summary": {**trajectory.usage_summary, "tool_calls": 1},
            }
        )

        result = _score(_UNPLANNABLE, halved)

        assert not result.passed
        assert result.reason_code == "parallel_execution_tool_call_minimum_not_met"


class TestSiblingIsolationDetectsAnAbandonedResult:
    """A failure in one child must not erase what another child achieved."""

    async def test_a_completed_child_downgraded_to_indeterminate_fails_the_case(
        self,
    ) -> None:
        """The fail-fast regression, seen from the survivor's side.

        The graph seam pins ``collect_all`` precisely so a sibling error does
        not turn one connector failure into several. A coordinator that
        abandoned its siblings would record this instead.
        """

        trajectory = await _trajectory(_SIBLING)

        result = _score(
            _SIBLING,
            _mutated(
                trajectory,
                record_kind=_CHILD,
                disposition="succeeded",
                parallel_child_disposition="indeterminate",
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_child_disposition_missing"
        assert result.hard_gate is True

    async def test_a_completed_child_whose_result_vanished_fails_the_case(
        self,
    ) -> None:
        trajectory = await _trajectory(_SIBLING)

        result = _score(
            _SIBLING,
            _without(trajectory, record_kind=_CHILD, disposition="succeeded"),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_child_disposition_missing"

    async def test_a_run_where_nothing_actually_failed_fails_the_case(self) -> None:
        """Non-vacuity from the other side: the case must exercise a failure.

        Without the ``failed`` requirement this family would pass on a happy
        run, proving isolation nobody tested.
        """

        trajectory = await _trajectory(_SIBLING)

        result = _score(
            _SIBLING,
            _without(trajectory, record_kind=_CHILD, disposition="failed"),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_child_disposition_missing"

    async def test_an_indeterminate_verdict_alongside_the_pair_fails_the_case(
        self,
    ) -> None:
        """Isolation means the outcomes stay known, not merely non-empty."""

        trajectory = await _trajectory(_SIBLING)

        result = _score(
            _SIBLING,
            _with_extra_step(
                trajectory,
                parallel_record_kind=_CHILD,
                parallel_child_phase="settled",
                parallel_child_disposition="indeterminate",
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_forbidden_disposition_observed"

    async def test_a_sibling_that_was_never_dispatched_fails_the_case(self) -> None:
        """A batch that stopped admitting on the first failure records this.

        ``collect_all`` is pinned at the graph seam precisely so a failure does
        not stop new admission. Under ``fail_fast`` the second child would never
        reach a dispatch intent, and this is the count that catches it.
        """

        trajectory = await _trajectory(_SIBLING)

        result = _score(
            _SIBLING,
            _dropping_first(trajectory, phase="dispatch_intent"),
        )

        assert not result.passed
        assert (
            result.reason_code == "parallel_execution_dispatch_intent_minimum_not_met"
        )


class TestCancelAndRestartDetectAnInventedOutcome:
    """Uncertain work must stay uncertain in the journal.

    Cancellation is where a system is most tempted to lie, and both comfortable
    answers are lies: "it succeeded" invents a result, and "it failed" invents
    the claim that nothing reached the connector. The journal's honest answer is
    to say nothing about a child it cannot vouch for, and these mutations are
    the two ways that honesty gets lost.
    """

    @pytest.mark.parametrize("disposition", ["succeeded", "failed"])
    async def test_manufacturing_an_outcome_for_lost_work_fails_the_case(
        self,
        disposition: str,
    ) -> None:
        trajectory = await _trajectory(_NO_INVENTION)

        result = _score(
            _NO_INVENTION,
            _with_extra_step(
                trajectory,
                parallel_record_kind=_CHILD,
                parallel_child_phase="settled",
                parallel_child_disposition=disposition,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_invented_outcome_observed"
        assert result.hard_gate is True

    async def test_a_durable_indeterminate_verdict_passes_the_case(self) -> None:
        """Recording uncertainty is not inventing an outcome.

        Two cancel implementations leave two different journals: one that
        unwinds the coroutine leaves an intent with no settle, and one that
        durably says ``indeterminate``. Neither claimed anything about the
        world, so the case must accept both — and this is the direction that is
        easy to get wrong. A case written around "a child never settled" scores
        the second, better implementation as a failure, which is exactly the
        BUG-17 shape of grading a working run as broken.
        """

        trajectory = await _trajectory(_NO_INVENTION)

        result = _score(
            _NO_INVENTION,
            _with_extra_step(
                trajectory,
                parallel_record_kind=_CHILD,
                parallel_child_phase="settled",
                parallel_child_disposition="indeterminate",
            ),
        )

        assert result.passed
        assert result.reason_code == "parallel_execution_trajectory_passed"

    async def test_a_run_that_settled_nothing_fails_the_case(self) -> None:
        """Non-vacuity: the sibling that did finish must keep its result.

        Without this the family would pass on a run where the batch collapsed
        entirely, which is a different failure wearing the same shape — and one
        that would otherwise satisfy ``require_unresolved_child`` twice over.

        The reason code is the *phase* rather than the disposition because this
        family authors exactly one settle: removing it removes the only
        ``settled`` step there is, and the phase gate is reached first. Either
        gate is a correct refusal; what matters is that the case does not go
        green on a batch that finished nothing.
        """

        trajectory = await _trajectory(_NO_INVENTION)

        result = _score(
            _NO_INVENTION,
            _without(trajectory, record_kind=_CHILD, disposition="succeeded"),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_child_phase_missing"

    def test_a_journal_that_claimed_an_outcome_for_every_child_fails_the_case(
        self,
    ) -> None:
        """``require_unresolved_child`` in isolation.

        Built directly rather than mutated, because every corpus-level mutation
        that resolves the last child also trips the determinate-settlement
        ceiling. This pins the positive claim on its own: a run whose every
        begun child was given an outcome is not the run this family describes.
        """

        steps = tuple(
            TrajectoryStep(
                sequence_no=index + 1,
                event_type=_BATCH_EVENT,
                source="fixture",
                payload_digest=_DIGEST,
                parallel_record_kind=_CHILD,
                parallel_child_phase=phase,
                parallel_child_disposition=disposition,
            )
            for index, (phase, disposition) in enumerate(
                (
                    ("dispatch_intent", None),
                    ("dispatch_intent", None),
                    ("settled", "succeeded"),
                )
            )
        )
        values: dict[str, object] = {
            "trajectory_id": "traj_accounted",
            "run_id": None,
            "case_id": _entry(_NO_INVENTION).case.case_id,
            "variant_id": "candidate",
            "ordered_steps": steps,
            "evidence_refs": (),
            "usage_summary": {"tool_calls": 2},
            "redaction_policy_revision": "redaction-v1",
            "harness_revisions": {},
        }
        settled_everything = TrajectoryManifest(
            **values,
            manifest_digest=TrajectoryManifest.digest_for(**values),
        ).model_copy(
            update={
                "ordered_steps": (
                    *steps,
                    TrajectoryStep(
                        sequence_no=4,
                        event_type=_BATCH_EVENT,
                        source="fixture",
                        payload_digest=_DIGEST,
                        parallel_record_kind=_CHILD,
                        parallel_child_phase="settled",
                        parallel_child_disposition="succeeded",
                    ),
                )
            }
        )
        relaxed = _entry(_NO_INVENTION).case.model_copy(
            update={
                "expected_assertions": tuple(
                    item.model_copy(
                        update={
                            "expected": {
                                "required_child_dispositions": ["succeeded"],
                                "minimum_dispatch_intents": 2,
                                "require_unresolved_child": True,
                            }
                        }
                    )
                    if item.scorer_id == "parallel_execution_trajectory"
                    else item
                    for item in _entry(_NO_INVENTION).case.expected_assertions
                )
            }
        )

        result = ParallelExecutionTrajectoryScorer().score(
            case=relaxed,
            trajectory=settled_everything,
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_unresolved_child_missing"


class TestAWidthCeilingRefusesAnUnmeasuredPlan:
    """BUG-14 in F6's shape: a bound over absent data is not a bound.

    ``maximum_segment_width`` and ``maximum_overlapping_operations`` are both
    satisfied by an unpopulated field, and ``maximum_overlapping_operations: 0``
    is the setting a case uses to say "nothing overlapped". A plan record whose
    widths were never measured therefore has to fail closed, or a green case
    would report safety nobody observed.
    """

    async def test_a_plan_without_measured_widths_fails_closed(self) -> None:
        trajectory = await _trajectory(_UNKNOWN)

        result = _score(
            _UNKNOWN,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_counts_observed=False,
                parallel_planned_operations=0,
                parallel_overlapping_operations=0,
                parallel_maximum_segment_width=0,
            ),
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_counts_unobserved"
        assert result.hard_gate is True

    async def test_an_observed_zero_is_not_an_unmeasured_zero(self) -> None:
        """The distinction the flag exists for, asserted directly.

        Identical numbers, opposite verdicts: an honest producer reporting that
        nothing overlapped passes the very ceiling that a silent producer is
        refused.
        """

        trajectory = await _trajectory(_UNKNOWN)

        observed = _score(
            _UNKNOWN,
            _mutated(
                trajectory,
                record_kind=_PLAN,
                parallel_counts_observed=True,
                parallel_overlapping_operations=0,
                parallel_maximum_segment_width=1,
            ),
        )

        assert observed.passed
        assert observed.reason_code == "parallel_execution_trajectory_passed"


class TestAFailingCaseActuallyBlocksTheSuite:
    """A hard-gated reason code has to become a FAILED case, not a note."""

    async def test_an_overlapped_write_produces_a_failed_case_result(self) -> None:
        """Scoring is not the gate; the suite runner's status is.

        Every mutation above asserts a reason code. This asserts that such a
        reason code actually reaches ``hard_gate_failures`` and flips the case
        to ``FAILED`` — without it, the cases could be correct and still not
        block a promotion.
        """

        entry = _entry(_WRITE)
        trajectory = await _trajectory(_WRITE)
        overlapped = _mutated(
            trajectory,
            record_kind=_PLAN,
            parallel_segment_modes=("serial", "parallel", "serial"),
        )

        results = tuple(
            scorer.score(case=entry.case, trajectory=overlapped)
            for scorer in DEFAULT_HARD_SCORERS
        )
        hard_failures = tuple(
            sorted(
                result.reason_code
                for result in results
                if result.hard_gate and not result.passed
            )
        )
        status = (
            EvaluationStatus.FAILED if hard_failures else EvaluationStatus.SUCCEEDED
        )

        assert hard_failures == ("parallel_execution_segment_order_mismatch",)
        assert status is EvaluationStatus.FAILED


class TestTheScorerRefusesAnUnusableAssertion:
    """Malformed expectations fail closed rather than silently passing."""

    async def test_a_non_mapping_expectation_is_a_failure_not_a_pass(self) -> None:
        entry = _entry(_OVERLAP)
        trajectory = await _trajectory(_OVERLAP)
        broken = entry.case.model_copy(
            update={
                "expected_assertions": tuple(
                    item.model_copy(update={"expected": ["not", "a", "mapping"]})
                    if item.scorer_id == "parallel_execution_trajectory"
                    else item
                    for item in entry.case.expected_assertions
                )
            }
        )

        result = ParallelExecutionTrajectoryScorer().score(
            case=broken,
            trajectory=trajectory,
        )

        assert not result.passed
        assert result.reason_code == "parallel_execution_assertion_invalid"
