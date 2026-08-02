"""CITATIONS stage — project one MCP result into the citation ledger (P2-5).

**Composed by the P2-8 registration flip**, behind ``MCP_PER_TOOL_ENABLED``
(default OFF). Until that flag is on for a deployment, the legacy
``call_mcp_tool`` gateway still owns the dispatch and nothing here runs.

The stage owns no extraction logic. Detecting sources inside a tool result — the
envelope walk, the content-block / results-list / resource shapes, the per-result
cap — already lives in
:class:`~agent_runtime.capabilities.citation_projection.CitationProjector`,
reached through the existing MCP-facing
:class:`~agent_runtime.capabilities.mcp.middleware.cite_mcp.CitationProjectingMcpMiddleware`.
This stage is the per-tool *call site* for it: it forwards
``(connector, tool_call_id, result)`` and nothing else.

The connector key is the **descriptor URN's namespace**, not the tool's name.
Under the legacy gateway every MCP result was projected under the connector the
model named in ``args.server_name``; under per-tool registration the tool name
IS the capability, so keying on it would file every connector's sources under a
different label and break ``[[N]]`` attribution. The URN namespace is the same
``server_slug`` the source registered and the stream seams resolve — one
connector, one identity, everywhere.

Innermost of the five stages (``MIDDLEWARE_ORDER``), so it sees the raw tool
result before any other stage transforms it, and it never sees a call the POLICY
stage refused. Projection is best-effort: a citation failure must never poison a
successful tool result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.middleware.cite_mcp import (
    CitationProjectingMcpMiddleware,
)
from agent_runtime.capabilities.mcp.middleware.compose import ToolSchemaIdentity
from agent_runtime.capabilities.mcp.middleware.observe_tool import McpCallBindingScope
from agent_runtime.capabilities.policy.contracts import (
    CapabilityDescriptor,
    CapabilityUrn,
    MiddlewareStage,
)

_LOGGER = logging.getLogger(__name__)

#: See ``McpObservedTool._tool_call_id`` — read, never added, never removed.
_TOOL_CALL_ID = "tool_call_id"


class McpCitationsTool(DelegatingTool):
    """Schema-identical wrapper that projects each result to the citation ledger.

    Only the async path projects, mirroring
    :class:`~agent_runtime.capabilities.citation_capturing_tool.CitationCapturingTool`:
    the ledger is async, the runtime always dispatches through ``_arun``, and a
    sync call (a direct unit-test invocation) must not need an event loop.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    #: Connector slug from the descriptor URN — the ledger's source key.
    connector: str

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync path: delegate unchanged — projection requires the async path."""

        return self.delegate(*args, config=config, **kwargs)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async path: delegate, then forward the result for source detection."""

        result = await self.adelegate(*args, config=config, **kwargs)
        await self._project(result, kwargs)
        return result

    async def _project(self, result: object, kwargs: dict[str, Any]) -> None:
        """Forward one result to the citation projector, best-effort.

        Never raises: a failure to record where an answer came from must not
        turn a successful connector call into a failed one. The result itself is
        returned to the model untouched either way.
        """

        try:
            await CitationProjectingMcpMiddleware.project(
                connector=self.connector,
                tool_call_id=self._tool_call_id(kwargs),
                result=result,
            )
        except Exception:  # noqa: BLE001 — best-effort enrichment
            _LOGGER.warning(
                "mcp_citation_projection_failed",
                extra={
                    "metadata": {
                        "connector": self.connector,
                        "tool_name": self.name,
                    }
                },
                exc_info=True,
            )

    @staticmethod
    def _tool_call_id(kwargs: dict[str, Any]) -> str | None:
        """Return this call's id: the injected kwarg, else the OBSERVE binding.

        The kwarg exists only when the inner tool's schema declares
        ``InjectedToolCallId``; it is read, not consumed, because removing an
        argument the inner asked for would break it. The OBSERVE stage sits
        directly outside this one and binds the same value, so the fallback
        costs nothing and keeps the two stages agreeing on one id. ``None`` is
        accepted by the ledger — a source with no call id is still a source.
        """

        value = kwargs.get(_TOOL_CALL_ID)
        if isinstance(value, str) and value:
            return value
        binding = McpCallBindingScope.current()
        return binding.tool_call_id if binding is not None else None


@dataclass(frozen=True)
class McpCitationsMiddleware:
    """The CITATIONS stage of the P0 pipeline."""

    # ``init=False`` -- see the note on the exec-policy stage: a mislabelled
    # middleware would defeat the composer's whole order guarantee.
    stage: MiddlewareStage = field(default=MiddlewareStage.CITATIONS, init=False)

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return ``tool`` wrapped so its results reach the citation ledger."""

        parsed = CapabilityUrn.parse(descriptor.urn)
        return McpCitationsTool(
            **ToolSchemaIdentity.fields_of(tool),
            inner=tool,
            connector=parsed.namespace,
        )


__all__ = [
    "McpCitationsMiddleware",
    "McpCitationsTool",
]
