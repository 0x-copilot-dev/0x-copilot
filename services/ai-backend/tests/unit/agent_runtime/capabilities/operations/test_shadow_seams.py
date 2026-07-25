from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpLoader,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.tools.builtin.ask_a_question import (
    AskAQuestionInput,
)
from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_atlas_task_tool,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import _structured_tool
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from tests.unit.agent_runtime.capabilities.desktop.test_workspace_backend_write import (
    WriteBackendMixin,
)
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
    RecordingEmitter as OperationEmitter,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


class TestMcpShadowSeam(DynamicMcpLoadingMixin, BoundContextMixin):
    @pytest.mark.asyncio
    async def test_off_and_shadow_return_byte_equivalent_provider_result(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        client = self.FakeMcpClient(
            tools=(self.make_tool(name="get_issue"),),
            resources=(),
            tool_outputs={"get_issue": {"issue": {"id": "ENG-1"}}},
        )
        original_call = client.call_tool
        calls = 0

        async def counting_call(**kwargs: object):
            nonlocal calls
            calls += 1
            return await original_call(**kwargs)  # type: ignore[arg-type]

        client.call_tool = counting_call  # type: ignore[method-assign]
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name="linear"),),
            clients={"linear": client},
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )
        arguments = {
            "server_name": "linear",
            "tool_name": "get_issue",
            "arguments": {"id": "ENG-1"},
        }

        off_token = self.bind(mode=OperationGatewayMode.OFF)
        try:
            off_result = await tool.ainvoke(arguments)
        finally:
            OperationContext.unbind(off_token)

        emitter = OperationEmitter(fail_on_call=2)
        shadow_token = self.bind(emitter=emitter)
        try:
            shadow_result = await tool.ainvoke(arguments)
        finally:
            OperationContext.unbind(shadow_token)

        assert shadow_result == off_result
        assert calls == 2
        assert emitter.calls == 3

    @pytest.mark.asyncio
    async def test_provider_dispatch_once_when_second_shadow_event_fails(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        client = self.FakeMcpClient(
            tools=(self.make_tool(name="get_issue"),),
            resources=(),
            tool_outputs={"get_issue": {"issue": {"id": "ENG-1"}}},
        )
        original_call = client.call_tool
        calls = 0

        async def counting_call(**kwargs: object):
            nonlocal calls
            calls += 1
            return await original_call(**kwargs)  # type: ignore[arg-type]

        client.call_tool = counting_call  # type: ignore[method-assign]
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name="linear"),),
            clients={"linear": client},
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )
        emitter = OperationEmitter(fail_on_call=2)
        token = self.bind(emitter=emitter)
        try:
            result = await tool.ainvoke(
                {
                    "server_name": "linear",
                    "tool_name": "get_issue",
                    "arguments": {"id": "ENG-1"},
                }
            )
        finally:
            OperationContext.unbind(token)

        assert calls == 1
        assert result["output"] == {"issue": {"id": "ENG-1"}}
        assert "surface" not in result
        assert emitter.calls == 3


class TestWorkspaceShadowSeam(WriteBackendMixin, BoundContextMixin):
    @pytest.mark.asyncio
    async def test_off_and_shadow_return_identical_write_result(self) -> None:
        off_backend, off_broker, _store, _snapshot_emitter = self.wired()
        off_token = self.bind(mode=OperationGatewayMode.OFF)
        try:
            off_result = await off_backend.awrite("/proj/new.md", "# Notes")
        finally:
            OperationContext.unbind(off_token)

        shadow_backend, shadow_broker, _store, _snapshot_emitter = self.wired()
        emitter = OperationEmitter(fail_on_call=2)
        shadow_token = self.bind(emitter=emitter)
        try:
            shadow_result = await shadow_backend.awrite(
                "/proj/new.md",
                "# Notes",
            )
        finally:
            OperationContext.unbind(shadow_token)

        assert shadow_result == off_result
        assert self._mutations(shadow_broker) == self._mutations(off_broker)
        assert len(self._mutations(shadow_broker)) == 1
        assert emitter.calls == 3

    @pytest.mark.asyncio
    async def test_write_once_when_shadow_emitter_fails_after_request(
        self,
    ) -> None:
        backend, broker, _store, _snapshot_emitter = self.wired()
        emitter = OperationEmitter(fail_on_call=2)
        token = self.bind(emitter=emitter)
        try:
            result = await backend.awrite("/proj/new.md", "# Notes")
        finally:
            OperationContext.unbind(token)

        assert result.error is None
        assert self._mutations(broker) == [
            (
                "/v1/fs/write",
                {
                    "grant_id": self.GRANT_RW,
                    "path": "new.md",
                    "content_base64": "IyBOb3Rlcw==",
                    "run_capability_context": "rcx_test_pinned",
                },
            )
        ]
        assert emitter.calls == 3

    @pytest.mark.asyncio
    async def test_edit_once_when_shadow_emitter_fails_after_request(
        self,
    ) -> None:
        backend, broker, _store, _snapshot_emitter = self.wired(
            files={"notes.md": b"old"}
        )
        emitter = OperationEmitter(fail_on_call=2)
        token = self.bind(emitter=emitter)
        try:
            result = await backend.aedit(
                "/proj/notes.md",
                "old",
                "new",
            )
        finally:
            OperationContext.unbind(token)

        assert result.error is None
        assert len(self._mutations(broker)) == 1
        assert broker.grants[self.GRANT_RW].files["notes.md"] == b"new"
        assert emitter.calls == 3


@dataclass
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
