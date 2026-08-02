"""OBSERVE stage — bind the connector identity to the in-flight call (P2-5).

**Composed by the P2-8 registration flip**, behind ``MCP_PER_TOOL_ENABLED``
(default OFF). Until that flag is on for a deployment, the legacy
``call_mcp_tool`` gateway still owns the dispatch and nothing here runs.

This stage is deliberately **thin**. It emits no event and writes no row: the
durable stream envelope and the ``ToolInvocationRecord`` stay in
``runtime_worker/stream_tools.py``, where P2-6 already re-hooked connector
identity for per-tool calls (``connector_slug_for_payload`` → the run's
``ToolConnectorResolver``). Duplicating either here would give one tool call two
independent projections that could disagree — the projection bug is worse than
the missing one.

What it does instead is make the connector binding **available inside the call**.
Everything nested under a wrapped tool — the ERROR_MAP and CITATIONS stages
below it, and any projection that runs during the call — can ask
:class:`McpCallBindingScope` which connector, which capability, and which effect
class it is currently executing, without that identity being threaded through
tool signatures (the same reason ``CitationLedger`` and the MCP annotations
registry are ``ContextVar``-bound).

The binding is per-call and always unbound in a ``finally``, so a nested or
concurrent call can never inherit another call's connector — which would
mis-attribute a result to the wrong app.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.middleware.compose import ToolSchemaIdentity
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    MiddlewareStage,
)
from agent_runtime.execution.contracts import RuntimeContract

#: The argument LangChain injects only into tools whose schema declares
#: ``Annotated[str, InjectedToolCallId]``. Read, never added and never removed —
#: adding it would change the schema, and removing it would starve an inner tool
#: that asked for it.
_TOOL_CALL_ID = "tool_call_id"


class McpCallBinding(RuntimeContract):
    """Which connector capability is executing, for the duration of one call.

    Frozen and ``extra="forbid"``. Identity only: a URN, a connector slug, the
    registered tool name, the effect class, and the call id when LangChain
    supplied one. No arguments, no result, no credential — a binding that can be
    read from anywhere inside the call must not be able to carry anything that
    would be unsafe to read from anywhere inside the call.
    """

    #: The descriptor's capability URN, e.g. ``"mcp:linear:create_issue"``.
    urn: str
    #: Connector slug (the URN's namespace) — the ``server_slug`` the stream
    #: seams and the citation ledger key on.
    connector: str
    #: The registered ``BaseTool`` name, verbatim. Not the URN's trailing
    #: segment: ``CapabilityUrn`` lowercases it, and the registration map, the
    #: stream payload, and this binding must all name the tool identically.
    tool_name: str
    #: The effect class the descriptor declared.
    action: Action
    #: Present only when the inner tool's schema opted into ``InjectedToolCallId``.
    tool_call_id: str | None = None

    def for_call(self, tool_call_id: str | None) -> "McpCallBinding":
        """Return this binding stamped with one call's id (or unchanged)."""

        if tool_call_id is None or tool_call_id == self.tool_call_id:
            return self
        return self.model_copy(update={"tool_call_id": tool_call_id})


_CALL_BINDING_CTX: ContextVar[McpCallBinding | None] = ContextVar(
    "mcp_call_binding", default=None
)


class McpCallBindingScope:
    """The per-call ``ContextVar`` holding the active :class:`McpCallBinding`.

    Mirrors the bind / unbind / active shape of ``McpToolAnnotationsRegistry``
    and ``CitationLedger`` so there is one recognisable pattern for run- and
    call-scoped context in this package.
    """

    @classmethod
    def bind(cls, binding: McpCallBinding) -> object:
        """Set the active binding; return the token that restores the previous."""

        return _CALL_BINDING_CTX.set(binding)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous binding. Safe to call with the bind result."""

        _CALL_BINDING_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def current(cls) -> McpCallBinding | None:
        """Return the binding of the call in flight, or ``None`` outside one."""

        return _CALL_BINDING_CTX.get(None)


class McpObservedTool(DelegatingTool):
    """Schema-identical wrapper that binds the connector identity around a call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    binding: McpCallBinding

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync path: bind for the call, delegate, always unbind."""

        token = McpCallBindingScope.bind(self._binding_for(kwargs))
        try:
            return self.delegate(*args, config=config, **kwargs)
        finally:
            McpCallBindingScope.unbind(token)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async path: bind for the call, delegate, always unbind."""

        token = McpCallBindingScope.bind(self._binding_for(kwargs))
        try:
            return await self.adelegate(*args, config=config, **kwargs)
        finally:
            McpCallBindingScope.unbind(token)

    def _binding_for(self, kwargs: dict[str, Any]) -> McpCallBinding:
        """Return this call's binding, stamped with the injected call id if any."""

        return self.binding.for_call(self._tool_call_id(kwargs))

    @staticmethod
    def _tool_call_id(kwargs: dict[str, Any]) -> str | None:
        """Read the injected call id without consuming it.

        LangChain injects ``tool_call_id`` only into a tool whose schema
        declares ``Annotated[str, InjectedToolCallId]``. MCP tools built by
        ``langchain-mcp-adapters`` do not, so this is normally ``None`` — and it
        stays ``None`` rather than being obtained by augmenting the schema,
        because a wrapper that adds a field is no longer schema-identical.
        The value is read and left in place so the inner tool that asked for it
        still receives it.
        """

        value = kwargs.get(_TOOL_CALL_ID)
        return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class McpObserveMiddleware:
    """The OBSERVE stage of the P0 pipeline."""

    # ``init=False`` -- see the note on the exec-policy stage: a mislabelled
    # middleware would defeat the composer's whole order guarantee.
    stage: MiddlewareStage = field(default=MiddlewareStage.OBSERVE, init=False)

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return ``tool`` wrapped so its connector identity is bound per call."""

        parsed = CapabilityUrn.parse(descriptor.urn)
        binding = McpCallBinding(
            urn=descriptor.urn,
            connector=parsed.namespace,
            tool_name=tool.name,
            action=descriptor.action,
        )
        return McpObservedTool(
            **ToolSchemaIdentity.fields_of(tool),
            inner=tool,
            binding=binding,
        )


__all__ = [
    "McpCallBinding",
    "McpCallBindingScope",
    "McpObserveMiddleware",
    "McpObservedTool",
]
