"""Pydantic contracts for dynamic MCP server loading."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
import json
from typing import Any, TypeAlias
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import (
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, RuntimeContract
from enterprise_search_ai.mcp.constants import Keys, Limits, Messages, Patterns, Values

JsonSchema: TypeAlias = Mapping[str, Any]
SUPPORTED_RESOURCE_URI_SCHEMES = frozenset(
    {Values.UriScheme.HTTPS, Values.UriScheme.MCP, Values.UriScheme.URN}
)


class McpTransport(StrEnum):
    """MCP transports supported by the AI backend boundary."""

    STDIO = Values.Transport.STDIO
    SSE = Values.Transport.SSE
    HTTP = Values.Transport.HTTP


class McpAuthMode(StrEnum):
    """Authentication modes disclosed without storing any secrets."""

    NONE = Values.AuthMode.NONE
    API_KEY = Values.AuthMode.API_KEY
    OAUTH2 = Values.AuthMode.OAUTH2
    SERVICE_ACCOUNT = Values.AuthMode.SERVICE_ACCOUNT


class McpServerHealth(StrEnum):
    """Health states used before a server card is visible or loadable."""

    HEALTHY = Values.Health.HEALTHY
    DEGRADED = Values.Health.DEGRADED
    UNAVAILABLE = Values.Health.UNAVAILABLE
    DISABLED = Values.Health.DISABLED


class McpRiskLevel(StrEnum):
    """Risk bands for MCP tools before exposing them to the model."""

    LOW = Values.Risk.LOW
    MEDIUM = Values.Risk.MEDIUM
    HIGH = Values.Risk.HIGH
    CRITICAL = Values.Risk.CRITICAL


class McpLoadErrorCode(StrEnum):
    """Typed dynamic MCP loading failures safe for public surfaces."""

    INVALID_SERVER_NAME = Values.ErrorCode.INVALID_SERVER_NAME
    UNKNOWN_SERVER = Values.ErrorCode.UNKNOWN_SERVER
    DUPLICATE_SERVER_NAME = Values.ErrorCode.DUPLICATE_SERVER_NAME
    SERVER_DISABLED = Values.ErrorCode.SERVER_DISABLED
    SERVER_UNHEALTHY = Values.ErrorCode.SERVER_UNHEALTHY
    PERMISSION_DENIED = Values.ErrorCode.PERMISSION_DENIED
    UNSUPPORTED_TRANSPORT = Values.ErrorCode.UNSUPPORTED_TRANSPORT
    AUTH_FAILURE = Values.ErrorCode.AUTH_FAILURE
    CONNECTION_FAILED = Values.ErrorCode.CONNECTION_FAILED
    TIMEOUT = Values.ErrorCode.TIMEOUT
    MALFORMED_DESCRIPTOR = Values.ErrorCode.MALFORMED_DESCRIPTOR
    DUPLICATE_DESCRIPTOR_NAME = Values.ErrorCode.DUPLICATE_DESCRIPTOR_NAME
    LOCAL_TOOL_COLLISION = Values.ErrorCode.LOCAL_TOOL_COLLISION
    LOAD_BUDGET_EXCEEDED = Values.ErrorCode.LOAD_BUDGET_EXCEEDED


class McpWarningCode(StrEnum):
    """Non-fatal warnings returned after a successful load."""

    SERVER_DEGRADED = Values.WarningCode.SERVER_DEGRADED


class McpServerCard(RuntimeContract):
    """Compact MCP server summary visible before explicit loading."""

    name: str
    short_description: str = Field(
        min_length=1,
        max_length=Limits.CARD_DESCRIPTION_MAX_LENGTH,
    )
    transport: McpTransport
    auth_mode: McpAuthMode
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    health: McpServerHealth
    load_cost: PositiveInt = Field(le=Limits.LOAD_COST_MAX)
    enabled: bool = True
    allowed_org_ids: frozenset[str] = Field(default_factory=frozenset)
    allowed_user_ids: frozenset[str] = Field(default_factory=frozenset)

    @field_validator(Keys.Field.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return McpValueNormalizer.normalize_slug(value, Keys.Field.NAME)

    @field_validator(Keys.Field.SHORT_DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        return McpValueNormalizer.normalize_nonempty_string(
            value,
            Keys.Field.SHORT_DESCRIPTION,
        )

    @field_validator(
        Keys.Field.TRANSPORT,
        Keys.Field.AUTH_MODE,
        Keys.Field.HEALTH,
        mode="before",
    )
    @classmethod
    def _normalize_enum_value(cls, value: object) -> str:
        if isinstance(value, StrEnum):
            return value.value
        return McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.CODE).lower()

    @field_validator(Keys.Field.REQUIRED_SCOPES, mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        return McpValueNormalizer.normalize_scope_set(value, Keys.Field.REQUIRED_SCOPES)

    @field_validator(Keys.Field.ALLOWED_ORG_IDS, Keys.Field.ALLOWED_USER_IDS, mode="before")
    @classmethod
    def _normalize_allowed_ids(cls, value: object, info: ValidationInfo) -> frozenset[str]:
        return McpValueNormalizer.normalize_id_set(value, info.field_name)


class McpLoadRequest(RuntimeContract):
    """Request to connect to and discover a selected MCP server."""

    server_name: str
    runtime_context: AgentRuntimeContext
    local_tool_names: frozenset[str] = Field(default_factory=frozenset)

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_server_name(cls, value: object) -> str:
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.LOCAL_TOOL_NAMES, mode="before")
    @classmethod
    def _normalize_local_tool_names(cls, value: object) -> frozenset[str]:
        return McpValueNormalizer.normalize_slug_set(value, Keys.Field.LOCAL_TOOL_NAMES)


class McpToolDescriptor(RuntimeContract):
    """Validated tool descriptor returned by a loaded MCP server."""

    name: str
    description: str = Field(
        min_length=1,
        max_length=Limits.DESCRIPTOR_DESCRIPTION_MAX_LENGTH,
    )
    input_schema: JsonSchema
    output_shape: JsonSchema
    risk_level: McpRiskLevel = McpRiskLevel.LOW

    @field_validator(Keys.Field.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return McpValueNormalizer.normalize_slug(value, Keys.Field.NAME)

    @field_validator(Keys.Field.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        return McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.DESCRIPTION)

    @field_validator(Keys.Field.INPUT_SCHEMA, Keys.Field.OUTPUT_SHAPE)
    @classmethod
    def _validate_schema(cls, value: JsonSchema, info: ValidationInfo) -> JsonSchema:
        return McpSchemaValidator.validate_json_schema(value, info.field_name)

    @field_validator(Keys.Field.RISK_LEVEL, mode="before")
    @classmethod
    def _normalize_risk_level(cls, value: object) -> str:
        if isinstance(value, StrEnum):
            return value.value
        return McpValueNormalizer.normalize_nonempty_string(
            value,
            Keys.Field.RISK_LEVEL,
        ).lower()


class McpResourceAccessPolicy(RuntimeContract):
    """Resource access policy after MCP discovery validation."""

    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    read_only: bool = True

    @field_validator(Keys.Field.REQUIRED_SCOPES, mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        return McpValueNormalizer.normalize_scope_set(value, Keys.Field.REQUIRED_SCOPES)


class McpResourceDescriptor(RuntimeContract):
    """Validated resource descriptor returned by a loaded MCP server."""

    uri: str
    name: str = Field(min_length=1, max_length=Limits.RESOURCE_NAME_MAX_LENGTH)
    mime_type: str = Field(min_length=1, max_length=Limits.MIME_TYPE_MAX_LENGTH)
    description: str = Field(
        min_length=1,
        max_length=Limits.DESCRIPTOR_DESCRIPTION_MAX_LENGTH,
    )
    access_policy: McpResourceAccessPolicy

    @field_validator(Keys.Field.URI)
    @classmethod
    def _normalize_uri(cls, value: object) -> str:
        normalized = McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.URI)
        parsed = urlsplit(normalized)
        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_RESOURCE_URI_SCHEMES:
            raise ValueError(Messages.Validation.UNSUPPORTED_RESOURCE_SCHEME)
        return normalized

    @field_validator(Keys.Field.NAME, Keys.Field.MIME_TYPE, Keys.Field.DESCRIPTION)
    @classmethod
    def _normalize_label(cls, value: object, info: ValidationInfo) -> str:
        return McpValueNormalizer.normalize_nonempty_string(value, info.field_name)


class McpConnectionMetadata(RuntimeContract):
    """Safe connection metadata for a loaded MCP server."""

    server_name: str
    transport: McpTransport
    auth_mode: McpAuthMode
    connection_id: str = Field(default_factory=lambda: uuid4().hex)
    connected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: int = Field(default=0, ge=0, le=Limits.METADATA_LATENCY_MAX_MS)

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_server_name(cls, value: object) -> str:
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.TRANSPORT, Keys.Field.AUTH_MODE, mode="before")
    @classmethod
    def _normalize_enum_value(cls, value: object) -> str:
        if isinstance(value, StrEnum):
            return value.value
        return McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.CODE).lower()

    @field_validator(Keys.Field.CONNECTION_ID)
    @classmethod
    def _normalize_connection_id(cls, value: object) -> str:
        return McpValueNormalizer.normalize_id(value, Keys.Field.CONNECTION_ID)


class McpLoadWarning(RuntimeContract):
    """Non-fatal loader warning safe for model and API surfaces."""

    code: McpWarningCode
    safe_message: str = Field(min_length=1, max_length=Limits.SAFE_MESSAGE_MAX_LENGTH)

    @field_validator(Keys.Field.SAFE_MESSAGE)
    @classmethod
    def _normalize_safe_message(cls, value: object) -> str:
        return McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.SAFE_MESSAGE)


class LoadedMcpServer(RuntimeContract):
    """Fully loaded MCP descriptors exposed after validation."""

    server_card: McpServerCard
    tools: tuple[McpToolDescriptor, ...] = Field(default_factory=tuple)
    resources: tuple[McpResourceDescriptor, ...] = Field(default_factory=tuple)
    connection_metadata: McpConnectionMetadata
    warnings: tuple[McpLoadWarning, ...] = Field(default_factory=tuple)


class McpLoadError(RuntimeContract):
    """Safe, typed error returned by dynamic MCP loading."""

    code: McpLoadErrorCode
    safe_message: str = Field(min_length=1, max_length=Limits.SAFE_MESSAGE_MAX_LENGTH)
    retryable: bool = False
    server_name: str | None = None
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator(Keys.Field.SAFE_MESSAGE)
    @classmethod
    def _normalize_safe_message(cls, value: object) -> str:
        return McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.SAFE_MESSAGE)

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_optional_server_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.CORRELATION_ID)
    @classmethod
    def _normalize_correlation_id(cls, value: object) -> str:
        return McpValueNormalizer.normalize_id(value, Keys.Field.CORRELATION_ID)


class McpLoadResult(RuntimeContract):
    """Result envelope containing either loaded descriptors or a typed error."""

    loaded_server: LoadedMcpServer | None = None
    error: McpLoadError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> "McpLoadResult":
        if (self.loaded_server is None) == (self.error is None):
            raise ValueError(Messages.Validation.EXACTLY_ONE_LOAD_OUTCOME)
        return self

    @classmethod
    def ok(cls, loaded_server: LoadedMcpServer) -> "McpLoadResult":
        return cls(loaded_server=loaded_server)

    @classmethod
    def fail(
        cls,
        code: McpLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        server_name: str | None = None,
        correlation_id: str | None = None,
    ) -> "McpLoadResult":
        return cls(
            error=McpLoadError(
                code=code,
                safe_message=safe_message,
                retryable=retryable,
                server_name=server_name,
                correlation_id=correlation_id or uuid4().hex,
            )
        )

    @property
    def succeeded(self) -> bool:
        return self.loaded_server is not None


class McpValueNormalizer:
    """Normalization helpers used by Pydantic validators."""

    @classmethod
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.SLUG.fullmatch(normalized):
            raise ValueError(Messages.Validation.stable_slug(field_name))
        return normalized

    @classmethod
    def normalize_nonempty_string(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(Messages.Validation.string_required(field_name))
        normalized = value.strip()
        if not normalized:
            raise ValueError(Messages.Validation.nonempty_string(field_name))
        return normalized

    @classmethod
    def normalize_slug_set(cls, value: object, field_name: str) -> frozenset[str]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(cls.normalize_slug(item, field_name) for item in values)

    @classmethod
    def normalize_scope_set(cls, value: object, field_name: str) -> frozenset[str]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(cls.normalize_scope(item, field_name) for item in values)

    @classmethod
    def normalize_scope(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.SCOPE.fullmatch(normalized):
            raise ValueError(Messages.Validation.explicit_permission_scopes(field_name))
        return normalized

    @classmethod
    def normalize_id_set(cls, value: object, field_name: str) -> frozenset[str]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(cls.normalize_id(item, field_name) for item in values)

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if not Patterns.ID.fullmatch(normalized):
            raise ValueError(Messages.Validation.id_contains_unsupported_characters(field_name))
        return normalized

    @classmethod
    def coerce_iterable(cls, value: object, field_name: str) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError(Messages.Validation.iterable_not_string(field_name))
        if not isinstance(value, Iterable):
            raise ValueError(Messages.Validation.iterable_required(field_name))
        return tuple(value)


class McpSchemaValidator:
    """JSON-schema compatibility validation for loaded MCP descriptors."""

    @classmethod
    def validate_json_schema(cls, value: JsonSchema, field_name: str) -> JsonSchema:
        if not isinstance(value, Mapping):
            raise ValueError(Messages.Validation.json_schema_object(field_name))
        if Keys.Schema.TYPE not in value:
            raise ValueError(Messages.Validation.schema_type_required(field_name))
        try:
            encoded = json.dumps(value, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(Messages.Validation.json_serializable(field_name)) from exc
        if len(encoded.encode(Keys.Encoding.UTF_8)) > Limits.MCP_SCHEMA_MAX_BYTES:
            raise ValueError(Messages.Validation.schema_size_exceeded(field_name))
        return dict(value)
