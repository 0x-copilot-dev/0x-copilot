"""F3.8 — how much of the F1 discovery criterion survives a real run.

Step 8's exit list says "F1 proves selection recall and end-to-end quality".
Lane F3.7 built the three cases and proved each of them bites against a
deliberately broken trajectory, but every one of those proofs runs through
``FixtureOnlyCaseExecutor`` over a corpus that *authors* its own observations.
The question this module answers is the one that is left: when the same case is
scored over a trajectory projected from **real runtime events**, which of its
assertions is still doing work?

**This module previously answered "one case of the three, and only its
non-numeric half".**  That answer was correct, and it was pinned here as BUG-14:
``quality.decision.v1`` carried no numeric field, so a real projection reported
recall rank ``0`` on every step and both ``capability_discovery_selection_recall``
and ``capability_discovery_end_to_end`` scored a *working* system as
``capability_discovery_recall_missing``.  The probe case's two numeric
ceilings passed on *absent* data rather than on observed safety.

The decision row now carries a bounded numeric extension — ``candidate_count``,
``selection_rank``, ``result_tokens``, ``model_turns`` — so every one of those
statements has flipped.  **The tests that pinned the defect are inverted below,
not deleted**: each one now asserts the opposite of what it used to, and says
which assertion it replaces.  What holds now:

* all three cases are runnable against real events, and a working system
  **passes** all three;
* each case still **fails** on a real run that is broken in the way the case
  exists to catch, by named reason code;
* the probe's ``maximum_recall_rank: 0`` and ``maximum_candidate_count: 0`` are
  live rather than inert — a leaking search fails them from runtime rows alone;
* a numeric bound over a row that carried *no* measurement fails closed, so a
  green case reports observed safety instead of missing evidence;
* rows written before the extension still validate and still project.
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

#: Sentinel for "this decision carried no numeric measurement at all", which is
#: what every ``quality.decision.v1`` row written before the extension looks
#: like. Distinct from an observed zero, and scored differently.
_UNMEASURED = object()


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
    candidate_count: object = _UNMEASURED,
    selection_rank: object = _UNMEASURED,
    result_tokens: object = _UNMEASURED,
    model_turns: object = _UNMEASURED,
) -> RuntimeEventEnvelope:
    """One ``quality.decision.v1`` envelope shaped exactly as the journal writes.

    The payload is built by the closed
    :class:`~runtime_api.schemas.events.QualityDecisionPayload` contract and
    dumped the same way ``EventJournalRunControlStore._decision_payload`` dumps
    it, so what is projected below is the real row rather than a hand-written
    mapping that happens to have the right keys.

    Numerics left at ``_UNMEASURED`` are omitted from the constructor entirely,
    which is exactly how a row written before the extension reaches a reader.
    """

    numerics = {
        key: value
        for key, value in (
            ("candidate_count", candidate_count),
            ("selection_rank", selection_rank),
            ("result_tokens", result_tokens),
            ("model_turns", model_turns),
        )
        if value is not _UNMEASURED
    }
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
        **numerics,
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


def _healthy_recall_run() -> TrajectoryManifest:
    """A real, working selection-recall run: search, then select the target.

    Two steps rather than one, because recall is a claim about a *selection*
    and a search selects nothing. The search reports how many candidates it
    offered and no rank at all; the describe that names one of them reports the
    position that reference held. A one-step version of this run is what
    BUG-17 was: the case asked for a rank and paid for only the offer.
    """

    return _projected(
        _decision_event(
            sequence_no=1,
            phase=_SEARCH,
            outcome_code="ok",
            candidate_count=4,
            result_tokens=180,
            model_turns=1,
        ),
        _decision_event(
            sequence_no=2,
            phase=_DESCRIBE,
            outcome_code="ok",
            selection_rank=1,
            result_tokens=150,
            model_turns=1,
        ),
    )


def _healthy_end_to_end_run() -> TrajectoryManifest:
    """A real, working search/describe/invoke chain.

    The rank is reported by the **invoke** step rather than the search step,
    which is where a real run learns it: a selection rank is only knowable once
    the model has picked a reference and it can be placed against the search
    that offered it. The recall run above reports it on its *describe* step, so
    between them these two fixtures prove the scorer reads the rank wherever
    along the trajectory the producer honestly knows it — and neither of them
    asks a search to invent one.
    """

    return _projected(
        _decision_event(
            sequence_no=1,
            phase=_SEARCH,
            outcome_code="ok",
            candidate_count=3,
            result_tokens=180,
            model_turns=1,
        ),
        _decision_event(
            sequence_no=2,
            phase=_DESCRIBE,
            outcome_code="ok",
            result_tokens=150,
            model_turns=1,
        ),
        _decision_event(
            sequence_no=3,
            phase=_INVOKE,
            outcome_code="ok",
            selection_rank=1,
            result_tokens=90,
            model_turns=1,
        ),
    )


def _healthy_probe_run() -> TrajectoryManifest:
    """A real, safe probe: the unauthorized name is refused at every phase.

    Every step carries an *observed* zero rather than no measurement, which is
    what lets the two ceilings mean something.
    """

    return _projected(
        *(
            _decision_event(
                sequence_no=ordinal,
                phase=phase,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            )
            for ordinal, phase in enumerate((_SEARCH, _DESCRIBE, _INVOKE), start=1)
        )
    )


class TestARealDecisionRowProjectsItsNumericsNow:
    """The measurement the rest of this module's verdicts rest on."""

    def test_phase_and_outcome_survive_the_projection(self) -> None:
        trajectory = _projected(
            _decision_event(sequence_no=1, phase=_SEARCH, outcome_code="ok")
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_phase == _SEARCH
        assert step.discovery_outcome == "ok"

    def test_every_numeric_discovery_field_survives_the_projection(self) -> None:
        """Inverts ``test_no_numeric_discovery_field_survives_the_projection``.

        That assertion pinned all four counts at their zero default because
        ``quality.decision.v1`` had no numeric field. It does now, so the same
        four are asserted here at the values the row carried.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=4,
                selection_rank=2,
                result_tokens=180,
                model_turns=1,
            )
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_candidate_count == 4
        assert step.discovery_recall_rank == 2
        assert step.discovery_result_tokens == 180
        assert step.discovery_model_turns == 1
        assert step.discovery_counts_observed

    def test_a_row_written_before_the_extension_still_projects(self) -> None:
        """Backward compatibility, as an assertion rather than a claim.

        A row carrying none of the four keys validates, projects, and reports
        its counts as zero — but as *unobserved* zeros, which is what stops a
        numeric bound from passing over it.
        """

        trajectory = _projected(
            _decision_event(sequence_no=1, phase=_SEARCH, outcome_code="ok")
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_candidate_count == 0
        assert step.discovery_recall_rank == 0
        assert step.discovery_result_tokens == 0
        assert step.discovery_model_turns == 0
        assert not step.discovery_counts_observed

    def test_an_observed_zero_is_distinguishable_from_no_measurement(self) -> None:
        """The distinction the probe case's ceilings rest on."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=0,
            )
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_candidate_count == 0
        assert step.discovery_counts_observed

    def test_another_features_decision_contributes_nothing(self) -> None:
        """The discriminator, so this is a projection of F3 and not of decisions."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase="task_policy_admission",
                outcome_code="ok",
                feature="f4",
                candidate_count=9,
                selection_rank=9,
            )
        )

        step = trajectory.ordered_steps[0]
        assert step.discovery_phase is None
        assert step.discovery_candidate_count == 0
        assert step.discovery_recall_rank == 0
        assert not step.discovery_counts_observed


class TestAllThreeCasesRunAgainstRealEvents:
    """Inverts ``TestTwoOfTheThreeCasesCannotRunAgainstRealEvents``.

    That class asserted that a *perfect* real run scored
    ``capability_discovery_recall_missing`` on both the recall and end-to-end
    cases, because their ``minimum_recall_rank: 1`` met a rank the projection
    could never populate. It can now, so the same two cases are asserted here to
    pass — which is the actual defect BUG-14 named.
    """

    def test_a_working_real_run_passes_the_selection_recall_case(self) -> None:
        result = _score(_RECALL, _healthy_recall_run())

        assert result.passed, result.reason_code

    def test_a_working_real_run_passes_the_end_to_end_case(self) -> None:
        result = _score(_END_TO_END, _healthy_end_to_end_run())

        assert result.passed, result.reason_code

    def test_a_working_real_run_passes_the_probe_case(self) -> None:
        result = _score(_PROBE, _healthy_probe_run())

        assert result.passed, result.reason_code

    @pytest.mark.parametrize("family", [_RECALL, _END_TO_END])
    def test_the_recall_floor_is_still_declared(self, family: str) -> None:
        """The floor that made them unrunnable is unchanged — only met now.

        Inverts ``test_the_recall_floor_is_what_makes_them_unrunnable``. The
        assertion is deliberately identical; what changed is that it is no
        longer the reason the cases cannot run.
        """

        assertions = {
            assertion.scorer_id: assertion
            for assertion in _case(family).expected_assertions
        }
        expected = assertions["capability_discovery_trajectory"].expected

        assert isinstance(expected, dict)
        assert expected["minimum_recall_rank"] >= 1

    @pytest.mark.parametrize("family", [_RECALL, _END_TO_END])
    async def test_the_same_cases_still_pass_against_the_authored_corpus(
        self, family: str
    ) -> None:
        """The control: the fixture path lane F3.7 proved is not disturbed."""

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


class TestEachCaseStillBitesOnARealRun:
    """A case that passes against a broken implementation is worse than none.

    Lane F3.7 proved this over authored fixtures. These are the same properties
    measured from real ``quality.decision.v1`` rows, one named mutation each.
    """

    def test_a_search_that_never_returns_the_target_fails_recall(self) -> None:
        """The miss selection recall exists to catch: rank never becomes positive.

        The search still answers and still offers candidates; what the run
        selected simply was not among them, so the describe reports an observed
        rank of zero. Zero is a measurement here, not a gap.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=4,
                result_tokens=180,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="ok",
                selection_rank=0,
                result_tokens=150,
                model_turns=1,
            ),
        )

        result = _score(_RECALL, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_missing"

    def test_a_target_buried_below_the_ceiling_fails_recall(self) -> None:
        """A ranker regression: the target comes back, but too far down."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=9,
                result_tokens=180,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="ok",
                selection_rank=7,
                result_tokens=150,
                model_turns=1,
            ),
        )

        result = _score(_RECALL, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_rank_exceeded"

    def test_a_search_that_answers_expensively_fails_recall(self) -> None:
        """The cost half of the case, measured rather than authored.

        Every other assertion the case makes passes here — the chain ran, the
        outcomes are ``ok``, the target came back first — and the case still
        fails, on the one step whose answer blew the budget.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=4,
                result_tokens=4_000,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="ok",
                selection_rank=1,
                result_tokens=150,
                model_turns=1,
            ),
        )

        result = _score(_RECALL, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_result_tokens_exceeded"

    def test_a_search_that_never_selects_anything_fails_recall(self) -> None:
        """The gate BUG-17 added: a lone search is not a recall run.

        A search that stops without describing anything has measured no
        selection, so there is no recall to grade. Before BUG-17 this was the
        *only* trajectory the case's one-turn ceiling admitted, which is why it
        could never go green on a real run.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=4,
                result_tokens=180,
                model_turns=1,
            )
        )

        result = _score(_RECALL, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"

    def test_a_leaking_probe_fails_the_security_case_on_a_real_run(self) -> None:
        """One bridge tool answering ``ok`` to an unauthorized name is caught."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=3,
                phase=_INVOKE,
                outcome_code="ok",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
        )

        result = _score(_PROBE, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_outcome_mismatch"

    def test_a_probe_that_never_reached_a_phase_fails_on_a_real_run(self) -> None:
        """Required phases are real too: a chain that stopped short is caught."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
        )

        result = _score(_PROBE, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"

    def test_a_chain_that_loses_a_step_fails_end_to_end(self) -> None:
        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=3,
                selection_rank=1,
                result_tokens=180,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="ok",
                result_tokens=150,
                model_turns=1,
            ),
        )

        result = _score(_END_TO_END, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"

    def test_a_chain_whose_recall_collapses_fails_end_to_end(self) -> None:
        """The numeric half of end-to-end quality, on real rows."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=3,
                result_tokens=180,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="ok",
                result_tokens=150,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=3,
                phase=_INVOKE,
                outcome_code="ok",
                selection_rank=0,
                result_tokens=90,
                model_turns=1,
            ),
        )

        result = _score(_END_TO_END, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_missing"

    def test_a_chain_that_gets_expensive_fails_end_to_end(self) -> None:
        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="ok",
                candidate_count=3,
                selection_rank=1,
                result_tokens=900,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="ok",
                result_tokens=150,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=3,
                phase=_INVOKE,
                outcome_code="ok",
                result_tokens=90,
                model_turns=1,
            ),
        )

        result = _score(_END_TO_END, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_result_tokens_exceeded"


class TestTheProbeCeilingsAreLiveOnARealRun:
    """Inverts ``TestTheProbeCeilingsAreInertOnARealRun``.

    That class asserted that ``maximum_recall_rank: 0`` and
    ``maximum_candidate_count: 0`` were satisfied by absent data, so a green
    probe case was no evidence that the search returned no candidates. Both
    ceilings now read observed values, and each is shown failing on a real run
    that violates it.
    """

    @pytest.mark.parametrize(
        "bound", ["maximum_recall_rank", "maximum_candidate_count"]
    )
    def test_the_probe_numeric_ceilings_are_declared(self, bound: str) -> None:
        """First, that the lines this module now calls live actually exist."""

        assertions = {
            assertion.scorer_id: assertion
            for assertion in _case(_PROBE).expected_assertions
        }
        expected = assertions["capability_discovery_trajectory"].expected

        assert isinstance(expected, dict)
        assert expected[bound] == 0

    def test_a_probe_whose_search_returns_the_name_fails_the_ceiling(self) -> None:
        """The exact reading the old module warned nobody should make.

        A search that leaks the unauthorized name now fails the case from
        runtime rows alone, even though every phase still answered
        ``capability_not_found``. Under the old projection this trajectory
        passed.
        """

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="capability_not_found",
                candidate_count=2,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=3,
                phase=_INVOKE,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
        )

        result = _score(_PROBE, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_candidate_count_exceeded"

    def test_a_probe_whose_name_becomes_rankable_fails_the_ceiling(self) -> None:
        """The rank half of the same guarantee."""

        trajectory = _projected(
            _decision_event(
                sequence_no=1,
                phase=_SEARCH,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=1,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=2,
                phase=_DESCRIBE,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
            _decision_event(
                sequence_no=3,
                phase=_INVOKE,
                outcome_code="capability_not_found",
                candidate_count=0,
                selection_rank=0,
                result_tokens=40,
                model_turns=1,
            ),
        )

        result = _score(_PROBE, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_rank_exceeded"


class TestANumericBoundRefusesToGradeAbsentData:
    """Passing because nothing was observed is not passing because nothing
    unsafe happened.

    This is the second half of the ``unauthorized_probe`` weakness the old
    module recorded. Making the ceilings read real values is not sufficient on
    its own: a producer that emits no numerics at all would leave every bound
    trivially satisfied again. The scorer therefore fails closed.
    """

    @pytest.mark.parametrize(
        ("family", "outcome_code"),
        [
            (_RECALL, "ok"),
            (_PROBE, "capability_not_found"),
            (_END_TO_END, "ok"),
        ],
    )
    def test_a_case_scores_unobserved_rather_than_passing(
        self, family: str, outcome_code: str
    ) -> None:
        """Each family's own healthy outcome, so only the numerics are absent.

        Using the outcome the case expects is what makes this a test of the
        numeric refusal rather than of the phase/outcome half: every non-numeric
        assertion passes here, and the case still refuses to go green.
        """

        trajectory = _projected(
            *(
                _decision_event(
                    sequence_no=ordinal,
                    phase=phase,
                    outcome_code=outcome_code,
                )
                for ordinal, phase in enumerate((_SEARCH, _DESCRIBE, _INVOKE), start=1)
            )
        )

        result = _score(family, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_counts_unobserved"

    def test_one_measured_step_is_enough_to_grade_the_trajectory(self) -> None:
        """The bound is refused only when *nothing* was measured."""

        result = _score(_PROBE, _healthy_probe_run())

        assert result.passed, result.reason_code
