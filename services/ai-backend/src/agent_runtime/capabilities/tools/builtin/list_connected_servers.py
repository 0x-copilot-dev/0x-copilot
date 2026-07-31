"""Model-facing inventory of the MCP servers this user has already connected.

This is the first step of the connector protocol: *check what is already
connected* before reaching for
:mod:`~agent_runtime.capabilities.tools.builtin.suggest_mcp_connector`, which
only proposes connectors the user has **not** installed. Without this tool the
only model-visible discovery surface is the suggestion tool, so a run whose
prompt does not enumerate server cards — the ``deferred`` posture, where the
card block is suppressed — has no way to learn that a usable server exists.

The listing is deliberately cheap. Server cards already travel with the run, so
the default call costs no MCP round-trip; the model picks a server and calls
``load_mcp_server`` for its descriptors. ``include_tools`` is the opt-in for the
case where the model genuinely has to compare tools across servers, and it
loads through the same cached :class:`~agent_runtime.capabilities.mcp.loader.McpLoader`
every other path uses rather than opening a second discovery route.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.api.constants import Values
from agent_runtime.capabilities.mcp.cards import (
    McpAuthState,
    McpServerCard,
    McpServerHealth,
)
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinOperationAdapter,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract

_OPERATION = BuiltinOperationAdapter(tool_name=Values.Tool.LIST_CONNECTED_SERVERS)


class _Limits:
    """Bounds that keep one listing from dominating the context window."""

    # A listing is a menu, not a catalog dump. Beyond this the model should be
    # narrowing by name, not reading further.
    MAX_SERVERS = 50
    # ``include_tools`` costs one cached descriptor load per server. Capping the
    # fan-out keeps a workspace with many connectors from turning one tool call
    # into a long serial stall.
    MAX_SERVERS_WITH_TOOLS = 8


class _Keys:
    """Stable field names in the tool's returned payload."""

    ACCESS_MODE = "access_mode"
    CONNECTED = "connected"
    COUNT = "count"
    DESCRIPTION = "description"
    DISPLAY_NAME = "display_name"
    HEALTH = "health"
    NEEDS_AUTH = "needs_auth"
    NEXT_STEP = "next_step"
    NOTE = "note"
    SERVER_NAME = "server_name"
    TOOLS = "tools"
    TOOLS_UNAVAILABLE = "tools_unavailable"


class _Messages:
    """Safe, model-facing guidance returned alongside the listing."""

    NEXT_STEP = (
        "Call load_mcp_server(server_name) for the server you need, then "
        "call_mcp_tool with a tool_name from the returned descriptors."
    )
    NEXT_STEP_EMPTY = (
        "Nothing is connected yet. If a catalog connector would serve this "
        "request, call suggest_mcp_connector with its slug."
    )
    NEEDS_AUTH_NOTE = (
        "These servers are installed but not authenticated. Call auth_mcp with "
        "the server_name to connect one — do NOT call suggest_mcp_connector, "
        "which only proposes connectors that are not installed at all."
    )
    TOOLS_TRUNCATED = (
        "Tool descriptors were loaded for the first "
        f"{_Limits.MAX_SERVERS_WITH_TOOLS} servers only. Call load_mcp_server "
        "for any server listed without tools."
    )
    TOOLS_UNAVAILABLE = "descriptors could not be loaded; call load_mcp_server to retry"


LIST_CONNECTED_SERVERS_DESCRIPTION = (
    "List the MCP servers this user has already connected, so you can use one "
    "instead of asking them to connect something. Call this FIRST whenever a "
    "request mentions an external service (tickets, docs, email, calendar, "
    "repos, payments). Returns each connected server's stable name and what it "
    "covers, plus any installed-but-unauthenticated servers. Cheap — no server "
    "round-trip unless you pass ``include_tools``.\n\n"
    "Args:\n"
    "  include_tools: When true, also load each connected server's tool names "
    "(bounded, slower). Leave false and call ``load_mcp_server`` for the one "
    "server you need unless you must compare tools across servers."
)


class ListConnectedServersInput(RuntimeContract):
    """Validated input contract for the connected-server listing."""

    include_tools: bool = Field(default=False)


class ConnectedServerLookup:
    """Splits the run's authorized cards into connected and needs-auth sets.

    ``AUTH_SKIPPED`` counts as connected because it is what a server with
    ``auth_mode=none`` reports — it needs no credential, so it is usable now.
    Every other non-authenticated state is surfaced under ``needs_auth`` rather
    than dropped: an installed server the user simply has not finished
    authenticating is reached with ``auth_mcp``, and silently omitting it is
    what pushes the model toward proposing a fresh install instead.
    """

    USABLE_AUTH_STATES = frozenset(
        {McpAuthState.AUTHENTICATED, McpAuthState.AUTH_SKIPPED}
    )

    @classmethod
    def partition(
        cls, cards: Sequence[McpServerCard]
    ) -> tuple[tuple[McpServerCard, ...], tuple[McpServerCard, ...]]:
        """Return ``(connected, needs_auth)`` from the run's authorized cards."""
        connected: list[McpServerCard] = []
        needs_auth: list[McpServerCard] = []
        for card in cards:
            if not card.enabled or card.health == McpServerHealth.DISABLED:
                continue
            # ``==`` not ``is``: re-validated Pydantic enums are not always
            # identity-equal to the imported singleton.
            if any(card.auth_state == state for state in cls.USABLE_AUTH_STATES):
                connected.append(card)
            else:
                needs_auth.append(card)
        return (
            tuple(connected[: _Limits.MAX_SERVERS]),
            tuple(needs_auth[: _Limits.MAX_SERVERS]),
        )

    @classmethod
    def entry(cls, card: McpServerCard) -> dict[str, Any]:
        """Project one card onto the compact payload the model reads."""
        return {
            _Keys.SERVER_NAME: card.name,
            _Keys.DISPLAY_NAME: card.display_name or card.name,
            _Keys.DESCRIPTION: card.short_description,
            _Keys.HEALTH: card.health.value,
            _Keys.ACCESS_MODE: card.access_mode.value,
        }


