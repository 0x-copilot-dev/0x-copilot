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
from agent_runtime.delegation.subagents.authority import (
    SubagentCapabilityGrant,
    SubagentPolicyGrant,
)
from agent_runtime.delegation.subagents.contracts import SubagentDefinition
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _context(
    *,
    scopes: frozenset[str] = frozenset({"docs:read", "web:read"}),
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-1",
        org_id="org-1",
        roles=frozenset({"user"}),
        permission_scopes=scopes,
        model_profile=ModelConfig(
            provider="openai",
            model_name="test-model",
            max_input_tokens=128_000,
            timeout_seconds=30,
            temperature=0,
        ),
        trace_id="trace-1",
    )


def _definition(
    *,
    name: str = "researcher",
    tools: frozenset[str] = frozenset({"web_search", "file_read"}),
    skills: frozenset[str] = frozenset({"research"}),
    required_scopes: frozenset[str] = frozenset({"docs:read"}),
) -> SubagentDefinition:
    return SubagentDefinition(
        name=name,
        description="Researches a bounded question using supplied evidence.",
        graph_id=f"graph:{name}",
        tools=tools,
        skills=skills,
        required_scopes=required_scopes,
        allowed_scopes=frozenset({"docs:read"}),
        policy=SubagentPolicyGrant(),
    )


def _parent_grant(
    *,
    capabilities: frozenset[str] = frozenset({"subagent"}),
    tools: frozenset[str] = frozenset({"web_search"}),
    skills: frozenset[str] = frozenset({"research"}),
    scopes: frozenset[str] = frozenset({"docs:read", "web:read"}),
) -> SubagentCapabilityGrant:
    return SubagentCapabilityGrant(
        capabilities=capabilities,
        tools=tools,
        skills=skills,
        permission_scopes=scopes,
    )


def _coordinator(
    policy: DelegationAdmissionPolicy | None = None,
    *,
    context: AgentRuntimeContext | None = None,
    definitions: tuple[SubagentDefinition, ...] | None = None,
    parent_grant: SubagentCapabilityGrant | None = None,
) -> DelegationCoordinator:
    return DelegationCoordinator(
        policy,
        context=_context() if context is None else context,
        definitions=(_definition(),) if definitions is None else definitions,
        parent_grant=_parent_grant() if parent_grant is None else parent_grant,
    )


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
    requested_tools: tuple[str, ...] = (),
    requested_skills: tuple[str, ...] = (),
) -> DelegationRequest:
    return DelegationRequest(
        delegation_id=delegation_id,
        subagent_name="researcher",
        objective=f"Produce the bounded output for {delegation_id}.",
        relevant_summary="Use only the supplied evidence references.",
        evidence_refs=evidence_refs,
        constraints=constraints,
        dependency_refs=dependencies,
        requested_tools=requested_tools,
        requested_skills=requested_skills,
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


def test_build_plan_has_stable_topological_order_and_serial_dispatch() -> None:
    coordinator = _coordinator()
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

    assert plan.dependency_stages == (
        ("collect-a", "collect-z"),
        ("synthesize",),
    )
    assert plan.dispatch_order == ("collect-a", "collect-z", "synthesize")
    assert plan.dispatch_mode.value == "serial_default"
    assert tuple(entry.request.delegation_id for entry in plan.entries) == (
        "collect-a",
        "collect-z",
        "synthesize",
    )
    assert tuple(entry.order for entry in plan.entries) == (0, 1, 2)
    assert all(entry.child_depth == 1 for entry in plan.entries)
    assert plan.reserved_budget.max_wall_ms == 60_000
    assert all(entry.handoff.allowed_tools == {"web_search"} for entry in plan.entries)
    assert all(
        entry.handoff.runtime_context_ref.permission_scopes == {"docs:read"}
        for entry in plan.entries
    )


def test_packet_is_compact_deterministic_and_contains_references_not_history() -> None:
    coordinator = _coordinator()
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
    coordinator = _coordinator(DelegationAdmissionPolicy(max_depth=1))

    _assert_admission_code(
        DelegationAdmissionCode.DEPTH_LIMIT_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(_request("child"),),
            parent_state=_parent(depth=1),
            now=NOW,
        ),
    )


