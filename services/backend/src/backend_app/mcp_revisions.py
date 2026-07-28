"""Domain authority and ports for MCP descriptor revision freshness."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from backend_app.contracts import (
    McpDescriptorRevision,
    McpDescriptorRevisionFeed,
    McpRevisionReason,
)
from backend_app.mcp_revision_store import (
    InMemoryMcpRevisionStore,
    PostgresMcpRevisionStore,
    RevisionCursorExpired,
)
from backend_app.store import InMemoryMcpStore, PostgresMcpStore


@runtime_checkable
class McpRevisionStorePort(Protocol):
    """Persistence port; descriptor authority contains no adapter SQL."""

    def get_current(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpDescriptorRevision | None: ...

    def feed(
        self,
        *,
        org_id: str,
        user_id: str,
        after_cursor: str | None,
        limit: int,
    ) -> McpDescriptorRevisionFeed: ...

    def invalidate(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        reason: McpRevisionReason,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> None: ...

    def publish_complete_descriptor_view(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        descriptor_digest: str,
        tool_count: int,
        resource_count: int,
        source: str,
        idempotency_key: str,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> McpDescriptorRevision: ...


class McpDescriptorRevisionPublisher(Protocol):
    """Clean port for a future complete, paginated descriptor observer."""

    def publish_complete_descriptor_view(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        descriptor_digest: str,
        tool_count: int,
        resource_count: int,
        source: str,
        idempotency_key: str,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> McpDescriptorRevision: ...


class McpRevisionAuthority:
    """Typed, body-free authority delegating persistence through one port."""

    def __init__(
        self,
        store: McpRevisionStorePort | None = None,
        *,
        retain_max: int = 10_000,
    ) -> None:
        self._store = store or InMemoryMcpRevisionStore(retain_max=retain_max)

    @classmethod
    def for_mcp_store(
        cls,
        store: InMemoryMcpStore | PostgresMcpStore,
        *,
        retain_max: int = 10_000,
    ) -> McpRevisionAuthority:
        # Composition decides the adapter once; domain methods never inspect
        # a concrete registry store or execute persistence SQL.
        if isinstance(store, PostgresMcpStore):
            return cls(PostgresMcpRevisionStore(store=store, retain_max=retain_max))
        return cls(InMemoryMcpRevisionStore(retain_max=retain_max))

    def get_current(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpDescriptorRevision | None:
        return self._store.get_current(
            org_id=org_id, user_id=user_id, server_id=server_id
        )

    def feed(
        self,
        *,
        org_id: str,
        user_id: str,
        after_cursor: str | None,
        limit: int,
    ) -> McpDescriptorRevisionFeed:
        return self._store.feed(
            org_id=org_id,
            user_id=user_id,
            after_cursor=after_cursor,
            limit=limit,
        )

    def invalidate(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        reason: McpRevisionReason,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> None:
        self._store.invalidate(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            reason=reason,
            credential_subject=credential_subject,
            conn=conn,
        )

    def publish_complete_descriptor_view(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        descriptor_digest: str,
        tool_count: int,
        resource_count: int,
        source: str,
        idempotency_key: str,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> McpDescriptorRevision:
        return self._store.publish_complete_descriptor_view(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            descriptor_digest=descriptor_digest,
            tool_count=tool_count,
            resource_count=resource_count,
            source=source,
            idempotency_key=idempotency_key,
            credential_subject=credential_subject,
            conn=conn,
        )


__all__ = [
    "McpDescriptorRevisionPublisher",
    "McpRevisionAuthority",
    "McpRevisionStorePort",
    "RevisionCursorExpired",
]
