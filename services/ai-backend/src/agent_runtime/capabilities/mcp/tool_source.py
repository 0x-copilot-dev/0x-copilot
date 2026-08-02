"""Per-tool MCP :class:`ToolSource` over ``langchain-mcp-adapters`` (P2-3).

**Consumed by the P2-8 registration flip**
(``capabilities/mcp/per_tool_registration.py``), behind ``MCP_PER_TOOL_ENABLED``
(default OFF). With the flag off nothing calls :meth:`McpToolSource.load`.

This is the MCP half of the P0 ``ToolSource`` seam
(``capabilities/policy/contracts.py``): it produces
``(BaseTool, CapabilityDescriptor)`` pairs, one pair per *real* MCP tool, in
place of the single ``call_mcp_tool`` gateway that decodes ``server``/``tool``
out of a model-supplied payload. Four steps, in this order:

1. :class:`AuthorizedCardLister` asks the card source for every registered
   :class:`McpServerCard` and keeps only the ones this run may expose, using the
   existing :class:`~agent_runtime.capabilities.mcp.permissions.McpPermissionPolicy`
   — no second authorization rule is written here.
2. :class:`McpConnectionBuilder` turns the credential-plane
   :class:`~agent_runtime.capabilities.mcp.connection.McpServerConnectionConfig`
   (from the injected ``McpConnectionDirectory``) plus the ``httpx.Auth`` /
   header mapping (from the injected P0 ``CredentialProvider``) into one
   ``langchain-mcp-adapters`` ``Connection`` per server. The URL and the bearer
   stay on the credential plane; neither is ever written onto the model-visible
   card (P2-PLAN §1).
3. The client discovers the server's tools as ``BaseTool``s, and
   :meth:`McpToolSource._ingest_annotations` captures each tool's **untrusted**
   tri-state protocol hints into
   :class:`~agent_runtime.capabilities.mcp.annotations.McpToolAnnotationsRegistry`.
4. :meth:`~agent_runtime.capabilities.mcp.descriptor_source.McpCapabilityDescriptorSource.describe`
   — the P1b derivation — emits the descriptor. **It is the only place a
   ``CapabilityDescriptor`` is built.** This module deliberately contains no
   ``action`` / ``trust`` / ``connector_state`` logic of its own; a second
   derivation would be a second security policy.

Two maps are published alongside the pairs (:class:`McpToolCatalog`):
``cards_by_urn`` (what P2-4's Policy stage needs to build a GATE interrupt
payload for a fixed tool) and ``tool_to_server`` (the ``tool_name → server_slug``
map that feeds P2-1's
:class:`~agent_runtime.capabilities.mcp.connector_resolver.ToolConnectorResolver`,
and through it the P2-6 stream connector-identity seams).

Two lifetime decisions this module settles, both load-bearing:

* **Sessions.** Discovery opens exactly one session per server and closes it;
  the returned tools are bound to the *connection*, so each later call opens its
  own session. Handing ``load_mcp_tools`` an already-open session instead would
  bind that session into every returned tool
  (``langchain_mcp_adapters.tools.convert_mcp_tool_to_langchain_tool``), and the
  tools would outlive it — every call after discovery would run against a closed
  session. The long-lived object is the client (and, with P2-2, the rotating
  ``httpx.Auth`` inside it), not the session. This is the HTTP/SSE half of the
  P2-PLAN §6.4 spike; **stdio remains deferred** and is refused with a typed
  ``unsupported_transport`` failure rather than guessed at.
* **The annotations registry is bound by the run, not by this source.** The
  registry is a per-run ``ContextVar``; ingest here is a no-op when nothing is
  bound, and every descriptor then falls back to fail-closed ``WRITE``. It must
  stay bound *past* ``load()`` because P2-4 re-evaluates the descriptor per call.

Failures are per-server and never fatal: one unreachable or unsupported
connector contributes no tools and one typed, safe
:class:`~agent_runtime.capabilities.mcp.cards.McpLoadError`, while every other
connector still registers. Blanking the whole tool surface because one connector
is down is the worse failure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
from types import MappingProxyType
from typing import Final, Literal, Protocol
from uuid import uuid4

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import (
    Connection,
    SSEConnection,
    StreamableHttpConnection,
)

from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.mcp.cards import (
    McpLoadError,
    McpLoadErrorCode,
    McpServerCard,
    McpTransport,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpConnectionError,
    McpNotFoundError,
    McpRequestRejectedError,
    McpTimeoutError,
)
from agent_runtime.capabilities.mcp.connection import (
    McpConnectionDirectory,
    McpServerConnectionConfig,
)
from agent_runtime.capabilities.mcp.connector_resolver import ToolConnectorResolver
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.descriptor_source import (
    McpCapabilityDescriptorSource,
)
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.policy.contracts import (
    CapabilityDescriptor,
    CredentialProvider,
)
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.execution.contracts import AgentRuntimeContext

#: One ``(tool, descriptor)`` pair — the unit the P0 ``ToolSource`` seam yields
#: and the composer (P2-5) wraps.
ToolDescriptorPair = tuple[BaseTool, CapabilityDescriptor]


class McpSourceFailure:
    """The single construction site for this module's safe, typed failures.

    Every per-server failure — refused transport, unaddressable credential,
    duplicate tool name, classified exception — is minted here, so the shape of
    what reaches a model or an API surface is decided once. Mirrors
    ``McpLoadResult.fail``'s ``correlation_id or uuid4().hex`` so a caller that
    has no trace id still gets a correlatable record.
    """

    @classmethod
    def build(
        cls,
        code: McpLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        server_name: str | None = None,
        correlation_id: str | None = None,
    ) -> McpLoadError:
        """Return the typed, public failure for one server."""

        return McpLoadError(
            code=code,
            safe_message=safe_message,
            retryable=retryable,
            server_name=server_name,
            correlation_id=correlation_id or uuid4().hex,
        )


class McpToolSourceError(Exception):
    """A per-server load failure carrying its safe, typed public error.

    The exception exists only to unwind one server's load attempt; the value
    that survives is the :class:`McpLoadError`, which is the MCP subsystem's
    existing public failure contract (typed code, length-bounded safe message,
    ``retryable`` flag, correlation id). Nothing internal — no URL, no header,
    no traceback text — is ever carried on it.
    """

    def __init__(self, error: McpLoadError) -> None:
        """Wrap the safe public error this failure reports."""

        super().__init__(error.safe_message)
        self.error = error

    @classmethod
    def of(
        cls,
        code: McpLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        server_name: str | None = None,
        correlation_id: str | None = None,
    ) -> "McpToolSourceError":
        """Build the failure from the typed code + safe message directly."""

        return cls(
            McpSourceFailure.build(
                code,
                safe_message,
                retryable=retryable,
                server_name=server_name,
                correlation_id=correlation_id,
            )
        )


class McpToolLoadFailure:
    """Map a load-time exception onto the safe, typed per-server failure.

    The direct-connect twin of the ``except`` chain in
    :meth:`~agent_runtime.capabilities.mcp.loader.McpLoader._load_uncached`, and
    deliberately the same taxonomy in the same order — a 4xx refusal is matched
    **before** the connection family and is never ``retryable``, because
    describing a deterministic refusal as a temporary outage is what once told a
    user to "try again in a moment" about a call that could never succeed.
    Order matters: :class:`McpNotFoundError` ⊂ :class:`McpRequestRejectedError`,
    and ``McpAmbiguousDispatchError`` ⊂ :class:`McpConnectionError`.
    """

    #: ``(exception types, code, safe message, retryable)`` — first match wins.
    _TAXONOMY: tuple[
        tuple[tuple[type[BaseException], ...], McpLoadErrorCode, str, bool], ...
    ] = (
        (
            (McpTimeoutError, TimeoutError),
            McpLoadErrorCode.TIMEOUT,
            Messages.Loader.TIMEOUT,
            True,
        ),
        (
            (McpAuthError, PermissionError),
            McpLoadErrorCode.AUTH_FAILURE,
            Messages.Loader.AUTH_FAILED,
            False,
        ),
        (
            (McpNotFoundError,),
            McpLoadErrorCode.UNKNOWN_SERVER,
            Messages.Loader.SERVER_NOT_FOUND,
            False,
        ),
        (
            (McpRequestRejectedError,),
            McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            Messages.Loader.REQUEST_REJECTED,
            False,
        ),
        (
            (McpConnectionError, ConnectionError),
            McpLoadErrorCode.CONNECTION_FAILED,
            Messages.Loader.CONNECTION_FAILED,
            True,
        ),
    )

    @classmethod
    def classify(
        cls,
        exc: BaseException,
        *,
        server_name: str,
        correlation_id: str | None = None,
    ) -> McpLoadError:
        """Return the safe public failure for ``exc`` while loading ``server_name``."""

        if isinstance(exc, McpToolSourceError):
            return exc.error
        for kinds, code, safe_message, retryable in cls._TAXONOMY:
            if isinstance(exc, kinds):
                return McpSourceFailure.build(
                    code,
                    safe_message,
                    retryable=retryable,
                    server_name=server_name,
                    correlation_id=correlation_id,
                )
        # Nothing recognised: the detail may be anything at all, so it is logged
        # for operators and never described to the model beyond the generic
        # load failure.
        logging.getLogger(__name__).warning(
            "Unexpected error loading MCP tools for server %s",
            server_name,
            exc_info=True,
        )
        return McpSourceFailure.build(
            McpLoadErrorCode.CONNECTION_FAILED,
            Messages.Loader.LOAD_FAILED,
            retryable=True,
            server_name=server_name,
            correlation_id=correlation_id,
        )


class McpServerCardSource(Protocol):
    """Yields every registered MCP card, unfiltered.

    Validation of raw provider payloads stays where it already lives (the
    registry); this seam hands over cards that are already
    :class:`McpServerCard`s, and :class:`AuthorizedCardLister` adds only the
    policy gate. Structurally satisfied by
    :class:`~agent_runtime.capabilities.mcp.registry.DynamicMcpRegistry`'s
    provider interface.
    """

    async def list_server_cards(self) -> Sequence[McpServerCard]: ...


@dataclass(frozen=True)
class AuthorizedCardLister:
    """The cards this run may expose, gated by :class:`McpPermissionPolicy`.

    Uses ``is_server_card_visible`` — which *is* ``is_server_card_authorized``
    plus the enabled/health gate — because under per-tool registration exposure
    to the model IS registration. That keeps parity with the legacy gateway,
    where a disabled or unhealthy server never appeared in the model's card
    listing either, and it keeps this class free of any authorization rule of
    its own.

    Availability is still re-checked per call: the descriptor's
    ``connector_state`` is recomputed from the run context by the P1b source
    every time P2-4 evaluates, so a connector paused mid-run is denied even
    though its tools registered while it was live.
    """

    cards: McpServerCardSource

    async def authorized(
        self, context: AgentRuntimeContext
    ) -> tuple[McpServerCard, ...]:
        """Return the visible+authorized cards, ordered by name for determinism."""

        registered = await self.cards.list_server_cards()
        return tuple(
            sorted(
                (
                    card
                    for card in registered
                    if McpPermissionPolicy.is_server_card_visible(context, card)
                ),
                key=lambda card: card.name,
            )
        )


class McpConnectionBuilder:
    """Build one ``langchain-mcp-adapters`` ``Connection`` from the credential plane.

    The endpoint half comes from the :class:`McpConnectionDirectory`, the
    credential half from the P0 ``CredentialProvider``. The provider's return is
    typed ``httpx.Auth | Mapping[str, str]``: an ``Auth`` rides the connection's
    ``auth`` slot (so P2-2's refresh happens inside the request flow rather than
    at client-construction time), while a mapping is merged into the request
    headers. Anything else is a provider defect and is refused — connecting
    unauthenticated because a credential arrived in an unrecognised shape is the
    one outcome that must never happen.
    """

    #: ``langchain-mcp-adapters`` transport discriminators. Its "streamable
    #: http" is our :attr:`McpTransport.HTTP`; the two vocabularies are mapped
    #: explicitly rather than value-coerced.
    _STREAMABLE_HTTP: Final[Literal["streamable_http"]] = "streamable_http"
    _SSE: Final[Literal["sse"]] = "sse"

    @classmethod
    def build(
        cls,
        *,
        config: McpServerConnectionConfig,
        auth: httpx.Auth | Mapping[str, str],
        server_name: str,
        correlation_id: str | None = None,
    ) -> Connection:
        """Return the connection for ``config``, or raise :class:`McpToolSourceError`."""

        headers = dict(config.headers)
        auth_flow: httpx.Auth | None = None
        if isinstance(auth, httpx.Auth):
            auth_flow = auth
        elif isinstance(auth, Mapping):
            headers.update({str(key): str(value) for key, value in auth.items()})
        else:
            raise McpToolSourceError.of(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                server_name=server_name,
                correlation_id=correlation_id,
            )

        if config.transport is McpTransport.HTTP:
            http: StreamableHttpConnection = {
                "transport": cls._STREAMABLE_HTTP,
                "url": config.url,
                "headers": headers,
            }
            if auth_flow is not None:
                http["auth"] = auth_flow
            return http
        if config.transport is McpTransport.SSE:
            sse: SSEConnection = {
                "transport": cls._SSE,
                "url": config.url,
                "headers": headers,
            }
            if auth_flow is not None:
                sse["auth"] = auth_flow
            return sse
        # STDIO: the directory yields a URL, and a local server needs a command
        # spec plus a process-lifetime decision (P2-PLAN §6.4, still open).
        # Refused, not guessed.
        raise McpToolSourceError.of(
            McpLoadErrorCode.UNSUPPORTED_TRANSPORT,
            Messages.Loader.UNSUPPORTED_TRANSPORT,
            server_name=server_name,
            correlation_id=correlation_id,
        )


class McpToolLoaderClient(Protocol):
    """The discovery surface this source needs from an MCP client.

    Structurally satisfied by ``langchain_mcp_adapters.client.MultiServerMCPClient``
    (and by a test fake). ``get_tools`` is ``load_mcp_tools(None, connection=…)``
    under the hood: one session opened for discovery and closed again, with the
    returned tools bound to the connection so each call opens its own session.
    """

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]: ...


class McpToolLoaderClientFactory(Protocol):
    """Constructs the client for a built connection map (dependency inversion)."""

    def create(self, connections: Mapping[str, Connection]) -> McpToolLoaderClient: ...


@dataclass(frozen=True)
class DirectMcpClientFactory:
    """Default factory: one ``MultiServerMCPClient`` over the built connections.

    One client for all servers, constructed once per ``load()``. It holds the
    rotating ``httpx.Auth`` objects, so a credential refresh (P2-2) never
    requires rebuilding the client.
    """

    def create(self, connections: Mapping[str, Connection]) -> McpToolLoaderClient:
        """Return a live multi-server client over ``connections``."""

        return MultiServerMCPClient(dict(connections))


@dataclass(frozen=True)
class McpToolCatalog:
    """What one :meth:`McpToolSource.load` published.

    ``pairs`` is the ``ToolSource`` return value; the two maps are the seams the
    later increments consume, and ``failures`` is the typed, safe record of every
    connector that contributed nothing. All maps are read-only snapshots — a
    published catalog cannot be edited by whoever holds it.
    """

    pairs: tuple[ToolDescriptorPair, ...]
    #: ``CapabilityDescriptor.urn -> McpServerCard`` — one entry per registered
    #: tool, so P2-4's Policy stage can build a GATE interrupt payload for the
    #: fixed tool it wraps without re-resolving the server.
    cards_by_urn: Mapping[str, McpServerCard]
    #: ``tool_name -> server_slug`` — the resolver map that replaces
    #: ``McpDispatcherUnwrap.effective_server_name`` in the per-tool world.
    tool_to_server: Mapping[str, str]
    #: Per-server load failures, safe for model and API surfaces.
    failures: tuple[McpLoadError, ...]

    @classmethod
    def empty(cls, failures: tuple[McpLoadError, ...] = ()) -> "McpToolCatalog":
        """A catalog that registered no tool — before ``load``, or after it found none.

        ``failures`` is non-empty when every authorized connector failed to
        connect: nothing registers, but the run still learns why.
        """

        return cls(
            pairs=(),
            cards_by_urn=MappingProxyType({}),
            tool_to_server=MappingProxyType({}),
            failures=failures,
        )

    def connector_resolver(self) -> ToolConnectorResolver:
        """Return the P2-1 resolver built from this catalog's registration map."""

        return ToolConnectorResolver(self.tool_to_server)


