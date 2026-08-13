"""The model-facing ``run_tool_program`` tool and its per-run factory.

The factory is the seam ``execution/factory._model_visible_tools`` calls once the
run's model-visible toolset is fully composed — which is the only moment at which
the authorized dispatch surface for a program is actually known. It binds that
exact toolset and nothing wider.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
)
from agent_runtime.capabilities.interpreter.policy_invoker import (
    AuthorizedToolResolver,
    ExternalCallBudgetGuard,
    HitlPolicyToolInvoker,
    LangChainToolDispatcher,
)
from agent_runtime.capabilities.interpreter.ports import PolicyInvocationContext
from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinOperationAdapter,
)
from agent_runtime.capabilities.tool_program.contracts import (
    RunToolProgramInput,
    ToolProgramErrorCode,
    ToolProgramLimits,
    ToolProgramResult,
)
from agent_runtime.capabilities.tool_program.executor import (
    ProgramIdentity,
    ToolProgramExecutor,
)

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

#: Supplies the current run identity from trusted context. The model never
#: influences it.
ProgramIdentityProvider = Callable[[], ProgramIdentity]


@runtime_checkable
class ToolProgramToolFactoryPort(Protocol):
    """Construction seam for the program tool, called with the run's toolset.

    ``execution/factory`` owns the toolset but must not own policy composition;
    the worker owns policy composition but cannot see the toolset until the
    factory has built it. This port is that handshake, and it is why the program
    can never be bound to a wider surface than the run already exposes.
    """

    def build_tool(self, *, tools_by_name: Mapping[str, object]) -> object | None: ...


class ProgramApprovalGate:
    """States the program's approval posture: it adds none of its own.

    Each step dispatches to the run's already-composed tool object, whose own
    pipeline owns the ALLOW / DENY / GATE decision for that call. Layering a
    second blanket approval on top would double-prompt every read. A tool that
    *does* need a human raises the runtime's approval interrupt from inside its
    own pipeline, and :class:`ToolProgramExecutor` stops the program there — see
    that module's header for why declining beats parking.
    """

    async def request_approval(
        self,
        *,
        spec: ExternalFunctionSpec,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> bool:
        del spec, call, context
        return True


class ToolProgramToolFactory:
    """Builds ``run_tool_program`` over the run's authorized toolset.

    Composes the **shared** :class:`HitlPolicyToolInvoker` rather than a private
    dispatch path, so a batched step is charged against the same per-run tool
    budget and dispatched through the same seam a direct call uses.
    """

    def __init__(
        self,
        *,
        identity_provider: ProgramIdentityProvider,
        limits: ToolProgramLimits,
        budget: ExternalCallBudgetGuard,
    ) -> None:
        self._identity_provider = identity_provider
        self._limits = limits
        self._budget = budget

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
            invoker=HitlPolicyToolInvoker(
                budget=self._budget,
                approval=ProgramApprovalGate(),
                dispatcher=LangChainToolDispatcher(callable_tools),
            ),
            resolver=AuthorizedToolResolver(callable_tools),
            limits=self._limits,
        )
        return RunToolProgramTool.build(
            executor=executor, identity_provider=self._identity_provider
        )


class RunToolProgramTool:
    """Wraps :class:`ToolProgramExecutor` as a LangChain ``StructuredTool``."""

    OPERATION = BuiltinOperationAdapter(tool_name=TOOL_NAME)
    SAFE_SUMMARY = "Tool program completed."

    @classmethod
    def build(
        cls,
        *,
        executor: ToolProgramExecutor,
        identity_provider: ProgramIdentityProvider,
    ) -> StructuredTool:
        async def _run_tool_program(
            steps: tuple[dict, ...] = (), result: object = None
        ) -> str:
            program = RunToolProgramInput.model_validate(
                {"steps": steps, "result": result}
            )

            async def _legacy() -> str:
                outcome = await executor.run(program, identity=identity_provider())
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
    "ProgramApprovalGate",
    "ProgramIdentityProvider",
    "RunToolProgramTool",
    "ToolProgramToolFactory",
    "ToolProgramToolFactoryPort",
)
