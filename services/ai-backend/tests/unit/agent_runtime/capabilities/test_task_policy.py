"""F4 task-policy resolver and deterministic duplicate-control tests."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.task_policy import (
    PlanningRequirement,
    RequestFingerprint,
    RunToolPlan,
    SuccessEvidenceRequirement,
    TaskFamily,
    TaskPolicyProfile,
    TaskPolicyRequest,
    TaskPolicyResolver,
    TaskPolicySelectionReason,
    ToolPlanCreator,
    ToolPlanStatus,
    ToolPlanStep,
    ToolPlanStepStatus,
    ToolOperationOutcome,
    ToolUseController,
    ToolUseDisposition,
    ToolUseIntent,
)


def _profile(
    *,
    family: TaskFamily = TaskFamily.PUBLIC_RESEARCH,
    limits: dict[str, int] | None = None,
    enforce_duplicates: bool = False,
) -> TaskPolicyProfile:
    return TaskPolicyProfile(
        profile_id=f"profile-{family.value}",
        revision="v1",
        task_family=family,
        planning_requirement=PlanningRequirement.REQUIRED,
        tool_call_limits=limits or {},
        enforce_exact_duplicates=enforce_duplicates,
    )


def _intent(
    *, operation_id: str, fingerprint: str, capability_id: str = "web.search"
) -> ToolUseIntent:
    return ToolUseIntent(
        operation_id=operation_id,
        capability_id=capability_id,
        canonical_request_fingerprint=fingerprint,
        objective="Find independently supported evidence.",
        expected_evidence_kind="web_source",
    )


def _request(**overrides: object) -> TaskPolicyRequest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "policy_revision": "v1",
    }
    values.update(overrides)
    return TaskPolicyRequest.model_validate(values)


class TestTaskPolicyResolver:
    def test_server_selected_family_wins_over_capability_hints(self) -> None:
        resolver = TaskPolicyResolver(
            [
                _profile(family=TaskFamily.UNKNOWN),
                _profile(family=TaskFamily.CODE_DIAGNOSIS),
            ]
        )

        resolved = resolver.resolve(
            _request(
                server_selected_family=TaskFamily.CODE_DIAGNOSIS,
                capability_hints=frozenset({"web"}),
            )
        )

        assert resolved.task_family is TaskFamily.CODE_DIAGNOSIS

    def test_effect_intent_cannot_be_downgraded_by_selected_family(self) -> None:
        resolver = TaskPolicyResolver(
            [
                _profile(family=TaskFamily.UNKNOWN),
                _profile(family=TaskFamily.PUBLIC_RESEARCH),
                _profile(family=TaskFamily.EFFECT_PROPOSAL),
            ]
        )

        selection = resolver.resolve_selection(
            _request(
                server_selected_family=TaskFamily.PUBLIC_RESEARCH,
                has_effect_intent=True,
            )
        )

        assert selection.task_family is TaskFamily.EFFECT_PROPOSAL
        assert selection.selection_reason is TaskPolicySelectionReason.EFFECT_INTENT

    def test_unknown_is_an_automatic_conservative_fallback(self) -> None:
        resolver = TaskPolicyResolver([_profile(family=TaskFamily.PUBLIC_RESEARCH)])

        resolved = resolver.resolve(_request())

        assert resolved.profile_id == "unknown.general"
        assert resolved.task_family is TaskFamily.UNKNOWN
        assert resolved.planning_requirement is PlanningRequirement.REQUIRED
        assert resolved.call_limit_for("unclassified.tool") == 3
        assert resolved.enforce_exact_duplicates is True

    def test_mixed_profile_revisions_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="policy_revision"):
            TaskPolicyResolver([])

        with pytest.raises(ValueError, match="share one revision"):
            TaskPolicyResolver(
                [
                    _profile(family=TaskFamily.UNKNOWN),
                    _profile(family=TaskFamily.CODE_DIAGNOSIS).model_copy(
                        update={"revision": "v2"}
                    ),
                ]
            )

    def test_run_request_must_match_resolver_revision(self) -> None:
        resolver = TaskPolicyResolver([], policy_revision="v2")

        with pytest.raises(ValueError, match="revision"):
            resolver.resolve(_request(policy_revision="v1"))

    def test_selection_binds_run_and_profile_revision(self) -> None:
        resolver = TaskPolicyResolver([_profile(family=TaskFamily.CODE_DIAGNOSIS)])

        selection = resolver.resolve_selection(
            _request(server_selected_family=TaskFamily.CODE_DIAGNOSIS)
        )

        assert selection.run_id == "run-1"
        assert selection.profile_id == "profile-code_diagnosis"
        assert selection.profile_revision == "v1"
        assert (
            selection.selection_reason
            is TaskPolicySelectionReason.SERVER_SELECTED_FAMILY
        )


class TestRunToolPlan:
    def test_plan_is_constructed_from_the_immutable_policy_selection(self) -> None:
        resolver = TaskPolicyResolver([_profile(family=TaskFamily.CODE_DIAGNOSIS)])
        selection = resolver.resolve_selection(
            _request(server_selected_family=TaskFamily.CODE_DIAGNOSIS)
        )

        plan = RunToolPlan.for_selection(
            selection=selection,
            plan_id="plan-1",
            objective="Diagnose the failing unit test.",
            steps=(
                ToolPlanStep(
                    step_id="inspect",
                    label="Inspect the failing test and implementation.",
                    expected_evidence_kinds=("source", "test_failure"),
                ),
            ),
            success_evidence=(
                SuccessEvidenceRequirement(
                    evidence_kind="test_result",
                    description="The focused regression test passes.",
                ),
            ),
            created_by=ToolPlanCreator.DETERMINISTIC,
        )

        assert plan.run_id == selection.run_id
        assert plan.profile_id == selection.profile_id
        assert plan.profile_revision == selection.profile_revision
        assert plan.task_family is TaskFamily.CODE_DIAGNOSIS

    def test_completed_plan_rejects_unfinished_or_duplicate_steps(self) -> None:
        base = {
            "plan_id": "plan-1",
            "run_id": "run-1",
            "profile_id": "profile-code",
            "profile_revision": "v1",
            "task_family": TaskFamily.CODE_DIAGNOSIS,
            "objective": "Confirm the diagnosis.",
            "success_evidence": (
                SuccessEvidenceRequirement(evidence_kind="test_result"),
            ),
            "created_by": ToolPlanCreator.MODEL,
        }

        with pytest.raises(ValueError, match="unfinished"):
            RunToolPlan(
                **base,
                steps=(ToolPlanStep(step_id="one", label="Run the test."),),
                status=ToolPlanStatus.COMPLETED,
            )

        with pytest.raises(ValueError, match="unique"):
            RunToolPlan(
                **base,
                steps=(
                    ToolPlanStep(
                        step_id="same",
                        label="First.",
                        status=ToolPlanStepStatus.COMPLETED,
                    ),
                    ToolPlanStep(
                        step_id="same",
                        label="Second.",
                        status=ToolPlanStepStatus.SKIPPED,
                    ),
                ),
                status=ToolPlanStatus.COMPLETED,
            )


class TestRequestFingerprint:
    def test_key_order_and_volatile_fields_do_not_change_fingerprint(self) -> None:
        fingerprints = RequestFingerprint(key=b"f" * 32)
        first = fingerprints.for_request(
            capability_id="web.search",
            arguments={"query": "a", "request_id": "one", "limit": 10},
        )
        second = fingerprints.for_request(
            capability_id="web.search",
            arguments={"limit": 10, "query": "a", "request_id": "two"},
        )

        assert first == second

    def test_pagination_cursor_is_meaningful_and_not_a_duplicate(self) -> None:
        fingerprints = RequestFingerprint(key=b"f" * 32)
        first = fingerprints.for_request(
            capability_id="web.search", arguments={"query": "a", "cursor": "one"}
        )
        second = fingerprints.for_request(
            capability_id="web.search", arguments={"query": "a", "cursor": "two"}
        )

        assert first != second


class TestToolUseController:
    def test_exact_duplicate_warns_without_counting_a_second_operation(self) -> None:
        controller = ToolUseController(profile=_profile(limits={"web.search": 2}))
        first = _intent(operation_id="op-1", fingerprint="a" * 64)
        duplicate = _intent(operation_id="op-2", fingerprint="a" * 64)

        assert (
            controller.before_operation(first).disposition
            is ToolUseDisposition.CONTINUE
        )
        feedback = controller.before_operation(duplicate)

        assert feedback.disposition is ToolUseDisposition.REPLAN
        assert feedback.reason_code == "exact_duplicate"
        assert feedback.duplicate_of_operation_id == "op-1"
        # A distinct request still has the second and final call available.
        assert (
            controller.before_operation(
                _intent(operation_id="op-3", fingerprint="b" * 64)
            ).disposition
            is ToolUseDisposition.CONTINUE
        )

    def test_enforced_duplicate_stops_before_another_tool_dispatch(self) -> None:
        controller = ToolUseController(profile=_profile(enforce_duplicates=True))
        assert (
            controller.before_operation(
                _intent(operation_id="op-1", fingerprint="a" * 64)
            ).disposition
            is ToolUseDisposition.CONTINUE
        )

        feedback = controller.before_operation(
            _intent(operation_id="op-2", fingerprint="a" * 64)
        )

        assert feedback.disposition is ToolUseDisposition.STOP

    def test_failure_returns_replan_and_same_request_is_not_dispatched_again(
        self,
    ) -> None:
        controller = ToolUseController(profile=_profile())
        first = _intent(operation_id="op-1", fingerprint="a" * 64)
        second = _intent(operation_id="op-2", fingerprint="a" * 64)
        assert (
            controller.before_operation(first).disposition
            is ToolUseDisposition.CONTINUE
        )
        assert (
            controller.after_operation(
                ToolOperationOutcome(
                    operation_id="op-1",
                    capability_id="web.search",
                    succeeded=False,
                    error_class="timeout",
                    retryable=True,
                )
            ).disposition
            is ToolUseDisposition.REPLAN
        )

        feedback = controller.before_operation(second)

        assert feedback.disposition is ToolUseDisposition.REPLAN
        assert feedback.reason_code == "exact_duplicate"

    def test_operation_id_replay_is_idempotent_but_changed_intent_conflicts(
        self,
    ) -> None:
        controller = ToolUseController(profile=_profile())
        intent = _intent(operation_id="op-1", fingerprint="a" * 64)
        assert (
            controller.before_operation(intent).disposition
            is ToolUseDisposition.CONTINUE
        )
        assert controller.before_operation(intent).reason_code == "operation_replayed"

        with pytest.raises(ValueError, match="operation_id"):
            controller.before_operation(
                _intent(operation_id="op-1", fingerprint="b" * 64)
            )
