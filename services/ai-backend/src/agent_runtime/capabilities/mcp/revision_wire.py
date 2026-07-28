"""Typed, body-free wire client for backend MCP revision authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import os

import httpx
from pydantic import Field

from agent_runtime.capabilities.http_pool import BackendHttpPool
from agent_runtime.execution.contracts import RuntimeContract


class BackendMcpRevisionReason(StrEnum):
    CONFIG_CHANGED = "config_changed"
    AUTH_CHANGED = "auth_changed"
    TRANSPORT_CHANGED = "transport_changed"
    TOOL_FILTER_CHANGED = "tool_filter_changed"
    SERVER_DELETED = "server_deleted"
    DESCRIPTOR_OBSERVED = "descriptor_observed"


class BackendMcpRevision(RuntimeContract):
    server_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    subject_scope_hash: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    config_generation: int = Field(ge=0)
    auth_generation: int = Field(ge=0)
    transport_generation: int = Field(ge=0)
    tool_filter_generation: int = Field(ge=0)
    tool_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)
    descriptor_digest: str = Field(min_length=1)
    observed_at: datetime
    source: str = Field(min_length=1)


class BackendMcpRevisionNotice(RuntimeContract):
    cursor: str = Field(min_length=1)
    server_id: str = Field(min_length=1)
    new_revision: str | None = None
    notice_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    profile_id: str = Field(min_length=1)
    subject_scope_hash: str = Field(min_length=1)
    old_revision: str | None = None
    reason: BackendMcpRevisionReason
    occurred_at: datetime


class BackendMcpRevisionFeed(RuntimeContract):
    notices: tuple[BackendMcpRevisionNotice, ...] = ()
    next_cursor: str | None = None


@dataclass(frozen=True)
class BackendMcpRevisionClient:
    """Internal HTTP client; never receives credentials or descriptor bodies."""

    backend_url: str
    timeout_seconds: float = 10
    http_client: httpx.AsyncClient = field(
        default_factory=BackendHttpPool.get, repr=False, compare=False
    )

    def _headers(self, *, org_id: str, user_id: str) -> dict[str, str]:
        headers = {"x-enterprise-org-id": org_id, "x-enterprise-user-id": user_id}
        token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if token:
            headers["x-enterprise-service-token"] = token
        return headers

    async def get_exact(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> BackendMcpRevision | None:
        response = await self.http_client.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/revision",
            params={"org_id": org_id, "user_id": user_id},
            headers=self._headers(org_id=org_id, user_id=user_id),
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return BackendMcpRevision.model_validate(response.json())

    async def feed(
        self, *, org_id: str, user_id: str, after_cursor: str | None, limit: int = 100
    ) -> BackendMcpRevisionFeed:
        response = await self.http_client.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/descriptor-revisions",
            params={
                "org_id": org_id,
                "user_id": user_id,
                "after_cursor": after_cursor,
                "limit": min(100, max(1, limit)),
            },
            headers=self._headers(org_id=org_id, user_id=user_id),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return BackendMcpRevisionFeed.model_validate(response.json())
