"""Typed contracts for product-owned MCP registry state."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
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
    SYSTEM = "system"


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
    # PR 3.4.1 — brand metadata for the connector popover. ``logo_url`` /
    # ``brand_color`` / ``scopes_summary`` are presentation-only; the
    # frontend falls through to a letter glyph when missing. ``default_scopes``
    # is the resume-from-paused payload PR 1.2 round-trips through PATCH /…/connectors;
    # ``admin_managed`` gates the popover's ``Enable in Settings`` action.
    logo_url: str | None = None
    brand_color: str | None = None
    scopes_summary: str | None = None
    default_scopes: tuple[str, ...] = ()
    admin_managed: bool = False
    # Marketing description copied from the catalog entry on install. Empty
    # for custom (non-catalog) servers. Distinct from ``scopes_summary``
    # (what the connector *is allowed to do*) — this is what it *is*.
    description: str = ""
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
    # PR 3.4.1 — brand metadata mirrored from ``McpServerRecord``. Optional
    # everywhere so old clients tolerate missing values; new clients
    # render the favicon / scopes subtitle / per-row resume target.
    logo_url: str | None = None
    brand_color: str | None = None
    scopes_summary: str | None = None
    default_scopes: tuple[str, ...] = ()
    admin_managed: bool = False
    description: str = ""
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
            logo_url=record.logo_url,
            brand_color=record.brand_color,
            scopes_summary=record.scopes_summary,
            default_scopes=record.default_scopes,
            admin_managed=record.admin_managed,
            description=record.description,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class McpServerListResponse(BackendContract):
    servers: tuple[McpServerResponse, ...]


class McpCatalogEntryResponse(BackendContract):
    """One curated catalog entry — the wire shape of ``CatalogEntry``.

    PR 4.4.6 — catalog endpoint payload. Org-agnostic; static; sourced
    from ``mcp_catalog.DEFAULT_CATALOG``. The frontend caches this for
    the lifetime of the McpOverlay and only re-fetches on user action.

    Distinct from ``McpServerResponse``: catalog is what we *ship*; the
    server response is what the user has *installed*. The frontend
    cross-references catalog entries with installed servers by
    ``server_id == "seed:" + slug`` to render Install / Resume install /
    Installed state per card.
    """

    slug: str
    display_name: str
    url: str
    transport: McpTransport
    auth_mode: McpAuthMode
    description: str = ""
    logo_url: str | None = None
    brand_color: str | None = None
    scopes_summary: str | None = None
    default_scopes: tuple[str, ...] = ()
    # When True, install requires a pre-registered OAuth client (the
    # vendor doesn't support DCR or auth-server-metadata discovery).
    # Frontend prompts for ``client_id`` / ``client_secret`` first.
    requires_pre_registered_client: bool = False
    verified: bool = True
    # PR 4.4.7 (Phase 1) — workspace's progressive-discovery default
    # for this entry. Phase 1 surfaces this in the catalog UI as a
    # toggle and persists per-user state in the browser only; Phase 2
    # adds a runtime "suggested connectors" surface. The field rides
    # along now so app-side storage migrations land before the runtime
    # consumes them. Default True — admins can flip a vendor here
    # without touching runtime code.
    discoverable: bool = True


class McpCatalogResponse(BackendContract):
    entries: tuple[McpCatalogEntryResponse, ...]


class InstallMcpServerRequest(BackendContract):
    """Install a curated catalog entry into the user's workspace.

    PR 4.4.6 — explicit install. Server resolves ``slug`` against
    ``DEFAULT_CATALOG`` and creates a row keyed by stable
    ``seed:<slug>`` server_id. Idempotent on slug — re-installing
    returns the existing record. When the catalog entry sets
    ``requires_pre_registered_client``, the request must include
    ``oauth_client``; otherwise the service responds with HTTP 422 and
    the frontend opens the credentials form.
    """

    org_id: str
    user_id: str
    slug: str
    oauth_client: McpOAuthClientRequest | None = None

    @field_validator(_Fields.ORG_ID, _Fields.USER_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: object) -> str:
        return Validators.normalize_skill_slug(value)


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


class ToolKind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"


class ToolListEntry(BackendContract):
    """One row in the composer Tools popover.

    Aggregates user-installed skill bundles and registered MCP servers into
    a single sectioned listing. The ``kind`` discriminator is what lets the
    frontend partition the popover into its Skills and MCPs sections — same
    field that the public ``packages/api-types`` mirror exposes.
    """

    name: str
    label: str
    description: str | None = None
    kind: ToolKind


class ToolListResponse(BackendContract):
    tools: tuple[ToolListEntry, ...]


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
    # C6: KMS key id under which the access/refresh ciphertexts were encrypted.
    # NULL for legacy Fernet rows; populated for ``kms_v1:`` envelopes so the
    # rotation script can scan WHERE kms_key_id IS DISTINCT FROM $new_key.
    kms_key_id: str | None = None


class AuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    server_id: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Chain fields populated by the store on append; optional so existing
    # callers don't need to construct them.
    seq: int | None = None
    prev_hash: bytes | None = None
    signature: bytes | None = None
    key_version: int | None = None


class SkillAuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    skill_id: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    seq: int | None = None
    prev_hash: bytes | None = None
    signature: bytes | None = None
    key_version: int | None = None


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


class DeployImageDigest(BackendContract):
    component: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=1, max_length=256)

    @field_validator("digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
            raise ValueError(
                "digest must be a sha256 reference of the form sha256:<64 hex>"
            )
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError as exc:
            raise ValueError("digest hex portion must be valid hex") from exc
        return value


class DeployAuditRequest(BackendContract):
    tenant_id: str = Field(min_length=1, max_length=64)
    environment: str = Field(min_length=1, max_length=32)
    release_sha: str = Field(min_length=7, max_length=64)
    image_digests: list[DeployImageDigest] = Field(min_length=1, max_length=16)
    approver: str = Field(min_length=1, max_length=128)
    workflow_run_url: str = Field(min_length=1, max_length=512)
    started_at: datetime
    completed_at: datetime
    outcome: str = Field(min_length=1, max_length=32)
    force_deploy: bool = False

    @field_validator("environment")
    @classmethod
    def _env_allowed(cls, value: str) -> str:
        if value not in {"staging", "production"}:
            raise ValueError("environment must be one of: staging, production")
        return value

    @field_validator("outcome")
    @classmethod
    def _outcome_allowed(cls, value: str) -> str:
        if value not in {"success", "failed", "rolled_back", "offboarded"}:
            raise ValueError(
                "outcome must be one of: success, failed, rolled_back, offboarded"
            )
        return value

    @field_validator("workflow_run_url")
    @classmethod
    def _run_url_shape(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("workflow_run_url must be an absolute http(s) URL")
        return value


class DeployAuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    tenant_id: str
    environment: str
    release_sha: str
    image_digests: list[DeployImageDigest]
    approver: str
    workflow_run_url: str
    started_at: datetime
    completed_at: datetime
    outcome: str
    force_deploy: bool
    actor_kind: str = "ci"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    seq: int | None = None
    prev_hash: bytes | None = None
    signature: bytes | None = None
    key_version: int | None = None


class DeployAuditEventResponse(BackendContract):
    audit_id: str
    received_at: datetime


# -----------------------------------------------------------------------------
# Identity & Access (A1)
#
# Pydantic records for the user / org / role / auth-provider tables introduced
# by services/backend/migrations/0004_identity_foundation.sql. No HTTP routes
# yet — A2 onwards consume these records.
# -----------------------------------------------------------------------------


class _IdentityFields:
    """Constant pool for identity-record field names referenced in validators."""

    AUDIT_ID = "audit_id"
    AUTH_KIND = "auth_kind"
    DEPLOYMENT_KIND = "deployment_kind"
    DISPLAY_NAME = "display_name"
    EMAIL = "primary_email"
    EMAIL_ATTEMPTED = "email_attempted"
    KIND = "kind"
    MEMBER_ID = "member_id"
    NAME = "name"
    ORG_ID = "org_id"
    OUTCOME = "outcome"
    PROVIDER_ID = "provider_id"
    ROLE_ID = "role_id"
    SLUG = "slug"
    SOURCE = "source"
    STATUS = "status"
    USER_ID = "user_id"


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class OrganizationDeploymentKind(StrEnum):
    SAAS = "saas"
    SINGLE_TENANT = "single_tenant"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING_INVITE = "pending_invite"


class OrganizationMemberSource(StrEnum):
    LOCAL = "local"
    OIDC = "oidc"
    SAML = "saml"
    SCIM = "scim"
    BOOTSTRAP = "bootstrap"
    INVITE = "invite"
    SIWE = "siwe"


class AuthProviderKind(StrEnum):
    LOCAL = "local"
    OIDC = "oidc"
    SAML = "saml"
    SCIM = "scim"


class LoginAttemptKind(StrEnum):
    LOCAL = "local"
    OIDC = "oidc"
    SAML = "saml"
    MFA = "mfa"
    SCIM_TOKEN = "scim_token"
    API_KEY = "api_key"
    MAGIC_LINK = "magic_link"
    SIWE = "siwe"


class LoginAttemptOutcome(StrEnum):
    SUCCESS = "success"
    BAD_PASSWORD = "bad_password"
    UNKNOWN_USER = "unknown_user"
    LOCKED_OUT = "locked_out"
    MFA_FAILED = "mfa_failed"
    PROVIDER_REJECTED = "provider_rejected"
    # PR 5.1 — magic-link + workspace-pick outcomes.
    MAGIC_LINK_REQUESTED = "magic_link_requested"
    MAGIC_LINK_CONSUMED = "magic_link_consumed"
    INVALID_TOKEN = "invalid_token"
    EXPIRED_TOKEN = "expired_token"
    CONSUMED_TOKEN = "consumed_token"
    RATE_LIMITED = "rate_limited"
    WORKSPACE_PICKER_ISSUED = "workspace_picker_issued"
    WORKSPACE_SELECTED = "workspace_selected"


# Email is normalized to NFKC + lower for the unique-index lookup. CITEXT in
# Postgres handles case-insensitivity at the DB level; this validator gives
# the in-memory adapter and the request payloads the same semantics.
class _IdentityValidators:
    @staticmethod
    def normalize_email(value: object) -> str:
        text = Validators.normalize_text(value).lower()
        if "@" not in text or text.startswith("@") or text.endswith("@"):
            raise ValueError("invalid email address")
        return text

    @staticmethod
    def normalize_optional_email(value: object) -> str | None:
        if value is None:
            return None
        return _IdentityValidators.normalize_email(value)


class OrganizationRecord(BackendContract):
    org_id: str = Field(default_factory=lambda: f"org_{uuid4().hex}")
    display_name: str
    slug: str
    deployment_kind: OrganizationDeploymentKind = OrganizationDeploymentKind.SAAS
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None

    @field_validator(_IdentityFields.ORG_ID)
    @classmethod
    def _normalize_org_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_IdentityFields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return Validators.normalize_text(value)

    @field_validator(_IdentityFields.SLUG)
    @classmethod
    def _normalize_slug(cls, value: object) -> str:
        text = Validators.normalize_text(value).lower()
        if not _SLUG_PATTERN.fullmatch(text):
            raise ValueError("slug must be lowercase alphanumerics, '-' or '_'")
        return text


class UserRecord(BackendContract):
    user_id: str = Field(default_factory=lambda: f"usr_{uuid4().hex}")
    org_id: str
    primary_email: str
    email_verified_at: datetime | None = None
    display_name: str
    status: UserStatus = UserStatus.ACTIVE
    is_service_account: bool = False
    last_seen_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None

    @field_validator(_IdentityFields.USER_ID, _IdentityFields.ORG_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_IdentityFields.EMAIL)
    @classmethod
    def _normalize_email(cls, value: object) -> str:
        return _IdentityValidators.normalize_email(value)

    @field_validator(_IdentityFields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return Validators.normalize_text(value)


class OrganizationMemberRecord(BackendContract):
    member_id: str = Field(default_factory=lambda: f"mem_{uuid4().hex}")
    org_id: str
    user_id: str
    joined_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    invited_by_user_id: str | None = None
    removed_at: datetime | None = None
    source: OrganizationMemberSource = OrganizationMemberSource.LOCAL

    @field_validator(
        _IdentityFields.MEMBER_ID, _IdentityFields.ORG_ID, _IdentityFields.USER_ID
    )
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class RoleRecord(BackendContract):
    role_id: str = Field(default_factory=lambda: f"role_{uuid4().hex}")
    org_id: str | None = None  # NULL only when is_system=True
    name: str
    display_name: str
    description: str = ""
    is_system: bool = False
    permission_scopes: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None

    @field_validator(_IdentityFields.ROLE_ID)
    @classmethod
    def _normalize_role_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_IdentityFields.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        text = Validators.normalize_text(value).lower()
        if not _SLUG_PATTERN.fullmatch(text):
            raise ValueError("role name must be lowercase alphanumerics, '-' or '_'")
        return text

    @field_validator(_IdentityFields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return Validators.normalize_text(value)

    @model_validator(mode="after")
    def _system_role_invariants(self) -> "RoleRecord":
        # Mirrors the DB CHECK constraint so in-memory adapters reject the
        # same shapes Postgres would.
        if self.is_system and self.org_id is not None:
            raise ValueError("system roles must not carry an org_id")
        if not self.is_system and self.org_id is None:
            raise ValueError("non-system roles must carry an org_id")
        return self


class RoleAssignmentRecord(BackendContract):
    assignment_id: str = Field(default_factory=lambda: f"asn_{uuid4().hex}")
    org_id: str
    user_id: str
    role_id: str
    granted_by_user_id: str | None = None
    granted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None
    reason: str | None = None

    @field_validator(
        _IdentityFields.ORG_ID, _IdentityFields.USER_ID, _IdentityFields.ROLE_ID
    )
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class AuthProviderRecord(BackendContract):
    provider_id: str = Field(default_factory=lambda: f"prv_{uuid4().hex}")
    org_id: str
    kind: AuthProviderKind
    display_name: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    encrypted_client_secret: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None

    @field_validator(_IdentityFields.PROVIDER_ID, _IdentityFields.ORG_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator(_IdentityFields.DISPLAY_NAME)
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return Validators.normalize_text(value)


class IdentityAuditEventRecord(BackendContract):
    audit_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}")
    org_id: str
    actor_user_id: str | None = None
    subject_user_id: str | None = None
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_ip: str | None = None
    user_agent: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(_IdentityFields.ORG_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class LoginAttemptRecord(BackendContract):
    attempt_id: str = Field(default_factory=lambda: f"att_{uuid4().hex}")
    org_id: str | None = None
    email_attempted: str | None = None
    user_id: str | None = None
    auth_kind: LoginAttemptKind
    outcome: LoginAttemptOutcome
    ip: str | None = None
    user_agent: str | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(_IdentityFields.EMAIL_ATTEMPTED)
    @classmethod
    def _normalize_email(cls, value: object) -> str | None:
        return _IdentityValidators.normalize_optional_email(value)


# -----------------------------------------------------------------------------
# Invitations (PR 4.2)
# -----------------------------------------------------------------------------


class InvitationRecord(BackendContract):
    """Pending workspace invitation. Token mint mirrors the SCIM-token shape
    (``0015_scim.sql``): ``token_hash = sha256(plaintext)``; the plaintext is
    surfaced exactly once at create time and never persisted. The 8-char
    ``token_prefix`` lets the admin pending-list UI identify a row without
    re-revealing the secret.

    Soft revoke + soft accept via timestamps. Re-issue after revoke or accept
    is fine because the partial unique-active index in
    ``0019_invitations.sql`` is scoped to
    ``WHERE accepted_at IS NULL AND revoked_at IS NULL``.
    """

    invite_id: str = Field(default_factory=lambda: f"inv_{uuid4().hex}")
    org_id: str
    email: str
    role_id: str
    token_hash: str
    token_prefix: str
    created_by_user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_user_id: str | None = None
    revoked_at: datetime | None = None
    revoked_by_user_id: str | None = None

    @field_validator(_IdentityFields.ORG_ID, _IdentityFields.ROLE_ID)
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("email")
    @classmethod
    def _normalize_invite_email(cls, value: object) -> str:
        return _IdentityValidators.normalize_email(value)


@dataclass(frozen=True)
class InvitationMintResult:
    """Returned by ``InvitationsService.create``. ``token_plaintext`` MUST be
    surfaced to the caller exactly once; the row only persists ``token_hash``."""

    invite_id: str
    token_plaintext: str
    token_prefix: str
    expires_at: datetime
    created_at: datetime


# -----------------------------------------------------------------------------
# Sessions (A2)
# -----------------------------------------------------------------------------


class SessionRecord(BackendContract):
    """Server-issued session bound to an HMAC-signed bearer token.

    ``token_hash`` is sha256(token signature) — never the plaintext bearer.
    The same shape works for dev-mint (auth_provider_id IS NULL) and the
    real login flows landing in A3..A5.
    """

    session_id: str = Field(default_factory=lambda: f"sid_{uuid4().hex}")
    org_id: str
    user_id: str
    token_hash: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    auth_provider_id: str | None = None
    mfa_satisfied_at: datetime | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    device_label: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    @field_validator("session_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class SessionMintResult(BackendContract):
    """Returned by ``SessionService.create`` / ``dev_mint``.

    The plaintext bearer is in this object only — once returned it is never
    available again (we only store the hash).
    """

    session_id: str
    bearer_token: str
    expires_at: datetime


class SessionTouchResult(BackendContract):
    """Result of a successful ``SessionService.touch``."""

    session_id: str
    org_id: str
    user_id: str
    roles: tuple[str, ...]
    permission_scopes: tuple[str, ...]
    connector_scopes: dict[str, tuple[str, ...]]
    mfa_satisfied: bool
    # Raw timestamp of the most recent MFA verify on this session. Returned
    # so the facade's step-up gate can compare against per-route windows
    # (the org's default window is on ``identity_policies`` but routes can
    # demand a stricter one). ``None`` when the session has never satisfied
    # MFA — same condition that flips ``mfa_satisfied`` to false.
    mfa_satisfied_at: datetime | None = None
    expires_at: datetime


# Internal-API request / response shapes for the session endpoints. Kept in
# contracts.py so the service-layer Pydantic record + the route's request/
# response models share validators where helpful.


class CreateSessionRequest(BackendContract):
    org_id: str
    user_id: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    auth_provider_id: str | None = None
    ttl_seconds: int | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    device_label: str | None = None


class TouchSessionRequest(BackendContract):
    session_id: str
    token_hash: str


class RevokeSessionRequest(BackendContract):
    org_id: str
    reason: str | None = None


class DevMintRequest(BackendContract):
    org_id: str = "org_dev"
    user_id: str = "usr_dev"
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ("runtime:use",)
    connector_scopes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    ttl_seconds: int = 24 * 60 * 60


class SessionListItem(BackendContract):
    session_id: str
    org_id: str
    user_id: str
    auth_provider_id: str | None
    device_label: str | None
    client_ip: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    mfa_satisfied: bool


class SessionListResponse(BackendContract):
    sessions: tuple[SessionListItem, ...] = ()


# -----------------------------------------------------------------------------
# OIDC SSO (A3)
# -----------------------------------------------------------------------------


class OidcAuthenticationRecord(BackendContract):
    """One OIDC authorize-code request in flight.

    Created when the user clicks the "Sign in with X" button; consumed when
    the IdP redirects back to /v1/auth/oidc/callback. ``state`` is the unique
    CSRF token, ``nonce`` is the OIDC nonce claim, ``code_verifier`` powers
    PKCE.
    """

    auth_id: str = Field(default_factory=lambda: f"oac_{uuid4().hex}")
    org_id: str
    provider_id: str
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    return_to: str | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    consumed_at: datetime | None = None
    ip: str | None = None
    user_agent: str | None = None

    @field_validator("auth_id", "org_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class OidcIdentityRecord(BackendContract):
    """Mapping from IdP `sub` claim to a local user.

    A user can have several OIDC identities (one per provider). The
    ``(provider_id, subject)`` pair is unique among non-unlinked rows.
    """

    identity_id: str = Field(default_factory=lambda: f"oid_{uuid4().hex}")
    org_id: str
    user_id: str
    provider_id: str
    subject: str
    email_at_link: str | None = None
    linked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    unlinked_at: datetime | None = None
    claims_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("identity_id", "org_id", "user_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class OidcRefreshTokenRecord(BackendContract):
    """Encrypted refresh token from the OIDC provider."""

    token_id: str = Field(default_factory=lambda: f"ort_{uuid4().hex}")
    org_id: str
    user_id: str
    provider_id: str
    encrypted_refresh_token: str
    scope: tuple[str, ...] = ()
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None

    @field_validator("token_id", "org_id", "user_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class OidcJwksCacheRecord(BackendContract):
    """Cached JWKS document for an OIDC provider."""

    cache_id: str = Field(default_factory=lambda: f"jwk_{uuid4().hex}")
    provider_id: str
    jwks: dict[str, Any]
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime

    @field_validator("cache_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class OidcAuthorizeResult(BackendContract):
    """Returned by the backend authorize endpoint."""

    auth_url: str
    state: str
    expires_at: datetime


class OidcCallbackResult(BackendContract):
    """Returned by the backend callback endpoint after a successful exchange."""

    user_id: str
    session_id: str
    bearer_token: str
    expires_at: datetime
    return_to: str | None = None
    # Mirrors LocalLoginResult.requires_mfa — when True the session was
    # minted with the ``mfa:pending`` scope and the frontend must route
    # to the MFA prompt.
    requires_mfa: bool = False


class OidcProviderSummary(BackendContract):
    """Per-provider entry in the public providers list."""

    provider_id: str
    kind: AuthProviderKind
    display_name: str
    enabled: bool


class OidcProvidersResponse(BackendContract):
    providers: tuple[OidcProviderSummary, ...] = ()


class OidcAuthorizeRequest(BackendContract):
    org_id: str
    provider_id: str
    redirect_uri: str
    return_to: str | None = None
    ip: str | None = None
    user_agent: str | None = None


class OidcCallbackRequest(BackendContract):
    state: str
    code: str
    ip: str | None = None
    user_agent: str | None = None


# -----------------------------------------------------------------------------
# Local password authentication (A4)
# -----------------------------------------------------------------------------


class LocalCredentialRecord(BackendContract):
    """A user's argon2id-hashed password.

    ``password_hash`` is the full argon2id encoded string (algorithm,
    parameters, salt, hash). ``previous_hashes`` keeps the last N hashes for
    the reuse-window policy check.
    """

    credential_id: str = Field(default_factory=lambda: f"crd_{uuid4().hex}")
    org_id: str
    user_id: str
    password_hash: str
    password_set_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    must_rotate_at: datetime | None = None
    last_used_at: datetime | None = None
    previous_hashes: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None

    @field_validator("credential_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class IdentityPolicyRecord(BackendContract):
    """Per-org auth-method toggle row.

    Holds the on/off flags that gate which IdPs the org accepts at all.
    Distinct from ``PasswordPolicyRecord`` (rotation, complexity) and
    ``LockoutPolicyRecord`` (failure-count thresholds): when this row says
    ``local_password_enabled=False`` the local-password route returns 404
    regardless of how strong the candidate password is.

    A6 added ``mfa_required`` + ``step_up_window_seconds``; A7 will add
    ``scim_required`` so a bank/gov deployment can lock down to SAML+SCIM
    only via a single UPDATE.
    """

    org_id: str
    local_password_enabled: bool = True
    mfa_required: bool = False
    step_up_window_seconds: int = 300
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("org_id")
    @classmethod
    def _normalize_org(cls, value: object) -> str:
        return Validators.normalize_id(value)


class PasswordPolicyRecord(BackendContract):
    """Per-org password policy. One row per org; defaults match OWASP."""

    policy_id: str = Field(default_factory=lambda: f"pwp_{uuid4().hex}")
    org_id: str
    min_length: int = 12
    require_upper: bool = True
    require_lower: bool = True
    require_digit: bool = True
    require_symbol: bool = False
    rotation_days: int | None = None
    reuse_window: int = 5
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("policy_id", "org_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class PasswordResetTokenRecord(BackendContract):
    """Single-use reset token. Only the sha256 hash is stored at rest."""

    token_id: str = Field(default_factory=lambda: f"prt_{uuid4().hex}")
    org_id: str
    user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    consumed_at: datetime | None = None
    request_ip: str | None = None

    @field_validator("token_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class LocalLoginRequest(BackendContract):
    org_id: str
    email: str
    password: str
    ip: str | None = None
    user_agent: str | None = None


class LocalLoginResult(BackendContract):
    user_id: str
    session_id: str
    bearer_token: str
    expires_at: datetime
    requires_password_change: bool = False
    # When True the session was minted with ``permission_scopes=("mfa:pending",)``
    # and protected routes will 401 until ``MfaService.verify*`` succeeds.
    # The frontend uses this to route to the MFA prompt instead of the
    # post-login destination.
    requires_mfa: bool = False


class PasswordChangeRequest(BackendContract):
    org_id: str
    user_id: str
    current_password: str
    new_password: str


class PasswordResetRequestRequest(BackendContract):
    """Anti-enumeration: always returns 200 even when email is unknown."""

    org_id: str
    email: str
    ip: str | None = None


class PasswordResetRequestResult(BackendContract):
    """Surface to the caller. ``token`` is non-None ONLY in dev / tests so
    the fixture can pick up the token without hitting an email worker.
    Production builds set ``token=None`` and rely on the notify event.
    """

    accepted: bool = True
    token: str | None = None


class PasswordResetConfirmRequest(BackendContract):
    token: str
    new_password: str


class BootstrapAdminRequest(BackendContract):
    """One-time first-run admin creation.

    The setup token comes from the operator's env (``BOOTSTRAP_ADMIN_TOKEN``)
    and is matched in the service. Refused if any admin user already exists.
    """

    org_id: str
    email: str
    display_name: str
    setup_token: str


# -----------------------------------------------------------------------------
# Account lockouts (A8)
# -----------------------------------------------------------------------------


class LockoutPolicyRecord(BackendContract):
    """Per-org sliding-window lockout policy.

    ``enforce_lockout`` defaults to ``False`` so the migration can land
    without immediately gating any user — operators see the failure curve
    in ``login_attempts`` first, then flip the toggle per-org.
    """

    policy_id: str = Field(default_factory=lambda: f"lkp_{uuid4().hex}")
    org_id: str
    enforce_lockout: bool = False
    max_failures: int = 5
    failure_window_seconds: int = 300
    lockout_duration_seconds: int = 900
    permanent_after_n_lockouts: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("policy_id", "org_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class AccountLockoutRecord(BackendContract):
    """One row per lockout window.

    ``unlocked_at`` distinguishes ended lockouts from active ones; the
    partial unique index ``WHERE unlocked_at IS NULL`` enforces "at most
    one active lockout per (org, user)".
    """

    lockout_id: str = Field(default_factory=lambda: f"lko_{uuid4().hex}")
    org_id: str
    user_id: str
    locked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lock_reason: str
    auto_unlock_at: datetime | None = None
    unlocked_at: datetime | None = None
    unlocked_by_user_id: str | None = None
    unlock_reason: str | None = None

    @field_validator("lockout_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class AccountLockoutListResponse(BackendContract):
    lockouts: tuple[AccountLockoutRecord, ...] = ()


class LoginAttemptListResponse(BackendContract):
    attempts: tuple[LoginAttemptRecord, ...] = ()


class AccountUnlockRequest(BackendContract):
    org_id: str
    reason: str | None = None


# -----------------------------------------------------------------------------
# MFA (A6) — TOTP + WebAuthn + recovery codes
# -----------------------------------------------------------------------------


class MfaFactorKind(StrEnum):
    TOTP = "totp"
    WEBAUTHN = "webauthn"


class MfaChallengeKind(StrEnum):
    TOTP = "totp"
    WEBAUTHN = "webauthn"
    RECOVERY = "recovery"


class MfaFactorRecord(BackendContract):
    """Generic per-user factor row. The ``kind`` column says which detail
    table (``totp_secrets`` / ``webauthn_credentials``) carries the
    cryptographic material."""

    factor_id: str = Field(default_factory=lambda: f"mff_{uuid4().hex}")
    org_id: str
    user_id: str
    kind: MfaFactorKind
    display_name: str
    enabled: bool = False
    enrolled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    disabled_at: datetime | None = None

    @field_validator("factor_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class TotpSecretRecord(BackendContract):
    """Encrypted TOTP seed + replay guard. ``encrypted_secret`` is the
    TokenVault wrapping of the raw base32 seed; the plaintext only exists
    in memory during enroll/verify."""

    secret_id: str = Field(default_factory=lambda: f"tot_{uuid4().hex}")
    factor_id: str
    encrypted_secret: str
    last_step: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("secret_id", "factor_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class WebAuthnCredentialRecord(BackendContract):
    """COSE public key + sign_count for one FIDO2 authenticator.

    ``credential_id_b64`` is the IdP-assigned credential id (urlsafe-b64,
    no padding); we keep it as the unique business key so the WebAuthn
    library's lookups land on the right row.
    """

    credential_id: str = Field(default_factory=lambda: f"wac_{uuid4().hex}")
    factor_id: str
    credential_id_b64: str
    public_key_cose: bytes
    sign_count: int = 0
    transports: tuple[str, ...] = ()
    aaguid: str | None = None
    attestation_format: str
    rp_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None

    @field_validator("credential_id", "factor_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class MfaChallengeRecord(BackendContract):
    """Single-use nonce binding a verify request to a (user, kind, factor)."""

    challenge_id: str = Field(default_factory=lambda: f"mfc_{uuid4().hex}")
    org_id: str
    user_id: str
    kind: MfaChallengeKind
    nonce: str
    expected_factor_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    expires_at: datetime
    consumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("challenge_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class MfaRecoveryCodeRecord(BackendContract):
    """One-shot recovery code; only the sha256 hash is stored."""

    code_id: str = Field(default_factory=lambda: f"mfr_{uuid4().hex}")
    org_id: str
    user_id: str
    code_hash: str
    consumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("code_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


# Wire shapes for the MFA facade routes -----------------------------------


class MfaFactorSummary(BackendContract):
    """Public-safe view of a factor (no secrets, no public-key bytes)."""

    factor_id: str
    kind: MfaFactorKind
    display_name: str
    enabled: bool
    enrolled_at: datetime
    last_used_at: datetime | None = None


class MfaFactorListResponse(BackendContract):
    factors: tuple[MfaFactorSummary, ...] = ()


class TotpEnrollResult(BackendContract):
    """Returned ONCE at enrollment. Recovery codes never re-surface."""

    factor_id: str
    otpauth_url: str
    secret_b32: str
    recovery_codes: tuple[str, ...] = ()


class TotpEnrollRequest(BackendContract):
    org_id: str
    user_id: str
    display_name: str = "Authenticator app"


class TotpConfirmRequest(BackendContract):
    org_id: str
    user_id: str
    factor_id: str
    code: str


class MfaChallengeRequest(BackendContract):
    org_id: str
    user_id: str
    kind: MfaChallengeKind = MfaChallengeKind.TOTP
    factor_id: str | None = None


class MfaChallengeResult(BackendContract):
    challenge_id: str
    nonce: str
    kind: MfaChallengeKind
    expected_factor_id: str | None = None
    expires_at: datetime
    # WebAuthn ``PublicKeyCredentialRequestOptions`` (already JSON-safe).
    webauthn_options: dict[str, object] | None = None


class MfaVerifyRequest(BackendContract):
    org_id: str
    user_id: str
    challenge_id: str
    code: str | None = None
    assertion: dict[str, object] | None = None


class MfaVerifyResult(BackendContract):
    factor_id: str
    kind: MfaChallengeKind
    mfa_satisfied_at: datetime


class MfaRecoveryConsumeRequest(BackendContract):
    org_id: str
    user_id: str
    code: str


class WebAuthnRegisterStartRequest(BackendContract):
    org_id: str
    user_id: str
    display_name: str = "Security key"
    rp_id: str
    rp_name: str
    user_name: str
    user_display_name: str | None = None


class WebAuthnRegisterStartResult(BackendContract):
    factor_id: str
    challenge_id: str
    options: dict[str, object]


class WebAuthnRegisterFinishRequest(BackendContract):
    org_id: str
    user_id: str
    factor_id: str
    challenge_id: str
    rp_id: str
    expected_origin: str
    attestation: dict[str, object]


# -----------------------------------------------------------------------------
# SAML 2.0 SSO (A5)
# -----------------------------------------------------------------------------


class SamlAuthenticationStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    REJECTED = "rejected"


class SamlAuthenticationRecord(BackendContract):
    """One in-flight SAML authn request (SP-initiated) or accepted assertion.

    The lifecycle is:

      1. SP-initiated start  → row created with ``status=pending``,
         ``request_id`` populated, ``assertion_id`` provisional.
      2. ACS POST            → row updated to ``status=consumed`` with the
         real ``assertion_id`` and ``consumed_at``.
      3. ACS validation fail → ``status=rejected`` (the row stays for audit).

    For IdP-initiated flows ``request_id`` is NULL — we still write a
    pending row with a synthetic id so the consume step has a single
    insertion point.
    """

    auth_id: str = Field(default_factory=lambda: f"sac_{uuid4().hex}")
    org_id: str
    provider_id: str
    request_id: str | None = None
    assertion_id: str
    relay_state: str | None = None
    status: SamlAuthenticationStatus = SamlAuthenticationStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    consumed_at: datetime | None = None
    ip: str | None = None
    user_agent: str | None = None

    @field_validator("auth_id", "org_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class SamlIdentityRecord(BackendContract):
    """Mapping from IdP ``NameID`` to a local user.

    ``(provider_id, name_id)`` is unique among non-unlinked rows — a re-link
    after explicit unlink is allowed.
    """

    identity_id: str = Field(default_factory=lambda: f"sid_{uuid4().hex}")
    org_id: str
    user_id: str
    provider_id: str
    name_id: str
    name_id_format: str
    linked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    unlinked_at: datetime | None = None
    attributes_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("identity_id", "org_id", "user_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class SamlAuthorizeResult(BackendContract):
    """Returned by the backend authorize endpoint.

    ``sso_url`` is where the browser should be sent. For HTTP-Redirect
    binding it includes the SAMLRequest in the query string; for HTTP-POST
    binding the caller should auto-submit ``request_xml`` to the IdP via
    a self-posting form. ``binding`` says which one.
    """

    auth_id: str
    request_id: str
    sso_url: str
    request_xml: str
    binding: str = "HTTP-Redirect"
    expires_at: datetime


class SamlConsumeResult(BackendContract):
    """Returned after the ACS endpoint validates an assertion.

    Mirrors :class:`OidcCallbackResult` so the facade and frontend
    deal with the two SSO paths uniformly.
    """

    user_id: str
    session_id: str
    bearer_token: str
    expires_at: datetime
    relay_state: str | None = None
    requires_mfa: bool = False


class SamlAuthorizeRequest(BackendContract):
    org_id: str
    provider_id: str
    relay_state: str | None = None
    ip: str | None = None
    user_agent: str | None = None


class SamlConsumeRequest(BackendContract):
    provider_id: str
    saml_response: str
    relay_state: str | None = None
    ip: str | None = None
    user_agent: str | None = None


# -----------------------------------------------------------------------------
# SCIM 2.0 (A7)
# -----------------------------------------------------------------------------


class ScimTokenRecord(BackendContract):
    """A per-org SCIM bearer token.

    ``token_hash`` is sha256 of the plaintext returned at mint;
    ``token_prefix`` is the first 8 chars of that plaintext so an admin
    listing can identify a token without re-revealing it (mirrors GitHub
    PAT handling).

    Mint never auto-revokes prior tokens — admins handle rotation
    explicitly so the IdP can swap credentials with both old and new
    accepted simultaneously.
    """

    token_id: str = Field(default_factory=lambda: f"sct_{uuid4().hex}")
    org_id: str
    provider_id: str
    token_hash: str
    token_prefix: str
    created_by_user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @field_validator("token_id", "org_id", "provider_id", "created_by_user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class ScimExternalIdRecord(BackendContract):
    """Maps an IdP-supplied ``externalId`` to a local user_id or group_id.

    Exactly one of ``user_id`` / ``group_id`` is populated — the DB CHECK
    enforces this; the model_validator below mirrors that for in-memory
    adapters.
    """

    mapping_id: str = Field(default_factory=lambda: f"sxi_{uuid4().hex}")
    org_id: str
    user_id: str | None = None
    group_id: str | None = None
    provider_id: str
    external_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("mapping_id", "org_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "ScimExternalIdRecord":
        if (self.user_id is None) == (self.group_id is None):
            raise ValueError(
                "scim_external_ids row must reference exactly one of user_id, group_id"
            )
        return self


class ScimGroupRecord(BackendContract):
    """A SCIM-managed group within one org."""

    group_id: str = Field(default_factory=lambda: f"scg_{uuid4().hex}")
    org_id: str
    display_name: str
    external_id: str | None = None
    mapped_role_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None

    @field_validator("group_id", "org_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: object) -> str:
        return Validators.normalize_text(value)


class ScimGroupMemberRecord(BackendContract):
    """One ``(group, user)`` membership. ``removed_at`` flips on remove."""

    membership_id: str = Field(default_factory=lambda: f"sgm_{uuid4().hex}")
    org_id: str
    group_id: str
    user_id: str
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    removed_at: datetime | None = None

    @field_validator("membership_id", "org_id", "group_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class ScimTokenMintResult(BackendContract):
    """Returned ONCE on mint. Plaintext is never available again."""

    token_id: str
    plaintext: str
    token_prefix: str
    created_at: datetime
    expires_at: datetime | None = None


class ScimTokenSummary(BackendContract):
    """Public-safe view of a SCIM token (no plaintext, no full hash)."""

    token_id: str
    token_prefix: str
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class ScimTokenListResponse(BackendContract):
    tokens: tuple[ScimTokenSummary, ...] = ()


class ScimTokenMintRequest(BackendContract):
    org_id: str
    created_by_user_id: str
    expires_at: datetime | None = None


# -----------------------------------------------------------------------------
# Login email-first / magic-link / workspace picker (PR 5.1)
# -----------------------------------------------------------------------------


class AuthDiscoverKind(StrEnum):
    """UI branch the discovery response tells the frontend to render.

    The shape is intentionally distinct from ``AuthProviderKind``: discovery
    speaks in *experiences* (SSO redirect / personal magic-link / unknown
    fallback / disabled), provider kinds speak in *protocols* (OIDC / SAML).
    """

    SSO = "sso"
    PERSONAL = "personal"
    MAGIC_LINK = "magic_link"
    UNKNOWN = "unknown"


class AuthProviderDomainRecord(BackendContract):
    """A domain → (org, provider) claim. One row per claim.

    Lookup is keyed by the partial unique index on ``(domain) WHERE deleted_at
    IS NULL``; case-insensitivity comes from CITEXT in Postgres and from the
    ``normalize_email`` validator on writes.
    """

    domain: str
    org_id: str
    provider_id: str
    sso_enforced: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_user_id: str | None = None
    deleted_at: datetime | None = None

    @field_validator("domain")
    @classmethod
    def _normalize_domain(cls, value: object) -> str:
        text = Validators.normalize_text(value).lower()
        if "." not in text or text.startswith(".") or text.endswith(".") or "@" in text:
            raise ValueError("invalid domain")
        return text

    @field_validator("org_id", "provider_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class AuthDiscoverRequest(BackendContract):
    email: str
    ip: str | None = None
    user_agent: str | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: object) -> str:
        return _IdentityValidators.normalize_email(value)


class AuthDiscoverResponse(BackendContract):
    kind: AuthDiscoverKind
    domain: str | None = None
    org_id: str | None = None
    org_display_name: str | None = None
    org_logo_url: str | None = None
    member_count: int | None = None
    provider_id: str | None = None
    provider_kind: AuthProviderKind | None = None
    provider_display_name: str | None = None
    sso_enforced: bool = False
    magic_link_supported: bool = True
    message: str | None = None


class MagicLinkTokenRecord(BackendContract):
    """Server-side row backing a magic-link plaintext token.

    Plaintext is sha256-hashed at write; the row's ``token_hash`` is the
    UNIQUE lookup key. ``user_id`` is always known when the row is written —
    if the email did not resolve to an existing active user we never insert
    a row (anti-enumeration). ``candidate_orgs`` is materialized once at
    request time so the ``consume`` path doesn't re-query membership.
    """

    token_id: str = Field(default_factory=lambda: f"mlt_{uuid4().hex}")
    org_id: str | None = None
    user_id: str
    email_lower: str
    token_hash: str
    candidate_orgs: list[dict[str, Any]] = Field(default_factory=list)
    return_to: str | None = None
    requested_ip: str | None = None
    requested_ua: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    consumed_at: datetime | None = None
    consumed_session_id: str | None = None

    @field_validator("user_id")
    @classmethod
    def _normalize_user_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("org_id")
    @classmethod
    def _normalize_optional_org_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return Validators.normalize_id(value)


class MagicLinkStartRequest(BackendContract):
    email: str
    return_to: str | None = None
    ip: str | None = None
    user_agent: str | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: object) -> str:
        return _IdentityValidators.normalize_email(value)


class MagicLinkStartResponse(BackendContract):
    """Anti-enumeration: always 202; ``expires_in_seconds`` is informational."""

    status: str = "queued"
    expires_in_seconds: int = 900


class MagicLinkCallbackRequest(BackendContract):
    token: str
    ip: str | None = None
    user_agent: str | None = None


class WorkspaceCandidate(BackendContract):
    org_id: str
    display_name: str
    logo_url: str | None = None
    role: str
    member_count: int
    last_active_at: datetime | None = None


class MagicLinkCallbackOutcome(StrEnum):
    SESSION_MINTED = "session_minted"
    WORKSPACE_PICK_REQUIRED = "workspace_pick_required"


class MagicLinkCallbackResult(BackendContract):
    outcome: MagicLinkCallbackOutcome
    user_id: str
    # session_minted branch:
    bearer_token: str | None = None
    session_id: str | None = None
    org_id: str | None = None
    requires_mfa: bool | None = None
    return_to: str | None = None
    expires_at: datetime | None = None
    # workspace_pick_required branch:
    pick_token: str | None = None
    expires_in_seconds: int | None = None
    workspaces: tuple[WorkspaceCandidate, ...] = ()


class SessionSelectRequest(BackendContract):
    pick_token: str
    org_id: str
    ip: str | None = None
    user_agent: str | None = None

    @field_validator("org_id")
    @classmethod
    def _normalize_org_id(cls, value: object) -> str:
        return Validators.normalize_id(value)


class SessionSelectResult(BackendContract):
    bearer_token: str
    session_id: str
    user_id: str
    org_id: str
    requires_mfa: bool = False
    expires_at: datetime


# -----------------------------------------------------------------------------
# Sign-In-With-Ethereum (SIWE, EIP-4361)
# -----------------------------------------------------------------------------

# Wire format only — EIP-55 checksum verification lives in
# backend_app.identity.siwe (it needs eth-account; contracts stays light).
_WALLET_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")


def _normalize_wallet_address(value: object) -> str:
    """Validate ``0x`` + 40 hex chars and normalize to lowercase.

    Storage and comparisons are always lowercase; EIP-55 checksumming is a
    display concern (and a parse-time strictness concern in siwe.py).
    """

    text = Validators.normalize_text(value)
    if not _WALLET_ADDRESS_PATTERN.fullmatch(text):
        raise ValueError("invalid ethereum address format")
    return text.lower()


class SiweNonceRecord(BackendContract):
    """One issued SIWE nonce, single-use.

    Mirrors ``OidcAuthenticationRecord``: created when the client asks to
    sign in, consumed exactly once by the verify endpoint (atomic CAS on
    ``consumed_at``). Bound to the requesting address + chain so a nonce
    minted for one wallet cannot authenticate another.
    """

    nonce_id: str = Field(default_factory=lambda: f"swn_{uuid4().hex}")
    nonce: str
    address: str
    chain_id: int
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    consumed_at: datetime | None = None
    ip: str | None = None
    user_agent: str | None = None

    @field_validator("nonce_id")
    @classmethod
    def _normalize_nonce_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("address")
    @classmethod
    def _normalize_address(cls, value: object) -> str:
        return _normalize_wallet_address(value)


class WalletIdentityRecord(BackendContract):
    """Mapping from a wallet address to a local user.

    The SIWE analogue of ``OidcIdentityRecord``. ``address`` is unique
    across the deployment (lowercase; the DB column is CITEXT + UNIQUE).
    ``chain_id`` records the chain used at first link — later logins may
    arrive from any allowlisted chain since the address is chain-agnostic.
    """

    wallet_id: str = Field(default_factory=lambda: f"wid_{uuid4().hex}")
    address: str
    org_id: str
    user_id: str
    chain_id: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("wallet_id", "org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("address")
    @classmethod
    def _normalize_address(cls, value: object) -> str:
        return _normalize_wallet_address(value)


class SiweNonceRequest(BackendContract):
    # ``address`` intentionally has NO format validator here: the service
    # validates and maps bad formats to 422 {"detail": "invalid_address"}
    # so the public wire carries a stable detail code instead of a pydantic
    # error list.
    address: str
    chain_id: int
    ip: str | None = None
    user_agent: str | None = None


class SiweNonceResult(BackendContract):
    nonce: str
    expires_at: datetime


class SiweVerifyRequest(BackendContract):
    message: str
    signature: str
    ip: str | None = None
    user_agent: str | None = None


class SiweVerifyResult(BackendContract):
    """Returned by the backend verify endpoint after a valid signature.

    Mirrors :class:`OidcCallbackResult` so the facade and frontend deal
    with every session-establishing login path uniformly.
    """

    user_id: str
    session_id: str
    bearer_token: str
    expires_at: datetime
    return_to: str | None = None
    requires_mfa: bool = False
