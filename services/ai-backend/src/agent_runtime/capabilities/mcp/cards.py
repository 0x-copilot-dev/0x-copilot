"""Pydantic contracts for MCP server cards, load requests/results, tool descriptors, and error types."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Annotated, Any, TypeAlias
from urllib.parse import urlsplit
from uuid import uuid4

from langchain_core.tools import InjectedToolCallId
from pydantic import (
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.capabilities.mcp.constants import (
    Keys,
    Limits,
    Messages,
    Values,
)

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


class McpAuthState(StrEnum):
    """Authentication state disclosed without exposing credentials."""

    UNAUTHENTICATED = Values.AuthState.UNAUTHENTICATED
    AUTH_SKIPPED = Values.AuthState.AUTH_SKIPPED
    AUTH_PENDING = Values.AuthState.AUTH_PENDING
    AUTHENTICATED = Values.AuthState.AUTHENTICATED
    AUTH_FAILED = Values.AuthState.AUTH_FAILED
    AUTH_UNSUPPORTED = Values.AuthState.AUTH_UNSUPPORTED


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
    INVALID_LOCAL_TOOL_NAMES = Values.ErrorCode.INVALID_LOCAL_TOOL_NAMES
    UNKNOWN_SERVER = Values.ErrorCode.UNKNOWN_SERVER
    DUPLICATE_SERVER_NAME = Values.ErrorCode.DUPLICATE_SERVER_NAME
    SERVER_DISABLED = Values.ErrorCode.SERVER_DISABLED
    SERVER_UNHEALTHY = Values.ErrorCode.SERVER_UNHEALTHY
    PERMISSION_DENIED = Values.ErrorCode.PERMISSION_DENIED
    UNSUPPORTED_TRANSPORT = Values.ErrorCode.UNSUPPORTED_TRANSPORT
    AUTH_FAILURE = Values.ErrorCode.AUTH_FAILURE
    CONNECTION_FAILED = Values.ErrorCode.CONNECTION_FAILED
    TIMEOUT = Values.ErrorCode.TIMEOUT
    UNKNOWN_TOOL = Values.ErrorCode.UNKNOWN_TOOL
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
    server_id: str | None = None
    display_name: str | None = None
    short_description: str = Field(
        min_length=1,
        max_length=Limits.CARD_DESCRIPTION_MAX_LENGTH,
    )
    transport: McpTransport
    auth_mode: McpAuthMode
    auth_state: McpAuthState = McpAuthState.AUTHENTICATED
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    health: McpServerHealth
    load_cost: PositiveInt = Field(le=Limits.LOAD_COST_MAX)
    enabled: bool = True
    allowed_org_ids: frozenset[str] = Field(default_factory=frozenset)
    allowed_user_ids: frozenset[str] = Field(default_factory=frozenset)
    display: ToolDisplayTemplate | None = None

    @field_validator(Keys.Field.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        """Coerce the name field to a stable slug identifier."""
        return McpValueNormalizer.normalize_slug(value, Keys.Field.NAME)

    @field_validator(Keys.Field.SERVER_ID)
    @classmethod
    def _normalize_optional_server_id(cls, value: str | None) -> str | None:
        """Coerce optional server id to a stable slug, or pass through ``None``."""
        if value is None:
            return None
        return McpValueNormalizer.normalize_id(value, Keys.Field.SERVER_ID)

    @field_validator("display_name")
    @classmethod
    def _normalize_optional_display_name(cls, value: str | None) -> str | None:
        """Strip and validate optional display name, or pass through ``None``."""
        if value is None:
            return None
        return McpValueNormalizer.normalize_nonempty_string(value, "display_name")

    @field_validator(Keys.Field.SHORT_DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        """Strip and validate the description field."""
        return McpValueNormalizer.normalize_nonempty_string(
            value,
            Keys.Field.SHORT_DESCRIPTION,
        )

    @field_validator(
        Keys.Field.TRANSPORT,
        Keys.Field.AUTH_MODE,
        Keys.Field.AUTH_STATE,
        Keys.Field.HEALTH,
        mode="before",
    )
    @classmethod
    def _normalize_enum_value(cls, value: object) -> str:
        """Coerce enum value field to its string representation."""
        if isinstance(value, StrEnum):
            return value.value
        return McpValueNormalizer.normalize_nonempty_string(
            value, Keys.Field.CODE
        ).lower()

    @field_validator(Keys.Field.REQUIRED_SCOPES, mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        """Coerce required-scopes input to a frozenset of valid scope strings."""
        return McpValueNormalizer.normalize_scope_set(value, Keys.Field.REQUIRED_SCOPES)

    @field_validator(
        Keys.Field.ALLOWED_ORG_IDS, Keys.Field.ALLOWED_USER_IDS, mode="before"
    )
    @classmethod
    def _normalize_allowed_ids(
        cls, value: object, info: ValidationInfo
    ) -> frozenset[str]:
        """Coerce allowed-ids input to a frozenset of slug strings."""
        return McpValueNormalizer.normalize_id_set(value, info.field_name)


class McpLoadRequest(RuntimeContract):
    """Request to connect to and discover a selected MCP server."""

    server_name: str
    runtime_context: AgentRuntimeContext
    local_tool_names: frozenset[str] = Field(default_factory=frozenset)

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_server_name(cls, value: object) -> str:
        """Coerce the server name to a stable slug identifier."""
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.LOCAL_TOOL_NAMES, mode="before")
    @classmethod
    def _normalize_local_tool_names(cls, value: object) -> frozenset[str]:
        """Coerce local-tool-names input to a frozenset of slug strings."""
        return McpValueNormalizer.normalize_slug_set(value, Keys.Field.LOCAL_TOOL_NAMES)


class McpToolCallRequest(RuntimeContract):
    """Request to invoke a validated tool on a selected MCP server.

    ``tool_call_id`` is injected by LangGraph via :class:`InjectedToolCallId`
    so the ordinal allocated for this call can be bound in the citations map.
    Defaults to the empty string for replay/eval harnesses; the runtime worker
    always supplies a non-empty value.
    """

    server_name: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: Annotated[str, InjectedToolCallId] = ""

    @model_validator(mode="before")
    @classmethod
    def _collect_misplaced_arguments(cls, value: object) -> object:
        """Fold unknown top-level keys into ``arguments`` so flat LangGraph calls are accepted."""
        if not isinstance(value, Mapping):
            return value
        known_keys = {
            Keys.Field.SERVER_NAME,
            Keys.Field.TOOL_NAME,
            Keys.Field.ARGUMENTS,
            Keys.Field.TOOL_CALL_ID,
        }
        extra_arguments = {
            str(key): item for key, item in value.items() if str(key) not in known_keys
        }
        if not extra_arguments:
            return value
        raw_arguments = value.get(Keys.Field.ARGUMENTS)
        arguments = raw_arguments if isinstance(raw_arguments, Mapping) else {}
        return {
            Keys.Field.SERVER_NAME: value.get(Keys.Field.SERVER_NAME),
            Keys.Field.TOOL_NAME: value.get(Keys.Field.TOOL_NAME),
            Keys.Field.ARGUMENTS: {
                **extra_arguments,
                **dict(arguments),
            },
            Keys.Field.TOOL_CALL_ID: value.get(Keys.Field.TOOL_CALL_ID, ""),
        }

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_server_name(cls, value: object) -> str:
        """Coerce the server name to a stable slug identifier."""
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.TOOL_NAME)
    @classmethod
    def _normalize_tool_name(cls, value: object) -> str:
        """Coerce the tool name to a stable slug identifier."""
        return McpValueNormalizer.normalize_slug(value, Keys.Field.TOOL_NAME)

    @field_validator(Keys.Field.ARGUMENTS, mode="before")
    @classmethod
    def _validate_arguments(cls, value: object) -> dict[str, Any]:
        """Validate that ``arguments`` is a JSON-serialisable object."""
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(
                Messages.Validation.json_schema_object(Keys.Field.ARGUMENTS)
            )
        try:
            json.dumps(value, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                Messages.Validation.json_serializable(Keys.Field.ARGUMENTS)
            ) from exc
        return dict(value)


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
    display: ToolDisplayTemplate | None = None

    @field_validator(Keys.Field.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        """Coerce the name field to a stable slug identifier."""
        return McpValueNormalizer.normalize_slug(value, Keys.Field.NAME)

    @field_validator(Keys.Field.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        """Strip and validate the description field."""
        return McpValueNormalizer.normalize_nonempty_string(
            value, Keys.Field.DESCRIPTION
        )

    @field_validator(Keys.Field.INPUT_SCHEMA, Keys.Field.OUTPUT_SHAPE)
    @classmethod
    def _validate_schema(cls, value: JsonSchema, info: ValidationInfo) -> JsonSchema:
        """Validate that the schema field is a JSON-serialisable object."""
        return McpSchemaValidator.validate_json_schema(value, info.field_name)

    @field_validator(Keys.Field.RISK_LEVEL, mode="before")
    @classmethod
    def _normalize_risk_level(cls, value: object) -> str:
        """Coerce risk level to a valid ``ToolRiskLevel`` string."""
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
        """Coerce required-scopes input to a frozenset of valid scope strings."""
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
        """Strip and validate the URI field."""
        normalized = McpValueNormalizer.normalize_nonempty_string(value, Keys.Field.URI)
        parsed = urlsplit(normalized)
        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_RESOURCE_URI_SCHEMES:
            raise ValueError(Messages.Validation.UNSUPPORTED_RESOURCE_SCHEME)
        return normalized

    @field_validator(Keys.Field.NAME, Keys.Field.MIME_TYPE, Keys.Field.DESCRIPTION)
    @classmethod
    def _normalize_label(cls, value: object, info: ValidationInfo) -> str:
        """Strip and validate the label field."""
        return McpValueNormalizer.normalize_nonempty_string(value, info.field_name)


class McpConnectionMetadata(RuntimeContract):
    """Safe connection metadata for a loaded MCP server."""

    server_name: str
    transport: McpTransport
    auth_mode: McpAuthMode
    connection_id: str = Field(default_factory=lambda: uuid4().hex)
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: int = Field(default=0, ge=0, le=Limits.METADATA_LATENCY_MAX_MS)

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_server_name(cls, value: object) -> str:
        """Coerce the server name to a stable slug identifier."""
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.TRANSPORT, Keys.Field.AUTH_MODE, mode="before")
    @classmethod
    def _normalize_enum_value(cls, value: object) -> str:
        """Coerce enum value field to its string representation."""
        if isinstance(value, StrEnum):
            return value.value
        return McpValueNormalizer.normalize_nonempty_string(
            value, Keys.Field.CODE
        ).lower()

    @field_validator(Keys.Field.CONNECTION_ID)
    @classmethod
    def _normalize_connection_id(cls, value: object) -> str:
        """Coerce connection id to a stable slug identifier."""
        return McpValueNormalizer.normalize_id(value, Keys.Field.CONNECTION_ID)


class McpLoadWarning(RuntimeContract):
    """Non-fatal loader warning safe for model and API surfaces."""

    code: McpWarningCode
    safe_message: str = Field(min_length=1, max_length=Limits.SAFE_MESSAGE_MAX_LENGTH)

    @field_validator(Keys.Field.SAFE_MESSAGE)
    @classmethod
    def _normalize_safe_message(cls, value: object) -> str:
        """Strip and validate the safe public error message."""
        return McpValueNormalizer.normalize_nonempty_string(
            value, Keys.Field.SAFE_MESSAGE
        )


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
        """Strip and validate the safe public error message."""
        return McpValueNormalizer.normalize_nonempty_string(
            value, Keys.Field.SAFE_MESSAGE
        )

    @field_validator(Keys.Field.SERVER_NAME)
    @classmethod
    def _normalize_optional_server_name(cls, value: str | None) -> str | None:
        """Coerce optional server name to a slug, or pass through ``None``."""
        if value is None:
            return None
        return McpValueNormalizer.normalize_slug(value, Keys.Field.SERVER_NAME)

    @field_validator(Keys.Field.CORRELATION_ID)
    @classmethod
    def _normalize_correlation_id(cls, value: object) -> str:
        """Coerce the correlation id to a non-empty string."""
        return McpValueNormalizer.normalize_id(value, Keys.Field.CORRELATION_ID)


class McpToolCallResult(RuntimeContract):
    """Result envelope for a generic MCP tool invocation."""

    server_name: str | None = None
    tool_name: str | None = None
    output: dict[str, Any] | None = None
    error: McpLoadError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> "McpToolCallResult":
        """Enforce that exactly one of ``output`` or ``error`` is set."""
        if (self.output is None) == (self.error is None):
            raise ValueError(Messages.Validation.EXACTLY_ONE_LOAD_OUTCOME)
        return self

    @classmethod
    def ok(
        cls,
        *,
        server_name: str,
        tool_name: str,
        output: Mapping[str, Any],
    ) -> "McpToolCallResult":
        """Return a successful result wrapping ``output``."""
        return cls(
            server_name=server_name,
            tool_name=tool_name,
            output=dict(output),
        )

    @classmethod
    def fail(
        cls,
        code: McpLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        server_name: str | None = None,
        tool_name: str | None = None,
        correlation_id: str | None = None,
    ) -> "McpToolCallResult":
        """Return a failure result with a typed ``McpLoadError``."""
        return cls(
            server_name=server_name,
            tool_name=tool_name,
            error=McpLoadError(
                code=code,
                safe_message=safe_message,
                retryable=retryable,
                server_name=server_name,
                correlation_id=correlation_id or uuid4().hex,
            ),
        )

    @classmethod
    def fail_from_load_error(
        cls,
        error: McpLoadError,
        *,
        tool_name: str | None = None,
    ) -> "McpToolCallResult":
        """Lift a pre-built ``McpLoadError`` into a failure result."""
        return cls(
            server_name=error.server_name,
            tool_name=tool_name,
            error=error,
        )


class McpLoadResult(RuntimeContract):
    """Result envelope containing either loaded descriptors or a typed error."""

    loaded_server: LoadedMcpServer | None = None
    error: McpLoadError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> "McpLoadResult":
        """Enforce that exactly one of ``loaded_server`` or ``error`` is set."""
        if (self.loaded_server is None) == (self.error is None):
            raise ValueError(Messages.Validation.EXACTLY_ONE_LOAD_OUTCOME)
        return self

    @classmethod
    def ok(cls, loaded_server: LoadedMcpServer) -> "McpLoadResult":
        """Return a successful load result."""
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
        """Return a failure load result with a typed ``McpLoadError``."""
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
        """Return ``True`` when the load completed without error."""
        return self.loaded_server is not None


class McpValueNormalizer:
    """Normalization helpers used by Pydantic validators.

    All common methods delegate to the shared ``ValueNormalizer``.
    """

    from agent_runtime.validation import ValueNormalizer as _V

    normalize_nonempty_string = _V.normalize_nonempty_string
    normalize_slug = _V.normalize_slug
    normalize_slug_set = _V.normalize_slug_set
    normalize_scope = _V.normalize_scope
    normalize_scope_set = _V.normalize_scope_set
    normalize_id = _V.normalize_id
    normalize_id_set = _V.normalize_id_set
    coerce_iterable = _V.coerce_iterable

    del _V


class McpSchemaValidator:
    """JSON-schema compatibility validation for loaded MCP descriptors."""

    @classmethod
    def validate_json_schema(cls, value: JsonSchema, field_name: str) -> JsonSchema:
        """Validate that ``value`` is a JSON-serialisable mapping with a ``type`` key."""
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
