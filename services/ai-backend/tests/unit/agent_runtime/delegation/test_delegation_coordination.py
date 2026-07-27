from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.delegation.subagents.coordination import (
    DelegationAdmissionCode,
    DelegationAdmissionError,
    DelegationAdmissionPolicy,
    DelegationBudget,
    DelegationCoordinator,
    DelegationParentState,
    DelegationRequest,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _budget(
    *,
    turns: int = 2,
    tools: int = 4,
    input_tokens: int = 2_000,
    output_tokens: int = 1_000,
    cost_microusd: int = 10_000,
    wall_ms: int = 20_000,
) -> DelegationBudget:
    return DelegationBudget(
        max_model_turns=turns,
        max_tool_calls=tools,
        max_input_tokens=input_tokens,
        max_output_tokens=output_tokens,
        max_cost_microusd=cost_microusd,
        max_wall_ms=wall_ms,
    )


def _parent(
    *,
    depth: int = 0,
    active_children: int = 0,
    budget: DelegationBudget | None = None,
) -> DelegationParentState:
    return DelegationParentState(
        current_depth=depth,
        active_children=active_children,
        remaining_budget=budget
        or _budget(
            turns=20,
            tools=40,
            input_tokens=40_000,
            output_tokens=20_000,
            cost_microusd=200_000,
            wall_ms=120_000,
        ),
        deadline_at=NOW + timedelta(minutes=2),
    )


def _request(
    delegation_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    budget: DelegationBudget | None = None,
    deadline_at: datetime | None = None,
) -> DelegationRequest:
    return DelegationRequest(
        delegation_id=delegation_id,
        subagent_name="researcher",
        objective=f"Produce the bounded output for {delegation_id}.",
        evidence_refs=evidence_refs,
        constraints=constraints,
        dependency_refs=dependencies,
        budget=budget or _budget(),
        deadline_at=deadline_at or NOW + timedelta(minutes=1),
    )


def _assert_admission_code(
    expected: DelegationAdmissionCode,
    call: Callable[[], object],
) -> None:
    with pytest.raises(DelegationAdmissionError) as error:
        call()
    assert error.value.code is expected


def test_build_plan_has_stable_topological_order_and_parallel_waves() -> None:
    coordinator = DelegationCoordinator()
    requests = (
        _request("collect-z"),
        _request("synthesize", dependencies=("collect-z", "collect-a")),
        _request("collect-a"),
    )

    plan = coordinator.build_plan(
        requests=requests,
        parent_state=_parent(),
        now=NOW,
    )

    assert plan.execution_waves == (
        ("collect-a", "collect-z"),
        ("synthesize",),
    )
    assert tuple(entry.request.delegation_id for entry in plan.entries) == (
        "collect-a",
        "collect-z",
        "synthesize",
    )
    assert tuple(entry.order for entry in plan.entries) == (0, 1, 2)
    assert all(entry.child_depth == 1 for entry in plan.entries)


def test_packet_is_compact_deterministic_and_contains_references_not_history() -> None:
    coordinator = DelegationCoordinator()
    first = _request(
        "research",
        evidence_refs=("artifact:z", "citation:a"),
        constraints=("No writes", "Use primary sources"),
    )
    second = _request(
        "research",
        evidence_refs=("citation:a", "artifact:z"),
        constraints=("Use primary sources", "No writes"),
    )

    first_packet = (
        coordinator.build_plan(
            requests=(first,),
            parent_state=_parent(),
            now=NOW,
        )
        .entries[0]
        .context_packet
    )
    second_packet = (
        coordinator.build_plan(
            requests=(second,),
            parent_state=_parent(),
            now=NOW,
        )
        .entries[0]
        .context_packet
    )

    assert first_packet.packet_digest == second_packet.packet_digest
    assert first_packet.evidence_refs == ("artifact:z", "citation:a")
    assert "conversation" not in first_packet.model_dump()
    assert "transcript" not in first_packet.model_dump()

    with pytest.raises(ValidationError):
        DelegationRequest.model_validate(
            {
                **first.model_dump(mode="json"),
                "conversation_history": [{"role": "user", "content": "secret"}],
            }
        )


def test_depth_limit_is_admitted_from_server_derived_parent_depth() -> None:
    coordinator = DelegationCoordinator(DelegationAdmissionPolicy(max_depth=1))

    _assert_admission_code(
        DelegationAdmissionCode.DEPTH_LIMIT_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(_request("child"),),
            parent_state=_parent(depth=1),
            now=NOW,
        ),
    )


