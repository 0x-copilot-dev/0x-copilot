"""Typed lifecycle hooks: the runtime's single in-process extension seam.

See :mod:`agent_runtime.hooks.contracts` for the phase catalogue, the
non-widening rules that are enforced in the types, and why there is
deliberately no ``tool.definition`` hook.
"""

from agent_runtime.hooks.contracts import (
    HookInvocationRecord,
    HookInvocationStatus,
    HookPhase,
    ModelRequestBeforeInput,
    PolicyDecideAfterInput,
    PromptAssembleAction,
    PromptAssembleInput,
    PromptAssembleOutcome,
    RunLifecycleInput,
    ToolExecuteAfterAction,
    ToolExecuteAfterInput,
    ToolExecuteAfterOutcome,
    ToolExecuteBeforeAction,
    ToolExecuteBeforeInput,
    ToolExecuteBeforeOutcome,
)
from agent_runtime.hooks.dispatch import HookDispatch, ToolCallVerdict
from agent_runtime.hooks.registry import (
    HookLedger,
    HookLedgerSummary,
    HookRegistry,
    HookSession,
    RegisteredHook,
    RuntimeHookContext,
    RuntimeHooks,
)

__all__ = [
    "HookDispatch",
    "HookInvocationRecord",
    "HookInvocationStatus",
    "HookLedger",
    "HookLedgerSummary",
    "HookPhase",
    "HookRegistry",
    "HookSession",
    "ModelRequestBeforeInput",
    "PolicyDecideAfterInput",
    "PromptAssembleAction",
    "PromptAssembleInput",
    "PromptAssembleOutcome",
    "RegisteredHook",
    "RunLifecycleInput",
    "RuntimeHookContext",
    "RuntimeHooks",
    "ToolCallVerdict",
    "ToolExecuteAfterAction",
    "ToolExecuteAfterInput",
    "ToolExecuteAfterOutcome",
    "ToolExecuteBeforeAction",
    "ToolExecuteBeforeInput",
    "ToolExecuteBeforeOutcome",
]
