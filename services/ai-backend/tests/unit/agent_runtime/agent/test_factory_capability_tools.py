"""Gated Wave-1 capability tools (Monty code mode, remote sandbox execute).

The factory appends each tool to the model-visible tool set and adds its prompt
guidance ONLY when the corresponding ``RuntimeDependencies`` slot is populated
(the worker built it because its flag+desktop gate held). When the slots are
``None`` — the default, and the only state on non-desktop / disabled runs — the
tools are absent and the prompt is unchanged (byte-identical).
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


class _Arg(BaseModel):
    value: str = ""


def _fake_tool(name: str) -> StructuredTool:
    async def _run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=_run, name=name, description=f"{name} tool.", args_schema=_Arg
    )


async def _tool_names_and_prompt(
    context: AgentRuntimeContext, dependencies: RuntimeDependencies
) -> tuple[set[str], str]:
    builder = CapturingAgentBuilder()
    await acreate_agent_runtime(
        context=context, dependencies=dependencies, agent_builder=builder
    )
    call = builder.calls[0]
    names = {str(getattr(tool, "name", "")) for tool in call.tools}
    return names, call.system_prompt


class TestCapabilityToolsAbsentByDefault:
    async def test_no_gated_tools_when_slots_unset(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        names, prompt = await _tool_names_and_prompt(
            runtime_context_admin, fake_dependencies
        )
        assert "run_code_mode" not in names
        assert "run_in_sandbox" not in names
        assert "run_code_mode" not in prompt
        assert "run_in_sandbox" not in prompt

    async def test_default_slots_are_none(
        self, fake_dependencies: RuntimeDependencies
    ) -> None:
        assert fake_dependencies.code_mode_tool is None
        assert fake_dependencies.sandbox_execute_tool is None


class TestCodeModeToolRegistration:
    async def test_present_when_slot_populated(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        deps = fake_dependencies.model_copy(
            update={"code_mode_tool": _fake_tool("run_code_mode")}
        )
        names, prompt = await _tool_names_and_prompt(runtime_context_admin, deps)
        assert "run_code_mode" in names
        # Guidance is present and states the pure-compute limitation.
        assert "run_code_mode" in prompt
        assert "calculation" in prompt.lower()
        # Sandbox stays absent — the two gates are independent.
        assert "run_in_sandbox" not in names


class TestSandboxExecuteToolRegistration:
    async def test_present_when_slot_populated(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        deps = fake_dependencies.model_copy(
            update={"sandbox_execute_tool": _fake_tool("run_in_sandbox")}
        )
        names, prompt = await _tool_names_and_prompt(runtime_context_admin, deps)
        assert "run_in_sandbox" in names
        assert "run_in_sandbox" in prompt
        # Code mode stays absent — the two gates are independent.
        assert "run_code_mode" not in names

    async def test_both_present_together(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        deps = fake_dependencies.model_copy(
            update={
                "code_mode_tool": _fake_tool("run_code_mode"),
                "sandbox_execute_tool": _fake_tool("run_in_sandbox"),
            }
        )
        names, prompt = await _tool_names_and_prompt(runtime_context_admin, deps)
        assert {"run_code_mode", "run_in_sandbox"} <= names
        assert "run_code_mode" in prompt
        assert "run_in_sandbox" in prompt
