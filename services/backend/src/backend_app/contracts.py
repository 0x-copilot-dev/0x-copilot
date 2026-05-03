"""Typed contracts for product-owned MCP registry state."""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Fields:
    """Flat constant pool for every field name referenced in validators or key lookups."""

    ALLOWED_TOOLS = "allowed_tools"
    AUTH_STATE = "auth_state"
    AUTHORIZATION_ENDPOINT = "authorization_endpoint"
    CLIENT_ID = "client_id"
    CLIENT_SECRET = "client_secret"
    CODE = "code"
    COMPATIBILITY = "compatibility"
    CREATED_AT = "created_at"
    DESCRIPTION = "description"
    DISPLAY_NAME = "display_name"
    ENABLED = "enabled"
    ERROR = "error"
    ERROR_DESCRIPTION = "error_description"
    HEALTH = "health"
    LICENSE = "license"
    MARKDOWN = "markdown"
    METADATA = "metadata"
    NAME = "name"
    OAUTH_CLIENT = "oauth_client"
    ORG_ID = "org_id"
    PAYLOAD = "payload"
    REDIRECT_URI = "redirect_uri"
    SCOPE = "scope"
    SERVER_ID = "server_id"
    SESSION_ID = "session_id"
    SKILL_ID = "skill_id"
    SOURCE_TYPE = "source_type"
    STATE = "state"
    TOKEN_ENDPOINT = "token_endpoint"
    TOKEN_ENDPOINT_AUTH_METHOD = "token_endpoint_auth_method"
    UPDATED_AT = "updated_at"
    URL = "url"
    USER_ID = "user_id"
    VERSION = "version"
    VIRTUAL_PATH = "virtual_path"


