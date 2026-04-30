"""Protocol boundaries consumed by the agent runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolRegistry(Protocol):
    """Lists model-visible tools after runtime permissions are applied."""

    def list_available_tools(self, context: object) -> Sequence[object]:
        """Return compact or loaded tool objects visible to this context."""


@runtime_checkable
class McpRegistry(Protocol):
    """Lists MCP servers or capabilities after runtime permissions are applied."""

    def list_available_servers(self, context: object) -> Sequence[object]:
        """Return MCP server descriptors visible to this context."""


@runtime_checkable
class MemoryBackendFactory(Protocol):
    """Creates request-scoped memory backends without leaking concrete stores."""

    def create(self, context: object) -> object:
        """Create a memory backend for this runtime context."""


@runtime_checkable
class SubagentCatalog(Protocol):
    """Lists subagents after runtime permissions are applied."""

    def list_available_subagents(self, context: object) -> Sequence[object]:
        """Return subagent definitions visible to this context."""


@runtime_checkable
class StreamNormalizer(Protocol):
    """Normalizes runtime events before they are emitted to product surfaces."""

    def normalize(self, raw_event: Mapping[str, object], context: object) -> Sequence[object]:
        """Return redacted, typed stream events for this context."""
