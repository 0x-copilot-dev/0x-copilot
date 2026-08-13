"""The model-facing ``run_tool_program`` tool and its per-run factory.

The factory is the seam ``execution/factory._model_visible_tools`` calls once the
run's model-visible toolset is fully composed — which is the only moment at which
the authorized dispatch surface for a program is actually known. It binds that
exact toolset and nothing wider.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinOperationAdapter,
)
from agent_runtime.capabilities.tool_program.contracts import (
    RunToolProgramInput,
    ToolProgramErrorCode,
    ToolProgramLimits,
    ToolProgramResult,
)
from agent_runtime.capabilities.tool_program.dispatch import MiddlewareStepDispatcher
from agent_runtime.capabilities.tool_program.executor import ToolProgramExecutor

TOOL_NAME = "run_tool_program"
TOOL_DESCRIPTION = (
    "Run several tool calls as one plan instead of one call per turn. Each step "
    "names a tool you are already allowed to call. A step argument may "
    'reference an earlier step\'s output structurally as {"$from": "<step id>", '
    '"path": ["key", 0]}; steps that reference nothing run concurrently. Only '
    "`result` comes back — build it from the same reference markers so the "
    "intermediate payloads never enter the conversation. Use it for dependent "
    "or fan-in reads. Do not use it for a step that needs human approval: the "
    "program stops and tells you to make that call directly."
)


@runtime_checkable
class ToolProgramToolFactoryPort(Protocol):
    """Construction seam for the program tool, called with the run's toolset.

    ``execution/factory`` owns the toolset but must not own limit resolution;
    the worker owns limit resolution but cannot see the toolset until the
    factory has built it. This port is that handshake, and it is why the program
    can never be bound to a wider surface than the run already exposes.
    """

    def build_tool(self, *, tools_by_name: Mapping[str, object]) -> object | None: ...


class ToolProgramToolFactory:
    """Builds ``run_tool_program`` over the run's authorized toolset.

    It composes no policy of its own. The executor it builds reaches tools only
    through :class:`MiddlewareStepDispatcher`, which routes each step back
    through the graph's own tool seam — so a batched step is admitted, budgeted
    and result-capped by exactly the code a direct call is.
    """

    def __init__(self, *, limits: ToolProgramLimits) -> None:
        self._limits = limits

    def build_tool(self, *, tools_by_name: Mapping[str, object]) -> object | None:
        """Return the program tool, or ``None`` when there is nothing to batch."""

        callable_tools = {
            name: tool
            for name, tool in tools_by_name.items()
            # The program never lists itself: a program that could plan a
            # program is unbounded recursion with a bounded step count.
            if name != TOOL_NAME and callable(getattr(tool, "ainvoke", None))
        }
        if not callable_tools:
            return None
        executor = ToolProgramExecutor(
            dispatcher=MiddlewareStepDispatcher(tools_by_name=callable_tools),
            authorized_tool_names=frozenset(callable_tools),
            limits=self._limits,
        )
        return RunToolProgramTool.build(executor=executor)


class RunToolProgramTool:
    """Wraps :class:`ToolProgramExecutor` as a LangChain ``StructuredTool``."""

    OPERATION = BuiltinOperationAdapter(tool_name=TOOL_NAME)
    SAFE_SUMMARY = "Tool program completed."

    @classmethod
    def build(cls, *, executor: ToolProgramExecutor) -> StructuredTool:
        async def _run_tool_program(
            steps: tuple[dict, ...] = (), result: object = None
        ) -> str:
            program = RunToolProgramInput.model_validate(
                {"steps": steps, "result": result}
            )

            async def _legacy() -> str:
                outcome = await executor.run(program)
                return json.dumps(outcome.model_dump(mode="json"))

            invocation = await cls.OPERATION.execute(
                arguments=program.model_dump(mode="json"),
                legacy=_legacy,
                safe_summary=cls.SAFE_SUMMARY,
            )
            if invocation.value is not None:
                return invocation.value
            return json.dumps(
                ToolProgramResult(
                    status="failed",
                    error_code=ToolProgramErrorCode.STEP_DENIED,
                    safe_message=invocation.safe_summary,
                ).model_dump(mode="json")
            )

        return StructuredTool.from_function(
            coroutine=_run_tool_program,
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            args_schema=RunToolProgramInput,
        )


__all__ = (
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "RunToolProgramTool",
    "ToolProgramToolFactory",
    "ToolProgramToolFactoryPort",
)
