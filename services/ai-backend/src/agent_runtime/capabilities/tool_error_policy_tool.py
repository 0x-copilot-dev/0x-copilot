"""LangChain ``BaseTool`` wrapper that routes failures through the policy.

Every tool the model can call ends up wrapped in this class. The wrapper
is the single chokepoint where tool exceptions get classified:

* ``SURFACE_TO_LLM`` — the wrapper returns the sanitized error as the
  tool's result. LangChain treats the return value as a normal tool
  output, so the agent's next model step sees a ``ToolMessage``
  containing the error + structured hints. The run does NOT fail.
* ``FAIL_RUN`` — typed :class:`RunFatalToolError`. The wrapper re-raises
  the exception, the run handler catches it, and the
  :class:`RunTerminationCoordinator` ends the run.

Cancellation, ``KeyboardInterrupt``, and ``SystemExit`` are re-raised
without classification — they are never routed through the policy.

This mirrors the structure of
:class:`agent_runtime.capabilities.tool_budget_guard.ToolBudgetGuardedTool`:
inner ``name``/``description``/``args_schema`` are propagated unchanged
so the model sees an identical surface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.execution.tool_error_policy import (
    DefaultToolErrorPolicy,
    ToolErrorOutcome,
    ToolErrorPolicy,
)
from agent_runtime.execution.tool_errors import RunFatalToolError
from agent_runtime.execution.tool_error_policy import ToolErrorClassification

_LOGGER = logging.getLogger("agent_runtime.capabilities.tool_error_policy_tool")


# Exceptions that must always propagate, never be classified. The policy
# would never see these in practice because the inner tool's contract
# already passes them through, but we double-check here for defense in
# depth so a misconfigured policy can't suppress cancellation.
_NEVER_CLASSIFY: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


class ToolErrorPolicyTool(BaseTool):
    """LangChain ``BaseTool`` wrapper that catches & routes inner errors."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    policy: ToolErrorPolicy = DefaultToolErrorPolicy()  # type: ignore[assignment]

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self.inner._run(*args, **kwargs)
        except _NEVER_CLASSIFY:
            raise
        except RunFatalToolError:
            # Typed fatal errors propagate. The run handler catches and
            # routes through the coordinator.
            raise
        except BaseException as exc:  # noqa: BLE001 — intentional breadth
            classification = self.policy.classify(exc, tool=self.inner)
            if classification.outcome is ToolErrorOutcome.FAIL_RUN:
                # The policy decided this should end the run even though
                # the exception wasn't a RunFatalToolError subclass.
                # Surface as RunFatalToolError so the run handler routes
                # it correctly.
                raise RunFatalToolError(
                    classification.sanitized_message,
                    audit_summary=classification.audit_trace,
                ) from exc
            self._log_surfaced(classification, sync=True)
            return classification.to_llm_message_content()

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self.inner._arun(*args, **kwargs)
        except _NEVER_CLASSIFY:
            raise
        except RunFatalToolError:
            raise
        except BaseException as exc:  # noqa: BLE001
            classification = self.policy.classify(exc, tool=self.inner)
            if classification.outcome is ToolErrorOutcome.FAIL_RUN:
                raise RunFatalToolError(
                    classification.sanitized_message,
                    audit_summary=classification.audit_trace,
                ) from exc
            self._log_surfaced(classification, sync=False)
            return classification.to_llm_message_content()

    def _log_surfaced(
        self,
        classification: "ToolErrorClassification",  # type: ignore[name-defined]
        *,
        sync: bool,
    ) -> None:
        _LOGGER.info(
            "tool_error_surfaced_to_llm",
            extra={
                "metadata": {
                    "tool_name": self.name,
                    "error_class": classification.error_class,
                    "category": classification.structured_hints.get("category"),
                    "sync_path": sync,
                }
            },
        )


class ToolErrorPolicyRegistry:
    """Wrap a tool registry so every returned tool routes errors via policy.

    Matches the decorator pattern used by
    :class:`agent_runtime.capabilities.tool_budget_guard.ToolBudgetGuardedRegistry`:
    the wrapped registry's ``list_available_tools`` is rewritten to wrap
    each LangChain ``BaseTool`` in a :class:`ToolErrorPolicyTool`. Non-
    ``BaseTool`` entries (internal adapters) pass through untouched.
    """

    def __init__(
        self,
        *,
        inner: object,
        policy: ToolErrorPolicy | None = None,
    ) -> None:
        self._inner = inner
        self._policy: ToolErrorPolicy = policy or DefaultToolErrorPolicy()

    def list_available_tools(self, context: object) -> tuple[object, ...]:
        rendered = self._inner.list_available_tools(context)  # type: ignore[attr-defined]
        return tuple(self._wrap(tool) for tool in rendered)

    def _wrap(self, tool: object) -> object:
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(tool, ToolErrorPolicyTool):
            return tool
        return ToolErrorPolicyTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            inner=tool,
            policy=self._policy,
        )


__all__ = (
    "ToolErrorPolicyRegistry",
    "ToolErrorPolicyTool",
)