class McpToolSource:
    """Loads one ``BaseTool`` per real MCP tool, each with its P1b descriptor.

    Implements the P0 :class:`~agent_runtime.capabilities.policy.contracts.ToolSource`
    Protocol. Every collaborator is injected — the card source, the credential
    plane (:class:`McpConnectionDirectory` + ``CredentialProvider``), and the
    client factory — so the whole load path is exercisable without a network,
    a credential, or a live MCP server.
    """

    def __init__(
        self,
        *,
        context: AgentRuntimeContext,
        cards: AuthorizedCardLister,
        directory: McpConnectionDirectory,
        credentials: CredentialProvider,
        client_factory: McpToolLoaderClientFactory | None = None,
        reserved_names: frozenset[str] = frozenset(),
    ) -> None:
        """Bind the run context and the injected credential/client collaborators.

        ``reserved_names`` is the set of NATIVE (non-MCP) tool names already
        registered for this run — Deep Agents builtins, the capability-bridge
        tools, anything the model can already call. A connector tool matching one
        of them is dropped with a typed ``LOCAL_TOOL_COLLISION`` rather than
        silently shadowing the builtin and hijacking its stream provenance. The
        registration site (P2-8) supplies it; the empty default keeps every
        collaborator injectable for tests.
        """

        self._context = context
        self._cards = cards
        self._directory = directory
        self._credentials = credentials
        self._reserved_names = frozenset(reserved_names)
        self._client_factory: McpToolLoaderClientFactory = (
            client_factory if client_factory is not None else DirectMcpClientFactory()
        )
        self._catalog = McpToolCatalog.empty()

    @property
    def catalog(self) -> McpToolCatalog:
        """The catalog published by the most recent :meth:`load` (empty before)."""

        return self._catalog

    async def load(self) -> list[ToolDescriptorPair]:
        """Discover every authorized server's tools and describe each one."""

        failures: list[McpLoadError] = []
        connections, cards_by_slug = await self._connect_authorized(failures)
        if not connections:
            self._catalog = McpToolCatalog.empty(tuple(failures))
            return []

        client = self._client_factory.create(connections)
        pairs: list[ToolDescriptorPair] = []
        cards_by_urn: dict[str, McpServerCard] = {}
        tool_to_server: dict[str, str] = {}
        for slug, card in cards_by_slug.items():
            try:
                tools = await client.get_tools(server_name=slug)
            except Exception as exc:  # converted to a typed, safe failure
                failures.append(self._failure(exc, card=card))
                continue
            for tool in tools:
                pair = self._describe(
                    tool=tool,
                    card=card,
                    taken=tool_to_server,
                    failures=failures,
                )
                if pair is None:
                    continue
                described, descriptor = pair
                pairs.append(pair)
                cards_by_urn[descriptor.urn] = card
                tool_to_server[described.name] = slug

        self._catalog = McpToolCatalog(
            pairs=tuple(pairs),
            cards_by_urn=MappingProxyType(cards_by_urn),
            tool_to_server=MappingProxyType(tool_to_server),
            failures=tuple(failures),
        )
        return list(pairs)

    async def _connect_authorized(
        self, failures: list[McpLoadError]
    ) -> tuple[dict[str, Connection], dict[str, McpServerCard]]:
        """Build the connection map for every card this run may expose.

        A card that cannot be connected (no ``server_id`` on the credential
        plane, an unsupported transport, a credential the provider could not
        yield) records a typed failure and is left out — it never reaches the
        client, so no unauthorized connector is ever contacted.
        """

        connections: dict[str, Connection] = {}
        cards_by_slug: dict[str, McpServerCard] = {}
        for card in await self._cards.authorized(self._context):
            slug = server_slug(card.name)
            if slug in cards_by_slug:
                # ``server_slug`` is lossy (it collapses every non-alphanumeric
                # run to ``-``), while ``McpServerCard.name`` admits both ``_``
                # and ``-`` — so two legal, distinct cards (``linear_app`` and
                # ``linear-app``) can land on one key. Clobbering would strand a
                # whole connector: its tools never register, yet nothing records
                # why. Fail it typed, and do it BEFORE ``_connection_for`` so no
                # credential is minted for a card that cannot register anyway.
                failures.append(
                    McpSourceFailure.build(
                        McpLoadErrorCode.DUPLICATE_SERVER_NAME,
                        Messages.Registry.DUPLICATE_SERVER_NAME,
                        server_name=card.name,
                        correlation_id=self._context.trace_id,
                    )
                )
                continue
            try:
                connection = await self._connection_for(card)
            except Exception as exc:  # converted to a typed, safe failure
                failures.append(self._failure(exc, card=card))
                continue
            connections[slug] = connection
            cards_by_slug[slug] = card
        return connections, cards_by_slug

    async def _connection_for(self, card: McpServerCard) -> Connection:
        """Resolve one card's endpoint + credential into a client connection."""

        if card.server_id is None:
            # The credential plane is keyed on ``server_id`` (the desktop broker
            # reads ``(workspace, "mcp", server_id)``), so a deployment-level
            # card with no id has no addressable credential. Fail closed.
            raise McpToolSourceError.of(
                McpLoadErrorCode.UNKNOWN_SERVER,
                Messages.Registry.REQUESTED_SERVER_UNKNOWN,
                server_name=card.name,
                correlation_id=self._context.trace_id,
            )
        config: McpServerConnectionConfig = await self._directory.connection_for(
            card.server_id
        )
        auth = await self._credentials.auth_for(card.server_id)
        return McpConnectionBuilder.build(
            config=config,
            auth=auth,
            server_name=card.name,
            correlation_id=self._context.trace_id,
        )

    def _describe(
        self,
        *,
        tool: BaseTool,
        card: McpServerCard,
        taken: Mapping[str, str],
        failures: list[McpLoadError],
    ) -> ToolDescriptorPair | None:
        """Ingest the tool's hints, then describe it via the P1b source.

        Returns ``None`` when the tool cannot be registered safely: a blank name
        names nothing, and a name already claimed by another connector would make
        the ``tool_name → server_slug`` map ambiguous — and an ambiguous map
        mis-attributes a call to the wrong connector, which is a provenance and
        policy failure, not a cosmetic one. First registration wins; the
        collision is dropped with a typed failure.

        The tool's own ``name`` is the single key throughout — the collision
        check, the derivation, and the published map all use it verbatim, so no
        two of them can ever disagree about what this tool is called.
        """

        name = tool.name or ""
        if not name.strip():
            return None
        if name in self._reserved_names:
            # A connector's tool may not take a NATIVE tool's name. The adapters
            # library defaults to no prefix, so an MCP server advertising
            # ``write_todos`` / ``read_file`` / ``task`` would otherwise shadow a
            # Deep Agents builtin in the composed tool list AND publish
            # ``tool_to_server[name]``, which makes the stream seam stamp MCP
            # provenance + access_mode onto that builtin's own events. Native
            # tools win; the connector's tool is dropped, typed and visible.
            failures.append(
                McpSourceFailure.build(
                    McpLoadErrorCode.LOCAL_TOOL_COLLISION,
                    Messages.Loader.LOCAL_TOOL_COLLISION,
                    server_name=card.name,
                    correlation_id=self._context.trace_id,
                )
            )
            return None
        if name in taken:
            failures.append(
                McpSourceFailure.build(
                    McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME,
                    Messages.Loader.DUPLICATE_TOOL_NAMES,
                    server_name=card.name,
                    correlation_id=self._context.trace_id,
                )
            )
            return None
        # Order is load-bearing: ``describe`` reads the annotations registry, so
        # the hints must be captured before the descriptor is derived.
        self._ingest_annotations(server=card.name, tool=tool)
        descriptor = McpCapabilityDescriptorSource.describe(
            card=card,
            server=card.name,
            tool=name,
            context=self._context,
        )
        return (tool, descriptor)

    def _ingest_annotations(self, *, server: str, tool: BaseTool) -> None:
        """Capture one tool's untrusted tri-state hints into the run registry.

        ``langchain-mcp-adapters`` flattens the MCP ``ToolAnnotations`` model
        straight onto ``BaseTool.metadata`` (``tools.py``:
        ``{**tool.annotations.model_dump(), **{"_meta": …}}``), so the wire's
        camelCase ``readOnlyHint`` / ``destructiveHint`` / ``idempotentHint``
        arrive at the top level and
        :meth:`McpToolAnnotations.from_wire` reads them directly — the same
        untrusted, tighten-only capture the legacy loader performs, with the same
        strictness (a non-``bool`` is no hint at all).

        A no-op when no per-run registry is bound; the descriptor derivation then
        falls back to catalog-or-``WRITE``, which is the fail-closed answer.
        """

        metadata = tool.metadata
        wire: Mapping[str, object] = metadata if isinstance(metadata, Mapping) else {}
        McpToolAnnotationsRegistry.register(
            server, tool.name, McpToolAnnotations.from_wire(wire)
        )

    def _failure(self, exc: BaseException, *, card: McpServerCard) -> McpLoadError:
        """Convert a load-time exception into this server's safe typed failure."""

        return McpToolLoadFailure.classify(
            exc,
            server_name=card.name,
            correlation_id=self._context.trace_id,
        )


__all__ = [
    "AuthorizedCardLister",
    "DirectMcpClientFactory",
    "McpConnectionBuilder",
    "McpServerCardSource",
    "McpSourceFailure",
    "McpToolCatalog",
    "McpToolLoadFailure",
    "McpToolLoaderClient",
    "McpToolLoaderClientFactory",
    "McpToolSource",
    "McpToolSourceError",
    "ToolDescriptorPair",
]
