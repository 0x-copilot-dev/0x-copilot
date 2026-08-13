"""``run_tool_program`` — batched, dependent tool execution in one model turn.

A declarative plan (steps + structural references + a final projection) executed
by the runtime, so a three-step "list, filter, fetch" costs one model turn and
one tool result instead of three of each.

It is deliberately **not** an interpreter. There is no sandbox boundary inside
this process to get wrong: a step is a tool name plus arguments, and the only
evaluation performed is walking a typed path into a prior step's output. Each
step is dispatched back through the graph's own tool seam, so batching adds no
policy surface of its own — see :mod:`.dispatch`.
"""

from __future__ import annotations

from agent_runtime.capabilities.tool_program.contracts import (
    RunToolProgramInput,
    StepOutcome,
    StepRef,
    StepStatus,
    ToolProgramError,
    ToolProgramErrorCode,
    ToolProgramLimits,
    ToolProgramResult,
    ToolProgramStep,
)
from agent_runtime.capabilities.tool_program.dispatch import (
    MiddlewareStepDispatcher,
    ProgramStepCallRuntime,
    StepDispatchOutcome,
    StepDispatchStatus,
    ToolProgramStepDispatcher,
)
from agent_runtime.capabilities.tool_program.executor import ToolProgramExecutor
from agent_runtime.capabilities.tool_program.plan import (
    ReferenceWalker,
    ToolProgramPlan,
)
from agent_runtime.capabilities.tool_program.tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    RunToolProgramTool,
    ToolProgramToolFactory,
    ToolProgramToolFactoryPort,
)

__all__ = (
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "MiddlewareStepDispatcher",
    "ProgramStepCallRuntime",
    "ReferenceWalker",
    "RunToolProgramInput",
    "RunToolProgramTool",
    "StepDispatchOutcome",
    "StepDispatchStatus",
    "StepOutcome",
    "StepRef",
    "StepStatus",
    "ToolProgramError",
    "ToolProgramErrorCode",
    "ToolProgramExecutor",
    "ToolProgramLimits",
    "ToolProgramPlan",
    "ToolProgramResult",
    "ToolProgramStep",
    "ToolProgramStepDispatcher",
    "ToolProgramToolFactory",
    "ToolProgramToolFactoryPort",
)