def test_active_and_new_children_share_the_same_parent_limit() -> None:
    coordinator = DelegationCoordinator(DelegationAdmissionPolicy(max_children=3))

    _assert_admission_code(
        DelegationAdmissionCode.CHILD_LIMIT_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(_request("child-a"), _request("child-b")),
            parent_state=_parent(active_children=2),
            now=NOW,
        ),
    )


def test_aggregate_budget_prevents_batch_oversubscription() -> None:
    coordinator = DelegationCoordinator()
    remaining = _budget(
        turns=3,
        tools=10,
        input_tokens=10_000,
        output_tokens=10_000,
        cost_microusd=100_000,
        wall_ms=60_000,
    )

    _assert_admission_code(
        DelegationAdmissionCode.TOTAL_BUDGET_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(_request("child-a"), _request("child-b")),
            parent_state=_parent(budget=remaining),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    "deadline,wall_ms",
    (
        (NOW, 1_000),
        (NOW + timedelta(minutes=3), 1_000),
        (NOW + timedelta(seconds=5), 10_000),
    ),
)
def test_deadline_admission_rejects_expired_parent_escape_and_short_window(
    deadline: datetime,
    wall_ms: int,
) -> None:
    coordinator = DelegationCoordinator()

    _assert_admission_code(
        DelegationAdmissionCode.DEADLINE_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(
                _request(
                    "child",
                    deadline_at=deadline,
                    budget=_budget(wall_ms=wall_ms),
                ),
            ),
            parent_state=_parent(),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    ("requests", "expected"),
    (
        (
            (_request("child", dependencies=("missing",)),),
            DelegationAdmissionCode.UNKNOWN_DEPENDENCY,
        ),
        (
            (_request("child", dependencies=("child",)),),
            DelegationAdmissionCode.SELF_DEPENDENCY,
        ),
        (
            (
                _request("child-a", dependencies=("child-b",)),
                _request("child-b", dependencies=("child-a",)),
            ),
            DelegationAdmissionCode.DEPENDENCY_CYCLE,
        ),
    ),
)
def test_invalid_dependency_graphs_fail_before_dispatch(
    requests: tuple[DelegationRequest, ...],
    expected: DelegationAdmissionCode,
) -> None:
    coordinator = DelegationCoordinator()

    _assert_admission_code(
        expected,
        lambda: coordinator.build_plan(
            requests=requests,
            parent_state=_parent(),
            now=NOW,
        ),
    )


def test_duplicate_delegation_ids_are_rejected() -> None:
    coordinator = DelegationCoordinator()

    _assert_admission_code(
        DelegationAdmissionCode.DUPLICATE_DELEGATION_ID,
        lambda: coordinator.build_plan(
            requests=(_request("same"), _request("same")),
            parent_state=_parent(),
            now=NOW,
        ),
    )


def test_packet_byte_limit_is_enforced_after_canonical_serialization() -> None:
    coordinator = DelegationCoordinator(
        DelegationAdmissionPolicy(max_packet_bytes=1_024)
    )
    request = DelegationRequest(
        **{
            **_request("child").model_dump(),
            "objective": "x" * 2_000,
        }
    )

    _assert_admission_code(
        DelegationAdmissionCode.PACKET_TOO_LARGE,
        lambda: coordinator.build_plan(
            requests=(request,),
            parent_state=_parent(),
            now=NOW,
        ),
    )
