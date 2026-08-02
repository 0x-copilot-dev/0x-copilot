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

This is the **outermost** wrapper of the built-in tool chain
(``ToolErrorPolicyRegistry`` over ``CitationCapturingRegistry`` over
``WebSearchToolRegistry``, see
:mod:`runtime_worker.dependencies`), which makes it the tool LangChain
actually dispatches — and LangChain reads ``response_format`` off the tool it
dispatches, not off anything nested inside it. Both halves of that fact are
load-bearing here:

* The wrapper must present the inner's **whole** surface, so it is built from
  :class:`~agent_runtime.capabilities.mcp.middleware.compose.ToolSchemaIdentity`
  rather than a hand-listed ``name`` / ``description`` / ``args_schema``. That
  hand-listed subset is what silently dropped ``response_format`` from
  ``web_search``: every inner layer declared ``content_and_artifact`` and
  returned ``(results, raw_results)`` while this layer declared plain
  ``content``, so LangChain stringified the whole pair into the model-visible
  ``ToolMessage.content`` and left ``artifact`` ``None`` on every call.
* The surfaced-error path returns a value this wrapper **authored itself**, so
  it has to be rendered in the shape the wrapper now promises — via
  :meth:`~agent_runtime.capabilities.mcp.middleware.compose.ToolResultShape.render`.
  A bare string handed back under ``content_and_artifact`` makes
  ``BaseTool.arun`` raise on the unpack, i.e. the error handler would crash on
  the very call it was there to rescue.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.middleware.compose import (
    ToolResultShape,
    ToolSchemaIdentity,
)
from pydantic import ConfigDict

from agent_runtime.execution.tool_error_policy import (
    DefaultToolErrorPolicy,
    ToolErrorClassification,
    ToolErrorOutcome,
    ToolErrorPolicy,
)
from agent_runtime.execution.tool_errors import RunFatalToolError

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


class ToolErrorPolicyTool(DelegatingTool):
    """LangChain ``BaseTool`` wrapper that catches & routes inner errors."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    policy: ToolErrorPolicy = DefaultToolErrorPolicy()  # type: ignore[assignment]

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync invocation path: classify exceptions and surface or re-raise per policy."""
        try:
            return self.delegate(*args, config=config, **kwargs)
        except _NEVER_CLASSIFY:
            raise
        except RunFatalToolError:
            # Typed fatal errors always propagate; the run handler routes them.
            raise
        except BaseException as exc:  # noqa: BLE001 — intentional breadth
            classification = self.policy.classify(exc, tool=self.inner)
            if classification.outcome is ToolErrorOutcome.FAIL_RUN:
                # Policy decided this exception should end the run; wrap as
                # RunFatalToolError so the handler routes it correctly.
                raise RunFatalToolError(
                    classification.sanitized_message,
                    audit_summary=classification.audit_trace,
                ) from exc
            self._log_surfaced(classification, sync=True)
            return self._surfaced_result(classification)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async invocation path: classify exceptions and surface or re-raise per policy."""
        try:
            return await self.adelegate(*args, config=config, **kwargs)
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
            return self._surfaced_result(classification)

    def _surfaced_result(self, classification: ToolErrorClassification) -> Any:
        """Render the sanitized error in the return shape this wrapper declares.

        The classification renders as a bare string. Under
        ``response_format="content_and_artifact"`` — which every MCP tool and
        the built-in ``web_search`` declare, and which this wrapper now
        propagates — LangChain unpacks exactly two values from a tool return and
        raises ``ValueError`` on anything else. ``ToolResultShape`` is the one
        definition of that mapping; the artifact half is ``None`` because a call
        that failed produced no raw tool output to carry.
        """

        return ToolResultShape.render(
            classification.to_llm_message_content(),
            response_format=self.response_format,
        )

    def _log_surfaced(
        self,
        classification: ToolErrorClassification,
        *,
        sync: bool,
    ) -> None:
        """Log a structured ``tool_error_surfaced_to_llm`` event."""
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
        """Wrap ``inner`` registry; default policy when none is supplied."""
        self._inner = inner
        self._policy: ToolErrorPolicy = policy or DefaultToolErrorPolicy()

    def list_available_tools(self, context: object) -> tuple[object, ...]:
        """Return every tool from the inner registry wrapped in :class:`ToolErrorPolicyTool`."""
        rendered = self._inner.list_available_tools(context)  # type: ignore[attr-defined]
        return tuple(self._wrap(tool) for tool in rendered)

    def _wrap(self, tool: object) -> object:
        """Wrap a single ``BaseTool`` in :class:`ToolErrorPolicyTool`; pass non-tools through unchanged.

        The propagated field set is
        :class:`~agent_runtime.capabilities.mcp.middleware.compose.ToolSchemaIdentity`'s
        — the one definition of what a wrapper reproduces from the tool it
        wraps, covering the dispatch surface (``response_format`` /
        ``return_direct`` / ``metadata`` / ``tags``) as well as the
        model-visible one. This site is where a hand-listed subset does the most
        damage: the tools it returns are the outermost layer, so whatever it
        fails to propagate is what LangChain sees.
        """
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(tool, ToolErrorPolicyTool):
            return tool
        return ToolErrorPolicyTool(
            **ToolSchemaIdentity.fields_of(tool),
            inner=tool,
            policy=self._policy,
        )


__all__ = (
    "ToolErrorPolicyRegistry",
    "ToolErrorPolicyTool",
)
