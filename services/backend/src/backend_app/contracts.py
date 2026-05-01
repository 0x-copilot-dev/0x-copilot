"""Typed contracts for product-owned MCP registry state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class BackendContract(BaseModel):
    """Base model for backend API and persistence boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class McpTransport(StrEnum):
    HTTP = "http"
    SSE = "sse"
    STDIO = "stdio"


class McpAuthMode(StrEnum):
    NONE = "none"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    SERVICE_ACCOUNT = "service_account"


class McpAuthState(StrEnum):
    UNAUTHENTICATED = "unauthenticated"
    AUTH_SKIPPED = "auth_skipped"
    AUTH_PENDING = "auth_pending"
    AUTHENTICATED = "authenticated"
    AUTH_FAILED = "auth_failed"
    AUTH_UNSUPPORTED = "auth_unsupported"


class McpServerHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class SkillScope(StrEnum):
    USER = "user"
    ORG = "org"


class SkillSourceType(StrEnum):
    USER = "user"
    PRELOADED = "preloaded"


class McpOAuthClientConfig(BackendContract):
    client_id: str
    encrypted_client_secret: str | None = None
    token_endpoint_auth_method: str = "none"
    scope: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None

    @field_validator("client_id", "token_endpoint_auth_method")
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        return normalize_text(value)

    @field_validator("scope")
    @classmethod
    def _normalize_scope(cls, value: object) -> str | None:
        if value is None:
            return None
        scopes = normalize_text(value).split()
        if not scopes:
            return None
        return " ".join(scopes)

    @field_validator("authorization_endpoint", "token_endpoint")
    @classmethod
    def _validate_optional_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return validate_public_mcp_url(value)


class McpOAuthClientRequest(BackendContract):
    client_id: str
    client_secret: str | None = None
    token_endpoint_auth_method: str | None = None
    scope: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None

    @field_validator("client_id")
    @classmethod
    def _normalize_client_id(cls, value: object) -> str:
        return normalize_text(value)

    @field_validator("client_secret", "token_endpoint_auth_method")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_text(value)

    @field_validator("scope")
    @classmethod
    def _normalize_scope(cls, value: object) -> str | None:
        if value is None:
            return None
        scopes = normalize_text(value).split()
        if not scopes:
            return None
        return " ".join(scopes)

    @field_validator("authorization_endpoint", "token_endpoint")
    @classmethod
    def _validate_optional_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return validate_public_mcp_url(value)


class McpServerRecord(BackendContract):
    server_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    name: str
    display_name: str
    url: str
    transport: McpTransport = McpTransport.HTTP
    auth_mode: McpAuthMode = McpAuthMode.OAUTH2
    auth_state: McpAuthState = McpAuthState.UNAUTHENTICATED
    health: McpServerHealth = McpServerHealth.HEALTHY
    enabled: bool = True
    required_scopes: tuple[str, ...] = ()
    last_discovery: dict[str, Any] = Field(default_factory=dict)
    oauth_client: McpOAuthClientConfig | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("server_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return normalize_skill_slug(value)

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return normalize_text(value)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: object) -> str:
        return validate_public_mcp_url(value)


class SkillManifestFields(BackendContract):
    name: str
    description: str
    license: str | None = None
    compatibility: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return normalize_skill_slug(value)

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        return normalize_text(value)


