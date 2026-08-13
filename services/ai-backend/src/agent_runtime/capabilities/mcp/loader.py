"""Explicit loader for dynamically selected MCP servers."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import ValidationError

from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpConnectionMetadata,
    McpLoadError,
    McpLoadErrorCode,
    McpLoadRequest,
    McpLoadResult,
    McpLoadWarning,
    McpResourceDescriptor,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
    McpValueNormalizer,
    McpWarningCode,
)
from agent_runtime.capabilities.mcp.schema_repair import McpSchemaRepairLog
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClient,
    McpClientError,
    McpConnectionError,
    McpNotFoundError,
    McpRequestRejectedError,
    McpResourceDiscoveryPage,
    McpTimeoutError,
    McpToolDiscoveryPage,
    PaginatedMcpClient,
    RawMcpConnectionMetadata,
    aclose_mcp_client_safely,
)
from agent_runtime.capabilities.mcp.constants import Defaults, Keys, Messages
from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCachePort,
    McpDiscoveryCacheKey,
)
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.mcp.registry import (
    DynamicMcpRegistry,
    RegisteredMcpServer,
)

_T = TypeVar("_T")
SUPPORTED_TRANSPORTS = frozenset(
    {McpTransport.STDIO, McpTransport.SSE, McpTransport.HTTP}
)


class _McpDiscoveryPaginationError(ValueError):
    """Content-free pagination failure mapped to a stable load error."""

    def __init__(self, code: McpLoadErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class McpLoadFailureLog:
    """Emit one warning line for every failed load, whatever killed it.

    A failed ``load_mcp_server`` used to leave *no trace at all*: the typed
    failure paths each return an ``McpLoadResult`` and only the catch-all
    ``except Exception`` logged anything. An entire capability could fail —
    OAuth completed, connector connected, 52 descriptors discovered — and
    the service log recorded nothing but a generic run start and finish, so
    naming the failure took a live reproduction and a database read instead
    of one grep.

    Everything logged here is already model-facing or non-identifying:
    ``safe_message`` is the string the loader hands the model, and the code /
    retryable / server-name fields are the same values it returns. No URL, no
    header, no token, no descriptor body.
    """

    _LOGGER = logging.getLogger(__name__)

    @classmethod
    def record(cls, request: McpLoadRequest, result: McpLoadResult) -> None:
        """Log ``result`` when it is a failure; do nothing when it succeeded."""
        error = result.error
        if error is None:
            return
        cls._LOGGER.warning(
            "MCP load failed: server=%s code=%s retryable=%s trace=%s detail=%s",
            error.server_name or request.server_name,
            error.code.value,
            error.retryable,
            request.runtime_context.trace_id,
            error.safe_message,
        )


@dataclass(frozen=True)
class McpLoader:
    """Connects to a selected MCP server and validates discovered descriptors.

    When ``cache`` is supplied, successful loads are memoized by
    ``(server_name, org_id, user_id)`` so subsequent turns skip the
    ``connect + list_tools + list_resources`` round-trips. Permission and
    transport checks always run on the live runtime context — they are
    never cached. Failure results are not cached either, so a transient
    upstream issue can recover on the next call.
    """

    registry: DynamicMcpRegistry
    timeout_seconds: float = Defaults.TIMEOUT_SECONDS
    max_tool_descriptors: int = Defaults.MAX_TOOL_DESCRIPTORS
    max_resource_descriptors: int = Defaults.MAX_RESOURCE_DESCRIPTORS
    max_discovery_pages: int = 100
    cache: McpDiscoveryCachePort | None = None

    async def load_server(self, request: McpLoadRequest) -> McpLoadResult:
        """Load a selected MCP server, logging the outcome when it fails.

        Wrapping the real load is what makes the logging exhaustive: every
        typed failure return in ``_load_server`` — permission, transport,
        auth, 4xx, timeout, descriptor validation, cache race — funnels
        through this one seam, so a new failure path cannot be added that
        stays silent.
        """

        result = await self._load_server(request)
        McpLoadFailureLog.record(request, result)
        return result

    async def _load_server(self, request: McpLoadRequest) -> McpLoadResult:
        """Load a selected MCP server while rechecking permissions and validation.

        When a cache is wired, the heavy network path
        (``connect + list_tools + list_resources`` + validation) runs
        through ``cache.get_or_load``, which provides:
          - fast-path read of a fresh memoized record,
          - per-key async lock so concurrent first-callers don't all hit
            the network (thundering-herd protection),
          - on-miss populate after a successful load.

        Permission and transport checks always run uncached on the live
        runtime context so a freshly revoked scope or downed transport
        cannot serve cached descriptors.
        """

        runtime_context = request.runtime_context
        resolution = await self.registry.resolve_server(request.server_name)
        if isinstance(resolution, McpLoadError):
            return McpLoaderHelpers.result_from_error(
                resolution, runtime_context.trace_id
            )

        card = resolution.card
        if card.transport not in SUPPORTED_TRANSPORTS:
            return McpLoadResult.fail(
                McpLoadErrorCode.UNSUPPORTED_TRANSPORT,
                Messages.Loader.UNSUPPORTED_TRANSPORT,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        if not McpPermissionPolicy.is_server_card_authorized(runtime_context, card):
            return McpLoadResult.fail(
                McpLoadErrorCode.PERMISSION_DENIED,
                Messages.Loader.UNAUTHORIZED_SERVER,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        if self.cache is None:
            # No-cache path matches pre-cache behaviour exactly.
            return await self._load_uncached(request, resolution)

        cache_key = McpDiscoveryCacheKey(
            server_name=card.name,
            org_id=runtime_context.org_id,
            user_id=runtime_context.user_id,
        )
        # The captured-result pattern lets us surface a typed
        # ``McpLoadResult`` failure (e.g. timeout) to the caller while
        # signalling "do not cache" to ``get_or_load`` via the ``None``
        # return — failure results are not memoized.
        captured_result: dict[str, McpLoadResult] = {}

        async def _load() -> LoadedMcpServer | None:
            result = await self._load_uncached(request, resolution)
            captured_result["value"] = result
            return result.loaded_server if result.succeeded else None

        cached_record = await self.cache.get_or_load_cache_entry(
            cache_key,
            source_id=card.server_id,
            load=_load,
        )
        if "value" in captured_result:
            live_result = captured_result["value"]
            if live_result.succeeded and cached_record is None:
                # The cache's invalidation generation changed while the live
                # discovery I/O was running. Do not surface descriptors that
                # crossed a re-auth, pause, uninstall, or revocation boundary.
                return McpLoadResult.fail(
                    McpLoadErrorCode.CONNECTION_FAILED,
                    Messages.Loader.LOAD_FAILED,
                    retryable=True,
                    server_name=card.name,
                    correlation_id=runtime_context.trace_id,
                )
            # Network path ran without an invalidation race, or produced a
            # typed failure that was intentionally not cached.
            return live_result
        if cached_record is not None:
            return McpLoadResult.ok(cached_record)
        # Defensive: ``get_or_load`` should never return ``None`` without
        # ``_load`` having run. Surface a generic failure so the model
        # still sees a typed result rather than an exception.
        return McpLoadResult.fail(
            McpLoadErrorCode.CONNECTION_FAILED,
            Messages.Loader.LOAD_FAILED,
            retryable=True,
            server_name=card.name,
            correlation_id=runtime_context.trace_id,
        )

    async def _load_uncached(
        self,
        request: McpLoadRequest,
        resolution: RegisteredMcpServer,
    ) -> McpLoadResult:
        """Bind the connector identity around the whole live discovery span.

        A schema repair fires inside a Pydantic field validator, which sees the
        descriptor's own fields but never the card that hosts it — so without
        this the repair log would say "some schema somewhere was rewritten" and
        the next reader could not tell which vendor to file the bug against.

        This is the one span that covers *both* places a descriptor gets built:
        the provider's own ``_tool_descriptor`` (which runs inside
        ``client.list_tools()``) and ``McpLoaderHelpers.parse_tools`` (which
        validates raw dicts afterwards). Wrapping here rather than at either
        one is what keeps a second construction path from going unattributed.
        """

        with McpSchemaRepairLog.for_server(resolution.card.name):
            return await self._discover_uncached(request, resolution)

    async def _discover_uncached(
        self,
        request: McpLoadRequest,
        resolution: RegisteredMcpServer,
    ) -> McpLoadResult:
        """Run the live discovery path: ``connect + list_tools + list_resources`` + validation.

        Permission and transport checks happen in ``load_server`` before
        this is reached; this helper assumes the card is already
        authorised. Returns a typed ``McpLoadResult`` for every outcome
        so the caller can route success/failure uniformly.
        """

        runtime_context = request.runtime_context
        card = resolution.card

        client: McpClient | None = None
        cancel_client = True
        try:
            client = resolution.provider.create_client(card)
            metadata = await self._connect(client, resolution)
            if isinstance(client, PaginatedMcpClient):
                raw_tools = await self._list_all_tool_pages(client)
                raw_resources = await self._list_all_resource_pages(client)
            else:
                raw_tools = await self._call_client(client.list_tools)
                raw_resources = await self._call_client(client.list_resources)
        except (McpTimeoutError, TimeoutError, asyncio.TimeoutError):
            return McpLoadResult.fail(
                McpLoadErrorCode.TIMEOUT,
                Messages.Loader.TIMEOUT,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except (McpAuthError, PermissionError):
            return McpLoadResult.fail(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        # Both 4xx classes are caught BEFORE the connection family and are
        # never ``retryable``. They are not connection failures at all — the
        # peer answered and refused — so describing them as "could not be
        # reached" is what produced the "temporary connection issue, try again
        # in a moment" copy for a deterministic 400.
        except McpNotFoundError:
            return McpLoadResult.fail(
                McpLoadErrorCode.UNKNOWN_SERVER,
                Messages.Loader.SERVER_NOT_FOUND,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except McpRequestRejectedError:
            return McpLoadResult.fail(
                McpLoadErrorCode.MCP_PROTOCOL_ERROR,
                Messages.Loader.REQUEST_REJECTED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except (McpConnectionError, ConnectionError):
            return McpLoadResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.CONNECTION_FAILED,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except ValidationError:
            return McpLoadResult.fail(
                McpLoadErrorCode.MALFORMED_DESCRIPTOR,
                Messages.Loader.INVALID_CONNECTION_METADATA,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except _McpDiscoveryPaginationError as exc:
            return McpLoadResult.fail(
                exc.code,
                McpLoaderHelpers.safe_descriptor_message(exc.code),
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except (AgentRuntimeError, McpClientError, TimeoutError, ConnectionError):
            return McpLoadResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "Unexpected error loading MCP server %s",
                card.name,
                exc_info=True,
            )
            return McpLoadResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        else:
            cancel_client = False
        finally:
            if client is not None:
                await aclose_mcp_client_safely(client, cancel=cancel_client)

        raw_tools = McpLoaderHelpers.coerce_raw_sequence(raw_tools)
        raw_resources = McpLoaderHelpers.coerce_raw_sequence(raw_resources)
        if raw_tools is None or raw_resources is None:
            return McpLoadResult.fail(
                McpLoadErrorCode.MALFORMED_DESCRIPTOR,
                Messages.Loader.DESCRIPTORS_INVALID,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        if len(raw_tools) > self.max_tool_descriptors:
            return McpLoadResult.fail(
                McpLoadErrorCode.LOAD_BUDGET_EXCEEDED,
                Messages.Loader.TOOL_BUDGET_EXCEEDED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        if len(raw_resources) > self.max_resource_descriptors:
            return McpLoadResult.fail(
                McpLoadErrorCode.LOAD_BUDGET_EXCEEDED,
                Messages.Loader.RESOURCE_BUDGET_EXCEEDED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        parsed_tools = McpLoaderHelpers.parse_tools(raw_tools)
        if isinstance(parsed_tools, McpLoadErrorCode):
            return McpLoadResult.fail(
                parsed_tools,
                McpLoaderHelpers.safe_descriptor_message(parsed_tools),
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        parsed_resources = McpLoaderHelpers.parse_resources(raw_resources)
        if isinstance(parsed_resources, McpLoadErrorCode):
            return McpLoadResult.fail(
                parsed_resources,
                McpLoaderHelpers.safe_descriptor_message(parsed_resources),
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        duplicate_tool_name = McpLoaderHelpers.first_duplicate_name(
            [tool.name for tool in parsed_tools]
        )
        if duplicate_tool_name is not None:
            return McpLoadResult.fail(
                McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME,
                Messages.Loader.DUPLICATE_TOOL_NAMES,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        duplicate_resource_name = McpLoaderHelpers.first_duplicate_name(
            [resource.name for resource in parsed_resources]
        )
        if duplicate_resource_name is not None:
            return McpLoadResult.fail(
                McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME,
                Messages.Loader.DUPLICATE_RESOURCE_NAMES,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        local_collision = McpLoaderHelpers.first_local_tool_collision(
            parsed_tools,
            request.local_tool_names,
        )
        if local_collision is not None:
            return McpLoadResult.fail(
                McpLoadErrorCode.LOCAL_TOOL_COLLISION,
                Messages.Loader.LOCAL_TOOL_COLLISION,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        warnings = ()
        if card.health == McpServerHealth.DEGRADED:
            warnings = (
                McpLoadWarning(
                    code=McpWarningCode.SERVER_DEGRADED,
                    safe_message=Messages.Loader.SERVER_DEGRADED,
                ),
            )

        return McpLoadResult.ok(
            LoadedMcpServer(
                server_card=card,
                tools=parsed_tools,
                resources=parsed_resources,
                connection_metadata=metadata,
                warnings=warnings,
            )
        )

    async def load_server_by_name(
        self,
        *,
        server_name: str,
        runtime_context: object,
        local_tool_names: object = (),
    ) -> McpLoadResult:
        """Parse an untrusted model request before loading a selected server."""

        try:
            request = McpLoadRequest(
                server_name=server_name,
                runtime_context=runtime_context,
                local_tool_names=local_tool_names,
            )
        except ValidationError as exc:
            if McpLoaderHelpers.validation_failed_for(exc, Keys.Field.LOCAL_TOOL_NAMES):
                return McpLoadResult.fail(
                    McpLoadErrorCode.INVALID_LOCAL_TOOL_NAMES,
                    Messages.Loader.LOCAL_TOOL_NAMES_INVALID,
                    server_name=McpLoaderHelpers.safe_server_name(server_name),
                )
            return McpLoadResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                server_name=McpLoaderHelpers.safe_server_name(server_name),
            )
        return await self.load_server(request)

    async def _list_all_tool_pages(
        self,
        client: PaginatedMcpClient,
    ) -> tuple[object, ...]:
        items: list[object] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(self.max_discovery_pages):
            page = await self._call_client(
                lambda: client.list_tools_page(cursor=cursor)
            )
            if not isinstance(page, McpToolDiscoveryPage):
                raise _McpDiscoveryPaginationError(
                    McpLoadErrorCode.MALFORMED_DESCRIPTOR
                )
            items.extend(page.items)
            if len(items) > self.max_tool_descriptors:
                return tuple(items)
            cursor = self._next_discovery_cursor(
                page.next_cursor,
                seen_cursors=seen_cursors,
            )
            if cursor is None:
                return tuple(items)
        raise _McpDiscoveryPaginationError(McpLoadErrorCode.LOAD_BUDGET_EXCEEDED)

    async def _list_all_resource_pages(
        self,
        client: PaginatedMcpClient,
    ) -> tuple[object, ...]:
        items: list[object] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(self.max_discovery_pages):
            page = await self._call_client(
                lambda: client.list_resources_page(cursor=cursor)
            )
            if not isinstance(page, McpResourceDiscoveryPage):
                raise _McpDiscoveryPaginationError(
                    McpLoadErrorCode.MALFORMED_DESCRIPTOR
                )
            items.extend(page.items)
            if len(items) > self.max_resource_descriptors:
                return tuple(items)
            cursor = self._next_discovery_cursor(
                page.next_cursor,
                seen_cursors=seen_cursors,
            )
            if cursor is None:
                return tuple(items)
        raise _McpDiscoveryPaginationError(McpLoadErrorCode.LOAD_BUDGET_EXCEEDED)

    @staticmethod
    def _next_discovery_cursor(
        next_cursor: str | None,
        *,
        seen_cursors: set[str],
    ) -> str | None:
        if next_cursor is None:
            return None
        if next_cursor in seen_cursors:
            raise _McpDiscoveryPaginationError(McpLoadErrorCode.MALFORMED_DESCRIPTOR)
        seen_cursors.add(next_cursor)
        return next_cursor

    async def _connect(
        self,
        client: McpClient,
        resolution: RegisteredMcpServer,
    ) -> McpConnectionMetadata:
        """Connect the client and coerce raw connection metadata to a typed record."""
        raw_metadata = await self._call_client(client.connect)
        return McpLoaderHelpers.metadata_from_raw(raw_metadata, resolution)

    async def _call_client(self, call: Callable[[], Awaitable[_T]]) -> _T:
        """Invoke a client coroutine under the configured timeout budget."""
        return await asyncio.wait_for(call(), timeout=self.timeout_seconds)


class McpLoaderHelpers:
    """Helper methods for parsing and comparing MCP load output."""

    @classmethod
    def metadata_from_raw(
        cls,
        raw_metadata: RawMcpConnectionMetadata,
        resolution: RegisteredMcpServer,
    ) -> McpConnectionMetadata:
        """Coerce raw connection metadata to a typed ``McpConnectionMetadata``."""
        card = resolution.card
        if raw_metadata is None:
            return McpConnectionMetadata(
                server_name=card.name,
                transport=card.transport,
                auth_mode=card.auth_mode,
            )
        if isinstance(raw_metadata, McpConnectionMetadata):
            return raw_metadata
        return McpConnectionMetadata.model_validate(raw_metadata)

    @classmethod
    def coerce_raw_sequence(cls, raw_value: object) -> Sequence[object] | None:
        """Return ``raw_value`` as a ``Sequence`` or ``None`` if it is not a valid list-like."""
        if isinstance(raw_value, (str, bytes)) or not isinstance(raw_value, Sequence):
            return None
        return raw_value

    @classmethod
    def parse_tools(
        cls,
        raw_tools: Sequence[object],
    ) -> tuple[McpToolDescriptor, ...] | McpLoadErrorCode:
        """Validate raw tool entries into typed ``McpToolDescriptor``s; return an error code on failure."""
        try:
            return tuple(
                raw_tool
                if isinstance(raw_tool, McpToolDescriptor)
                else McpToolDescriptor.model_validate(raw_tool)
                for raw_tool in raw_tools
            )
        except (TypeError, ValidationError):
            return McpLoadErrorCode.MALFORMED_DESCRIPTOR

    @classmethod
    def parse_resources(
        cls,
        raw_resources: Sequence[object],
    ) -> tuple[McpResourceDescriptor, ...] | McpLoadErrorCode:
        """Validate raw resource entries into typed ``McpResourceDescriptor``s; return an error code on failure."""
        try:
            return tuple(
                raw_resource
                if isinstance(raw_resource, McpResourceDescriptor)
                else McpResourceDescriptor.model_validate(raw_resource)
                for raw_resource in raw_resources
            )
        except (TypeError, ValidationError):
            return McpLoadErrorCode.MALFORMED_DESCRIPTOR

    @classmethod
    def first_duplicate_name(cls, names: Sequence[str]) -> str | None:
        """Return the lexicographically first duplicate name in ``names``, or ``None``."""
        counts = Counter(names)
        duplicate_names = sorted(name for name, count in counts.items() if count > 1)
        if not duplicate_names:
            return None
        return duplicate_names[0]

    @classmethod
    def first_local_tool_collision(
        cls,
        tools: Sequence[McpToolDescriptor],
        local_tool_names: frozenset[str],
    ) -> str | None:
        """Return the first tool name that collides with a local tool, or ``None``."""
        collisions = sorted(
            {tool.name for tool in tools}.intersection(local_tool_names)
        )
        if not collisions:
            return None
        return collisions[0]

    @classmethod
    def result_from_error(
        cls, error: McpLoadError, correlation_id: str
    ) -> McpLoadResult:
        """Lift a pre-built ``McpLoadError`` into a ``McpLoadResult``."""
        return McpLoadResult.fail(
            error.code,
            error.safe_message,
            retryable=error.retryable,
            server_name=error.server_name,
            correlation_id=correlation_id,
        )

    @classmethod
    def validation_failed_for(cls, exc: ValidationError, field_name: str) -> bool:
        """Return ``True`` when ``exc`` has at least one error located at ``field_name``."""
        return any(error.get("loc", ())[:1] == (field_name,) for error in exc.errors())

    @classmethod
    def safe_descriptor_message(cls, code: McpLoadErrorCode) -> str:
        """Return a safe user-facing message for a descriptor error code."""
        if code == McpLoadErrorCode.MALFORMED_DESCRIPTOR:
            return Messages.Loader.DESCRIPTORS_INVALID
        return Messages.Loader.DESCRIPTORS_LOAD_FAILED

    @classmethod
    def safe_server_name(cls, server_name: str) -> str | None:
        """Normalise ``server_name`` to a slug, or ``None`` if invalid."""
        try:
            return McpValueNormalizer.normalize_slug(
                server_name, Keys.Field.SERVER_NAME
            )
        except ValueError:
            return None
