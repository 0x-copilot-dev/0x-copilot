from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.tools.builtin.ask_a_question import (
    AskAQuestionInput,
)
from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_atlas_task_tool,
)
from agent_runtime.execution.factory import _structured_tool
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
    RecordingEmitter as OperationEmitter,
)


class CountingBuiltin:
    name: str = "ask_a_question"
    description: str = "Ask once."
    calls: int = 0

    async def ainvoke(self, raw: object) -> object:
        self.calls += 1
        return raw


class TestBuiltinAssemblyShadowSeam(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_off_and_shadow_return_identical_builtin_result(self) -> None:
        adapter = CountingBuiltin()
        tool = _structured_tool(adapter, AskAQuestionInput)
        payload = {"question": "Continue?"}

        off_token = self.bind(mode=OperationGatewayMode.OFF)
        try:
            off_result = await tool.ainvoke(payload)
        finally:
            OperationContext.unbind(off_token)

        emitter = OperationEmitter(fail_on_call=2)
        shadow_token = self.bind(emitter=emitter)
        try:
            shadow_result = await tool.ainvoke(payload)
        finally:
            OperationContext.unbind(shadow_token)

        assert shadow_result == off_result
        assert adapter.calls == 2
        assert emitter.calls == 3

    @pytest.mark.asyncio
    async def test_structured_builtin_once_when_telemetry_fails(self) -> None:
        adapter = CountingBuiltin()
        tool = _structured_tool(adapter, AskAQuestionInput)
        emitter = OperationEmitter(fail_on_call=2)
        token = self.bind(emitter=emitter)
        payload = {"question": "Continue?"}
        try:
            result = await tool.ainvoke(payload)
        finally:
            OperationContext.unbind(token)

        assert result["question"] == payload["question"]
        assert adapter.calls == 1
        assert emitter.calls == 3


class CountingSubagent:
    def __init__(self) -> None:
        self.calls = 0

    def with_config(self, *_args: object, **_kwargs: object):
        return self

    def invoke(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("async task seam must not call sync invocation")

    async def ainvoke(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        return {"messages": [AIMessage(content="subagent done")]}


class TestSubagentShadowSeam(BoundContextMixin):
    def test_enforced_sync_task_reuses_authoritative_coroutine(self) -> None:
        subagent = CountingSubagent()
        tool = build_atlas_task_tool(
            [
                {
                    "name": "researcher",
                    "description": "Researches.",
                    "runnable": subagent,
                }
            ]
        )
        runtime = SimpleNamespace(
            tool_call_id="call-task-sync-enforced",
            state={},
            config={},
        )
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        try:
            assert tool.func is not None
            command = tool.func(
                description="Find facts",
                subagent_type="researcher",
                runtime=runtime,
            )
        finally:
            OperationContext.unbind(token)

        assert command is not None
        assert subagent.calls == 1

    @pytest.mark.asyncio
    async def test_enforced_task_and_dispatch_use_gateway_once_with_parent_link(
        self,
    ) -> None:
        subagent = CountingSubagent()
        tool = build_atlas_task_tool(
            [
                {
                    "name": "researcher",
                    "description": "Researches.",
                    "runnable": subagent,
                }
            ]
        )
        runtime = SimpleNamespace(
            tool_call_id="call-task-enforced",
            state={},
            config={},
        )
        emitter = OperationEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        try:
            assert tool.coroutine is not None
            command = await tool.coroutine(
                description="Find facts",
                subagent_type="researcher",
                runtime=runtime,
            )
        finally:
            OperationContext.unbind(token)

        assert command is not None
        assert subagent.calls == 1
        requested = [
            payload
            for event_type, payload, _ in emitter.events
            if event_type is LedgerEventType.OPERATION_REQUESTED
        ]
        assert len(requested) == 2
        outer, inner = requested
        assert outer["capability"] == "builtin"
        assert outer["op"] == "task"
        assert outer["producer"] == "model"
        assert inner["capability"] == "subagent"
        assert inner["op"] == "dispatch"
        assert inner["producer"] == "subagent"
        assert inner["parent_operation_id"] == outer["operation_id"]

    @pytest.mark.asyncio
    async def test_off_and_shadow_return_identical_subagent_command(self) -> None:
        subagent = CountingSubagent()
        tool = build_atlas_task_tool(
            [
                {
                    "name": "researcher",
                    "description": "Researches.",
                    "runnable": subagent,
                }
            ]
        )
        runtime = SimpleNamespace(
            tool_call_id="call-task-identity",
            state={},
            config={},
        )
        assert tool.coroutine is not None

        off_token = self.bind(mode=OperationGatewayMode.OFF)
        try:
            off_command = await tool.coroutine(
                description="Find facts",
                subagent_type="researcher",
                runtime=runtime,
            )
        finally:
            OperationContext.unbind(off_token)

        emitter = OperationEmitter(fail_on_call=2)
        shadow_token = self.bind(emitter=emitter)
        try:
            shadow_command = await tool.coroutine(
                description="Find facts",
                subagent_type="researcher",
                runtime=runtime,
            )
        finally:
            OperationContext.unbind(shadow_token)

        assert shadow_command == off_command
        assert subagent.calls == 2
        assert emitter.calls == 6

    @pytest.mark.asyncio
    async def test_delegation_and_dispatch_each_observe_one_real_call(
        self,
    ) -> None:
        subagent = CountingSubagent()
        tool = build_atlas_task_tool(
            [
                {
                    "name": "researcher",
                    "description": "Researches.",
                    "runnable": subagent,
                }
            ]
        )
        runtime = SimpleNamespace(
            tool_call_id="call-task-1",
            state={},
            config={},
        )
        emitter = OperationEmitter(fail_on_call=2)
        token = self.bind(emitter=emitter)
        try:
            assert tool.coroutine is not None
            command = await tool.coroutine(
                description="Find facts",
                subagent_type="researcher",
                runtime=runtime,
            )
        finally:
            OperationContext.unbind(token)

        assert subagent.calls == 1
        assert command is not None
        requested = [
            payload
            for event_type, payload, _ in emitter.events
            if event_type is LedgerEventType.OPERATION_REQUESTED
        ]
        assert len(requested) == 2
        outer, inner = requested
        assert outer["producer"] == "model"
        assert inner["producer"] == "subagent"
        assert inner["parent_operation_id"] == outer["operation_id"]
