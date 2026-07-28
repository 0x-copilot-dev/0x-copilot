"""F4 task-policy resolver and deterministic duplicate-control tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.capabilities.task_policy import (
    ErrorFingerprint,
    EvidenceFingerprint,
    ModelTurnRecord,
    PlanningRequirement,
    RequestFingerprint,
    ResultFingerprint,
    RunToolPlan,
    RunToolPlanFactory,
    SuccessEvidenceRequirement,
    TaskFamily,
    TaskPolicyBudgetRecord,
    TaskPolicyBundle,
    TaskPolicyProfile,
    TaskPolicyReducer,
    TaskPolicyRequest,
    TaskPolicyResolver,
    TaskPolicySelectionReason,
    ToolPlanProgressRecord,
    ToolPlanCreator,
    ToolPlanStatus,
    ToolPlanStep,
    ToolPlanStepStatus,
    ToolOperationOutcome,
    ToolUseController,
    ToolUseDisposition,
    ToolUseFeedback,
    ToolUseFeedbackRecord,
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

    def test_retryable_failure_may_retry_but_nonretryable_repeat_stops(
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

        assert feedback.disposition is ToolUseDisposition.CONTINUE
        repeated_error = controller.after_operation(
            ToolOperationOutcome(
                operation_id="op-2",
                capability_id="web.search",
                succeeded=False,
                error_class="timeout",
                retryable=False,
            )
        )
        assert repeated_error.disposition is ToolUseDisposition.STOP
        assert repeated_error.reason_code == "same_error_without_changed_input"
        stopped = controller.before_operation(
            _intent(operation_id="op-3", fingerprint="a" * 64)
        )
        assert stopped.disposition is ToolUseDisposition.STOP
        assert stopped.reason_code == "same_error_without_changed_input"

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


class TestTaskPolicyBundle:
    def test_bundle_and_selection_refs_authenticate_canonical_bodies(self) -> None:
        bundle = TaskPolicyBundle.with_conservative_unknown(
            bundle_id="desktop-default",
            revision="v1",
            profiles=(_profile(family=TaskFamily.CODE_DIAGNOSIS),),
        )
        resolver = TaskPolicyResolver(bundle=bundle)

        selection = resolver.resolve_selection(
            _request(server_selected_family=TaskFamily.CODE_DIAGNOSIS)
        )

        assert bundle.bundle_digest in bundle.bundle_ref
        assert selection.bundle_ref == bundle.bundle_ref
        assert selection.selection_digest in selection.selection_ref
        with pytest.raises(ValueError, match="digest"):
            TaskPolicyBundle.model_validate(
                {
                    **bundle.model_dump(mode="json"),
                    "bundle_digest": "0" * 64,
                }
            )
        with pytest.raises(TypeError, match="immutable"):
            bundle.profiles[0].tool_call_limits["unexpected"] = 99

    def test_effect_and_delegation_signals_only_tighten_family_selection(
        self,
    ) -> None:
        resolver = TaskPolicyResolver(
            [
                _profile(family=TaskFamily.PUBLIC_RESEARCH),
                _profile(family=TaskFamily.EFFECT_PROPOSAL),
                _profile(family=TaskFamily.DELEGATED_ANALYSIS),
            ]
        )

        effect = resolver.resolve_selection(
            _request(
                server_selected_family=TaskFamily.PUBLIC_RESEARCH,
                has_effect_intent=True,
                has_subagent_intent=True,
            )
        )
        delegated = resolver.resolve_selection(
            _request(
                server_selected_family=TaskFamily.PUBLIC_RESEARCH,
                has_subagent_intent=True,
            )
        )

        assert effect.task_family is TaskFamily.EFFECT_PROPOSAL
        assert delegated.task_family is TaskFamily.DELEGATED_ANALYSIS


class TestDeterministicPlanFactory:
    def test_plan_identity_and_template_are_restart_stable(self) -> None:
        selection = TaskPolicyResolver(
            [_profile(family=TaskFamily.CODE_DIAGNOSIS)]
        ).resolve_selection(_request(server_selected_family=TaskFamily.CODE_DIAGNOSIS))

        first = RunToolPlanFactory.create_for_selection(selection)
        second = RunToolPlanFactory.create_for_selection(selection)

        assert first == second
        assert first is not None
        assert first.created_by is ToolPlanCreator.DETERMINISTIC
        assert tuple(step.step_id for step in first.steps) == (
            "reproduce",
            "inspect",
            "verify",
        )

    def test_no_plan_is_created_when_selected_profile_skips_planning(self) -> None:
        profile = _profile(family=TaskFamily.TRANSFORMATION).model_copy(
            update={"planning_requirement": PlanningRequirement.NONE}
        )
        selection = TaskPolicyResolver([profile]).resolve_selection(
            _request(server_selected_family=TaskFamily.TRANSFORMATION)
        )

        assert RunToolPlanFactory.create_for_selection(selection) is None


class TestCanonicalFingerprints:
    def test_request_result_evidence_and_error_domains_are_distinct(self) -> None:
        key = b"k" * 32
        request = RequestFingerprint(key=key).for_request(
            capability_id="records.list",
            arguments={"fields": ["name", "id"], "idempotency_key": "secret"},
        )
        reordered = RequestFingerprint(key=key).for_request(
            capability_id="records.list",
            arguments={"fields": ["id", "name"], "idempotency_key": "different"},
        )
        result = ResultFingerprint(key=key).for_result(
            capability_id="records.list",
            result_metadata={"count": 2},
        )
        evidence = EvidenceFingerprint(key=key).for_evidence(
            source_kind="record",
            source_identity={"record_id": "r-1"},
        )
        error = ErrorFingerprint(key=key).for_error(
            capability_id="records.list",
            request_fingerprint=request,
            error_class="TIMEOUT",
            retryable=False,
        )

        assert request == reordered
        assert len({request, result, evidence, error}) == 4


class TestRestartSafeReducer:
    def test_rebuild_preserves_duplicate_budget_cost_and_progress_state(self) -> None:
        profile = _profile(
            limits={"web.search": 3},
            enforce_duplicates=True,
        ).model_copy(
            update={
                "model_turn_limit": 4,
                "total_tool_call_limit": 3,
                "cost_limit_microusd": 100,
                "objective_evidence_threshold": 1,
            }
        )
        intent = _intent(operation_id="op-1", fingerprint="a" * 64)
        outcome = ToolOperationOutcome(
            operation_id="op-1",
            capability_id="web.search",
            succeeded=True,
            source_fingerprints=("b" * 64,),
            evidence_fingerprint="c" * 64,
            result_fingerprint="d" * 64,
            cost_microusd=25,
        )
        records = (
            TaskPolicyBudgetRecord(
                budget_id="effective-1",
                model_turn_limit=3,
                total_tool_call_limit=2,
                cost_limit_microusd=80,
                deadline_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            ),
            ModelTurnRecord(turn_id="turn-1", cost_microusd=10),
            intent,
            outcome,
            ToolUseFeedbackRecord(
                decision_id="decision-1",
                operation_id="op-1",
                feedback=ToolUseFeedback(
                    disposition=ToolUseDisposition.CONTINUE,
                    reason_code="objective_satisfied",
                    new_evidence_count=1,
                ),
            ),
            ToolPlanProgressRecord(
                progress_id="progress-1",
                plan_id="plan-1",
                completed_step_ids=("discover",),
                evidence_count=1,
                objective_satisfied=True,
            ),
        )

        rebuilt = ToolUseController.rebuild(profile=profile, records=records)
        reduced = TaskPolicyReducer.reduce(profile=profile, records=records)

        assert rebuilt.state == reduced
        assert rebuilt.state.model_turns == 1
        assert rebuilt.state.tool_calls == 1
        assert rebuilt.state.cost_microusd == 35
        assert rebuilt.state.deadline_at == datetime(2026, 7, 29, tzinfo=timezone.utc)
        assert rebuilt.state.objective_satisfied is True
        duplicate = rebuilt.before_operation(
            _intent(operation_id="op-2", fingerprint="a" * 64)
        )
        assert duplicate.disposition is ToolUseDisposition.STOP

    def test_replay_detects_idempotency_conflicts(self) -> None:
        intent = _intent(operation_id="op-1", fingerprint="a" * 64)
        with pytest.raises(ValueError, match="operation_id"):
            ToolUseController.rebuild(
                profile=_profile(),
                records=(
                    intent,
                    _intent(operation_id="op-1", fingerprint="b" * 64),
                ),
            )

        controller = ToolUseController(profile=_profile())
        assert (
            controller.record_model_turn(ModelTurnRecord(turn_id="turn-1")).disposition
            is ToolUseDisposition.CONTINUE
        )
        with pytest.raises(ValueError, match="turn_id"):
            controller.record_model_turn(
                ModelTurnRecord(turn_id="turn-1", cost_microusd=1)
            )

    def test_source_and_semantic_histories_are_bounded(self) -> None:
        profile = _profile().model_copy(
            update={
                "max_source_history": 500,
                "semantic_history_limit": 20,
                "total_tool_call_limit": 1_000,
            }
        )
        records: list[ToolUseIntent | ToolOperationOutcome] = []
        for index in range(25):
            fingerprint = f"{index:064x}"
            records.append(
                ToolUseIntent(
                    operation_id=f"op-{index}",
                    capability_id="web.search",
                    canonical_request_fingerprint=fingerprint,
                    semantic_fingerprint=fingerprint,
                )
            )
            records.append(
                ToolOperationOutcome(
                    operation_id=f"op-{index}",
                    capability_id="web.search",
                    succeeded=True,
                    source_fingerprints=tuple(
                        f"{index * 25 + source:064x}" for source in range(25)
                    ),
                )
            )

        state = ToolUseController.rebuild(profile=profile, records=records).state

        assert state.source_fingerprint_count == 500
        assert state.semantic_history_count == 20


class TestControllerHardAndAdvisoryRules:
    def test_model_turn_cost_tool_and_deadline_limits_survive_rebuild(self) -> None:
        now = datetime(2026, 7, 28, tzinfo=timezone.utc)
        profile = _profile(limits={"web.search": 2}).model_copy(
            update={
                "model_turn_limit": 1,
                "total_tool_call_limit": 1,
                "cost_limit_microusd": 10,
                "wall_time_limit_seconds": 60,
            }
        )
        controller = ToolUseController(
            profile=profile,
            started_at=now,
            clock=lambda: now,
        )
        assert (
            controller.record_model_turn(
                ModelTurnRecord(turn_id="turn-1", cost_microusd=10)
            ).disposition
            is ToolUseDisposition.CONTINUE
        )
        assert (
            controller.record_model_turn(ModelTurnRecord(turn_id="turn-2")).reason_code
            == "profile_model_turn_limit"
        )
        assert (
            controller.before_operation(
                _intent(operation_id="op-1", fingerprint="a" * 64)
            ).disposition
            is ToolUseDisposition.CONTINUE
        )
        assert (
            controller.before_operation(
                _intent(operation_id="op-2", fingerprint="b" * 64)
            ).reason_code
            == "profile_total_tool_call_limit"
        )
        expired = ToolUseController(
            profile=profile,
            started_at=now,
            clock=lambda: now + timedelta(seconds=60),
        )
        assert (
            expired.before_operation(
                _intent(operation_id="late", fingerprint="c" * 64)
            ).reason_code
            == "profile_deadline_exhausted"
        )

    def test_semantic_low_yield_and_objective_rules_remain_advisory(self) -> None:
        profile = _profile().model_copy(
            update={
                "low_yield_streak_threshold": 1,
                "objective_evidence_threshold": 1,
            }
        )
        controller = ToolUseController(profile=profile)
        first = _intent(operation_id="op-1", fingerprint="a" * 64).model_copy(
            update={"semantic_fingerprint": "1" * 64}
        )
        assert controller.before_operation(first).reason_code == "admitted"
        objective = controller.after_operation(
            ToolOperationOutcome(
                operation_id="op-1",
                capability_id="web.search",
                succeeded=True,
                source_fingerprints=("2" * 64,),
            )
        )
        assert objective.disposition is ToolUseDisposition.CONTINUE
        assert objective.reason_code == "objective_satisfied"

        overlapping = _intent(operation_id="op-2", fingerprint="b" * 64).model_copy(
            update={"semantic_fingerprint": "1" * 64}
        )
        semantic = controller.before_operation(overlapping)
        assert semantic.disposition is ToolUseDisposition.CONTINUE
        assert semantic.reason_code == "semantic_query_overlap"
        low_yield = controller.after_operation(
            ToolOperationOutcome(
                operation_id="op-2",
                capability_id="web.search",
                succeeded=True,
                source_fingerprints=("2" * 64,),
            )
        )
        assert low_yield.disposition is ToolUseDisposition.CONTINUE
        assert low_yield.reason_code == "objective_satisfied"

        low_yield_controller = ToolUseController(
            profile=_profile().model_copy(update={"low_yield_streak_threshold": 1})
        )
        assert (
            low_yield_controller.before_operation(
                _intent(operation_id="low-1", fingerprint="d" * 64)
            ).disposition
            is ToolUseDisposition.CONTINUE
        )
        first_sources = low_yield_controller.after_operation(
            ToolOperationOutcome(
                operation_id="low-1",
                capability_id="web.search",
                succeeded=True,
                source_fingerprints=("3" * 64,),
            )
        )
        assert first_sources.reason_code == "new_evidence"
        assert (
            low_yield_controller.before_operation(
                _intent(operation_id="low-2", fingerprint="e" * 64)
            ).disposition
            is ToolUseDisposition.CONTINUE
        )
        repeated_sources = low_yield_controller.after_operation(
            ToolOperationOutcome(
                operation_id="low-2",
                capability_id="web.search",
                succeeded=True,
                source_fingerprints=("3" * 64,),
            )
        )
        assert repeated_sources.disposition is ToolUseDisposition.CONTINUE
        assert repeated_sources.reason_code == "same_sources_no_new_evidence"