@dataclass(frozen=True)
class ListConnectedServersTool:
    """Adapter wrapped by LangChain's ``StructuredTool`` in the factory."""

    registry: object
    runtime_context: AgentRuntimeContext
    loader: McpLoader | None = None
    name: str = Values.Tool.LIST_CONNECTED_SERVERS
    description: str = LIST_CONNECTED_SERVERS_DESCRIPTION

    async def ainvoke(
        self, raw_input: ListConnectedServersInput | Mapping[str, Any] | str | None
    ) -> dict[str, Any]:
        """Return the connected-server listing, or a typed safe failure."""
        parsed = ListConnectedServersInputParser.parse(raw_input)
        invocation = await _OPERATION.execute(
            arguments=parsed.model_dump(mode="json"),
            legacy=lambda: self._listing(include_tools=parsed.include_tools),
            safe_summary="Connected MCP servers were listed.",
        )
        if invocation.value is not None:
            return dict(invocation.value)
        return {
            _Keys.CONNECTED: [],
            _Keys.COUNT: 0,
            _Keys.NOTE: invocation.safe_summary,
        }

    async def __call__(
        self, raw_input: ListConnectedServersInput | Mapping[str, Any] | str | None
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)

    async def _listing(self, *, include_tools: bool) -> dict[str, Any]:
        """Build the payload from the run's already-authorized server cards."""
        cards = await self._authorized_cards()
        connected, needs_auth = ConnectedServerLookup.partition(cards)
        entries = [ConnectedServerLookup.entry(card) for card in connected]
        payload: dict[str, Any] = {
            _Keys.CONNECTED: entries,
            _Keys.COUNT: len(entries),
            _Keys.NEXT_STEP: (
                _Messages.NEXT_STEP if entries else _Messages.NEXT_STEP_EMPTY
            ),
        }
        if needs_auth:
            payload[_Keys.NEEDS_AUTH] = [
                ConnectedServerLookup.entry(card) for card in needs_auth
            ]
            payload[_Keys.NOTE] = _Messages.NEEDS_AUTH_NOTE
        if include_tools and entries:
            await self._attach_tool_names(entries, connected)
            if len(connected) > _Limits.MAX_SERVERS_WITH_TOOLS:
                payload[_Keys.NOTE] = _Messages.TOOLS_TRUNCATED
        return payload

    async def _authorized_cards(self) -> tuple[McpServerCard, ...]:
        """Read the run's authorized cards, or an empty tuple when unavailable.

        The registry is the single authority on which servers this context may
        see; the tool never widens that set, so an absent or non-conforming
        registry lists nothing rather than guessing.
        """
        list_available = getattr(self.registry, "list_available_servers", None)
        if not callable(list_available):
            return ()
        cards = await list_available(self.runtime_context)
        return tuple(card for card in cards if isinstance(card, McpServerCard))

    async def _attach_tool_names(
        self,
        entries: list[dict[str, Any]],
        connected: Sequence[McpServerCard],
    ) -> None:
        """Load bounded tool names onto the listed entries, in place.

        Loads through ``McpLoader`` so permission checks, transport gating, and
        the discovery cache all apply exactly as they do for
        ``load_mcp_server``. A server that fails to load is annotated rather
        than dropped — the listing's job is to say what is connected, and a
        transient descriptor failure does not change that answer.
        """
        if self.loader is None:
            return
        for entry, card in zip(
            entries[: _Limits.MAX_SERVERS_WITH_TOOLS],
            connected[: _Limits.MAX_SERVERS_WITH_TOOLS],
            strict=False,
        ):
            result = await self.loader.load_server_by_name(
                server_name=card.name,
                runtime_context=self.runtime_context,
            )
            if result.succeeded and result.loaded_server is not None:
                entry[_Keys.TOOLS] = [tool.name for tool in result.loaded_server.tools]
            else:
                entry[_Keys.TOOLS_UNAVAILABLE] = _Messages.TOOLS_UNAVAILABLE


class ListConnectedServersInputParser:
    """Parser for untrusted ``list_connected_servers`` tool input."""

    @classmethod
    def parse(
        cls,
        raw_input: ListConnectedServersInput | Mapping[str, Any] | str | None,
    ) -> ListConnectedServersInput:
        """Return a validated input model, defaulting on anything unusable.

        The tool takes only an optional boolean and has no failure mode that
        depends on it, so a malformed argument resolves to the cheap default
        rather than refusing a listing the model is entitled to.
        """
        if isinstance(raw_input, ListConnectedServersInput):
            return raw_input
        if not isinstance(raw_input, Mapping):
            return ListConnectedServersInput()
        try:
            return ListConnectedServersInput.model_validate(raw_input)
        except ValidationError:
            return ListConnectedServersInput()
