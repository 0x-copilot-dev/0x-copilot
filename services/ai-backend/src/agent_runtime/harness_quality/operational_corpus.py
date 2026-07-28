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


OPERATIONAL_CORPUS_REVISION = "operational-corpus-v4"
_TOOL_POLICY_EVENT = "tool_policy.journal.v1"
_PROMPT_ASSEMBLED_EVENT = "prompt.assembled.v1"
_PROMPT_CACHE_EVENT = "prompt.cache.observed.v1"
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
            },
        )


def operational_corpus() -> tuple[OperationalFixture, ...]:
    """Return the complete deterministic corpus in canonical family order."""

    return tuple(_fixture(family) for family in OPERATIONAL_TASK_FAMILIES)


def _fixture(family: str) -> OperationalFixture:
    f4 = _f4_scenario(family)
    f2 = _f2_scenario(family)
    f10 = _f10_scenario(family)
    call_count = max(
        int(f4.get("call_count", 1)),
        int(f2.get("call_count", 1)),
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
