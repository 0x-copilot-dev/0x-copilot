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


OPERATIONAL_CORPUS_REVISION = "operational-corpus-v2"
_TOOL_POLICY_EVENT = "tool_policy.journal.v1"
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
            harness_revisions={"suite": "suite-v1", "task_policy": "f4-v1"},
        )


def operational_corpus() -> tuple[OperationalFixture, ...]:
    """Return the complete deterministic corpus in canonical family order."""

    return tuple(_fixture(family) for family in OPERATIONAL_TASK_FAMILIES)


def _fixture(family: str) -> OperationalFixture:
    f4 = _f4_scenario(family)
    call_count = int(f4.get("call_count", 1))
    capability_id = f"fixture.{family}"
    calls = tuple(
        _call(
            family=family,
            capability_id=capability_id,
            ordinal=ordinal,
            f4=f4,
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
            model_turns=max(1, int(f4.get("model_turns", 1))),
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
        before_observations=_observations(
            family=family,
            ordinal=ordinal,
            stage="before",
            f4=f4,
        ),
        after_observations=_observations(
            family=family,
            ordinal=ordinal,
            stage="after",
            f4=f4,
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


__all__ = [
    "OPERATIONAL_CORPUS_REVISION",
    "OPERATIONAL_TASK_FAMILIES",
    "OperationalFixture",
    "OperationalFixtureCall",
    "operational_corpus",
]