def test_active_and_new_children_share_the_same_parent_limit() -> None:
    coordinator = _coordinator(DelegationAdmissionPolicy(max_children=3))

    _assert_admission_code(
        DelegationAdmissionCode.CHILD_LIMIT_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(_request("child-a"), _request("child-b")),
            parent_state=_parent(active_children=2),
            now=NOW,
        ),
    )


def test_aggregate_budget_prevents_batch_oversubscription() -> None:
    coordinator = _coordinator()
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
    coordinator = _coordinator()

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


def test_serial_schedule_reserves_prior_children_against_later_deadlines() -> None:
    coordinator = _coordinator()

    _assert_admission_code(
        DelegationAdmissionCode.DEADLINE_EXCEEDED,
        lambda: coordinator.build_plan(
            requests=(
                _request(
                    "child-a",
                    budget=_budget(wall_ms=40_000),
                ),
                _request(
                    "child-b",
                    budget=_budget(wall_ms=40_000),
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
    coordinator = _coordinator()

    _assert_admission_code(
        expected,
        lambda: coordinator.build_plan(
            requests=requests,
            parent_state=_parent(),
            now=NOW,
        ),
    )


def test_duplicate_delegation_ids_are_rejected() -> None:
    coordinator = _coordinator()

    _assert_admission_code(
        DelegationAdmissionCode.DUPLICATE_DELEGATION_ID,
        lambda: coordinator.build_plan(
            requests=(_request("same"), _request("same")),
            parent_state=_parent(),
            now=NOW,
        ),
    )


def test_packet_byte_limit_is_enforced_after_canonical_serialization() -> None:
    coordinator = _coordinator(DelegationAdmissionPolicy(max_packet_bytes=1_024))
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


def test_handoff_composes_parent_definition_request_and_context_authority() -> None:
    coordinator = _coordinator(
        context=_context(scopes=frozenset({"docs:read"})),
        definitions=(
            _definition(
                tools=frozenset({"web_search", "file_read"}),
                skills=frozenset({"research", "summarize"}),
            ),
        ),
        parent_grant=_parent_grant(
            tools=frozenset({"web_search", "shell"}),
            skills=frozenset({"research", "admin"}),
            scopes=frozenset({"docs:read", "admin:write"}),
        ),
    )

    plan = coordinator.build_plan(
        requests=(
            _request(
                "child",
                requested_tools=("web_search", "file_read"),
                requested_skills=("research", "summarize"),
            ),
        ),
        parent_state=_parent(),
        now=NOW,
    )

    handoff = plan.entries[0].handoff
    assert handoff.allowed_tools == {"web_search"}
    assert handoff.allowed_skills == {"research"}
    assert handoff.authority.permission_scopes == {"docs:read"}
    assert handoff.runtime_context_ref.permission_scopes == {"docs:read"}


def test_parent_without_dispatch_authority_is_rejected() -> None:
    coordinator = _coordinator(parent_grant=_parent_grant(capabilities=frozenset()))

    _assert_admission_code(
        DelegationAdmissionCode.AUTHORITY_DENIED,
        lambda: coordinator.build_plan(
            requests=(_request("child"),),
            parent_state=_parent(),
            now=NOW,
        ),
    )


def test_unknown_or_context_invisible_subagent_is_rejected() -> None:
    unknown = _coordinator(definitions=())
    invisible = _coordinator(
        context=_context(scopes=frozenset({"docs:read"})),
        definitions=(_definition(required_scopes=frozenset({"admin:write"})),),
    )

    for coordinator in (unknown, invisible):
        _assert_admission_code(
            DelegationAdmissionCode.SUBAGENT_UNAVAILABLE,
            lambda coordinator=coordinator: coordinator.build_plan(
                requests=(_request("child"),),
                parent_state=_parent(),
                now=NOW,
            ),
        )


def test_duplicate_trusted_subagent_definitions_fail_before_planning() -> None:
    _assert_admission_code(
        DelegationAdmissionCode.DUPLICATE_SUBAGENT_DEFINITION,
        lambda: _coordinator(definitions=(_definition(), _definition())),
    )


def test_legacy_parallel_wave_field_is_not_part_of_the_plan_contract() -> None:
    plan = _coordinator().build_plan(
        requests=(_request("child"),),
        parent_state=_parent(),
        now=NOW,
    )
    payload = plan.model_dump(mode="json")
    payload["execution_waves"] = [["child"]]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(plan).model_validate(payload)