class SkillRecord(BackendContract):
    skill_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    name: str
    display_name: str
    description: str
    markdown: str
    virtual_path: str
    enabled: bool = True
    scope: SkillScope = SkillScope.USER
    source_type: SkillSourceType = SkillSourceType.USER
    version: int = Field(default=1, ge=1)
    allowed_tools: tuple[str, ...] = ()
    compatibility: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("skill_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return normalize_skill_slug(value)

    @field_validator("display_name", "description", "virtual_path")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return normalize_text(value)

    @field_validator("markdown")
    @classmethod
    def _validate_markdown(cls, value: object) -> str:
        return validate_markdown(value)


class CreateSkillRequest(BackendContract):
    org_id: str
    user_id: str
    markdown: str
    display_name: str | None = None
    enabled: bool = True
    scope: SkillScope = SkillScope.USER

    @field_validator("org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("markdown")
    @classmethod
    def _normalize_markdown(cls, value: object) -> str:
        return validate_markdown(value)

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_text(value)


class UpdateSkillRequest(BackendContract):
    markdown: str | None = None
    display_name: str | None = None
    enabled: bool | None = None
    scope: SkillScope | None = None

    @field_validator("display_name")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_text(value)

    @field_validator("markdown")
    @classmethod
    def _validate_optional_markdown(cls, value: object) -> str | None:
        if value is None:
            return None
        return validate_markdown(value)


class SkillResponse(BackendContract):
    skill_id: str
    name: str
    display_name: str
    description: str
    markdown: str
    virtual_path: str
    enabled: bool
    scope: SkillScope
    source_type: SkillSourceType
    version: int
    allowed_tools: tuple[str, ...]
    compatibility: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: SkillRecord) -> "SkillResponse":
        return cls(
            skill_id=record.skill_id,
            name=record.name,
            display_name=record.display_name,
            description=record.description,
            markdown=record.markdown,
            virtual_path=record.virtual_path,
            enabled=record.enabled,
            scope=record.scope,
            source_type=record.source_type,
            version=record.version,
            allowed_tools=record.allowed_tools,
            compatibility=record.compatibility,
            metadata=record.metadata,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class SkillListResponse(BackendContract):
    skills: tuple[SkillResponse, ...]


class InternalSkillCard(BackendContract):
    skill_id: str
    name: str
    display_name: str
    description: str
    virtual_path: str
    scope: SkillScope
    source_type: SkillSourceType
    version: int
    allowed_tools: tuple[str, ...] = ()
    enabled: bool = True


class InternalSkillListResponse(BackendContract):
    skills: tuple[InternalSkillCard, ...]


class InternalSkillBundle(BackendContract):
    skill_id: str
    name: str
    display_name: str
    description: str
    markdown: str
    virtual_path: str
    version: int
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateMcpServerRequest(BackendContract):
    org_id: str
    user_id: str
    url: str
    display_name: str | None = None
    transport: McpTransport = McpTransport.HTTP
    auth_mode: McpAuthMode = McpAuthMode.OAUTH2
    oauth_client: McpOAuthClientRequest | None = None

    @field_validator("org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: object) -> str:
        return validate_public_mcp_url(value)


class UpdateMcpServerRequest(BackendContract):
    display_name: str | None = None
    enabled: bool | None = None
    oauth_client: McpOAuthClientRequest | None = None

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_text(value)


class McpServerResponse(BackendContract):
    server_id: str
    name: str
    display_name: str
    url: str
    transport: McpTransport
    auth_mode: McpAuthMode
    auth_state: McpAuthState
    health: McpServerHealth
    enabled: bool
    oauth_client_configured: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: McpServerRecord) -> "McpServerResponse":
        return cls(
            server_id=record.server_id,
            name=record.name,
            display_name=record.display_name,
            url=record.url,
            transport=record.transport,
            auth_mode=record.auth_mode,
            auth_state=record.auth_state,
            health=record.health,
            enabled=record.enabled,
            oauth_client_configured=record.oauth_client is not None,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class McpServerListResponse(BackendContract):
    servers: tuple[McpServerResponse, ...]


class McpAuthSessionRecord(BackendContract):
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    server_id: str
    org_id: str
    user_id: str
    state: str = Field(default_factory=lambda: uuid4().hex)
    code_verifier: str
    redirect_uri: str
    auth_url: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("session_id", "server_id", "org_id", "user_id", "state")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)


class McpAuthStartRequest(BackendContract):
    org_id: str
    user_id: str
    redirect_uri: str

    @field_validator("org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("redirect_uri")
    @classmethod
    def _validate_redirect_uri(cls, value: object) -> str:
        return validate_public_mcp_url(value, allow_localhost=True)


class McpAuthStartResponse(BackendContract):
    server_id: str
    auth_url: str
    expires_at: datetime


class McpAuthCallbackRequest(BackendContract):
    state: str
    code: str | None = None
    error: str | None = None
    error_description: str | None = None

    @field_validator("state")
    @classmethod
    def _normalize_state(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("code", "error", "error_description")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_text(value)

    @model_validator(mode="after")
    def _require_code_or_error(self) -> "McpAuthCallbackRequest":
        if self.code is None and self.error is None:
            raise ValueError("OAuth callback must include code or error")
        return self


class InternalMcpServerCard(BackendContract):
    server_id: str
    name: str
    display_name: str
    short_description: str
    transport: McpTransport
    auth_mode: McpAuthMode
    auth_state: McpAuthState
    required_scopes: tuple[str, ...] = ()
    health: McpServerHealth
    load_cost: int = Field(default=1, ge=1, le=100_000)
    enabled: bool = True


class InternalMcpServerListResponse(BackendContract):
    servers: tuple[InternalMcpServerCard, ...]


class InternalMcpAuthRequest(BackendContract):
    org_id: str
    user_id: str
    redirect_uri: str

    @field_validator("org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)


class InternalMcpClientSession(BackendContract):
    server_id: str
    url: str
    transport: McpTransport
    auth_state: McpAuthState
    credential_ref: str | None = None


class InternalMcpRpcRequest(BackendContract):
    org_id: str
    user_id: str
    payload: dict[str, Any]

    @field_validator("org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return normalize_id(value)

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("payload must not be empty")
        return value


class InternalMcpRpcResponse(BackendContract):
    payload: dict[str, Any]


class TokenEnvelope(BackendContract):
    connection_id: str = Field(default_factory=lambda: uuid4().hex)
    server_id: str
    org_id: str
    user_id: str
    encrypted_access_token: str
    encrypted_refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    server_id: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SkillAuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    skill_id: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OAuthTokenRequest(BackendContract):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    scope: str | None = None

    @model_validator(mode="after")
    def _require_access_token(self) -> "OAuthTokenRequest":
        if not self.access_token.strip():
            raise ValueError("access_token must not be empty")
        return self


def normalize_id(value: object) -> str:
    text = normalize_text(value)
    if not _ID_PATTERN.fullmatch(text):
        raise ValueError("identifier contains unsupported characters")
    return text


def normalize_skill_slug(value: object) -> str:
    text = normalize_text(value).lower().replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValueError("name must be a stable slug")
    return slug


def normalize_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    text = value.strip()
    if not text:
        raise ValueError("value must not be empty")
    return text


def validate_markdown(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    if not value.strip():
        raise ValueError("value must not be empty")
    return value


def validate_public_mcp_url(value: object, *, allow_localhost: bool = False) -> str:
    url = normalize_text(value)
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("MCP URL must use http or https")
    if parsed.scheme == "http" and not allow_localhost:
        raise ValueError("MCP URL must use https")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("MCP URL must include a host")
    blocked_hosts = {"0.0.0.0", "127.0.0.1", "::1", "localhost"}
    if not allow_localhost and hostname in blocked_hosts:
        raise ValueError("MCP URL cannot target localhost")
    if not allow_localhost and (
        hostname.startswith("10.")
        or hostname.startswith("192.168.")
        or hostname.startswith("172.16.")
    ):
        raise ValueError("MCP URL cannot target private networks")
    return url