class Validators:
    """Reusable input normalization and validation logic."""

    _LOCAL_HOSTNAMES = {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }

    @staticmethod
    def normalize_id(value: object) -> str:
        text = Validators.normalize_text(value)
        if not _ID_PATTERN.fullmatch(text):
            raise ValueError("identifier contains unsupported characters")
        return text

    @staticmethod
    def normalize_skill_slug(value: object) -> str:
        text = (
            Validators.normalize_text(value).lower().replace(" ", "_").replace("-", "_")
        )
        slug = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
        if not slug or not _SLUG_PATTERN.fullmatch(slug):
            raise ValueError("name must be a stable slug")
        return slug

    @staticmethod
    def normalize_text(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        text = value.strip()
        if not text:
            raise ValueError("value must not be empty")
        return text

    @staticmethod
    def validate_markdown(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @staticmethod
    def validate_public_mcp_url(value: object, *, allow_localhost: bool = False) -> str:
        url = Validators.normalize_text(value)
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("MCP URL must use http or https")
        if parsed.scheme == "http" and not allow_localhost:
            raise ValueError("MCP URL must use https")
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("MCP URL must include a host")

        if not allow_localhost:
            try:
                addr = ipaddress.ip_address(hostname)
            except ValueError:
                addr = None

            if addr is not None:
                if (
                    addr.is_private
                    or addr.is_loopback
                    or addr.is_link_local
                    or addr.is_reserved
                    or addr.is_unspecified
                ):
                    raise ValueError(
                        "MCP URL cannot target private or reserved networks"
                    )
            else:
                if hostname in Validators._LOCAL_HOSTNAMES or hostname.endswith(
                    ".local"
                ):
                    raise ValueError("MCP URL cannot target localhost")

        return url


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

    @field_validator(_Fields.CLIENT_ID, _Fields.TOKEN_ENDPOINT_AUTH_METHOD)
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        return Validators.normalize_text(value)

    @field_validator(_Fields.SCOPE)
    @classmethod
    def _normalize_scope(cls, value: object) -> str | None:
        if value is None:
            return None
        scopes = Validators.normalize_text(value).split()
        if not scopes:
            return None
        return " ".join(scopes)

    @field_validator(_Fields.AUTHORIZATION_ENDPOINT, _Fields.TOKEN_ENDPOINT)
    @classmethod
    def _validate_optional_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.validate_public_mcp_url(value)


class McpOAuthClientRequest(BackendContract):
    client_id: str
    client_secret: str | None = None
    token_endpoint_auth_method: str | None = None
    scope: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None

    @field_validator(_Fields.CLIENT_ID)
    @classmethod
    def _normalize_client_id(cls, value: object) -> str:
        return Validators.normalize_text(value)

    @field_validator(_Fields.CLIENT_SECRET, _Fields.TOKEN_ENDPOINT_AUTH_METHOD)
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.normalize_text(value)

    @field_validator(_Fields.SCOPE)
    @classmethod
    def _normalize_scope(cls, value: object) -> str | None:
        if value is None:
            return None
        scopes = Validators.normalize_text(value).split()
        if not scopes:
            return None
        return " ".join(scopes)

    @field_validator(_Fields.AUTHORIZATION_ENDPOINT, _Fields.TOKEN_ENDPOINT)
    @classmethod
    def _validate_optional_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.validate_public_mcp_url(value)


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(_Fields.SERVER_ID, _Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return Validators.normalize_skill_slug(value)

    @field_validator(_Fields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return Validators.normalize_text(value)

    @field_validator(_Fields.URL)
    @classmethod
    def _validate_url(cls, value: object) -> str:
        return Validators.validate_public_mcp_url(value)


class SkillManifestFields(BackendContract):
    name: str
    description: str
    license: str | None = None
    compatibility: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(_Fields.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return Validators.normalize_skill_slug(value)

    @field_validator(_Fields.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        return Validators.normalize_text(value)


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(_Fields.SKILL_ID, _Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return Validators.normalize_skill_slug(value)

    @field_validator(_Fields.DISPLAY_NAME, _Fields.DESCRIPTION, _Fields.VIRTUAL_PATH)
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return Validators.normalize_text(value)

    @field_validator(_Fields.MARKDOWN)
    @classmethod
    def _validate_markdown(cls, value: object) -> str:
        return Validators.validate_markdown(value)


class CreateSkillRequest(BackendContract):
    org_id: str
    user_id: str
    markdown: str
    display_name: str | None = None
    enabled: bool = True
    scope: SkillScope = SkillScope.USER

    @field_validator(_Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.MARKDOWN)
    @classmethod
    def _normalize_markdown(cls, value: object) -> str:
        return Validators.validate_markdown(value)

    @field_validator(_Fields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.normalize_text(value)


class UpdateSkillRequest(BackendContract):
    markdown: str | None = None
    display_name: str | None = None
    enabled: bool | None = None
    scope: SkillScope | None = None

    @field_validator(_Fields.DISPLAY_NAME)
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.normalize_text(value)

    @field_validator(_Fields.MARKDOWN)
    @classmethod
    def _validate_optional_markdown(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.validate_markdown(value)


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

    @field_validator(_Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.URL)
    @classmethod
    def _validate_url(cls, value: object) -> str:
        return Validators.validate_public_mcp_url(value)


class UpdateMcpServerRequest(BackendContract):
    display_name: str | None = None
    enabled: bool | None = None
    oauth_client: McpOAuthClientRequest | None = None

    @field_validator(_Fields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.normalize_text(value)


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        _Fields.SESSION_ID,
        _Fields.SERVER_ID,
        _Fields.ORG_ID,
        _Fields.USER_ID,
        _Fields.STATE,
    )
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class McpAuthStartRequest(BackendContract):
    org_id: str
    user_id: str
    redirect_uri: str

    @field_validator(_Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.REDIRECT_URI)
    @classmethod
    def _validate_redirect_uri(cls, value: object) -> str:
        return Validators.validate_public_mcp_url(value, allow_localhost=True)


class McpAuthStartResponse(BackendContract):
    server_id: str
    auth_url: str
    expires_at: datetime


class McpAuthCallbackRequest(BackendContract):
    state: str
    code: str | None = None
    error: str | None = None
    error_description: str | None = None

    @field_validator(_Fields.STATE)
    @classmethod
    def _normalize_state(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.CODE, _Fields.ERROR, _Fields.ERROR_DESCRIPTION)
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.normalize_text(value)

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

    @field_validator(_Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


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

    @field_validator(_Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_Fields.PAYLOAD)
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    server_id: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SkillAuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    skill_id: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
