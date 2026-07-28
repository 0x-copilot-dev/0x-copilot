"""Typed, body-free wire client for backend MCP revision authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import os

import httpx
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from pydantic import Field, field_validator

from agent_runtime.capabilities.http_pool import BackendHttpPool
from agent_runtime.execution.contracts import RuntimeContract

_ID_MAX_LENGTH = 256
_REVISION_MAX_LENGTH = 512
_CURSOR_MAX_LENGTH = 512
_SOURCE_MAX_LENGTH = 128
_MAX_PAGE_LENGTH = 100


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _bounded(value: str, *, field_name: str, max_length: int) -> str:
    if not value or len(value) > max_length:
        raise ValueError(f"{field_name} must be between 1 and {max_length} characters")
    return value


class BackendMcpRevisionWireError(RuntimeError):
    """Base error for body-free MCP revision authority requests."""


class BackendMcpRevisionNotFound(BackendMcpRevisionWireError):
    """The requested server has no revision authority record."""


class BackendMcpRevisionCursorExpired(BackendMcpRevisionWireError):
    """The revision feed cursor is no longer retained by the backend."""


class BackendMcpRevisionUnavailable(BackendMcpRevisionWireError):
    """The revision authority could not be contacted or rejected the request."""


class BackendMcpRevisionReason(StrEnum):
    CONFIG_CHANGED = "config_changed"
    AUTH_CHANGED = "auth_changed"
    TRANSPORT_CHANGED = "transport_changed"
    TOOL_FILTER_CHANGED = "tool_filter_changed"
    SERVER_DELETED = "server_deleted"
    DESCRIPTOR_OBSERVED = "descriptor_observed"


class BackendMcpRevision(RuntimeContract):
    server_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    revision: str = Field(min_length=1, max_length=_REVISION_MAX_LENGTH)
    subject_scope_hash: str = Field(min_length=1, max_length=_REVISION_MAX_LENGTH)
    profile_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    config_generation: int = Field(ge=0)
    auth_generation: int = Field(ge=0)
    transport_generation: int = Field(ge=0)
    tool_filter_generation: int = Field(ge=0)
    tool_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)
    descriptor_digest: str = Field(min_length=1, max_length=_REVISION_MAX_LENGTH)
    observed_at: datetime
    source: str = Field(min_length=1, max_length=_SOURCE_MAX_LENGTH)

    @field_validator("observed_at")
    @classmethod
    def _validate_observed_at(cls, value: datetime) -> datetime:
        return _aware(value, "observed_at")


class BackendMcpRevisionNotice(RuntimeContract):
    cursor: str = Field(min_length=1, max_length=_CURSOR_MAX_LENGTH)
    server_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    new_revision: str | None = Field(default=None, max_length=_REVISION_MAX_LENGTH)
    notice_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    sequence_no: int = Field(ge=1)
    profile_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    subject_scope_hash: str = Field(min_length=1, max_length=_REVISION_MAX_LENGTH)
    old_revision: str | None = Field(default=None, max_length=_REVISION_MAX_LENGTH)
    reason: BackendMcpRevisionReason
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _validate_occurred_at(cls, value: datetime) -> datetime:
        return _aware(value, "occurred_at")


class BackendMcpRevisionFeed(RuntimeContract):
    notices: tuple[BackendMcpRevisionNotice, ...] = Field(
        default=(), max_length=_MAX_PAGE_LENGTH
    )
    next_cursor: str | None = Field(default=None, max_length=_CURSOR_MAX_LENGTH)


@dataclass(frozen=True)
class BackendMcpRevisionClient:
    """Internal HTTP client; never receives credentials or descriptor bodies."""

    backend_url: str
    timeout_seconds: float = 10
    http_client: httpx.AsyncClient = field(
        default_factory=BackendHttpPool.get, repr=False, compare=False
    )

    def _headers(self, *, org_id: str, user_id: str) -> dict[str, str]:
        return {
            ORG_HEADER: _bounded(
                org_id, field_name="org_id", max_length=_ID_MAX_LENGTH
            ),
            USER_HEADER: _bounded(
                user_id, field_name="user_id", max_length=_ID_MAX_LENGTH
            ),
            **(
                {SERVICE_TOKEN_HEADER: token}
                if (token := os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip())
                else {}
            ),
        }

    async def get_exact(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> BackendMcpRevision:
        server_id = _bounded(
            server_id, field_name="server_id", max_length=_ID_MAX_LENGTH
        )
        try:
            response = await self.http_client.get(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/revision",
                params={"org_id": org_id, "user_id": user_id},
                headers=self._headers(org_id=org_id, user_id=user_id),
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise BackendMcpRevisionUnavailable(
                "revision authority request failed"
            ) from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            raise BackendMcpRevisionNotFound(server_id)
        if response.is_error:
            raise BackendMcpRevisionUnavailable(
                f"revision authority returned HTTP {response.status_code}"
            )
        return BackendMcpRevision.model_validate(response.json())

    async def feed(
        self, *, org_id: str, user_id: str, after_cursor: str | None, limit: int = 100
    ) -> BackendMcpRevisionFeed:
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_PAGE_LENGTH:
            raise ValueError(f"limit must be between 1 and {_MAX_PAGE_LENGTH}")
        if after_cursor is not None:
            _bounded(
                after_cursor, field_name="after_cursor", max_length=_CURSOR_MAX_LENGTH
            )
        try:
            response = await self.http_client.get(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/descriptor-revisions",
                params={
                    "org_id": org_id,
                    "user_id": user_id,
                    "after_cursor": after_cursor,
                    "limit": limit,
                },
                headers=self._headers(org_id=org_id, user_id=user_id),
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise BackendMcpRevisionUnavailable("revision feed request failed") from exc
        if response.status_code == httpx.codes.GONE:
            raise BackendMcpRevisionCursorExpired(after_cursor or "initial feed")
        if response.is_error:
            raise BackendMcpRevisionUnavailable(
                f"revision feed returned HTTP {response.status_code}"
            )
        return BackendMcpRevisionFeed.model_validate(response.json())
