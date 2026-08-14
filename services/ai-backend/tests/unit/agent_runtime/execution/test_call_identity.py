"""Stable runtime tool-call identity and checkpoint reducer tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
    RuntimeModelTurnReducer,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import (
    OperationArgumentStore,
    OperationGatewayMode,
)
from agent_runtime.capabilities.operations.errors import (
    OperationContextUnboundError,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.control_plane.context import (
    RunControlBinding,
    RunControlContext,
    RuntimeToolControlOutcome,
    RuntimeToolLifecycleReducer,
)
from agent_runtime.control_plane.contracts import (
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import FeatureModeSet
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
)
from agent_runtime.surfaces_v2.ledger_ids import (
    OperationArgsRefCodec,
    OperationIdCodec,
)

_SHA256 = "0" * 64
_POLICY_REVISIONS = RunPolicyRevisions(
    prompt="prompt-v1",
    capability="capability-v1",
    context="context-v1",
    tool_controller="tool-controller-v1",
    concurrency="concurrency-v1",
    dataflow="dataflow-v1",
    mcp_freshness="mcp-freshness-v1",
    delegation="delegation-v1",
    model_route="model-route-v1",
    workspace_edit="workspace-edit-v1",
    answer_verification="answer-verification-v1",
)


def _binding(
    *,
    run_id: str = "run-1",
    snapshot_id: str = "snapshot-1",
) -> RunControlBinding:
    snapshot = RunControlSnapshot.create(
        run_id=run_id,
        conversation_id="conversation-1",
        subject_fingerprint=_SHA256,
        deployment_profile="single-user-desktop",
        harness_variant_ref="harness://baseline-v1",
        task_policy_selection_ref="task-policy://bounded-v1",
        policy_revisions=_POLICY_REVISIONS,
        feature_modes=FeatureModeSet(),
        budget_envelope_ref=f"budget://bounded-v1/sha256/{_SHA256}",
        assignment_revision="assignment-v1",
        snapshot_id=snapshot_id,
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=snapshot.feature_modes,
        decisions=(),
    )


@contextmanager
def _bound_run(binding: RunControlBinding) -> Iterator[None]:
    token = RunControlContext.bind_for_run(binding)
    try:
        yield
    finally:
        RunControlContext.unbind(token)


def _current_identity(
    binding: RunControlBinding,
    *,
    execution_scope: str = "supervisor",
    model_turn: int = 3,
    model_tool_call_id: str = "provider-call-1",
) -> RuntimeToolCallIdentity:
    with _bound_run(binding):
        identity = RuntimeToolCallIdentity.from_current(
            execution_scope=execution_scope,
            model_turn=model_turn,
            model_tool_call_id=model_tool_call_id,
        )
    assert identity is not None
    return identity


def _tool_request(
    *,
    call_id: str = "provider-call-1",
    node_attempt: int = 1,
    runtime: object | None = None,
) -> ToolCallRequest:
    active_runtime = runtime or SimpleNamespace(
        execution_info=SimpleNamespace(
            checkpoint_id="checkpoint-1",
            checkpoint_ns="supervisor",
            task_id="task-1",
            node_attempt=node_attempt,
        ),
        config={},
    )
    return ToolCallRequest(
        tool_call={
            "name": "observed_tool",
            "args": {"private": "must-not-enter-lifecycle"},
            "id": call_id,
            "type": "tool_call",
        },
        tool=None,
        state={"runtime_control_model_turn": 3},
        runtime=cast(Any, active_runtime),
    )


class _RuntimeEventJournalTrap:
    """Runtime-shaped trap proving feature-off middleware performs no writes."""

    def __init__(self) -> None:
        self.config: dict[str, object] = {}
        self.execution_info = SimpleNamespace(
            checkpoint_id="checkpoint-1",
            checkpoint_ns="supervisor",
            task_id="task-1",
            node_attempt=1,
        )
        self.calls: list[str] = []

    async def append_api_event(self, **_kwargs: object) -> None:
        self.calls.append("append_api_event")

    async def append_event(self, **_kwargs: object) -> None:
        self.calls.append("append_event")

    async def emit(self, **_kwargs: object) -> None:
        self.calls.append("emit")


def test_identity_binds_only_run_snapshot_scope_turn_and_provider_call_id() -> None:
    identity = _current_identity(_binding())

    assert identity.run_id == "run-1"
    assert identity.snapshot_id == "snapshot-1"
    assert identity.execution_scope == "supervisor"
    assert identity.model_turn == 3
    assert identity.model_tool_call_id == "provider-call-1"
    assert identity.control_call_id.startswith("runtime-control:")
    OperationIdCodec.parse(identity.operation_id)

    serialized = identity.model_dump_json()
    assert "tool arguments must remain private" not in serialized
    assert "tool result bytes must remain private" not in serialized
    assert set(identity.model_dump()) == {
        "run_id",
        "snapshot_id",
        "execution_scope",
        "model_turn",
        "model_tool_call_id",
        "operation_id",
        "control_call_id",
    }


def test_identity_is_deterministic_across_configuration_rebuild_and_restart() -> None:
    original = _binding()
    serialized = original.model_dump(mode="json")
    rebuilt = RunControlBinding.model_validate(
        {key: serialized[key] for key in reversed(serialized)}
    )

    before_restart = _current_identity(original)
    after_restart = _current_identity(rebuilt)

    assert rebuilt is not original
    assert rebuilt.snapshot is not original.snapshot
    assert after_restart == before_restart


@pytest.mark.parametrize(
    (
        "changed_dimension",
        "binding",
        "execution_scope",
        "model_turn",
        "model_tool_call_id",
    ),
    [
        ("run", _binding(run_id="run-2"), "supervisor", 3, "provider-call-1"),
        (
            "snapshot",
            _binding(snapshot_id="snapshot-2"),
            "supervisor",
            3,
            "provider-call-1",
        ),
        (
            "scope",
            _binding(),
            "subagent:parent-provider-call",
            3,
            "provider-call-1",
        ),
        ("turn", _binding(), "supervisor", 4, "provider-call-1"),
        ("provider call", _binding(), "supervisor", 3, "provider-call-2"),
    ],
)
def test_each_bound_dimension_changes_the_identity(
    changed_dimension: str,
    binding: RunControlBinding,
    execution_scope: str,
    model_turn: int,
    model_tool_call_id: str,
) -> None:
    baseline = _current_identity(_binding())
    changed = _current_identity(
        binding,
        execution_scope=execution_scope,
        model_turn=model_turn,
        model_tool_call_id=model_tool_call_id,
    )

    assert changed.control_call_id != baseline.control_call_id, changed_dimension
    assert changed.operation_id != baseline.operation_id, changed_dimension


def test_supervisor_and_subagent_scopes_do_not_collide_for_reused_provider_id() -> None:
    binding = _binding()
    supervisor = _current_identity(
        binding,
        execution_scope="supervisor",
        model_turn=1,
        model_tool_call_id="provider-reused-id",
    )
    subagent = _current_identity(
        binding,
        execution_scope="subagent:parent-provider-call",
        model_turn=1,
        model_tool_call_id="provider-reused-id",
    )
    resumed_subagent = _current_identity(
        RunControlBinding.model_validate(binding.model_dump(mode="json")),
        execution_scope="subagent:parent-provider-call",
        model_turn=1,
        model_tool_call_id="provider-reused-id",
    )

    assert subagent != supervisor
    assert subagent.operation_id != supervisor.operation_id
    assert subagent.control_call_id != supervisor.control_call_id
    assert resumed_subagent == subagent


def test_inner_operation_ids_are_distinct_ordered_and_reproducible() -> None:
    identity = _current_identity(_binding())

    expected = tuple(identity.derived_operation_id(ordinal) for ordinal in range(1, 4))
    with RuntimeCallContext.bind(identity):
        allocated = tuple(RuntimeCallContext.next_operation_id() for _ in range(3))
    with RuntimeCallContext.bind(identity):
        replayed = tuple(RuntimeCallContext.next_operation_id() for _ in range(3))

    assert allocated == expected
    assert replayed == expected
    assert len(set(expected)) == 3
    assert expected[0] == identity.operation_id
    for operation_id in expected:
        assert operation_id is not None
        OperationIdCodec.parse(operation_id)
    with pytest.raises(ValueError, match="ordinal must be positive"):
        identity.derived_operation_id(0)


def test_operation_request_factory_adopts_call_id_and_keeps_gateway_invariants() -> (
    None
):
    identity = _current_identity(_binding())

    with RuntimeCallContext.bind(identity):
        with pytest.raises(OperationContextUnboundError):
            OperationRequestFactory.create(
                capability="builtin",
                op="read",
                arguments={"secret": "gateway-owned"},
            )

    arguments = OperationArgumentStore()
    operation_token = OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id="org-1",
            user_id="user-1",
            conversation_id="conversation-1",
            run_id="run-1",
        ),
        policy_snapshot=ToolUsePolicySnapshot.from_response(),
        ledger_emitter=None,
        artifact_service=None,
        mode=OperationGatewayMode.SHADOW,
        arguments=arguments,
    )
    try:
        with RuntimeCallContext.bind(identity):
            first = OperationRequestFactory.create(
                capability="builtin",
                op="read",
                arguments={"secret": "gateway-owned", "ordinal": 1},
            )
            second = OperationRequestFactory.create(
                capability="builtin",
                op="read",
                arguments={"secret": "gateway-owned", "ordinal": 2},
            )
    finally:
        OperationContext.unbind(operation_token)

    assert first.operation_id == identity.operation_id
    assert second.operation_id == identity.derived_operation_id(2)
    assert OperationArgsRefCodec.parse(first.canonical_args_ref).operation_id == (
        first.operation_id
    )
    assert OperationArgsRefCodec.parse(second.canonical_args_ref).operation_id == (
        second.operation_id
    )
    assert arguments.get(first.canonical_args_ref) == (
        first.args_digest,
        b'{"ordinal":1,"secret":"gateway-owned"}',
    )
    assert arguments.get(second.canonical_args_ref) == (
        second.args_digest,
        b'{"ordinal":2,"secret":"gateway-owned"}',
    )


def test_missing_legacy_run_binding_falls_back_without_leaking_identity() -> None:
    assert RunControlContext.current() is None
    assert (
        RuntimeToolCallIdentity.from_current(
            model_turn=1,
            model_tool_call_id="legacy-provider-call",
        )
        is None
    )
    assert RuntimeCallContext.current() is None
    assert RuntimeCallContext.next_operation_id() is None

    arguments = OperationArgumentStore()
    operation_token = OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id="org-1",
            user_id="user-1",
            conversation_id="conversation-1",
            run_id="run-1",
        ),
        policy_snapshot=ToolUsePolicySnapshot.from_response(),
        ledger_emitter=None,
        artifact_service=None,
        mode=OperationGatewayMode.SHADOW,
        arguments=arguments,
    )
    try:
        first = OperationRequestFactory.create(
            capability="builtin",
            op="read",
            arguments={},
        )
        second = OperationRequestFactory.create(
            capability="builtin",
            op="read",
            arguments={},
        )
    finally:
        OperationContext.unbind(operation_token)

    assert first.operation_id != second.operation_id
    OperationIdCodec.parse(first.operation_id)
    OperationIdCodec.parse(second.operation_id)


def test_nested_call_context_restores_outer_identity_and_allocator() -> None:
    binding = _binding()
    outer = _current_identity(binding, model_tool_call_id="outer")
    inner = _current_identity(binding, model_tool_call_id="inner")

    with RuntimeCallContext.bind(outer):
        assert RuntimeCallContext.current() is outer
        assert RuntimeCallContext.next_operation_id() == outer.operation_id
        with RuntimeCallContext.bind(inner):
            assert RuntimeCallContext.current() is inner
            assert RuntimeCallContext.next_operation_id() == inner.operation_id
        assert RuntimeCallContext.current() is outer
        assert RuntimeCallContext.next_operation_id() == outer.derived_operation_id(2)

    assert RuntimeCallContext.current() is None


async def test_concurrent_call_contexts_isolate_identity_and_operation_ordinals() -> (
    None
):
    binding = _binding()
    first_identity = _current_identity(binding, model_tool_call_id="concurrent-1")
    second_identity = _current_identity(binding, model_tool_call_id="concurrent-2")
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def allocate(
        identity: RuntimeToolCallIdentity,
        entered: asyncio.Event,
    ) -> tuple[
        RuntimeToolCallIdentity | None,
        str | None,
        str | None,
    ]:
        with RuntimeCallContext.bind(identity):
            current = RuntimeCallContext.current()
            first = RuntimeCallContext.next_operation_id()
            entered.set()
            await release.wait()
            second = RuntimeCallContext.next_operation_id()
            return current, first, second

    first_task = asyncio.create_task(allocate(first_identity, first_entered))
    second_task = asyncio.create_task(allocate(second_identity, second_entered))
    await asyncio.gather(first_entered.wait(), second_entered.wait())
    release.set()
    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result == (
        first_identity,
        first_identity.operation_id,
        first_identity.derived_operation_id(2),
    )
    assert second_result == (
        second_identity,
        second_identity.operation_id,
        second_identity.derived_operation_id(2),
    )
    assert RuntimeCallContext.current() is None


def test_resume_reuses_exact_identity_and_first_operation_id() -> None:
    binding = _binding()
    initial = _current_identity(binding, model_turn=7, model_tool_call_id="resume-call")
    with RuntimeCallContext.bind(initial):
        initial_operation_id = RuntimeCallContext.next_operation_id()

    resumed_binding = RunControlBinding.model_validate(binding.model_dump(mode="json"))
    resumed = _current_identity(
        resumed_binding,
        model_turn=7,
        model_tool_call_id="resume-call",
    )
    with RuntimeCallContext.bind(resumed):
        resumed_operation_id = RuntimeCallContext.next_operation_id()

    assert resumed.model_dump(mode="json") == initial.model_dump(mode="json")
    assert resumed_operation_id == initial_operation_id == initial.operation_id


@pytest.mark.parametrize("outcome", list(RuntimeToolControlOutcome))
def test_lifecycle_reducer_records_each_terminal_outcome_exactly_once(
    outcome: RuntimeToolControlOutcome,
) -> None:
    identity = _current_identity(_binding())
    reducer = RuntimeToolLifecycleReducer()
    reducer.observe_open(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-1",
    )

    first = reducer.observe_terminal(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-1",
        operation_id=identity.operation_id,
        execution_scope=identity.execution_scope,
        outcome=outcome,
    )
    replayed = reducer.observe_terminal(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-1",
        operation_id=identity.operation_id,
        execution_scope=identity.execution_scope,
        outcome=outcome,
    )

    assert replayed == first
    assert reducer.records() == (first,)
    assert first.model_dump(mode="json") == {
        "control_call_id": identity.control_call_id,
        "attempt_id": "attempt-1",
        "operation_id": identity.operation_id,
        "execution_scope": "supervisor",
        "outcome": outcome.value,
    }


def test_lifecycle_reducer_rejects_conflicting_terminal_replay() -> None:
    identity = _current_identity(_binding())
    reducer = RuntimeToolLifecycleReducer()
    reducer.observe_terminal(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-1",
        operation_id=identity.operation_id,
        execution_scope=identity.execution_scope,
        outcome=RuntimeToolControlOutcome.ERROR,
    )

    with pytest.raises(RuntimeError, match="conflicting terminal outcome"):
        reducer.observe_terminal(
            control_call_id=identity.control_call_id,
            attempt_id="attempt-1",
            operation_id=identity.operation_id,
            execution_scope=identity.execution_scope,
            outcome=RuntimeToolControlOutcome.SUCCESS,
        )

    assert len(reducer.records()) == 1


def test_lifecycle_reducer_fails_closed_at_its_record_bound() -> None:
    first_identity = _current_identity(
        _binding(),
        model_tool_call_id="bounded-call-1",
    )
    second_identity = _current_identity(
        _binding(),
        model_tool_call_id="bounded-call-2",
    )
    reducer = RuntimeToolLifecycleReducer(max_records=1)
    first = reducer.observe_terminal(
        control_call_id=first_identity.control_call_id,
        attempt_id="attempt-1",
        operation_id=first_identity.operation_id,
        execution_scope=first_identity.execution_scope,
        outcome=RuntimeToolControlOutcome.SUCCESS,
    )

    with pytest.raises(RuntimeError, match="record bound exhausted"):
        reducer.observe_open(
            control_call_id=second_identity.control_call_id,
            attempt_id="attempt-2",
        )

    assert reducer.records() == (first,)


def test_interrupt_resume_reconstructs_and_settles_one_terminal_per_attempt() -> None:
    identity = _current_identity(
        _binding(),
        model_turn=7,
        model_tool_call_id="resumable-call",
    )
    interrupted = RuntimeToolLifecycleReducer()
    interrupt_record = interrupted.observe_terminal(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-before-interrupt",
        operation_id=identity.operation_id,
        execution_scope=identity.execution_scope,
        outcome=RuntimeToolControlOutcome.INTERRUPT,
    )

    resumed = RuntimeToolLifecycleReducer(initial_records=interrupted.records())
    resumed.observe_open(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-before-interrupt",
    )
    replayed_interrupt = resumed.observe_terminal(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-before-interrupt",
        operation_id=identity.operation_id,
        execution_scope=identity.execution_scope,
        outcome=RuntimeToolControlOutcome.INTERRUPT,
    )
    resumed_success = resumed.observe_terminal(
        control_call_id=identity.control_call_id,
        attempt_id="attempt-after-resume",
        operation_id=identity.operation_id,
        execution_scope=identity.execution_scope,
        outcome=RuntimeToolControlOutcome.SUCCESS,
    )

    assert replayed_interrupt == interrupt_record
    assert resumed.records() == (resumed_success, interrupt_record)
    assert {record.operation_id for record in resumed.records()} == {
        identity.operation_id
    }
    assert {record.control_call_id for record in resumed.records()} == {
        identity.control_call_id
    }


@pytest.mark.parametrize(
    ("case", "expected_outcome"),
    [
        ("success", RuntimeToolControlOutcome.SUCCESS),
        ("error_message", RuntimeToolControlOutcome.ERROR),
        ("exception", RuntimeToolControlOutcome.ERROR),
        ("interrupt", RuntimeToolControlOutcome.INTERRUPT),
        ("command", RuntimeToolControlOutcome.COMMAND),
        ("cancelled", RuntimeToolControlOutcome.CANCELLED),
    ],
)
async def test_middleware_emits_one_content_free_terminal_per_outcome(
    case: str,
    expected_outcome: RuntimeToolControlOutcome,
) -> None:
    middleware = RuntimeControlMiddleware()
    request = _tool_request()

    async def handler(inner_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        if case == "exception":
            raise RuntimeError("private failure detail")
        if case == "interrupt":
            raise GraphInterrupt()
        if case == "cancelled":
            raise asyncio.CancelledError
        if case == "command":
            return Command(update={"safe_state": True})
        return ToolMessage(
            content="private result bytes",
            tool_call_id=str(inner_request.tool_call["id"]),
            status="error" if case == "error_message" else "success",
        )

    with _bound_run(_binding()):
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        identity = RuntimeToolCallIdentity.from_current(
            model_turn=3,
            model_tool_call_id="provider-call-1",
        )
        assert identity is not None
        if case == "exception":
            with pytest.raises(RuntimeError, match="private failure detail"):
                await middleware.awrap_tool_call(request, handler)
        elif case == "interrupt":
            with pytest.raises(GraphInterrupt):
                await middleware.awrap_tool_call(request, handler)
        elif case == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await middleware.awrap_tool_call(request, handler)
        else:
            await middleware.awrap_tool_call(request, handler)
        records = reducer.records()

    assert len(records) == 1
    record = records[0]
    assert record.control_call_id == identity.control_call_id
    assert record.operation_id == identity.operation_id
    assert record.execution_scope == identity.execution_scope
    assert record.outcome is expected_outcome
    serialized = record.model_dump_json()
    assert "must-not-enter-lifecycle" not in serialized
    assert "private result bytes" not in serialized
    assert "private failure detail" not in serialized


@pytest.mark.parametrize("result_kind", ["tool_message", "command"])
async def test_feature_off_without_guard_preserves_output_and_emits_no_events(
    result_kind: str,
) -> None:
    middleware = RuntimeControlMiddleware()
    runtime = _RuntimeEventJournalTrap()
    request = _tool_request(runtime=runtime)
    message = ToolMessage(
        content="wire-exact:\x00 café 🙂",
        tool_call_id="provider-call-1",
        additional_kwargs={"opaque": "unchanged"},
    )
    command = Command(
        update={
            "messages": [message],
            "opaque_state": {"bytes": [0, 255, 17]},
        }
    )
    expected: ToolMessage | Command[Any] = (
        message if result_kind == "tool_message" else command
    )
    message_bytes = message.model_dump_json().encode("utf-8")

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        return expected

    binding = _binding()
    assert set(binding.effective_modes.as_safe_mapping().values()) == {"off"}
    assert ToolBudgetGuard.active() is None
    with _bound_run(binding):
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        result = await middleware.awrap_tool_call(request, handler)
        lifecycle_records = reducer.records()

    assert result is expected
    assert message.model_dump_json().encode("utf-8") == message_bytes
    if isinstance(result, Command):
        assert result.update["messages"][0] is message
        assert result.update["opaque_state"] == {"bytes": [0, 255, 17]}
    assert len(lifecycle_records) == 1
    assert runtime.calls == []
    assert ToolBudgetGuard.active() is None


async def test_middleware_terminal_replay_is_idempotent_for_same_attempt() -> None:
    middleware = RuntimeControlMiddleware()
    request = _tool_request()
    handler_calls = 0

    async def handler(inner_request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_calls
        handler_calls += 1
        return ToolMessage(
            content="done",
            tool_call_id=str(inner_request.tool_call["id"]),
        )

    with _bound_run(_binding()):
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        await middleware.awrap_tool_call(request, handler)
        first_records = reducer.records()
        await middleware.awrap_tool_call(request, handler)
        replayed_records = reducer.records()

    assert handler_calls == 2
    assert replayed_records == first_records
    assert len(replayed_records) == 1
    assert replayed_records[0].outcome is RuntimeToolControlOutcome.SUCCESS


async def test_middleware_rejects_conflicting_same_attempt_terminal_replay() -> None:
    middleware = RuntimeControlMiddleware()
    request = _tool_request()

    async def success_handler(inner_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="done",
            tool_call_id=str(inner_request.tool_call["id"]),
        )

    async def error_handler(_request: ToolCallRequest) -> ToolMessage:
        raise ValueError("conflicting replay")

    with _bound_run(_binding()):
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        await middleware.awrap_tool_call(request, success_handler)
        with pytest.raises(RuntimeError, match="conflicting terminal outcome"):
            await middleware.awrap_tool_call(request, error_handler)
        records = reducer.records()

    assert len(records) == 1
    assert records[0].outcome is RuntimeToolControlOutcome.SUCCESS


async def test_run_shares_lifecycle_reducer_across_middleware_instances() -> None:
    first_middleware = RuntimeControlMiddleware()
    second_middleware = RuntimeControlMiddleware()
    request = _tool_request()

    async def handler(inner_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="done",
            tool_call_id=str(inner_request.tool_call["id"]),
        )

    with _bound_run(_binding()):
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        await first_middleware.awrap_tool_call(request, handler)
        await second_middleware.awrap_tool_call(request, handler)
        records = reducer.records()

    assert len(records) == 1
    assert records[0].outcome is RuntimeToolControlOutcome.SUCCESS


async def test_middleware_approval_resume_keeps_identity_and_changes_attempt() -> None:
    middleware = RuntimeControlMiddleware()
    interrupted_request = _tool_request(node_attempt=1)
    resumed_request = _tool_request(node_attempt=2)

    async def interrupt_handler(_request: ToolCallRequest) -> ToolMessage:
        raise GraphInterrupt()

    async def success_handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="done after resume",
            tool_call_id=str(request.tool_call["id"]),
        )

    with _bound_run(_binding()):
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        with pytest.raises(GraphInterrupt):
            await middleware.awrap_tool_call(interrupted_request, interrupt_handler)
        await middleware.awrap_tool_call(resumed_request, success_handler)
        records = reducer.records()

    assert len(records) == 2
    assert {record.outcome for record in records} == {
        RuntimeToolControlOutcome.INTERRUPT,
        RuntimeToolControlOutcome.SUCCESS,
    }
    assert len({record.attempt_id for record in records}) == 2
    assert len({record.control_call_id for record in records}) == 1
    assert len({record.operation_id for record in records}) == 1
    assert len({record.execution_scope for record in records}) == 1


@pytest.mark.parametrize(
    ("current", "replayed_update", "expected"),
    [
        (0, 1, 1),
        (4, 2, 4),
        (4, 4, 4),
        (4, 5, 5),
    ],
)
def test_model_turn_reducer_is_monotonic_across_replay(
    current: int,
    replayed_update: int,
    expected: int,
) -> None:
    assert RuntimeModelTurnReducer.reduce(current, replayed_update) == expected
