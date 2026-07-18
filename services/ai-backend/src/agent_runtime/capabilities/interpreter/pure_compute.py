"""Pure-compute posture for AC6 code mode (the shipping default).

Code mode ships **calculation / transformation only** until the direct-path
four-mode tool-policy engine is wired (see :class:`PolicyToolInvoker` in
``ports.py``: *"the direct-path four-mode engine is not yet wired; until it
lands, AC6 ships pure-compute-only"*). There is no production
``PolicyToolInvoker`` / ``ExternalFunctionResolver`` yet — reusing the normal
tool approval path is not possible because that path is deepagents'
interrupt-based ``HumanInTheLoopMiddleware``, which is not exposed as the
synchronous ``invoke()`` the interpreter calls mid-program.

Rather than weaken anything, this module supplies the **fail-closed** collaborators
the runtime wires today:

* :class:`PureComputeResolver` — resolves **no** alias, so any external function
  the model declares fails closed with ``EXTERNAL_FUNCTION_UNKNOWN`` before a
  program even starts. Interpreted code therefore has no tool surface at all.
* :class:`ClosedPolicyInvoker` — a defence-in-depth backstop for the invoke seam
  that can never be reached while the resolver resolves nothing; if it ever is,
  it returns ``DENIED`` (never ``ALLOWED``), so no side effect can occur.

When the real policy engine lands, swap these two for the production
resolver/invoker at the wiring site — the interpreter bridge does not change.
"""

from __future__ import annotations

from uuid import uuid4

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterErrorCode,
)
from agent_runtime.capabilities.interpreter.ports import (
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
)


class PureComputeResolver:
    """External-function resolver that authorizes nothing (pure-compute mode)."""

    def resolve(self, alias: str) -> ExternalFunctionSpec | None:
        """Return ``None`` for every alias so no tool is ever reachable."""

        del alias
        return None


class ClosedPolicyInvoker:
    """Fail-closed policy invoker: never ``ALLOWED``, so no side effect can run.

    Unreachable while :class:`PureComputeResolver` is in use (nothing resolves,
    so nothing suspends for a policy decision); present only as a hard backstop
    for the invoke seam so an unexpected code path cannot execute a real tool.
    """

    async def invoke(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> PolicyToolInvocationOutcome:
        """Deny every external call; the underlying tool is never run."""

        del call, context
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.DENIED,
            invocation_id=uuid4().hex,
            error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN,
            safe_message="external functions are not available in code mode",
        )


__all__ = ("ClosedPolicyInvoker", "PureComputeResolver")
