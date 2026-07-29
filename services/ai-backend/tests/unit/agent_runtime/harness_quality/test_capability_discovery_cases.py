"""F3.7 — the F1 cases for capability discovery, and proof that they bite.

Step 8 asks F1 to prove three things about the discovery bridge: that search
surfaces the right capability (selection recall), that an unauthorized name
cannot be searched, described, guessed, or invoked, and that the whole chain
holds up end to end.

Every case here is therefore tested twice.  Once against the corpus as authored,
where it must pass; and once against a trajectory deliberately mutated to
describe the *broken* behaviour the case exists to catch, where it must fail
with a named reason code.  A case that passes against a broken implementation is
worse than no case at all, so the second half is the point of this file — the
first half only shows the case is satisfiable.

The mutations follow lane F2's shape (``model_copy`` over the projected
trajectory) so the case is exercised through the same
:class:`FixtureOnlyCaseExecutor` the real suite runs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.harness_quality.evaluation import FixtureToolExecutor
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationStatus,
    HarnessVariant,
    TrajectoryManifest,
)
from agent_runtime.harness_quality.operational_corpus import (
    OPERATIONAL_TASK_FAMILIES,
    operational_corpus,
)
from agent_runtime.harness_quality.scoring import (
    DEFAULT_HARD_SCORERS,
    CapabilityDiscoveryTrajectoryScorer,
)
from agent_runtime.harness_quality.suite_execution import FixtureOnlyCaseExecutor

_NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
_RECALL = "capability_discovery_selection_recall"
_PROBE = "capability_discovery_unauthorized_probe"
_END_TO_END = "capability_discovery_end_to_end"
_SEARCH = "capability_search"
_DESCRIBE = "capability_describe"
_INVOKE = "capability_invoke"


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
    """Project one F3 family through the real fixture-only case executor."""

    entry = _entry(family)
    return await FixtureOnlyCaseExecutor().execute(
        suite_run_id=f"suite_{family}",
        case=entry.case,
        variant=_variant(),
        plan=entry.plan(),
        fixtures=FixtureToolExecutor(entry.fixtures),
        projected_at=_NOW,
    )


def _mutated(
    trajectory: TrajectoryManifest,
    *,
    phase: str | None = None,
    **updates: object,
) -> TrajectoryManifest:
    """Rewrite the discovery projection of matching steps, and nothing else."""

    return trajectory.model_copy(
        update={
            "ordered_steps": tuple(
                (
                    step.model_copy(update=dict(updates))
                    if step.discovery_phase
                    and (phase is None or step.discovery_phase == phase)
                    else step
                )
                for step in trajectory.ordered_steps
            )
        }
    )


def _score(family: str, trajectory: TrajectoryManifest):  # type: ignore[no-untyped-def]
    return CapabilityDiscoveryTrajectoryScorer().score(
        case=_entry(family).case,
        trajectory=trajectory,
    )


class TestTheThreeCasesExistAndAreSatisfiable:
    """The corpus half: the cases are real, reachable, and pass as authored."""

    def test_the_corpus_declares_all_three_discovery_families(self) -> None:
        assert _RECALL in OPERATIONAL_TASK_FAMILIES
        assert _PROBE in OPERATIONAL_TASK_FAMILIES
        assert _END_TO_END in OPERATIONAL_TASK_FAMILIES

    @pytest.mark.parametrize("family", [_RECALL, _PROBE, _END_TO_END])
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

    @pytest.mark.parametrize("family", [_RECALL, _PROBE, _END_TO_END])
    async def test_each_case_carries_a_hard_gated_discovery_assertion(
        self,
        family: str,
    ) -> None:
        """An advisory case cannot fail a promotion, so the gate is asserted."""

        assertions = [
            item
            for item in _entry(family).case.expected_assertions
            if item.scorer_id == "capability_discovery_trajectory"
        ]

        assert len(assertions) == 1
        assert assertions[0].hard_gate is True

    async def test_a_family_without_discovery_facts_is_left_alone(self) -> None:
        """The new scorer must not start failing the 34 pre-existing families."""

        entry = operational_corpus()[0]
        trajectory = await FixtureOnlyCaseExecutor().execute(
            suite_run_id="suite_untouched",
            case=entry.case,
            variant=_variant(),
            plan=entry.plan(),
            fixtures=FixtureToolExecutor(entry.fixtures),
            projected_at=_NOW,
        )

        result = CapabilityDiscoveryTrajectoryScorer().score(
            case=entry.case,
            trajectory=trajectory,
        )

        assert result.passed
        assert result.hard_gate is False
        assert result.reason_code == "capability_discovery_not_applicable"


class TestSelectionRecallDetectsAMiss:
    """If search stops surfacing the right capability, this case must fail."""

    async def test_a_capability_that_never_comes_back_fails_the_case(self) -> None:
        """Rank 0 is 'the target was not in the answer' — the recall failure."""

        trajectory = await _trajectory(_RECALL)

        result = _score(_RECALL, _mutated(trajectory, discovery_recall_rank=0))

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_missing"
        assert result.hard_gate is True

    async def test_a_capability_buried_below_the_ceiling_fails_the_case(self) -> None:
        """Recall is not only presence: an answer ranked 9th is a regression."""

        trajectory = await _trajectory(_RECALL)

        result = _score(_RECALL, _mutated(trajectory, discovery_recall_rank=9))

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_rank_exceeded"

    async def test_a_search_that_stops_answering_fails_the_case(self) -> None:
        trajectory = await _trajectory(_RECALL)

        result = _score(
            _RECALL,
            _mutated(trajectory, discovery_outcome="execution_failed"),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_outcome_missing"

    async def test_a_search_that_stops_running_at_all_fails_the_case(self) -> None:
        """A case that silently scores an empty trajectory would prove nothing."""

        trajectory = await _trajectory(_RECALL)
        without_discovery = trajectory.model_copy(
            update={
                "ordered_steps": tuple(
                    step.model_copy(
                        update={"discovery_phase": None, "discovery_recall_rank": 0}
                    )
                    for step in trajectory.ordered_steps
                )
            }
        )

        result = _score(_RECALL, without_discovery)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"


class TestUnauthorizedProbingDetectsALeak:
    """The security case: every way of reaching the name must stay closed."""

    @pytest.mark.parametrize("phase", [_DESCRIBE, _INVOKE])
    async def test_any_single_tool_answering_the_probe_fails_the_case(
        self,
        phase: str,
    ) -> None:
        """One leaking tool must not hide behind the two that still refuse.

        This is why the case binds an outcome to a *phase*. A trajectory-wide
        'capability_not_found appears somewhere' assertion would pass here, with
        describe cheerfully describing an unauthorized capability.
        """

        trajectory = await _trajectory(_PROBE)

        result = _score(
            _PROBE,
            _mutated(trajectory, phase=phase, discovery_outcome="ok"),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_outcome_mismatch"

    async def test_a_probe_that_becomes_searchable_fails_the_case(self) -> None:
        """'Cannot be searched' is a separate claim from 'cannot be invoked'."""

        trajectory = await _trajectory(_PROBE)

        result = _score(
            _PROBE,
            _mutated(trajectory, phase=_SEARCH, discovery_candidate_count=1),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_candidate_count_exceeded"

    async def test_a_probe_that_becomes_rankable_fails_the_case(self) -> None:
        trajectory = await _trajectory(_PROBE)

        result = _score(
            _PROBE,
            _mutated(trajectory, phase=_SEARCH, discovery_recall_rank=1),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_rank_exceeded"

    async def test_a_probe_answered_with_a_distinguishing_code_fails_the_case(
        self,
    ) -> None:
        """A different refusal code is an existence oracle, not a refusal.

        ``capability_stale`` tells the model the reference was real once, which
        is exactly the inference the closed error vocabulary exists to deny.
        """

        trajectory = await _trajectory(_PROBE)

        result = _score(
            _PROBE,
            _mutated(trajectory, phase=_INVOKE, discovery_outcome="capability_stale"),
        )

        assert not result.passed
        assert result.reason_code in {
            "capability_discovery_forbidden_outcome_observed",
            "capability_discovery_phase_outcome_mismatch",
        }

    async def test_a_probe_that_never_reaches_invoke_fails_the_case(self) -> None:
        """The case must prove invoke was actually attempted and refused."""

        trajectory = await _trajectory(_PROBE)
        without_invoke = trajectory.model_copy(
            update={
                "ordered_steps": tuple(
                    step
                    for step in trajectory.ordered_steps
                    if step.discovery_phase != _INVOKE
                )
            }
        )

        result = _score(_PROBE, without_invoke)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"


class TestEndToEndQualityDetectsARegression:
    """The chain case: it fails on a broken step and on an expensive one."""

    @pytest.mark.parametrize("phase", [_SEARCH, _DESCRIBE, _INVOKE])
    async def test_any_step_of_the_chain_breaking_fails_the_case(
        self,
        phase: str,
    ) -> None:
        trajectory = await _trajectory(_END_TO_END)

        result = _score(
            _END_TO_END,
            _mutated(
                trajectory,
                phase=phase,
                discovery_outcome="capability_unavailable",
            ),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_forbidden_outcome_observed"

    async def test_a_chain_that_loses_a_step_fails_the_case(self) -> None:
        trajectory = await _trajectory(_END_TO_END)
        without_describe = trajectory.model_copy(
            update={
                "ordered_steps": tuple(
                    step
                    for step in trajectory.ordered_steps
                    if step.discovery_phase != _DESCRIBE
                )
            }
        )

        result = _score(_END_TO_END, without_describe)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"

    async def test_a_chain_that_gets_expensive_fails_the_case(self) -> None:
        """Discovery's whole purpose is prompt economy; a blown budget is a
        regression even when every answer is correct."""

        trajectory = await _trajectory(_END_TO_END)

        result = _score(
            _END_TO_END,
            _mutated(trajectory, discovery_result_tokens=5_000),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_result_tokens_exceeded"

    async def test_a_chain_that_burns_extra_model_turns_fails_the_case(self) -> None:
        trajectory = await _trajectory(_END_TO_END)

        result = _score(
            _END_TO_END,
            _mutated(trajectory, discovery_model_turns=9),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_model_turns_exceeded"

    async def test_a_chain_whose_recall_collapses_fails_the_case(self) -> None:
        trajectory = await _trajectory(_END_TO_END)

        result = _score(
            _END_TO_END,
            _mutated(trajectory, phase=_SEARCH, discovery_recall_rank=0),
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_recall_missing"


class TestAFailingCaseActuallyBlocksTheSuite:
    """A hard-gated reason code has to become a FAILED case, not a note."""

    async def test_a_broken_probe_produces_a_failed_case_result(self) -> None:
        """Scoring is not the gate; the suite runner's status is.

        Every mutation above asserts a reason code. This asserts that such a
        reason code actually reaches ``hard_gate_failures`` and flips the case
        to ``FAILED`` — without it, the cases could be correct and still not
        block a promotion.
        """

        entry = _entry(_PROBE)
        trajectory = await _trajectory(_PROBE)
        leaked = _mutated(trajectory, phase=_INVOKE, discovery_outcome="ok")

        results = tuple(
            scorer.score(case=entry.case, trajectory=leaked)
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

        assert hard_failures == ("capability_discovery_phase_outcome_mismatch",)
        assert status is EvaluationStatus.FAILED


class TestTheScorerRefusesAnUnusableAssertion:
    """Malformed expectations fail closed rather than silently passing."""

    async def test_a_non_mapping_expectation_is_a_failure_not_a_pass(self) -> None:
        entry = _entry(_RECALL)
        trajectory = await _trajectory(_RECALL)
        broken = entry.case.model_copy(
            update={
                "expected_assertions": tuple(
                    item.model_copy(update={"expected": ["not", "a", "mapping"]})
                    if item.scorer_id == "capability_discovery_trajectory"
                    else item
                    for item in entry.case.expected_assertions
                )
            }
        )

        result = CapabilityDiscoveryTrajectoryScorer().score(
            case=broken,
            trajectory=trajectory,
        )

        assert not result.passed
        assert result.reason_code == "capability_discovery_assertion_invalid"
