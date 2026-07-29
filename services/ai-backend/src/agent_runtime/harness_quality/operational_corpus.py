"""Versioned synthetic operational corpus required by the F1 promotion spine."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation import FixtureToolExecutor
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationAssertion,
    EvaluationCase,
    FixtureResponse,
)
from agent_runtime.harness_quality.suite_execution import (
    FixtureCallPlan,
    FixtureCasePlan,
    FixtureTrajectoryObservation,
    FixtureUsage,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


OPERATIONAL_CORPUS_REVISION = "operational-corpus-v7"
_TOOL_POLICY_EVENT = "tool_policy.journal.v1"
#: F6 already owns a canonical run-event family, so its cases are authored
#: against the event a real batch actually writes rather than against a
#: fixture-only vocabulary invented for the corpus.
_OPERATION_BATCH_EVENT = "operation_batch.journal.v1"
_PROMPT_ASSEMBLED_EVENT = "prompt.assembled.v1"
_PROMPT_CACHE_EVENT = "prompt.cache.observed.v1"
# F3 discovery decisions ride the existing closed quality-decision family rather
# than a family of their own, so this corpus needs no new event registration.
_QUALITY_DECISION_EVENT = "quality.decision.v1"
_MODEL_INVOCATION_EVENTS = {
    "invocation_planned": "model.invocation.planned.v1",
    "route_eligible": "model.invocation.route.v1",
    "route_excluded": "model.invocation.exclusion.v1",
    "attempt_admission": "model.attempt.admission.v1",
    "attempt_state": "model.attempt.state.v1",
    "attempt_usage": "model.attempt.usage.v1",
    "attempt_failed": "model.attempt.failed.v1",
    "invocation_recovery": "model.invocation.recovery.v1",
    "invocation_completed": "model.invocation.completed.v1",
    "invocation_failed": "model.invocation.failed.v1",
}
_F4_TASK_FAMILIES = (
    "task_policy_one_call_lookup",
    "task_policy_plan_before_tool",
    "task_policy_pagination_changed_cursor",
    "task_policy_exact_duplicate_blocked",
    "task_policy_retryable_error_changed_input",
    "task_policy_nonretryable_error_stopped",
    "task_policy_same_source_advisory",
    "task_policy_objective_completeness",
    "task_policy_cost_budget_exhaustion",
    "task_policy_tool_budget_exhaustion",
    "task_policy_turn_budget_exhaustion",
    "task_policy_deadline_exhaustion",
    "task_policy_restart_replay",
    "task_policy_approval_resume",
    "task_policy_shadow_enforce_comparison",
)
_F2_TASK_FAMILIES = ("prompt_cache_prefix_reuse",)
_F3_TASK_FAMILIES = (
    "capability_discovery_selection_recall",
    "capability_discovery_unauthorized_probe",
    "capability_discovery_end_to_end",
)
_F6_TASK_FAMILIES = (
    "parallel_independent_reads_overlap",
    "parallel_unknown_capability_serialized",
    "parallel_write_after_planned_reads",
    "parallel_approval_gated_unplannable",
    "parallel_sibling_failure_isolated",
    "parallel_cancel_restart_no_invention",
)
OPERATIONAL_TASK_FAMILIES = (
    "connector_selection",
    "mcp_auth",
    "web_evidence",
    "library_evidence",
    "bulk_filtering",
    "long_context_recall",
    "duplicate_error_loop",
    "safe_parallel_reads",
    "conflicting_writes",
    "dataflow",
    "local_subagents",
    "multi_file_workspace_edits",
    "provider_pre_content_failure",
    "provider_ambiguous_failure",
    "evidence_supported",
    "evidence_conflicting",
    "evidence_stale",
    "evidence_revoked",
    *_F4_TASK_FAMILIES,
    *_F2_TASK_FAMILIES,
    *_F3_TASK_FAMILIES,
    *_F6_TASK_FAMILIES,
)


class OperationalFixtureCall(RuntimeContract):
    """One exact call and redacted controller observations for a scenario."""

    capability_id: str = Field(min_length=1, max_length=160)
    arguments: dict[str, object]
    fixture: FixtureResponse
    evidence_ref: str = Field(min_length=1, max_length=160)
    before_observations: tuple[FixtureTrajectoryObservation, ...] = ()
    after_observations: tuple[FixtureTrajectoryObservation, ...] = ()

    def plan(self) -> FixtureCallPlan:
        return FixtureCallPlan(
            capability_id=self.capability_id,
            arguments=self.arguments,
            before_observations=self.before_observations,
            after_observations=self.after_observations,
        )


class OperationalFixture(RuntimeContract):
    """A content-free case and its reviewed exact fixture program."""

    family: str = Field(min_length=1, max_length=80)
    case: EvaluationCase
    calls: tuple[OperationalFixtureCall, ...] = Field(min_length=1, max_length=8)
    usage: FixtureUsage

    @property
    def capability_id(self) -> str:
        """Compatibility accessor for the first fixture call."""

        return self.calls[0].capability_id

    @property
    def arguments(self) -> dict[str, object]:
        """Compatibility accessor for the first fixture call."""

        return self.calls[0].arguments

    @property
    def fixture(self) -> FixtureResponse:
        """Compatibility accessor for the first fixture response."""

        return self.calls[0].fixture

    @property
    def evidence_ref(self) -> str:
        """Compatibility accessor for the first fixture evidence ref."""

        return self.calls[0].evidence_ref

    @property
    def fixtures(self) -> tuple[FixtureResponse, ...]:
        return tuple(call.fixture for call in self.calls)

    def plan(self) -> FixtureCasePlan:
        return FixtureCasePlan(
            case_id=self.case.case_id,
            case_revision=self.case.revision,
            calls=tuple(call.plan() for call in self.calls),
            usage=self.usage,
            redaction_policy_revision="redaction-v1",
            harness_revisions={
                "suite": "suite-v1",
                "task_policy": "f4-v1",
                "prompt_cache": "f2-v1",
                "model_invocation": "f10.3-v1",
                "capability_discovery": "f3-v1",
                "parallel_execution": "f6-v1",
            },
        )


def operational_corpus() -> tuple[OperationalFixture, ...]:
    """Return the complete deterministic corpus in canonical family order."""

    return tuple(_fixture(family) for family in OPERATIONAL_TASK_FAMILIES)


def _fixture(family: str) -> OperationalFixture:
    f4 = _f4_scenario(family)
    f2 = _f2_scenario(family)
    f3 = _f3_scenario(family)
    f6 = _f6_scenario(family)
    f10 = _f10_scenario(family)
    call_count = max(
        int(f4.get("call_count", 1)),
        int(f2.get("call_count", 1)),
        int(f3.get("call_count", 1)),
        int(f6.get("call_count", 1)),
        int(f10.get("call_count", 1)),
    )
    capability_id = f"fixture.{family}"
    calls = tuple(
        _call(
            family=family,
            capability_id=capability_id,
            ordinal=ordinal,
            f4=f4,
            f2=f2,
            f3=f3,
            f6=f6,
            f10=f10,
        )
        for ordinal in range(1, call_count + 1)
    )
    evidence_refs = tuple(sorted({call.evidence_ref for call in calls}))
    maximum_occurrences = {capability_id: call_count}
    assertions: tuple[EvaluationAssertion, ...] = (
        EvaluationAssertion(
            scorer_id="hard_safety",
            expected={"live_effect_dispatches": 0},
            hard_gate=True,
        ),
        EvaluationAssertion(
            scorer_id="hard_groundedness",
            expected={"required_evidence_refs": evidence_refs},
            hard_gate=True,
        ),
        EvaluationAssertion(
            scorer_id="hard_constraints",
            expected={
                "required_capabilities": [capability_id],
                "maximum_occurrences": maximum_occurrences,
            },
            hard_gate=True,
        ),
    )
    policy_assertion = f4.get("policy_assertion")
    if isinstance(policy_assertion, Mapping):
        assertions = (
            *assertions,
            EvaluationAssertion(
                scorer_id="task_policy_trajectory",
                expected=dict(policy_assertion),
                hard_gate=bool(f4.get("hard_gate", True)),
            ),
        )
    prompt_assertion = f2.get("prompt_assertion")
    if isinstance(prompt_assertion, Mapping):
        assertions = (
            *assertions,
            EvaluationAssertion(
                scorer_id="prompt_cache_trajectory",
                expected=dict(prompt_assertion),
                hard_gate=bool(f2.get("hard_gate", True)),
            ),
        )
    invocation_assertion = f10.get("invocation_assertion")
    if isinstance(invocation_assertion, Mapping):
        assertions = (
            *assertions,
            EvaluationAssertion(
                scorer_id="model_invocation_trajectory",
                expected=dict(invocation_assertion),
                hard_gate=bool(f10.get("hard_gate", True)),
            ),
        )
    parallel_assertion = f6.get("parallel_assertion")
    if isinstance(parallel_assertion, Mapping):
        assertions = (
            *assertions,
            EvaluationAssertion(
                scorer_id="parallel_execution_trajectory",
                expected=dict(parallel_assertion),
                hard_gate=bool(f6.get("hard_gate", True)),
            ),
        )
    discovery_assertion = f3.get("discovery_assertion")
    if isinstance(discovery_assertion, Mapping):
        assertions = (
            *assertions,
            EvaluationAssertion(
                scorer_id="capability_discovery_trajectory",
                expected=dict(discovery_assertion),
                hard_gate=bool(f3.get("hard_gate", True)),
            ),
        )
    case = EvaluationCase(
        case_id=f"case_{family}_v1",
        suite_id="suite_operational_v1",
        revision=OPERATIONAL_CORPUS_REVISION,
        task_family=family,
        input_ref=f"input_{family}_v1",
        fixture_catalog_ref="fixture_catalog_operational_v1",
        expected_assertions=assertions,
        allowed_capabilities=frozenset(call.capability_id for call in calls),
        forbidden_capabilities=frozenset({"live_effect.dispatch"}),
        scorer_set_id="deterministic_hard_gates_v2",
    )
    return OperationalFixture(
        family=family,
        case=case,
        calls=calls,
        usage=FixtureUsage(
            cost_microusd=10 * call_count,
            model_turns=max(
                1,
                max(
                    int(f4.get("model_turns", 1)),
                    int(f2.get("model_turns", 1)),
                    int(f3.get("model_turns", 1)),
                    int(f6.get("model_turns", 1)),
                    int(f10.get("model_turns", 1)),
                ),
            ),
            tool_calls=call_count,
            tokens=100 * call_count,
            elapsed_ms=10 * call_count,
        ),
    )


def _call(
    *,
    family: str,
    capability_id: str,
    ordinal: int,
    f4: Mapping[str, object],
    f2: Mapping[str, object],
    f3: Mapping[str, object],
    f6: Mapping[str, object],
    f10: Mapping[str, object],
) -> OperationalFixtureCall:
    arguments: dict[str, object] = {
        "scenario_id": family,
        "synthetic": True,
        "ordinal": ordinal,
    }
    if family == "task_policy_pagination_changed_cursor":
        arguments["cursor"] = "initial" if ordinal == 1 else "next-page"
    elif family == "task_policy_retryable_error_changed_input":
        arguments["retry_nonce"] = "first" if ordinal == 1 else "changed"
    evidence_ref = (
        f"evidence_{family}_shared_v1"
        if family == "task_policy_same_source_advisory"
        else f"evidence_{family}_{ordinal}_v1"
    )
    request_digest = FixtureToolExecutor.request_digest(
        capability_id=capability_id,
        arguments=arguments,
    )
    is_error = family in {
        "task_policy_retryable_error_changed_input",
        "task_policy_nonretryable_error_stopped",
    } and (ordinal == 1 or family == "task_policy_nonretryable_error_stopped")
    response_digest = canonical_json_sha256(
        {
            "scenario_id": family,
            "ordinal": ordinal,
            "outcome": "fixture-error" if is_error else "fixture-only",
            "evidence_ref": evidence_ref,
        }
    )
    return OperationalFixtureCall(
        capability_id=capability_id,
        arguments=arguments,
        fixture=FixtureResponse(
            capability_id=capability_id,
            request_digest=request_digest,
            response_ref=evidence_ref,
            response_digest=response_digest,
            is_error=is_error,
        ),
        evidence_ref=evidence_ref,
        before_observations=(
            *_observations(
                family=family,
                ordinal=ordinal,
                stage="before",
                f4=f4,
            ),
            *_prompt_observations(
                family=family,
                ordinal=ordinal,
                stage="before",
                f2=f2,
            ),
            *_discovery_observations(
                family=family,
                ordinal=ordinal,
                stage="before",
                f3=f3,
            ),
            *_parallel_observations(
                family=family,
                ordinal=ordinal,
                stage="before",
                f6=f6,
            ),
            *_invocation_observations(
                family=family,
                ordinal=ordinal,
                stage="before",
                f10=f10,
            ),
        ),
        after_observations=(
            *_observations(
                family=family,
                ordinal=ordinal,
                stage="after",
                f4=f4,
            ),
            *_prompt_observations(
                family=family,
                ordinal=ordinal,
                stage="after",
                f2=f2,
            ),
            *_discovery_observations(
                family=family,
                ordinal=ordinal,
                stage="after",
                f3=f3,
            ),
            *_parallel_observations(
                family=family,
                ordinal=ordinal,
                stage="after",
                f6=f6,
            ),
            *_invocation_observations(
                family=family,
                ordinal=ordinal,
                stage="after",
                f10=f10,
            ),
        ),
    )


def _observations(
    *,
    family: str,
    ordinal: int,
    stage: str,
    f4: Mapping[str, object],
) -> tuple[FixtureTrajectoryObservation, ...]:
    raw = f4.get(f"{stage}_observations", ())
    if not isinstance(raw, tuple):
        return ()
    return tuple(
        FixtureTrajectoryObservation(
            event_type=_TOOL_POLICY_EVENT,
            policy_record_kind=record_kind,
            policy_disposition=disposition,
            policy_reason_codes=reason_codes,
            policy_exhausted_dimensions=exhausted_dimensions,
            payload_digest=canonical_json_sha256(
                {
                    "scenario_id": family,
                    "ordinal": ordinal,
                    "stage": stage,
                    "record_kind": record_kind,
                    "disposition": disposition,
                    "reason_codes": list(reason_codes),
                    "exhausted_dimensions": list(exhausted_dimensions),
                }
            ),
        )
        for record_kind, disposition, reason_codes, exhausted_dimensions in raw
    )


def _f4_scenario(family: str) -> Mapping[str, object]:
    profile = ("profile_selected", None, (), ())
    plan = ("plan_bound", None, (), ())
    intent = ("intent_recorded", None, (), ())
    admitted = ("admission_recorded", "admitted", ("admitted",), ())
    success = ("outcome_recorded", None, ("operation_succeeded",), ())
    feedback = ("feedback_recorded", "continue", ("new_evidence",), ())
    scenarios: dict[str, Mapping[str, object]] = {
        "task_policy_one_call_lookup": {
            "before_observations": (profile, intent, admitted),
            "after_observations": (success, feedback),
            "policy_assertion": {
                "required_record_kinds": [
                    "profile_selected",
                    "intent_recorded",
                    "admission_recorded",
                    "outcome_recorded",
                    "feedback_recorded",
                ],
                "required_dispositions": ["admitted"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_plan_before_tool": {
            "before_observations": (profile, plan, intent, admitted),
            "after_observations": (success, feedback),
            "policy_assertion": {
                "required_record_kinds": [
                    "profile_selected",
                    "plan_bound",
                    "intent_recorded",
                ],
                "required_dispositions": ["admitted"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_pagination_changed_cursor": {
            "call_count": 2,
            "before_observations": (profile, plan, intent, admitted),
            "after_observations": (success, feedback),
            "policy_assertion": {
                "required_record_kinds": ["plan_bound", "admission_recorded"],
                "required_dispositions": ["admitted"],
                "minimum_tool_calls": 2,
                "maximum_tool_calls": 2,
                "forbidden_reason_codes": ["exact_duplicate"],
            },
        },
        "task_policy_exact_duplicate_blocked": {
            "before_observations": (profile, intent, admitted),
            "after_observations": (
                success,
                ("intent_recorded", None, (), ()),
                ("admission_recorded", "blocked", ("exact_duplicate",), ()),
                ("feedback_recorded", "blocked", ("exact_duplicate",), ()),
            ),
            "policy_assertion": {
                "required_dispositions": ["admitted", "blocked"],
                "required_reason_codes": ["exact_duplicate"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_retryable_error_changed_input": {
            "call_count": 2,
            "before_observations": (profile, plan, intent, admitted),
            "after_observations": (
                ("outcome_recorded", None, ("operation_failed_retryable",), ()),
                ("feedback_recorded", "replan", ("retryable_error",), ()),
            ),
            "policy_assertion": {
                "required_reason_codes": [
                    "operation_failed_retryable",
                    "retryable_error",
                ],
                "required_dispositions": ["admitted", "replan"],
                "minimum_tool_calls": 2,
                "maximum_tool_calls": 2,
                "forbidden_reason_codes": ["same_error_without_changed_input"],
            },
        },
        "task_policy_nonretryable_error_stopped": {
            "before_observations": (profile, intent, admitted),
            "after_observations": (
                ("outcome_recorded", None, ("operation_failed",), ()),
                (
                    "feedback_recorded",
                    "stop",
                    ("same_error_without_changed_input",),
                    (),
                ),
            ),
            "policy_assertion": {
                "required_reason_codes": ["same_error_without_changed_input"],
                "required_dispositions": ["stop"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_same_source_advisory": {
            "call_count": 2,
            "hard_gate": False,
            "before_observations": (profile, plan, intent, admitted),
            "after_observations": (
                success,
                ("feedback_recorded", "replan", ("same_sources_no_new_evidence",), ()),
            ),
            "policy_assertion": {
                "required_reason_codes": ["same_sources_no_new_evidence"],
                "maximum_tool_calls": 2,
            },
        },
        "task_policy_objective_completeness": {
            "before_observations": (profile, plan, intent, admitted),
            "after_observations": (
                success,
                ("feedback_recorded", "stop", ("objective_satisfied",), ()),
                ("progress_recorded", None, ("objective_satisfied",), ()),
            ),
            "policy_assertion": {
                "required_reason_codes": ["objective_satisfied"],
                "required_dispositions": ["stop"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_restart_replay": {
            "before_observations": (profile, plan, intent, admitted),
            "after_observations": (
                success,
                ("feedback_recorded", "continue", ("operation_replayed",), ()),
            ),
            "policy_assertion": {
                "required_reason_codes": ["operation_replayed"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_approval_resume": {
            "before_observations": (profile, plan, intent),
            "after_observations": (
                ("progress_recorded", None, (), ()),
                admitted,
                success,
                feedback,
            ),
            "policy_assertion": {
                "required_record_kinds": [
                    "profile_selected",
                    "plan_bound",
                    "progress_recorded",
                ],
                "required_dispositions": ["admitted"],
                "maximum_tool_calls": 1,
            },
        },
        "task_policy_shadow_enforce_comparison": {
            "before_observations": (profile, intent, admitted),
            "after_observations": (
                success,
                ("feedback_recorded", "blocked", ("exact_duplicate",), ()),
            ),
            "policy_assertion": {
                "required_reason_codes": ["exact_duplicate"],
                "maximum_tool_calls": 1,
            },
        },
    }
    if family in {
        "task_policy_cost_budget_exhaustion",
        "task_policy_tool_budget_exhaustion",
        "task_policy_turn_budget_exhaustion",
        "task_policy_deadline_exhaustion",
    }:
        dimension = family.removeprefix("task_policy_").removesuffix(
            "_budget_exhaustion"
        )
        if dimension == "deadline":
            dimension = "deadline"
        elif dimension == "turn":
            dimension = "model_turns"
        elif dimension == "tool":
            dimension = "tool_calls"
        scenarios[family] = {
            "before_observations": (profile, intent, admitted),
            "after_observations": (
                success,
                ("budget_recorded", None, ("budget_exhausted",), (dimension,)),
                ("feedback_recorded", "stop", ("budget_exhausted",), ()),
            ),
            "policy_assertion": {
                "required_record_kinds": ["budget_recorded"],
                "required_reason_codes": ["budget_exhausted"],
                "required_dispositions": ["stop"],
                "required_exhausted_dimensions": [dimension],
                "maximum_tool_calls": 1,
            },
        }
    return scenarios.get(family, {})


def _prompt_observations(
    *,
    family: str,
    ordinal: int,
    stage: str,
    f2: Mapping[str, object],
) -> tuple[FixtureTrajectoryObservation, ...]:
    raw = f2.get(f"{stage}_observations", ())
    if not isinstance(raw, tuple):
        return ()
    observations: list[FixtureTrajectoryObservation] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        target_ordinal = item.get("ordinal")
        if target_ordinal is not None and target_ordinal != ordinal:
            continue
        record_kind = str(item.get("record_kind", ""))
        event_type = (
            _PROMPT_ASSEMBLED_EVENT
            if record_kind == "assembled"
            else _PROMPT_CACHE_EVENT
        )
        payload = {
            "scenario_id": family,
            "ordinal": ordinal,
            "stage": stage,
            **dict(item),
        }
        observations.append(
            FixtureTrajectoryObservation(
                event_type=event_type,
                prompt_record_kind=record_kind,
                prompt_cache_outcome=_optional_text(item.get("outcome")),
                prompt_cache_owner=_optional_text(item.get("cache_owner")),
                prompt_reason_code=_optional_text(item.get("reason_code")),
                prompt_provider_reported=_optional_bool(item.get("provider_reported")),
                prompt_input_tokens=_non_negative_int(item.get("input_tokens")),
                prompt_cached_input_tokens=_non_negative_int(
                    item.get("cached_input_tokens")
                ),
                prompt_cache_creation_input_tokens=_non_negative_int(
                    item.get("cache_creation_input_tokens")
                ),
                payload_digest=canonical_json_sha256(payload),
            )
        )
    return tuple(observations)


def _f2_scenario(family: str) -> Mapping[str, object]:
    if family != "prompt_cache_prefix_reuse":
        return {}
    assembled = {
        "record_kind": "assembled",
        "cache_owner": "product",
        "outcome": "enforced",
        "reason_code": "typed_plan_enforced",
    }
    return {
        "call_count": 2,
        "model_turns": 2,
        "before_observations": (assembled,),
        "after_observations": (
            {
                "ordinal": 1,
                "record_kind": "cache_observed",
                "cache_owner": "product",
                "outcome": "write",
                "reason_code": "provider_reported_write",
                "provider_reported": True,
                "input_tokens": 1000,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 800,
            },
            {
                "ordinal": 2,
                "record_kind": "cache_observed",
                "cache_owner": "product",
                "outcome": "read",
                "reason_code": "provider_reported_read",
                "provider_reported": True,
                "input_tokens": 1000,
                "cached_input_tokens": 800,
                "cache_creation_input_tokens": 0,
            },
        ),
        "prompt_assertion": {
            "required_record_kinds": ["assembled", "cache_observed"],
            "required_outcomes": ["read", "write"],
            "required_cache_owners": ["product"],
            "require_provider_reported": True,
            "minimum_cached_input_tokens": 800,
            "minimum_cache_creation_input_tokens": 800,
            "forbidden_reason_codes": ["provider_metadata_not_reported"],
        },
    }


def _discovery_observations(
    *,
    family: str,
    ordinal: int,
    stage: str,
    f3: Mapping[str, object],
) -> tuple[FixtureTrajectoryObservation, ...]:
    raw = f3.get(f"{stage}_observations", ())
    if not isinstance(raw, tuple):
        return ()
    observations: list[FixtureTrajectoryObservation] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        target_ordinal = item.get("ordinal")
        if target_ordinal is not None and target_ordinal != ordinal:
            continue
        payload = {
            "scenario_id": family,
            "ordinal": ordinal,
            "stage": stage,
            **dict(item),
        }
        observations.append(
            FixtureTrajectoryObservation(
                event_type=_QUALITY_DECISION_EVENT,
                discovery_phase=_optional_text(item.get("phase")),
                discovery_outcome=_optional_text(item.get("outcome")),
                discovery_candidate_count=_non_negative_int(
                    item.get("candidate_count")
                ),
                discovery_recall_rank=_non_negative_int(item.get("recall_rank")),
                discovery_result_tokens=_non_negative_int(item.get("result_tokens")),
                discovery_model_turns=_non_negative_int(item.get("model_turns")),
                # Every observation built here is an authored F3 measurement,
                # so its zeros are observed zeros. Stating it keeps the fixture
                # path and the real-event path scoring a ceiling identically.
                discovery_counts_observed=True,
                payload_digest=canonical_json_sha256(payload),
            )
        )
    return tuple(observations)


def _f3_scenario(family: str) -> Mapping[str, object]:
    """Return the reviewed F3 discovery program for one family, if any.

    Three families, one per property Step 8 must prove. They are authored as
    bridge *decisions* rather than as connector traffic because that is what the
    F3 lane actually emits: the model-visible surface is three bounded tools,
    and a discovery answer is a decision about an opaque reference.

    ``capability_discovery_unauthorized_probe`` is the security case and is
    deliberately the strictest. The unauthorized name is searched, described,
    and then guessed at by invoking it directly, and every one of those three
    must answer ``capability_not_found`` — the same answer an unknown reference
    gets, so the model cannot use the error to learn that the capability exists.
    ``maximum_recall_rank: 0`` and ``maximum_candidate_count: 0`` say the search
    half of that: the name must not merely be un-invocable, it must not come
    back from a search at all.
    """

    search = {
        "ordinal": 1,
        "phase": "capability_search",
        "model_turns": 1,
    }
    describe = {
        "ordinal": 2,
        "phase": "capability_describe",
        "model_turns": 1,
    }
    invoke = {
        "ordinal": 3,
        "phase": "capability_invoke",
        "model_turns": 1,
    }
    scenarios: dict[str, Mapping[str, object]] = {
        "capability_discovery_selection_recall": {
            # Search *then* describe, because a rank is a property of a
            # selection and a search selects nothing. One bridge call is one
            # model turn, so a one-turn ceiling admits exactly one call — a
            # search — and no honest producer can report a rank on it. That is
            # why this case previously scored a working run as failing: it
            # asked for a selection fact and paid for only the offer that
            # precedes it. Two calls is the shortest trajectory that contains a
            # selection at all, so ``maximum_model_turns: 2`` is the tightest
            # ceiling this property can honestly carry. The rank therefore sits
            # on the describe step here, exactly where a real run reports it.
            "call_count": 2,
            "model_turns": 2,
            "after_observations": (
                {
                    **search,
                    "outcome": "ok",
                    "candidate_count": 4,
                    "result_tokens": 180,
                },
                {
                    **describe,
                    "outcome": "ok",
                    "recall_rank": 1,
                    "result_tokens": 150,
                },
            ),
            "discovery_assertion": {
                "required_phases": ["capability_search", "capability_describe"],
                "required_outcomes": ["ok"],
                "minimum_recall_rank": 1,
                "maximum_recall_rank": 3,
                "maximum_result_tokens": 400,
                "maximum_model_turns": 2,
            },
        },
        "capability_discovery_unauthorized_probe": {
            "call_count": 3,
            "model_turns": 3,
            "after_observations": (
                {
                    **search,
                    "outcome": "ok",
                    "candidate_count": 0,
                    "recall_rank": 0,
                    "result_tokens": 40,
                },
                {
                    **describe,
                    "outcome": "capability_not_found",
                    "result_tokens": 40,
                },
                {
                    **invoke,
                    "outcome": "capability_not_found",
                    "result_tokens": 40,
                },
            ),
            "discovery_assertion": {
                "required_phases": [
                    "capability_search",
                    "capability_describe",
                    "capability_invoke",
                ],
                "required_outcomes": ["capability_not_found"],
                "required_phase_outcomes": {
                    "capability_describe": "capability_not_found",
                    "capability_invoke": "capability_not_found",
                },
                "forbidden_outcomes": [
                    "capability_stale",
                    "capability_unavailable",
                    "execution_failed",
                ],
                "maximum_recall_rank": 0,
                "maximum_candidate_count": 0,
                "maximum_model_turns": 3,
            },
        },
        "capability_discovery_end_to_end": {
            "call_count": 3,
            "model_turns": 3,
            "after_observations": (
                {
                    **search,
                    "outcome": "ok",
                    "candidate_count": 3,
                    "recall_rank": 1,
                    "result_tokens": 180,
                },
                {**describe, "outcome": "ok", "result_tokens": 150},
                {**invoke, "outcome": "ok", "result_tokens": 90},
            ),
            "discovery_assertion": {
                "required_phases": [
                    "capability_search",
                    "capability_describe",
                    "capability_invoke",
                ],
                "required_outcomes": ["ok"],
                "forbidden_outcomes": [
                    "capability_not_found",
                    "capability_stale",
                    "capability_unavailable",
                    "execution_failed",
                    "invalid_request",
                    "catalog_inactive",
                    "tool_raised",
                    "unrecognized",
                ],
                "minimum_recall_rank": 1,
                "maximum_recall_rank": 2,
                "maximum_result_tokens": 600,
                "maximum_model_turns": 3,
            },
        },
    }
    return scenarios.get(family, {})


def _parallel_observations(
    *,
    family: str,
    ordinal: int,
    stage: str,
    f6: Mapping[str, object],
) -> tuple[FixtureTrajectoryObservation, ...]:
    """Build the F6 batch-journal observations for one fixture call.

    A ``plan_bound`` item names its segments as ``(mode, reason, width)``
    triples, which is exactly the shape the durable record carries: an ordered
    segment list whose only content-free facts are a closed mode, a closed
    planner reason, and how many operations the segment holds. The operation
    *ids* never enter, so the widths below are counts and nothing else.
    """

    raw = f6.get(f"{stage}_observations", ())
    if not isinstance(raw, tuple):
        return ()
    observations: list[FixtureTrajectoryObservation] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        target_ordinal = item.get("ordinal")
        if target_ordinal is not None and target_ordinal != ordinal:
            continue
        record_kind = str(item.get("record_kind", ""))
        segments = item.get("segments", ())
        segments = segments if isinstance(segments, tuple) else ()
        widths = tuple(int(width) for _mode, _reason, width in segments)
        observations.append(
            FixtureTrajectoryObservation(
                event_type=_OPERATION_BATCH_EVENT,
                parallel_record_kind=record_kind or None,
                parallel_segment_modes=tuple(
                    str(mode) for mode, _reason, _width in segments
                ),
                parallel_parallel_segment_reasons=tuple(
                    str(reason)
                    for mode, reason, _width in segments
                    if mode == "parallel"
                ),
                parallel_serial_segment_reasons=tuple(
                    str(reason) for mode, reason, _width in segments if mode == "serial"
                ),
                parallel_kill_switch_reason=_optional_text(
                    item.get("kill_switch_reason")
                ),
                parallel_child_phase=_optional_text(item.get("phase")),
                parallel_child_disposition=_optional_text(item.get("disposition")),
                parallel_planned_operations=sum(widths),
                parallel_overlapping_operations=sum(
                    int(width)
                    for mode, _reason, width in segments
                    if mode == "parallel"
                ),
                parallel_maximum_segment_width=max(widths, default=0),
                # Only a plan record measures widths. A child transition leaves
                # this false so its three zeroes stay distinguishable from an
                # observed zero, exactly as the real projection does.
                parallel_counts_observed=record_kind == "plan_bound",
                payload_digest=canonical_json_sha256(
                    {
                        "scenario_id": family,
                        "ordinal": ordinal,
                        "stage": stage,
                        "record_kind": record_kind,
                        "segments": [list(segment) for segment in segments],
                        "phase": item.get("phase"),
                        "disposition": item.get("disposition"),
                    }
                ),
            )
        )
    return tuple(observations)


def _f6_scenario(family: str) -> Mapping[str, object]:
    """Return the reviewed F6 parallel-execution program for one family.

    Six families, one per property ARQ-010's precondition names. They are
    authored as ``operation_batch.journal.v1`` records because that is the only
    thing F6 makes durable: a plan record per turn and up to two transitions per
    child. Nothing else about a batch survives the run, so nothing else can be
    graded after it.

    Two families deserve their reasoning stated rather than inferred.

    ``parallel_approval_gated_unplannable`` asserts an **absence**. Approval-
    gated work is not planned into serial segments — the graph seam refuses it a
    plan entry, which makes the whole turn unplannable and drops it onto the
    pre-F6 exclusive permit. So the durable evidence is that no plan record
    exists, and the case pairs ``maximum_planned_batches: 0`` with a tool-call
    floor. Without that floor the case would pass on an empty trajectory, which
    is the failure mode BUG-14 found in a different assertion.

    ``parallel_cancel_restart_no_invention`` grades the property rather than the
    shape. Two cancel implementations produce two different journals: one that
    unwinds the coroutine leaves a dispatch intent with no settle beside it, and
    one that records its uncertainty leaves a durable ``indeterminate``. Both
    are honest — neither claims an outcome — so the case is written over
    *determinate* settlements and passes on either. Writing it around the
    absence of a settle would have scored a correctly cancelled run as a failure
    the moment cancellation became durable, which is the BUG-17 shape.
    """

    scenarios: dict[str, Mapping[str, object]] = {
        "parallel_independent_reads_overlap": {
            "call_count": 3,
            "after_observations": (
                {
                    "ordinal": 3,
                    "record_kind": "plan_bound",
                    "kill_switch_reason": "snapshot_governs",
                    "segments": (("parallel", "independent_reads", 3),),
                },
            ),
            "parallel_assertion": {
                "required_record_kinds": ["plan_bound"],
                "required_kill_switch_reasons": ["snapshot_governs"],
                "required_segment_mode_order": ["parallel"],
                "forbidden_segment_modes": ["serial"],
                "required_parallel_segment_reasons": ["independent_reads"],
                "allowed_parallel_segment_reasons": ["independent_reads"],
                "minimum_planned_operations": 3,
                # The load-bearing floor. A planner that emitted a parallel
                # segment holding nothing would satisfy every check above and
                # overlap no work at all.
                "minimum_overlapping_operations": 3,
                "minimum_segment_width": 2,
                "maximum_segment_width": 4,
            },
        },
        "parallel_unknown_capability_serialized": {
            "call_count": 2,
            "after_observations": (
                {
                    "ordinal": 2,
                    "record_kind": "plan_bound",
                    "kill_switch_reason": "snapshot_governs",
                    # Both routes into "we do not know enough to overlap this":
                    # a capability nobody declared falls to the conservative
                    # floor, and a declared one whose effect class is unknown
                    # is refused on its own terms.
                    "segments": (
                        ("serial", "conservative_policy_default", 1),
                        ("serial", "unknown_side_effect", 1),
                    ),
                },
            ),
            "parallel_assertion": {
                "required_record_kinds": ["plan_bound"],
                "forbidden_segment_modes": ["parallel"],
                "required_serial_segment_reasons": [
                    "conservative_policy_default",
                    "unknown_side_effect",
                ],
                "required_segment_mode_order": ["serial", "serial"],
                "maximum_overlapping_operations": 0,
                "maximum_segment_width": 1,
            },
        },
        "parallel_write_after_planned_reads": {
            "call_count": 4,
            "after_observations": (
                {
                    "ordinal": 4,
                    "record_kind": "plan_bound",
                    "kill_switch_reason": "snapshot_governs",
                    # The exact segmentation ``BatchPlanner`` produces for two
                    # independent reads followed by two writes. The reads are
                    # flushed into their own segment *before* either write's
                    # barrier opens a new one, so each write is alone and
                    # strictly after them.
                    #
                    # Both writes are here because they are serialized by
                    # different rules, and only one of them is interesting.
                    # ``policy_requires_serial`` is the easy case: the policy
                    # said serial. ``effectful_operation`` is the load-bearing
                    # one — that operation's policy declares ``parallel_safe``,
                    # and it is refused the overlap on its effect class alone.
                    # A fixture carrying only the mode-serial write would prove
                    # nothing about an operator who mislabels a write.
                    "segments": (
                        ("parallel", "independent_reads", 2),
                        ("serial", "effectful_operation", 1),
                        ("serial", "policy_requires_serial", 1),
                    ),
                },
            ),
            "parallel_assertion": {
                "required_record_kinds": ["plan_bound"],
                # Order is the whole assertion: the set {parallel, serial} is
                # equally true of a plan that put the writes inside the overlap.
                "required_segment_mode_order": ["parallel", "serial", "serial"],
                "required_parallel_segment_reasons": ["independent_reads"],
                "allowed_parallel_segment_reasons": ["independent_reads"],
                "required_serial_segment_reasons": [
                    "effectful_operation",
                    "policy_requires_serial",
                ],
                "minimum_overlapping_operations": 2,
                "maximum_overlapping_operations": 2,
                "maximum_segment_width": 2,
            },
        },
        "parallel_approval_gated_unplannable": {
            "call_count": 2,
            "parallel_assertion": {
                "maximum_planned_batches": 0,
                "forbidden_record_kinds": ["plan_bound", "child_transition"],
                # Absence of a plan is only evidence when the turn demonstrably
                # ran. Without this floor an empty trajectory would pass.
                "minimum_tool_calls": 2,
            },
        },
        "parallel_sibling_failure_isolated": {
            "call_count": 2,
            "after_observations": (
                {
                    "ordinal": 1,
                    "record_kind": "plan_bound",
                    "kill_switch_reason": "snapshot_governs",
                    "segments": (("parallel", "independent_reads", 2),),
                },
                {
                    "ordinal": 1,
                    "record_kind": "child_transition",
                    "phase": "dispatch_intent",
                },
                {
                    "ordinal": 2,
                    "record_kind": "child_transition",
                    "phase": "dispatch_intent",
                },
                {
                    "ordinal": 2,
                    "record_kind": "child_transition",
                    "phase": "settled",
                    "disposition": "succeeded",
                },
                {
                    "ordinal": 2,
                    "record_kind": "child_transition",
                    "phase": "settled",
                    "disposition": "failed",
                },
            ),
            "parallel_assertion": {
                "required_record_kinds": ["plan_bound", "child_transition"],
                "required_child_phases": ["dispatch_intent", "settled"],
                # Both halves matter. ``succeeded`` is the completed child's
                # result surviving; ``failed`` is the sibling that actually
                # failed, without which the case would pass on a run where
                # nothing went wrong.
                "required_child_dispositions": ["succeeded", "failed"],
                "forbidden_child_dispositions": ["indeterminate"],
                "minimum_dispatch_intents": 2,
                "maximum_settled_children": 2,
                "required_segment_mode_order": ["parallel"],
                "minimum_overlapping_operations": 2,
            },
        },
        "parallel_cancel_restart_no_invention": {
            "call_count": 2,
            "after_observations": (
                {
                    "ordinal": 1,
                    "record_kind": "plan_bound",
                    "kill_switch_reason": "snapshot_governs",
                    "segments": (("parallel", "independent_reads", 2),),
                },
                {
                    "ordinal": 1,
                    "record_kind": "child_transition",
                    "phase": "dispatch_intent",
                },
                {
                    "ordinal": 2,
                    "record_kind": "child_transition",
                    "phase": "dispatch_intent",
                },
                {
                    "ordinal": 2,
                    "record_kind": "child_transition",
                    "phase": "settled",
                    "disposition": "succeeded",
                },
            ),
            "parallel_assertion": {
                "required_record_kinds": ["plan_bound", "child_transition"],
                "required_child_phases": ["dispatch_intent", "settled"],
                # The sibling that finished keeps its result, which is what
                # stops this case from passing on a run that did nothing.
                "required_child_dispositions": ["succeeded"],
                "minimum_dispatch_intents": 2,
                # The claim, stated positively and over *determinate*
                # settlements only: two children were begun and the journal
                # claimed an outcome for at most one. Manufacturing either
                # answer for the second — ``succeeded`` or the ``failed`` that
                # would imply nothing reached the connector — breaks both
                # bounds.
                #
                # Phrasing it this way rather than as "one child never settled"
                # is deliberate, and it is what keeps the case correct across
                # both cancel implementations. A cancel path that records
                # nothing leaves an intent with no settle; one that records its
                # uncertainty leaves a durable ``indeterminate``. Neither
                # invented anything, so both must pass, and a case written
                # around the *absence* of a settle would score the second as a
                # failure the moment cancellation became durable.
                "maximum_determinate_settlements": 1,
                "require_unresolved_child": True,
            },
        },
    }
    return scenarios.get(family, {})


def _invocation_observations(
    *,
    family: str,
    ordinal: int,
    stage: str,
    f10: Mapping[str, object],
) -> tuple[FixtureTrajectoryObservation, ...]:
    raw = f10.get(f"{stage}_observations", ())
    if not isinstance(raw, tuple):
        return ()
    observations: list[FixtureTrajectoryObservation] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        target_ordinal = item.get("ordinal")
        if target_ordinal is not None and target_ordinal != ordinal:
            continue
        record_kind = str(item.get("record_kind", ""))
        event_type = _MODEL_INVOCATION_EVENTS.get(record_kind)
        if event_type is None:
            continue
        payload = {
            "scenario_id": family,
            "ordinal": ordinal,
            "stage": stage,
            **{
                key: list(value) if isinstance(value, tuple) else value
                for key, value in item.items()
            },
        }
        observations.append(
            FixtureTrajectoryObservation(
                event_type=event_type,
                invocation_record_kind=record_kind,
                invocation_status=_optional_text(item.get("status")),
                invocation_fallback_policy=_optional_text(item.get("fallback_policy")),
                invocation_credential_mode=_optional_text(item.get("credential_mode")),
                invocation_decision=_optional_text(item.get("decision")),
                invocation_reason=(
                    _optional_text(item.get("reason"))
                    or _optional_text(item.get("decision_reason"))
                ),
                invocation_attempt_state=_optional_text(item.get("state")),
                invocation_failure_class=_optional_text(item.get("failure_class")),
                invocation_recovery_outcome=_optional_text(item.get("outcome")),
                invocation_exclusion_reasons=_string_tuple(item.get("reasons")),
                invocation_provider_reported_usage=_optional_bool(
                    item.get("provider_reported")
                ),
                invocation_route_ordinal=_non_negative_int(item.get("route_ordinal")),
                invocation_attempt_ordinal=_non_negative_int(
                    item.get("attempt_ordinal")
                ),
                invocation_attempt_count=_non_negative_int(item.get("attempt_count")),
                invocation_input_tokens=_non_negative_int(
                    item.get("input_tokens", item.get("total_input_tokens"))
                ),
                invocation_output_tokens=_non_negative_int(
                    item.get("output_tokens", item.get("total_output_tokens"))
                ),
                invocation_cost_microusd=_non_negative_int(
                    item.get("cost_microusd", item.get("total_cost_microusd"))
                ),
                payload_digest=canonical_json_sha256(payload),
            )
        )
    return tuple(observations)


def _f10_scenario(family: str) -> Mapping[str, object]:
    planned = {
        "record_kind": "invocation_planned",
        "status": "planned",
        "fallback_policy": "none",
    }
    route = {
        "record_kind": "route_eligible",
        "route_ordinal": 1,
        "credential_mode": "byok",
    }
    first_admission = {
        "record_kind": "attempt_admission",
        "decision": "admit",
        "reason": "first_attempt",
        "attempt_ordinal": 1,
    }
    scenarios: dict[str, Mapping[str, object]] = {
        "provider_pre_content_failure": {
            "before_observations": (
                planned,
                route,
                {
                    "record_kind": "route_excluded",
                    "reasons": ("region_mismatch",),
                },
                first_admission,
                {
                    "record_kind": "attempt_state",
                    "state": "dispatching",
                    "attempt_ordinal": 1,
                },
            ),
            "after_observations": (
                {
                    "record_kind": "attempt_failed",
                    "failure_class": "pre_dispatch_transient",
                    "attempt_ordinal": 1,
                },
                {
                    "record_kind": "invocation_recovery",
                    "outcome": "admitted",
                    "decision_reason": "safe_same_deployment_retry",
                },
                {
                    "record_kind": "attempt_admission",
                    "decision": "admit",
                    "reason": "safe_same_deployment_retry",
                    "attempt_ordinal": 2,
                },
                {
                    "record_kind": "attempt_usage",
                    "provider_reported": True,
                    "attempt_ordinal": 2,
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cost_microusd": 700,
                },
                {
                    "record_kind": "attempt_state",
                    "state": "completed",
                    "attempt_ordinal": 2,
                },
                {
                    "record_kind": "invocation_completed",
                    "status": "completed",
                    "attempt_count": 2,
                    "total_input_tokens": 1000,
                    "total_output_tokens": 100,
                    "total_cost_microusd": 700,
                },
            ),
            "invocation_assertion": {
                "required_record_kinds": [
                    "invocation_planned",
                    "route_eligible",
                    "route_excluded",
                    "attempt_admission",
                    "attempt_usage",
                    "attempt_failed",
                    "invocation_recovery",
                    "invocation_completed",
                ],
                "required_statuses": ["planned", "completed"],
                "required_decisions": ["admit"],
                "required_reasons": ["safe_same_deployment_retry"],
                "required_attempt_states": ["completed"],
                "required_failure_classes": ["pre_dispatch_transient"],
                "required_recovery_outcomes": ["admitted"],
                "required_credential_modes": ["byok"],
                "required_exclusion_reasons": ["region_mismatch"],
                "require_provider_reported_usage": True,
                "require_contiguous_route_ordinals": True,
                "minimum_attempts": 2,
                "maximum_attempts": 2,
            },
        },
        "provider_ambiguous_failure": {
            "before_observations": (
                planned,
                route,
                first_admission,
            ),
            "after_observations": (
                {
                    "record_kind": "attempt_state",
                    "state": "ambiguous",
                    "attempt_ordinal": 1,
                },
                {
                    "record_kind": "invocation_recovery",
                    "outcome": "ambiguous",
                },
                {
                    "record_kind": "invocation_failed",
                    "status": "failed",
                    "reason": "ambiguous_recovery",
                    "failure_class": "ambiguous_provider_state",
                    "attempt_count": 1,
                },
            ),
            "invocation_assertion": {
                "required_record_kinds": [
                    "invocation_planned",
                    "route_eligible",
                    "attempt_admission",
                    "attempt_state",
                    "invocation_recovery",
                    "invocation_failed",
                ],
                "required_statuses": ["planned", "failed"],
                "required_attempt_states": ["ambiguous"],
                "required_failure_classes": ["ambiguous_provider_state"],
                "required_recovery_outcomes": ["ambiguous"],
                "required_credential_modes": ["byok"],
                "require_contiguous_route_ordinals": True,
                "minimum_attempts": 1,
                "maximum_attempts": 1,
            },
        },
    }
    return scenarios.get(family, {})


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


__all__ = [
    "OPERATIONAL_CORPUS_REVISION",
    "OPERATIONAL_TASK_FAMILIES",
    "OperationalFixture",
    "OperationalFixtureCall",
    "operational_corpus",
]
