"""F4 task-policy resolver and deterministic duplicate-control tests."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.task_policy import (
    PlanningRequirement,
    RequestFingerprint,
    TaskFamily,
    TaskPolicyProfile,
    TaskPolicyRequest,
    TaskPolicyResolver,
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


class TestTaskPolicyResolver:
    def test_explicit_family_wins_over_hints(self) -> None:
        resolver = TaskPolicyResolver(
            [
                _profile(family=TaskFamily.UNKNOWN),
                _profile(family=TaskFamily.CODE_DIAGNOSIS),
            ]
        )

        resolved = resolver.resolve(
            TaskPolicyRequest(
                explicit_family=TaskFamily.CODE_DIAGNOSIS,
                capability_hints=frozenset({"web"}),
            )
        )

        assert resolved.task_family is TaskFamily.CODE_DIAGNOSIS

    def test_unknown_is_the_closed_conservative_fallback(self) -> None:
        unknown = _profile(family=TaskFamily.UNKNOWN)
        resolver = TaskPolicyResolver([unknown])

        assert resolver.resolve(TaskPolicyRequest()).profile_id == unknown.profile_id

    def test_unknown_profile_is_required(self) -> None:
        with pytest.raises(ValueError, match="unknown"):
            TaskPolicyResolver([_profile()])


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
