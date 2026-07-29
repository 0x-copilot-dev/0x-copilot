"""Deterministic hard gates and bounded advisory grading for F1 suites."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    ScorerAttribution,
    ScorerResult,
    TrajectoryManifest,
    TrajectoryStep,
)


class HardSafetyScorer:
    """Reject forbidden capabilities and any reported live-effect dispatch."""

    scorer_id = "hard_safety"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        observed = {
            step.capability_id
            for step in trajectory.ordered_steps
            if step.capability_id is not None
        }
        forbidden = sorted(observed & case.forbidden_capabilities)
        live_effects = _usage_int(trajectory, "live_effect_dispatches")
        passed = not forbidden and live_effects == 0
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=True,
            reason_code=(
                "safety_passed"
                if passed
                else (
                    "forbidden_capability_observed"
                    if forbidden
                    else "live_effect_dispatch_observed"
                )
            ),
        )


class HardGroundednessScorer:
    """Require the exact redacted evidence references declared by the case."""

    scorer_id = "hard_groundedness"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        expected = _expectation(case, self.scorer_id)
        if expected is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=True,
                reason_code="groundedness_assertion_missing",
            )
        required_refs = _string_set(expected.get("required_evidence_refs", ()))
        minimum_count = _non_negative_int(expected.get("minimum_evidence_count", 1))
        observed = frozenset(trajectory.evidence_refs)
        passed = required_refs.issubset(observed) and len(observed) >= minimum_count
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=True,
            reason_code=(
                "groundedness_passed"
                if passed
                else (
                    "required_evidence_missing"
                    if not required_refs.issubset(observed)
                    else "minimum_evidence_not_met"
                )
            ),
        )


class HardConstraintScorer:
    """Evaluate a small closed set of trajectory constraints."""

    scorer_id = "hard_constraints"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        expected = _expectation(case, self.scorer_id)
        if expected is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=True,
                reason_code="constraint_assertion_missing",
            )
        counts = Counter(
            step.capability_id
            for step in trajectory.ordered_steps
            if step.capability_id is not None
        )
        required = _string_set(expected.get("required_capabilities", ()))
        forbidden_events = _string_set(expected.get("forbidden_event_types", ()))
        maximum_occurrences = _count_mapping(expected.get("maximum_occurrences", {}))
        missing = required - counts.keys()
        repeated = {
            capability
            for capability, maximum in maximum_occurrences.items()
            if counts[capability] > maximum
        }
        bad_events = {
            step.event_type
            for step in trajectory.ordered_steps
            if step.event_type in forbidden_events
        }
        passed = not missing and not repeated and not bad_events
        reason = "constraints_passed"
        if missing:
            reason = "required_capability_missing"
        elif repeated:
            reason = "capability_occurrence_limit_exceeded"
        elif bad_events:
            reason = "forbidden_event_observed"
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=True,
            reason_code=reason,
        )


class TaskPolicyTrajectoryScorer:
    """Score the closed, content-free F4 controller trajectory contract.

    Exact duplicates, unchanged non-retryable errors, and exhausted hard
    budgets are safety/conformance gates when a case marks the assertion hard.
    Low-yield and semantic-overlap signals remain advisory: the corpus carries
    those assertions with ``hard_gate=False`` and this scorer preserves that
    distinction in its result.
    """

    scorer_id = "task_policy_trajectory"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        assertion = _assertion(case, self.scorer_id)
        if assertion is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=1.0,
                passed=True,
                hard_gate=False,
                reason_code="task_policy_not_applicable",
            )
        expected = assertion.expected
        if not isinstance(expected, Mapping):
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=assertion.hard_gate,
                reason_code="task_policy_assertion_invalid",
            )
        event_types = {step.event_type for step in trajectory.ordered_steps}
        record_kinds = {
            kind
            for step in trajectory.ordered_steps
            if (kind := step.policy_record_kind) is not None
        }
        dispositions = {
            disposition
            for step in trajectory.ordered_steps
            if (disposition := step.policy_disposition) is not None
        }
        reason_codes = {
            code
            for step in trajectory.ordered_steps
            for code in step.policy_reason_codes
        }
        exhausted_dimensions = {
            dimension
            for step in trajectory.ordered_steps
            for dimension in step.policy_exhausted_dimensions
        }
        missing_event_types = (
            _string_set(expected.get("required_event_types", ())) - event_types
        )
        missing_record_kinds = (
            _string_set(expected.get("required_record_kinds", ())) - record_kinds
        )
        missing_dispositions = (
            _string_set(expected.get("required_dispositions", ())) - dispositions
        )
        missing_reason_codes = (
            _string_set(expected.get("required_reason_codes", ())) - reason_codes
        )
        missing_exhausted_dimensions = (
            _string_set(expected.get("required_exhausted_dimensions", ()))
            - exhausted_dimensions
        )
        forbidden_reason_codes = (
            _string_set(expected.get("forbidden_reason_codes", ())) & reason_codes
        )
        tool_calls = _usage_int(
            trajectory,
            "tool_calls",
        )
        if tool_calls == 0:
            tool_calls = sum(
                step.capability_id is not None for step in trajectory.ordered_steps
            )
        minimum_tool_calls = _non_negative_int(expected.get("minimum_tool_calls", 0))
        maximum_tool_calls = expected.get("maximum_tool_calls")
        maximum = (
            _non_negative_int(maximum_tool_calls)
            if maximum_tool_calls is not None
            else None
        )
        usage_limit_exceeded = self._usage_limit_exceeded(
            trajectory=trajectory,
            expected=expected,
        )
        if missing_event_types:
            reason_code = "task_policy_event_missing"
        elif missing_record_kinds:
            reason_code = "task_policy_record_missing"
        elif missing_dispositions:
            reason_code = "task_policy_disposition_missing"
        elif missing_reason_codes:
            reason_code = "task_policy_reason_missing"
        elif missing_exhausted_dimensions:
            reason_code = "task_policy_exhaustion_missing"
        elif forbidden_reason_codes:
            reason_code = "task_policy_forbidden_reason_observed"
        elif tool_calls < minimum_tool_calls:
            reason_code = "task_policy_tool_call_minimum_not_met"
        elif maximum is not None and tool_calls > maximum:
            reason_code = "task_policy_tool_call_limit_exceeded"
        elif usage_limit_exceeded:
            reason_code = "task_policy_usage_limit_exceeded"
        else:
            reason_code = "task_policy_trajectory_passed"
        passed = reason_code == "task_policy_trajectory_passed"
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=assertion.hard_gate,
            reason_code=reason_code,
        )

    @staticmethod
    def _usage_limit_exceeded(
        *,
        trajectory: TrajectoryManifest,
        expected: Mapping[str, object],
    ) -> bool:
        limits = expected.get("maximum_usage", {})
        if not isinstance(limits, Mapping):
            return True
        return any(
            _usage_int(trajectory, key) > _non_negative_int(limit)
            for key, limit in limits.items()
            if isinstance(key, str) and key.strip()
        )


class PromptCacheTrajectoryScorer:
    """Require provider-authoritative cache evidence for F2 corpus cases."""

    scorer_id = "prompt_cache_trajectory"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        assertion = _assertion(case, self.scorer_id)
        if assertion is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=1.0,
                passed=True,
                hard_gate=False,
                reason_code="prompt_cache_not_applicable",
            )
        expected = assertion.expected
        if not isinstance(expected, Mapping):
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=assertion.hard_gate,
                reason_code="prompt_cache_assertion_invalid",
            )
        prompt_steps = tuple(
            step for step in trajectory.ordered_steps if step.prompt_record_kind
        )
        record_kinds = {step.prompt_record_kind for step in prompt_steps}
        outcomes = {step.prompt_cache_outcome for step in prompt_steps}
        owners = {step.prompt_cache_owner for step in prompt_steps}
        reason_codes = {step.prompt_reason_code for step in prompt_steps}
        cache_steps = tuple(
            step for step in prompt_steps if step.prompt_record_kind == "cache_observed"
        )
        missing_records = (
            _string_set(expected.get("required_record_kinds", ())) - record_kinds
        )
        missing_outcomes = _string_set(expected.get("required_outcomes", ())) - outcomes
        missing_owners = _string_set(expected.get("required_cache_owners", ())) - owners
        forbidden_reasons = (
            _string_set(expected.get("forbidden_reason_codes", ())) & reason_codes
        )
        cached_tokens = sum(step.prompt_cached_input_tokens for step in cache_steps)
        created_tokens = sum(
            step.prompt_cache_creation_input_tokens for step in cache_steps
        )
        minimum_cached = _non_negative_int(
            expected.get("minimum_cached_input_tokens", 0)
        )
        minimum_created = _non_negative_int(
            expected.get("minimum_cache_creation_input_tokens", 0)
        )
        require_provider_reported = expected.get("require_provider_reported", False)
        provider_report_missing = bool(
            require_provider_reported
            and (
                not cache_steps
                or any(
                    step.prompt_provider_reported is not True for step in cache_steps
                )
            )
        )
        if missing_records:
            reason = "prompt_cache_record_missing"
        elif missing_outcomes:
            reason = "prompt_cache_outcome_missing"
        elif missing_owners:
            reason = "prompt_cache_owner_missing"
        elif forbidden_reasons:
            reason = "prompt_cache_forbidden_reason_observed"
        elif provider_report_missing:
            reason = "prompt_cache_provider_report_missing"
        elif cached_tokens < minimum_cached:
            reason = "prompt_cache_read_tokens_below_minimum"
        elif created_tokens < minimum_created:
            reason = "prompt_cache_write_tokens_below_minimum"
        else:
            reason = "prompt_cache_trajectory_passed"
        passed = reason == "prompt_cache_trajectory_passed"
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=assertion.hard_gate,
            reason_code=reason,
        )


class ModelInvocationTrajectoryScorer:
    """Score only closed, body-free F10 invocation lineage projections."""

    scorer_id = "model_invocation_trajectory"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        assertion = _assertion(case, self.scorer_id)
        if assertion is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=1.0,
                passed=True,
                hard_gate=False,
                reason_code="model_invocation_not_applicable",
            )
        expected = assertion.expected
        if not isinstance(expected, Mapping):
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=assertion.hard_gate,
                reason_code="model_invocation_assertion_invalid",
            )
        steps = tuple(
            step for step in trajectory.ordered_steps if step.invocation_record_kind
        )
        record_kinds = {step.invocation_record_kind for step in steps}
        statuses = {step.invocation_status for step in steps}
        decisions = {step.invocation_decision for step in steps}
        reasons = {step.invocation_reason for step in steps}
        states = {step.invocation_attempt_state for step in steps}
        failures = {step.invocation_failure_class for step in steps}
        recovery_outcomes = {step.invocation_recovery_outcome for step in steps}
        credential_modes = {step.invocation_credential_mode for step in steps}
        exclusion_reasons = {
            reason for step in steps for reason in step.invocation_exclusion_reasons
        }
        usage_steps = tuple(
            step for step in steps if step.invocation_record_kind == "attempt_usage"
        )
        route_ordinals = tuple(
            step.invocation_route_ordinal
            for step in steps
            if step.invocation_record_kind == "route_eligible"
        )
        attempt_count = max(
            (
                max(step.invocation_attempt_ordinal, step.invocation_attempt_count)
                for step in steps
            ),
            default=0,
        )

        checks = (
            (
                _string_set(expected.get("required_record_kinds", ())) - record_kinds,
                "model_invocation_record_missing",
            ),
            (
                _string_set(expected.get("required_statuses", ())) - statuses,
                "model_invocation_status_missing",
            ),
            (
                _string_set(expected.get("required_decisions", ())) - decisions,
                "model_invocation_decision_missing",
            ),
            (
                _string_set(expected.get("required_reasons", ())) - reasons,
                "model_invocation_reason_missing",
            ),
            (
                _string_set(expected.get("required_attempt_states", ())) - states,
                "model_invocation_attempt_state_missing",
            ),
            (
                _string_set(expected.get("required_failure_classes", ())) - failures,
                "model_invocation_failure_class_missing",
            ),
            (
                _string_set(expected.get("required_recovery_outcomes", ()))
                - recovery_outcomes,
                "model_invocation_recovery_missing",
            ),
            (
                _string_set(expected.get("required_credential_modes", ()))
                - credential_modes,
                "model_invocation_credential_mode_missing",
            ),
            (
                _string_set(expected.get("required_exclusion_reasons", ()))
                - exclusion_reasons,
                "model_invocation_exclusion_missing",
            ),
        )
        reason = next(
            (reason_code for missing, reason_code in checks if missing),
            None,
        )
        require_reported = expected.get("require_provider_reported_usage", False)
        if (
            reason is None
            and require_reported
            and (
                not usage_steps
                or any(
                    step.invocation_provider_reported_usage is not True
                    for step in usage_steps
                )
            )
        ):
            reason = "model_invocation_usage_report_missing"
        if (
            reason is None
            and expected.get("require_contiguous_route_ordinals", False)
            and route_ordinals != tuple(range(1, len(route_ordinals) + 1))
        ):
            reason = "model_invocation_route_order_invalid"
        minimum_attempts = _non_negative_int(expected.get("minimum_attempts", 0))
        maximum_attempts = _non_negative_int(
            expected.get("maximum_attempts", attempt_count)
        )
        if reason is None and attempt_count < minimum_attempts:
            reason = "model_invocation_attempt_count_below_minimum"
        if reason is None and attempt_count > maximum_attempts:
            reason = "model_invocation_attempt_count_above_maximum"
        if reason is None:
            reason = "model_invocation_trajectory_passed"
        passed = reason == "model_invocation_trajectory_passed"
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=assertion.hard_gate,
            reason_code=reason,
        )


#: Every numeric expectation an F3 discovery case may declare. Naming them in
#: one place is what lets the scorer refuse to grade a numeric assertion whose
#: quantity was never measured.
_DISCOVERY_NUMERIC_KEYS = (
    "minimum_recall_rank",
    "maximum_recall_rank",
    "maximum_candidate_count",
    "maximum_result_tokens",
    "maximum_model_turns",
)


class CapabilityDiscoveryTrajectoryScorer:
    """Score the closed, body-free F3 capability-discovery projection.

    Three properties Step 8 must prove are all expressible over the same
    projection, which is why there is one scorer rather than three:

    * **selection recall** — ``minimum_recall_rank``/``maximum_recall_rank``
      bound the position at which the case's target capability came back from
      ``search_capabilities``. Rank ``0`` means it never came back at all, and
      that is the failure recall exists to catch.
    * **unauthorized-name probing** — ``forbidden_outcomes`` and
      ``forbidden_phases`` state that an unauthorized name must not be
      searchable, describable, guessable, or invocable. A probe case forbids
      ``ok`` across every phase, so *any* successful answer fails it.
    * **end-to-end quality** — ``required_phases`` pins that the chain actually
      ran, and the token/turn ceilings pin that it stayed cheap.

    Recall is checked over every discovery step, considering positive ranks
    only. The rank is a *selection* fact — the position the reference the run
    actually acted on held in the search that offered it — so on a real run it
    is reported by the describe or invoke step that made the selection. Ignoring
    zeroes is what makes that safe, and it preserves the original reason for
    looking at search steps alone: a step with no rank to report contributes
    nothing rather than reading as a miss.

    Every numeric bound is refused outright when no step carried a measurement.
    A ``maximum_`` bound over an unpopulated field is satisfied by the absence
    of data, so without that refusal a green case would attest to safety nobody
    observed.
    """

    scorer_id = "capability_discovery_trajectory"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        assertion = _assertion(case, self.scorer_id)
        if assertion is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=1.0,
                passed=True,
                hard_gate=False,
                reason_code="capability_discovery_not_applicable",
            )
        expected = assertion.expected
        if not isinstance(expected, Mapping):
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=assertion.hard_gate,
                reason_code="capability_discovery_assertion_invalid",
            )
        steps = tuple(step for step in trajectory.ordered_steps if step.discovery_phase)
        phases = {step.discovery_phase for step in steps}
        outcomes = {step.discovery_outcome for step in steps}
        search_steps = tuple(
            step for step in steps if step.discovery_phase == "capability_search"
        )
        # Rank is read across every discovery step rather than over search
        # steps alone. On a real run the rank is a *selection* fact: it is
        # known when the model describes or invokes a reference and can be
        # placed against the search that offered it, which is a describe/invoke
        # step, not a search one. Folding the other phases in is safe because
        # only positive ranks are considered, so a step with nothing to report
        # still contributes nothing — the reason the original narrowing
        # existed. An authored fixture that puts its rank on the search step
        # scores identically.
        ranks = tuple(step.discovery_recall_rank for step in steps)
        best_rank = min((rank for rank in ranks if rank > 0), default=0)
        result_tokens = sum(step.discovery_result_tokens for step in steps)
        model_turns = sum(step.discovery_model_turns for step in steps)
        counts_observed = any(step.discovery_counts_observed for step in steps)

        checks = (
            (
                _string_set(expected.get("required_phases", ())) - phases,
                "capability_discovery_phase_missing",
            ),
            (
                _string_set(expected.get("required_outcomes", ())) - outcomes,
                "capability_discovery_outcome_missing",
            ),
            (
                _string_set(expected.get("forbidden_phases", ())) & phases,
                "capability_discovery_forbidden_phase_observed",
            ),
            (
                _string_set(expected.get("forbidden_outcomes", ())) & outcomes,
                "capability_discovery_forbidden_outcome_observed",
            ),
        )
        reason = next(
            (reason_code for observed, reason_code in checks if observed),
            None,
        )
        # Trajectory-wide ``required_outcomes`` is satisfied by *any* step, which
        # is too weak for the probe case: one bridge tool answering ``ok`` would
        # hide behind the two that still answered ``capability_not_found``. This
        # binds an outcome to a phase, so each probed tool is gated on its own.
        required_phase_outcomes = expected.get("required_phase_outcomes")
        if reason is None and isinstance(required_phase_outcomes, Mapping):
            mismatched = any(
                step.discovery_outcome
                != str(required_phase_outcomes[step.discovery_phase])
                for step in steps
                if step.discovery_phase in required_phase_outcomes
            )
            unobserved = _string_set(tuple(required_phase_outcomes)) - phases
            if mismatched or unobserved:
                reason = "capability_discovery_phase_outcome_mismatch"
        # A numeric assertion that never saw a number is not a passing
        # assertion. Every ``maximum_`` bound below is satisfied by an
        # unpopulated field — ``maximum_recall_rank: 0`` and
        # ``maximum_candidate_count: 0`` most dangerously, because they are how
        # the security case says an unauthorized name must not come back from a
        # search at all, and a bound of zero over absent data checks nothing.
        # Failing closed here means a green case reports observed safety rather
        # than missing evidence.
        if (
            reason is None
            and not counts_observed
            and any(key in expected for key in _DISCOVERY_NUMERIC_KEYS)
        ):
            reason = "capability_discovery_counts_unobserved"
        minimum_rank = _non_negative_int(expected.get("minimum_recall_rank", 0))
        if reason is None and minimum_rank and best_rank < minimum_rank:
            # ``best_rank`` is 0 when the target never appeared, so an absent
            # capability and a badly ranked one fail through the same gate.
            reason = "capability_discovery_recall_missing"
        # The two probe ceilings are read by key presence rather than by a
        # non-zero value, because ``0`` is their most important setting: it is
        # how an unauthorized-name case says the capability must not come back
        # from a search at all. A truthiness check would silently disable
        # exactly the assertion the security case rests on.
        if reason is None and "maximum_recall_rank" in expected:
            maximum_rank = _non_negative_int(expected.get("maximum_recall_rank"))
            if best_rank > maximum_rank:
                reason = "capability_discovery_recall_rank_exceeded"
        if reason is None and "maximum_candidate_count" in expected:
            maximum_candidates = _non_negative_int(
                expected.get("maximum_candidate_count")
            )
            observed_candidates = max(
                (step.discovery_candidate_count for step in search_steps),
                default=0,
            )
            if observed_candidates > maximum_candidates:
                reason = "capability_discovery_candidate_count_exceeded"
        maximum_tokens = _non_negative_int(expected.get("maximum_result_tokens", 0))
        if reason is None and maximum_tokens and result_tokens > maximum_tokens:
            reason = "capability_discovery_result_tokens_exceeded"
        maximum_turns = _non_negative_int(expected.get("maximum_model_turns", 0))
        if reason is None and maximum_turns and model_turns > maximum_turns:
            reason = "capability_discovery_model_turns_exceeded"
        if reason is None:
            reason = "capability_discovery_trajectory_passed"
        passed = reason == "capability_discovery_trajectory_passed"
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=assertion.hard_gate,
            reason_code=reason,
        )


#: Every expectation whose quantity comes from a *plan record's* segment list
#: rather than from counting steps. Named in one place so the scorer can refuse
#: to grade a width nobody measured — the same refusal
#: :data:`_DISCOVERY_NUMERIC_KEYS` exists for, and for the same reason.
#:
#: ``maximum_planned_batches``, ``minimum_dispatch_intents``,
#: ``maximum_determinate_settlements``, and ``minimum_tool_calls`` are
#: deliberately *not* here. Those count observable steps, so their quantity is
#: present whenever the trajectory is, and an unplannable case must be able to
#: assert "no plan was bound" without a plan record to read it from.
_PARALLEL_SEGMENT_NUMERIC_KEYS = (
    "minimum_planned_operations",
    "minimum_overlapping_operations",
    "maximum_overlapping_operations",
    "minimum_segment_width",
    "maximum_segment_width",
)

#: The two dispositions that *claim* something about the world.
#: ``indeterminate`` is deliberately absent: it is a settlement that asserts
#: nothing, which is the honest record for work whose outcome nobody can
#: establish. Counting it as an outcome would make a correctly cancelled run
#: indistinguishable from one that invented a result.
_DETERMINATE_DISPOSITIONS = frozenset({"succeeded", "failed"})


class ParallelExecutionTrajectoryScorer:
    """Score the closed, body-free F6 batch-journal projection.

    Step 10 turns on graph-level parallel execution, and ARQ-010 gates that on
    F1 evaluation being present. Six properties carry that gate, and all six are
    expressible over one projection of ``operation_batch.journal.v1``:

    * **independent reads overlap** — a ``parallel`` segment exists, every one
      of them carries ``independent_reads``, and enough operations actually sat
      inside one. ``minimum_overlapping_operations`` is the load-bearing half:
      a planner that emitted a parallel segment of nothing would satisfy the
      mode check and overlap no work at all.
    * **unknown stays serial** — ``forbidden_segment_modes`` plus the serial
      reasons the planner is required to have used. Stated as reasons rather
      than as "no parallel segment" so a plan that went serial for the *wrong*
      reason still fails.
    * **a write never overlaps the reads planned before it** —
      ``required_segment_mode_order`` compares the plan's modes as an ordered
      tuple. A set cannot express this: ``{parallel, serial}`` is equally true
      of a plan that overlapped the write.
    * **approval-gated work is unplannable** — ``maximum_planned_batches: 0``
      with ``minimum_tool_calls``. The floor is what stops the ceiling from
      passing on an empty trajectory: absence of a plan proves nothing unless
      the turn demonstrably ran.
    * **a sibling failure leaves a completed child intact** — the settled
      dispositions must still contain ``succeeded``.
    * **cancel and restart invent neither rollback nor success** —
      ``require_unresolved_child`` states positively that a child was begun and
      the journal never claimed an outcome for it, and
      ``maximum_determinate_settlements`` bounds how many outcomes were claimed
      at all. Both are phrased over *determinate* settlements — ``succeeded``
      and ``failed`` only — rather than over settlements, because a durable
      ``indeterminate`` row is the honest answer rather than a violation. That
      distinction is what lets one case grade a run whose cancel path records
      nothing and a run whose cancel path records its uncertainty: the property
      is that no outcome was manufactured, not which of the two shapes the
      journal took. A manufactured ``succeeded`` (invented result) or
      ``failed`` (invented "nothing reached the connector") fails both bounds.

    Every segment-derived numeric bound is refused outright when no step carried
    a plan record, because a ``maximum_`` ceiling over an unpopulated width is
    satisfied by absence.
    """

    scorer_id = "parallel_execution_trajectory"

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult:
        assertion = _assertion(case, self.scorer_id)
        if assertion is None:
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=1.0,
                passed=True,
                hard_gate=False,
                reason_code="parallel_execution_not_applicable",
            )
        expected = assertion.expected
        if not isinstance(expected, Mapping):
            return ScorerResult(
                scorer_id=self.scorer_id,
                score=0,
                passed=False,
                hard_gate=assertion.hard_gate,
                reason_code="parallel_execution_assertion_invalid",
            )
        reason = self._reason(expected=expected, trajectory=trajectory)
        passed = reason == "parallel_execution_trajectory_passed"
        return ScorerResult(
            scorer_id=self.scorer_id,
            score=1.0 if passed else 0.0,
            passed=passed,
            hard_gate=assertion.hard_gate,
            reason_code=reason,
        )

    @classmethod
    def _reason(
        cls,
        *,
        expected: Mapping[str, object],
        trajectory: TrajectoryManifest,
    ) -> str:
        steps = tuple(
            step for step in trajectory.ordered_steps if step.parallel_record_kind
        )
        plan_steps = tuple(
            step for step in steps if step.parallel_record_kind == "plan_bound"
        )
        child_steps = tuple(
            step for step in steps if step.parallel_record_kind == "child_transition"
        )
        record_kinds = {step.parallel_record_kind for step in steps}
        # Modes are concatenated across plan steps in trajectory order, so a
        # turn planned as one batch reads as one ordered tuple.
        segment_modes = tuple(
            mode for step in plan_steps for mode in step.parallel_segment_modes
        )
        parallel_reasons = {
            item
            for step in plan_steps
            for item in step.parallel_parallel_segment_reasons
        }
        serial_reasons = {
            item for step in plan_steps for item in step.parallel_serial_segment_reasons
        }
        kill_switch_reasons = {
            reason
            for step in plan_steps
            if (reason := step.parallel_kill_switch_reason) is not None
        }
        child_phases = {
            phase
            for step in child_steps
            if (phase := step.parallel_child_phase) is not None
        }
        child_dispositions = {
            disposition
            for step in child_steps
            if (disposition := step.parallel_child_disposition) is not None
        }
        dispatch_intents = sum(
            step.parallel_child_phase == "dispatch_intent" for step in child_steps
        )
        # Settlements that *claim an outcome*, as opposed to recording that the
        # outcome is unknown. This is the count the cancel property is about,
        # and it is deliberately not "settlements": a cancel that durably says
        # ``indeterminate`` has settled a child without asserting anything about
        # the world, which is the honest answer rather than a violation.
        determinate_settlements = sum(
            step.parallel_child_disposition in _DETERMINATE_DISPOSITIONS
            for step in child_steps
        )
        counts_observed = any(step.parallel_counts_observed for step in plan_steps)

        checks = (
            (
                _string_set(expected.get("required_record_kinds", ())) - record_kinds,
                "parallel_execution_record_missing",
            ),
            (
                _string_set(expected.get("forbidden_record_kinds", ())) & record_kinds,
                "parallel_execution_forbidden_record_observed",
            ),
            (
                _string_set(expected.get("forbidden_segment_modes", ()))
                & set(segment_modes),
                "parallel_execution_forbidden_mode_observed",
            ),
            (
                _string_set(expected.get("required_parallel_segment_reasons", ()))
                - parallel_reasons,
                "parallel_execution_parallel_reason_missing",
            ),
            (
                _string_set(expected.get("required_serial_segment_reasons", ()))
                - serial_reasons,
                "parallel_execution_serial_reason_missing",
            ),
            (
                _string_set(expected.get("required_kill_switch_reasons", ()))
                - kill_switch_reasons,
                "parallel_execution_kill_switch_reason_missing",
            ),
            (
                _string_set(expected.get("required_child_phases", ())) - child_phases,
                "parallel_execution_child_phase_missing",
            ),
            (
                _string_set(expected.get("required_child_dispositions", ()))
                - child_dispositions,
                "parallel_execution_child_disposition_missing",
            ),
            (
                _string_set(expected.get("forbidden_child_dispositions", ()))
                & child_dispositions,
                "parallel_execution_forbidden_disposition_observed",
            ),
        )
        reason = next(
            (reason_code for observed, reason_code in checks if observed),
            None,
        )
        # An allowlist rather than a denylist, because the planner's reason
        # vocabulary is open to extension and the claim being made is "every
        # segment that overlapped did so *only* because the reads were
        # independent". A forbidden-list version of this would silently admit
        # any reason added after the case was written.
        if reason is None and "allowed_parallel_segment_reasons" in expected:
            allowed = _string_set(expected.get("allowed_parallel_segment_reasons"))
            if parallel_reasons - allowed:
                reason = "parallel_execution_parallel_reason_unexpected"
        if reason is None and "required_segment_mode_order" in expected:
            required_order = _string_tuple(expected.get("required_segment_mode_order"))
            if segment_modes != required_order:
                reason = "parallel_execution_segment_order_mismatch"
        if reason is None:
            reason = cls._count_reason(
                expected=expected,
                trajectory=trajectory,
                plan_steps=plan_steps,
                counts_observed=counts_observed,
                dispatch_intents=dispatch_intents,
                determinate_settlements=determinate_settlements,
            )
        return reason or "parallel_execution_trajectory_passed"

    @staticmethod
    def _count_reason(
        *,
        expected: Mapping[str, object],
        trajectory: TrajectoryManifest,
        plan_steps: Sequence[TrajectoryStep],
        counts_observed: bool,
        dispatch_intents: int,
        determinate_settlements: int,
    ) -> str | None:
        # A plan whose widths were never measured cannot answer a width
        # question. Every ``maximum_`` bound below is satisfied by an
        # unpopulated field, so a case declaring one over an unmeasured plan
        # would report safety nobody observed. This is BUG-14 in F6's shape.
        if not counts_observed and any(
            key in expected for key in _PARALLEL_SEGMENT_NUMERIC_KEYS
        ):
            return "parallel_execution_counts_unobserved"
        planned_operations = sum(
            step.parallel_planned_operations for step in plan_steps
        )
        overlapping = sum(step.parallel_overlapping_operations for step in plan_steps)
        widest = max(
            (step.parallel_maximum_segment_width for step in plan_steps),
            default=0,
        )
        # ``maximum_planned_batches`` counts plan records, which is why it is
        # not gated above: an unplannable case has no plan record by definition
        # and must still be gradeable.
        if "maximum_planned_batches" in expected and len(
            plan_steps
        ) > _non_negative_int(expected.get("maximum_planned_batches")):
            return "parallel_execution_unexpected_plan"
        tool_calls = _usage_int(trajectory, "tool_calls") or sum(
            step.capability_id is not None for step in trajectory.ordered_steps
        )
        # The floor that stops an absence assertion from passing on an empty
        # trajectory: "no plan was bound" is only evidence when the turn ran.
        if tool_calls < _non_negative_int(expected.get("minimum_tool_calls", 0)):
            return "parallel_execution_tool_call_minimum_not_met"
        if planned_operations < _non_negative_int(
            expected.get("minimum_planned_operations", 0)
        ):
            return "parallel_execution_planned_operations_below_minimum"
        if overlapping < _non_negative_int(
            expected.get("minimum_overlapping_operations", 0)
        ):
            return "parallel_execution_overlap_below_minimum"
        if "maximum_overlapping_operations" in expected and overlapping > (
            _non_negative_int(expected.get("maximum_overlapping_operations"))
        ):
            return "parallel_execution_overlap_above_maximum"
        if widest < _non_negative_int(expected.get("minimum_segment_width", 0)):
            return "parallel_execution_segment_width_below_minimum"
        # Read by key presence rather than truthiness, because ``0`` and ``1``
        # are this ceiling's most important settings: they are how a case says
        # "nothing overlapped".
        if "maximum_segment_width" in expected and widest > _non_negative_int(
            expected.get("maximum_segment_width")
        ):
            return "parallel_execution_segment_width_exceeded"
        if dispatch_intents < _non_negative_int(
            expected.get("minimum_dispatch_intents", 0)
        ):
            return "parallel_execution_dispatch_intent_minimum_not_met"
        if "maximum_determinate_settlements" in expected and (
            determinate_settlements
            > _non_negative_int(expected.get("maximum_determinate_settlements"))
        ):
            return "parallel_execution_invented_outcome_observed"
        if (
            expected.get("require_unresolved_child", False)
            and dispatch_intents <= determinate_settlements
        ):
            return "parallel_execution_unresolved_child_missing"
        return None


class RedactedGradeRequest(RuntimeContract):
    """The complete, content-free payload available to an optional grader."""

    case_id: str = Field(min_length=1, max_length=160)
    task_family: str = Field(min_length=1, max_length=80)
    variant_id: str = Field(min_length=1, max_length=160)
    trajectory_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    deterministic_reason_codes: tuple[str, ...]
    maximum_output_tokens: int = Field(ge=1)
    maximum_cost_microusd: int = Field(ge=0)


class GraderAttribution(RuntimeContract):
    """Bounded advisory attribution; it has no hard-gate authority field."""

    grader_id: str = Field(min_length=1, max_length=160)
    grader_revision: str = Field(min_length=1, max_length=160)
    model_revision: str = Field(min_length=1, max_length=160)
    prompt_revision: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0, le=1)
    passed: bool
    reason_code: str = Field(min_length=1, max_length=120)
    tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)


class RedactedGraderPort(Protocol):
    async def grade(self, request: RedactedGradeRequest) -> GraderAttribution: ...


class BoundedRedactedGrader:
    """Apply a strict request/time bound and always return an advisory score."""

    def __init__(
        self,
        *,
        grader: RedactedGraderPort,
        maximum_requests: int,
        timeout_ms: int,
        maximum_tokens: int,
        maximum_cost_microusd: int,
    ) -> None:
        if maximum_requests < 0:
            raise ValueError("maximum_requests must be non-negative")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if maximum_tokens <= 0:
            raise ValueError("maximum_tokens must be positive")
        if maximum_cost_microusd < 0:
            raise ValueError("maximum_cost_microusd must be non-negative")
        self._grader = grader
        self._remaining = maximum_requests
        self._timeout_seconds = timeout_ms / 1_000
        self._remaining_tokens = maximum_tokens
        self._remaining_cost_microusd = maximum_cost_microusd

    async def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
        deterministic_results: Sequence[ScorerResult],
    ) -> ScorerResult | None:
        if (
            self._remaining <= 0
            or self._remaining_tokens <= 0
            or self._remaining_cost_microusd < 0
        ):
            return None
        self._remaining -= 1
        request = RedactedGradeRequest(
            case_id=case.case_id,
            task_family=case.task_family,
            variant_id=trajectory.variant_id,
            trajectory_digest=trajectory.manifest_digest,
            deterministic_reason_codes=tuple(
                result.reason_code for result in deterministic_results
            ),
            maximum_output_tokens=self._remaining_tokens,
            maximum_cost_microusd=self._remaining_cost_microusd,
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                attribution = await self._grader.grade(request)
        except TimeoutError:
            return ScorerResult(
                scorer_id="optional_grader",
                score=0,
                passed=False,
                hard_gate=False,
                reason_code="optional_grader_timeout",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return ScorerResult(
                scorer_id="optional_grader",
                score=0,
                passed=False,
                hard_gate=False,
                reason_code="optional_grader_error",
            )
        if (
            attribution.tokens > self._remaining_tokens
            or attribution.cost_microusd > self._remaining_cost_microusd
        ):
            self._remaining = 0
            self._remaining_tokens = 0
            self._remaining_cost_microusd = -1
            return ScorerResult(
                scorer_id="optional_grader",
                score=0,
                passed=False,
                hard_gate=False,
                reason_code="optional_grader_budget_exceeded",
                attribution=_scorer_attribution(attribution),
            )
        self._remaining_tokens -= attribution.tokens
        self._remaining_cost_microusd -= attribution.cost_microusd
        return ScorerResult(
            scorer_id=(
                f"optional_grader:{attribution.grader_id}:{attribution.grader_revision}"
            ),
            score=attribution.score,
            passed=attribution.passed,
            hard_gate=False,
            reason_code=attribution.reason_code,
            attribution=_scorer_attribution(attribution),
        )


DEFAULT_HARD_SCORERS = (
    HardSafetyScorer(),
    HardGroundednessScorer(),
    HardConstraintScorer(),
    TaskPolicyTrajectoryScorer(),
    PromptCacheTrajectoryScorer(),
    ModelInvocationTrajectoryScorer(),
    CapabilityDiscoveryTrajectoryScorer(),
    ParallelExecutionTrajectoryScorer(),
)


def _scorer_attribution(
    attribution: GraderAttribution,
) -> ScorerAttribution:
    return ScorerAttribution(
        scorer_revision=attribution.grader_revision,
        model_revision=attribution.model_revision,
        prompt_revision=attribution.prompt_revision,
        tokens=attribution.tokens,
        cost_microusd=attribution.cost_microusd,
    )


def _expectation(
    case: EvaluationCase,
    scorer_id: str,
) -> Mapping[str, object] | None:
    assertions = tuple(
        assertion
        for assertion in case.expected_assertions
        if assertion.scorer_id == scorer_id
    )
    if len(assertions) != 1:
        return None
    expected = assertions[0].expected
    return expected if isinstance(expected, Mapping) else None


def _assertion(
    case: EvaluationCase,
    scorer_id: str,
):
    assertions = tuple(
        assertion
        for assertion in case.expected_assertions
        if assertion.scorer_id == scorer_id
    )
    return assertions[0] if len(assertions) == 1 else None


def _usage_int(trajectory: TrajectoryManifest, key: str) -> int:
    return _non_negative_int(trajectory.usage_summary.get(key, 0))


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _string_set(value: object) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return frozenset()
    return frozenset(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    """Return a declared sequence in order, for expectations where order is the
    assertion rather than an incidental detail."""

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    return tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _count_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, item in value.items():
        if (
            isinstance(key, str)
            and key.strip()
            and isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
        ):
            counts[key.strip()] = item
    return counts


__all__ = [
    "BoundedRedactedGrader",
    "CapabilityDiscoveryTrajectoryScorer",
    "DEFAULT_HARD_SCORERS",
    "GraderAttribution",
    "HardConstraintScorer",
    "HardGroundednessScorer",
    "HardSafetyScorer",
    "ModelInvocationTrajectoryScorer",
    "ParallelExecutionTrajectoryScorer",
    "RedactedGradeRequest",
    "RedactedGraderPort",
    "PromptCacheTrajectoryScorer",
    "TaskPolicyTrajectoryScorer",
]
