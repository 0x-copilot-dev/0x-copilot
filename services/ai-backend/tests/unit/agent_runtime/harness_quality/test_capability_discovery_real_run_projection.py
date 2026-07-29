"""F3.8 — exactly how much of the F1 discovery criterion survives a real run.

Step 8's exit list says "F1 proves selection recall and end-to-end quality".
Lane F3.7 built the three cases and proved each of them bites against a
deliberately broken trajectory, but every one of those proofs runs through
``FixtureOnlyCaseExecutor`` over a corpus that *authors* its own observations.
The question this module answers is the one that is left: when the same case is
scored over a trajectory projected from **real runtime events**, which of its
assertions is still doing work?

The answer is: **one case of the three**, and only its non-numeric half.
``TrajectoryProjector`` carries ``phase`` and ``outcome_code`` off a real
``quality.decision.v1`` row and nothing else, because that event family has no
numeric field.  Measured consequences, each asserted below:

* ``capability_discovery_unauthorized_probe`` — the security case — **is**
  runnable against real events. Its whole assertion set is expressible in phase
  and outcome, and a leak really is caught: one bridge tool answering ``ok``
  fails it from runtime rows alone.
* ``capability_discovery_selection_recall`` **and**
  ``capability_discovery_end_to_end`` are **not** runnable against real events.
  Both declare ``minimum_recall_rank: 1``; a real projection reports rank ``0``
  on every search step, so the scorer reads a *working* system as a recall miss.
  The failure direction is safe, but both cases are fixture-only today.
* the probe case's two numeric ceilings (``maximum_recall_rank: 0``,
  ``maximum_candidate_count: 0``) **pass without checking anything** on a real
  run, because a bound of zero over an unpopulated field is satisfied by the
  absence of data. The case is still sound — its security value sits in the
  phase/outcome half — but nobody should read a green probe as evidence that the
  search returned no candidates.

So Step 8's "F1 proves selection recall and end-to-end quality" is today met by
*authored fixtures* rather than by measurement.  Every statement above flips the
moment the numeric fields become projectable, which is the moment to revisit the
verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_runtime.harness_quality.evaluation import (
    FixtureToolExecutor,
    TrajectoryProjector,
)
from agent_runtime.harness_quality.evaluation_contracts import (
    HarnessVariant,
    TrajectoryManifest,
)
from agent_runtime.harness_quality.operational_corpus import operational_corpus
from agent_runtime.harness_quality.scoring import CapabilityDiscoveryTrajectoryScorer
from agent_runtime.harness_quality.suite_execution import FixtureOnlyCaseExecutor
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)
from runtime_api.schemas.events import QualityDecisionPayload


_RECALL = "capability_discovery_selection_recall"
_PROBE = "capability_discovery_unauthorized_probe"
_END_TO_END = "capability_discovery_end_to_end"
_SEARCH = "capability_search"
_DESCRIBE = "capability_describe"
_INVOKE = "capability_invoke"
_DECISION_EVENT = "quality.decision.v1"
_DIGEST = "a" * 64


def _variant() -> HarnessVariant:
    return HarnessVariant(
        variant_id="candidate",
        revision="variant-v1",
        prompt_plan_revision="prompt-v1",
        capability_policy_revision="capability-v1",
        context_policy_revision="context-v1",
        model_route_revision="model-v1",
    )


def _decision_event(
    *,
    sequence_no: int,
    phase: str,
    outcome_code: str,
    feature: str = "f3",
) -> RuntimeEventEnvelope:
    """One ``quality.decision.v1`` envelope shaped exactly as the journal writes.

    The payload is built by the closed
    :class:`~runtime_api.schemas.events.QualityDecisionPayload` contract and
    dumped the same way ``RunControlEventJournal._decision_payload`` dumps it, so
    what is projected below is the real row rather than a hand-written mapping
    that happens to have the right keys.  That contract is flat, body-free, and
    carries **no numeric field at all** — which is the whole point of this
    module.
    """

    payload = QualityDecisionPayload(
        schema_version=1,
        decision_id=f"decision_{sequence_no}",
        decision_digest=_DIGEST,
        snapshot_id="snapshot_f38",
        phase=phase,
        feature=feature,
        policy_revision="capability-v1",
        input_digest=_DIGEST,
        outcome_code=outcome_code,
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
    ).model_dump(mode="json")
    return RuntimeEventEnvelope(
        run_id="run_f38",
        conversation_id="conv_f38",
        trace_id="trace_f38",
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.QUALITY_DECISION,
        activity_kind=RuntimeActivityKind.EVENT,
        sequence_no=sequence_no,
        payload=payload,
    )


def _projected(*events: RuntimeEventEnvelope) -> TrajectoryManifest:
    """Project real runtime envelopes through the production F1 projector."""

    return TrajectoryProjector(redaction_policy_revision="redaction-v1").project(
        run_id="run_f38",
        variant_id="candidate",
        events=events,
    )


def _entry(family: str):  # type: ignore[no-untyped-def]
    return next(item for item in operational_corpus() if item.family == family)


def _case(family: str):  # type: ignore[no-untyped-def]
    return _entry(family).case


def _score(family: str, trajectory: TrajectoryManifest):  # type: ignore[no-untyped-def]
    return CapabilityDiscoveryTrajectoryScorer().score(
        case=_case(family),
        trajectory=trajectory,
    )


class TestARealDecisionRowProjectsPhaseAndOutcomeOnly:
    """The measurement the rest of this module's verdicts rest on."""

    def test_phase_and_outcome_survive_the_projection(self) -> None:
        trajectory = _projected(
            _decision_event(sequence_no=1, phase=_SEARCH, outcome_code="ok")
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_phase == _SEARCH
        assert step.discovery_outcome == "ok"

    def test_no_numeric_discovery_field_survives_the_projection(self) -> None:
        """The honest limitation, stated as an assertion rather than a comment.

        ``quality.decision.v1`` carries no candidate count, recall rank, result
        token count, or model-turn count, so a real run projects all four as
        their zero default. This is what makes selection recall unmeasurable
        below, and it flips the moment the event family grows a numeric field.
        """

        trajectory = _projected(
            _decision_event(sequence_no=1, phase=_SEARCH, outcome_code="ok")
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_candidate_count == 0
        assert step.discovery_recall_rank == 0
        assert step.discovery_result_tokens == 0
        assert step.discovery_model_turns == 0

    def test_another_features_decision_contributes_nothing(self) -> None:
        """The discriminator, so this is a projection of F3 and not of decisions."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase="task_policy_admission",
                outcome_code="ok",
                feature="f4",
            )
        )

        assert trajectory.ordered_steps[0].discovery_phase is None


class TestTheOutcomeHalfIsRealOnARealRun:
    """What F1 genuinely proves once the fixtures are taken away."""

    def test_a_healthy_real_run_still_passes_the_probe_case(self) -> None:
        """The one discovery case that is runnable against a live cohort.

        Its whole assertion set — required phases, required per-phase outcomes,
        forbidden outcomes — is expressible in phase and ``outcome_code``, which
        is precisely what a real ``quality.decision.v1`` row carries.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1, phase=_SEARCH, outcome_code="capability_not_found"
            ),
            _decision_event(
                sequence_no=2, phase=_DESCRIBE, outcome_code="capability_not_found"
            ),
            _decision_event(
                sequence_no=3, phase=_INVOKE, outcome_code="capability_not_found"
            ),
        )

        result = _score(_PROBE, trajectory)

        assert result.passed, result.reason_code

    def test_a_leaking_probe_fails_the_security_case_on_a_real_run(self) -> None:
        """The property the criterion actually exists for, measured for real.

        One bridge tool answering ``ok`` to an unauthorized name is caught from
        real events alone — no fixture-authored count is involved.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1, phase=_SEARCH, outcome_code="capability_not_found"
            ),
            _decision_event(
                sequence_no=2, phase=_DESCRIBE, outcome_code="capability_not_found"
            ),
            _decision_event(sequence_no=3, phase=_INVOKE, outcome_code="ok"),
        )

        result = _score(_PROBE, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_outcome_mismatch"

    def test_a_probe_that_never_reached_a_phase_fails_on_a_real_run(self) -> None:
        """Required phases are real too: a chain that stopped short is caught."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1, phase=_SEARCH, outcome_code="capability_not_found"
            ),
            _decision_event(
                sequence_no=2, phase=_DESCRIBE, outcome_code="capability_not_found"
            ),
        )

        result = _score(_PROBE, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"


class TestTwoOfTheThreeCasesCannotRunAgainstRealEvents:
    """The criterion's weakest point, pinned rather than glossed.

    Both ``capability_discovery_selection_recall`` and
    ``capability_discovery_end_to_end`` declare ``minimum_recall_rank: 1``.  A
    real projection reports rank ``0`` on every search step, so the scorer reads
    a **working** system as a recall miss.  The failure direction is the safe
    one, but the consequence is blunt: neither case can be evaluated against a
    live cohort today.  They are fixture-only, and Step 8's "F1 proves selection
    recall and end-to-end quality" is met by authored fixtures rather than by
    measurement.
    """

    @pytest.mark.parametrize("family", [_RECALL, _END_TO_END])
    def test_a_perfect_real_run_still_reports_a_recall_miss(self, family: str) -> None:
        trajectory = _projected(
            _decision_event(sequence_no=1, phase=_SEARCH, outcome_code="ok"),
            _decision_event(sequence_no=2, phase=_DESCRIBE, outcome_code="ok"),
            _decision_event(sequence_no=3, phase=_INVOKE, outcome_code="ok"),
        )

        result = _score(family, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_missing"

    @pytest.mark.parametrize("family", [_RECALL, _END_TO_END])
    def test_the_recall_floor_is_what_makes_them_unrunnable(self, family: str) -> None:
        """Named precisely, so the fix is obvious when the numerics land."""

        assertions = {
            assertion.scorer_id: assertion
            for assertion in _case(family).expected_assertions
        }
        expected = assertions["capability_discovery_trajectory"].expected

        assert isinstance(expected, dict)
        assert expected["minimum_recall_rank"] >= 1

    @pytest.mark.parametrize("family", [_RECALL, _END_TO_END])
    async def test_the_same_cases_pass_against_the_authored_corpus(
        self, family: str
    ) -> None:
        """The control: the cases are satisfiable, just not from runtime events.

        This is the fixture path lane F3.7 proved, run here so the failures
        above are attributable to the projection rather than to the cases.
        """

        entry = _entry(family)
        trajectory = await FixtureOnlyCaseExecutor().execute(
            suite_run_id=f"suite_{family}",
            case=entry.case,
            variant=_variant(),
            plan=entry.plan(),
            fixtures=FixtureToolExecutor(entry.fixtures),
            projected_at=datetime(2026, 7, 29, tzinfo=UTC),
        )

        result = _score(family, trajectory)

        assert result.passed, result.reason_code


class TestTheProbeCeilingsAreInertOnARealRun:
    """A bound of zero over an unpopulated field checks nothing."""

    @pytest.mark.parametrize(
        "bound", ["maximum_recall_rank", "maximum_candidate_count"]
    )
    def test_the_probe_numeric_ceilings_are_declared(self, bound: str) -> None:
        """First, that the lines this module calls inert actually exist."""

        assertions = {
            assertion.scorer_id: assertion
            for assertion in _case(_PROBE).expected_assertions
        }
        expected = assertions["capability_discovery_trajectory"].expected

        assert isinstance(expected, dict)
        assert expected[bound] == 0

    def test_they_are_satisfied_by_absent_data_rather_than_by_safety(self) -> None:
        """The sharp form of the limitation.

        A projected search step reports ``0`` candidates and rank ``0`` whether
        the search leaked ten unauthorized names or none. Both ceilings
        therefore pass on any real run, so the probe case's security value comes
        entirely from its phase/outcome half — which
        ``test_a_leaking_probe_fails_the_security_case_on_a_real_run`` shows is
        real. Recorded so nobody reads a green probe case as evidence that the
        search returned no candidates.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1, phase=_SEARCH, outcome_code="capability_not_found"
            ),
            _decision_event(
                sequence_no=2, phase=_DESCRIBE, outcome_code="capability_not_found"
            ),
            _decision_event(
                sequence_no=3, phase=_INVOKE, outcome_code="capability_not_found"
            ),
        )

        result = _score(_PROBE, trajectory)

        assert result.passed, result.reason_code
        step = trajectory.ordered_steps[0]
        assert step.discovery_candidate_count == 0
        assert step.discovery_recall_rank == 0
