"""Non-blocking MCP discovery tool that emits a Connect/Skip card without interrupting the run.

The agent calls this when an unauthenticated MCP server would improve the answer. Unlike
:class:`~agent_runtime.capabilities.mcp.middleware.auth_mcp.AuthMcpTool`, this tool never
interrupts — it emits one ``mcp_auth_required`` event with ``discovery_reason`` set, then
returns immediately. The harness keeps streaming.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.api.constants import Values
from agent_runtime.api.mcp_discovery_service import McpDiscoveryService
from agent_runtime.execution.contracts import RuntimeContract


class _Limits:
    """Length caps for tool input — keep payloads tight on the wire."""

    SERVER_ID_MAX = 128
    REASON_MAX = 240
    EXPECTED_VALUE_MAX = 240


class _Messages:
    """Safe public messages returned to the agent on tool failures."""

    SERVER_ID_REQUIRED = "A non-empty ``server_id`` is required."
    REASON_REQUIRED = "A non-empty ``reason`` is required."
    EXPECTED_VALUE_REQUIRED = "A non-empty ``expected_value`` is required."


SUGGEST_MCP_CONNECTOR_DESCRIPTION = (
    "Suggest a Connect/Skip card for an MCP server you noticed would improve "
    "the answer but isn't authenticated yet. Non-blocking — the run keeps "
    "streaming while the user decides. Call once per server per run; "
    "subsequent calls for the same server return ``already_suggested``. "
    "Use the blocking ``auth_mcp`` tool only when the run cannot proceed "
    "without the connector.\n\n"
    "Args:\n"
    "  server_id: Stable id of the MCP server (e.g. ``linear``).\n"
    "  reason: Short, user-facing reason — what the connector would help "
    "with (e.g. ``fetch ticket statuses``).\n"
    "  expected_value: One-line value statement for the user "
    "(e.g. ``could ground claims about ticket progress``)."
)


class SuggestMcpConnectorInput(RuntimeContract):
    """Validated input contract for the discovery tool."""

    server_id: str = Field(min_length=1, max_length=_Limits.SERVER_ID_MAX)
    reason: str = Field(min_length=1, max_length=_Limits.REASON_MAX)
    expected_value: str = Field(min_length=1, max_length=_Limits.EXPECTED_VALUE_MAX)


@dataclass(frozen=True)
class SuggestMcpConnectorTool:
    """Adapter wrapped by LangChain's ``StructuredTool`` in the factory."""

    name: str = Values.Tool.SUGGEST_MCP_CONNECTOR
    description: str = SUGGEST_MCP_CONNECTOR_DESCRIPTION

    async def ainvoke(
        self, raw_input: SuggestMcpConnectorInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Validate input, call the discovery service, and return its result."""
        parsed = SuggestMcpConnectorInputParser.parse(raw_input)
        if isinstance(parsed, dict):
            return parsed
        result = await McpDiscoveryService.offer(
            server_id=parsed.server_id,
            reason=parsed.reason,
            expected_value=parsed.expected_value,
        )
        return dict(result)

    async def __call__(
        self, raw_input: SuggestMcpConnectorInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)


class SuggestMcpConnectorInputParser:
    """Parser for untrusted ``suggest_mcp_connector`` tool input."""

    @classmethod
    def parse(
        cls,
        raw_input: SuggestMcpConnectorInput | Mapping[str, Any] | str,
    ) -> SuggestMcpConnectorInput | dict[str, Any]:
        """Return a validated input model or a rejection dict on validation failure."""
        if isinstance(raw_input, SuggestMcpConnectorInput):
            return raw_input
        if isinstance(raw_input, str):
            # Bare-string callers are rare but legal; treat as
            # ``server_id`` only — the model should call with the full
            # contract, so failing closed (no event emitted) is correct
            # behavior here.
            return {"ok": False, "message": _Messages.REASON_REQUIRED}
        try:
            return SuggestMcpConnectorInput.model_validate(raw_input)
        except ValidationError as exc:
            return {
                "ok": False,
                "message": cls._first_error_message(exc),
            }

    @staticmethod
    def _first_error_message(exc: ValidationError) -> str:
        """Return the most specific safe message for the first validation error."""
        for error in exc.errors():
            field = error.get("loc", ("?",))[0]
            if field == "server_id":
                return _Messages.SERVER_ID_REQUIRED
            if field == "reason":
                return _Messages.REASON_REQUIRED
            if field == "expected_value":
                return _Messages.EXPECTED_VALUE_REQUIRED
        return "Invalid suggest_mcp_connector input."
