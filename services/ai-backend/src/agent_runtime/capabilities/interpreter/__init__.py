"""AC6 Monty code mode — an embedded, gated Python-subset code interpreter.

Public surface for the registration seam and contracts. The Monty dependency is
imported lazily and only by :mod:`.monty_adapter`; importing this package does
not import Monty.
"""

from __future__ import annotations

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterCompleted,
    InterpreterErrorCode,
    InterpreterFailed,
    InterpreterLimitKind,
    InterpreterLimitProfiles,
    InterpreterLimits,
    InterpreterRequest,
    RunCodeModeInput,
    SnapshotRef,
)
from agent_runtime.capabilities.interpreter.policy_invoker import (
    AuthorizedToolResolver,
    ExternalCallApprovalGate,
    ExternalCallBudgetGuard,
    ExternalCallDispatcher,
    ExternalToolDispatchError,
    HitlPolicyToolInvoker,
    InterruptApprovalGate,
    LangChainToolDispatcher,
)
from agent_runtime.capabilities.interpreter.ports import (
    InterpreterPort,
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
    PolicyToolInvoker,
)
from agent_runtime.capabilities.interpreter.registration import (
    MontyCodeModeConfig,
    build_code_mode_tool,
    build_monty_interpreter,
    build_snapshot_store,
)

__all__ = (
    "AuthorizedToolResolver",
    "ExternalCallApprovalGate",
    "ExternalCallBudgetGuard",
    "ExternalCallDispatcher",
    "ExternalFunctionCall",
    "ExternalFunctionSpec",
    "ExternalToolDispatchError",
    "HitlPolicyToolInvoker",
    "InterpreterCompleted",
    "InterpreterErrorCode",
    "InterpreterFailed",
    "InterpreterLimitKind",
    "InterpreterLimitProfiles",
    "InterpreterLimits",
    "InterpreterPort",
    "InterpreterRequest",
    "InterruptApprovalGate",
    "LangChainToolDispatcher",
    "MontyCodeModeConfig",
    "PolicyInvocationContext",
    "PolicyToolInvocationOutcome",
    "PolicyToolInvoker",
    "RunCodeModeInput",
    "SnapshotRef",
    "build_code_mode_tool",
    "build_monty_interpreter",
    "build_snapshot_store",
)
