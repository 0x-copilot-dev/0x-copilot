"""Constants and message factories for dynamic MCP loading."""

from __future__ import annotations

import re

from agent_runtime.prompts.tools import (
    AUTH_MCP_TOOL_DESCRIPTION as _AUTH_MCP_TOOL_DESCRIPTION,
    CALL_MCP_TOOL_DESCRIPTION as _CALL_MCP_TOOL_DESCRIPTION,
    LOAD_MCP_SERVER_TOOL_DESCRIPTION as _LOAD_MCP_SERVER_TOOL_DESCRIPTION,
)


class Keys:
    """Stable keys used at MCP validation and serialization boundaries."""

    class Encoding:
        """Encoding name constants."""

        UTF_8 = "utf-8"

    class Field:
        """JSON field name constants for MCP payloads."""

        ACCESS_POLICY = "access_policy"
        ALLOWED_ORG_IDS = "allowed_org_ids"
        ALLOWED_USER_IDS = "allowed_user_ids"
        ARGUMENTS = "arguments"
        AUTH_MODE = "auth_mode"
        AUTH_STATE = "auth_state"
        CODE = "code"
        CONNECTED_AT = "connected_at"
        CONNECTION_ID = "connection_id"
        CORRELATION_ID = "correlation_id"
        DESCRIPTION = "description"
        ENABLED = "enabled"
        HEALTH = "health"
        INPUT_SCHEMA = "input_schema"
        LATENCY_MS = "latency_ms"
        LOAD_COST = "load_cost"
        LOADED_SERVER = "loaded_server"
        LOCAL_TOOL_NAMES = "local_tool_names"
        MIME_TYPE = "mime_type"
        NAME = "name"
        OUTPUT_SHAPE = "output_shape"
        ORG_ID = "org_id"
        READ_ONLY = "read_only"
        REDIRECT_URI = "redirect_uri"
        REQUIRED_SCOPES = "required_scopes"
        RESOURCES = "resources"
        RETRYABLE = "retryable"
        RISK_LEVEL = "risk_level"
        SAFE_MESSAGE = "safe_message"
        SERVER_ID = "server_id"
        SERVER_CARD = "server_card"
        SERVER_NAME = "server_name"
        SHORT_DESCRIPTION = "short_description"
        TOOL_CALL_ID = "tool_call_id"
        TOOL_NAME = "tool_name"
        TOOLS = "tools"
        TRANSPORT = "transport"
        URI = "uri"
        URL = "url"
        USER_ID = "user_id"
        VERSION = "version"
        WARNINGS = "warnings"

    class JsonRpc:
        """JSON-RPC 2.0 envelope field name constants."""

        CAPABILITIES = "capabilities"
        CLIENT_INFO = "clientInfo"
        ERROR = "error"
        ID = "id"
        JSONRPC = "jsonrpc"
        METHOD = "method"
        PARAMS = "params"
        PAYLOAD = "payload"
        PROTOCOL_VERSION = "protocolVersion"
        RESULT = "result"

    class NativeDescriptor:
        """camelCase field names used in raw MCP descriptor payloads."""

        INPUT_SCHEMA_CAMEL = "inputSchema"
        MIME_TYPE_CAMEL = "mimeType"
        OUTPUT_SCHEMA_CAMEL = "outputSchema"

    class Schema:
        """JSON schema structural key names."""

        PROPERTIES = "properties"
        QUERY = "query"
        REQUIRED = "required"
        TYPE = "type"


class Values:
    """Stable string values exposed by MCP contracts and tests."""

    class AuthMode:
        """``auth_mode`` string values."""

        API_KEY = "api_key"
        NONE = "none"
        OAUTH2 = "oauth2"
        SERVICE_ACCOUNT = "service_account"

    class AuthState:
        """``auth_state`` string values."""

        AUTH_FAILED = "auth_failed"
        AUTH_PENDING = "auth_pending"
        AUTH_SKIPPED = "auth_skipped"
        AUTH_UNSUPPORTED = "auth_unsupported"
        AUTHENTICATED = "authenticated"
        UNAUTHENTICATED = "unauthenticated"

    class ErrorCode:
        """Load-error code string values."""

        AUTH_FAILURE = "auth_failure"
        CONNECTION_FAILED = "connection_failed"
        DUPLICATE_DESCRIPTOR_NAME = "duplicate_descriptor_name"
        DUPLICATE_SERVER_NAME = "duplicate_server_name"
        INVALID_LOCAL_TOOL_NAMES = "invalid_local_tool_names"
        INVALID_SERVER_NAME = "invalid_server_name"
        LOAD_BUDGET_EXCEEDED = "load_budget_exceeded"
        LOCAL_TOOL_COLLISION = "local_tool_collision"
        MALFORMED_DESCRIPTOR = "malformed_descriptor"
        PERMISSION_DENIED = "permission_denied"
        SERVER_DISABLED = "server_disabled"
        SERVER_UNHEALTHY = "server_unhealthy"
        TIMEOUT = "timeout"
        UNKNOWN_SERVER = "unknown_server"
        UNKNOWN_TOOL = "unknown_tool"
        UNSUPPORTED_TRANSPORT = "unsupported_transport"

    class Health:
        """Server health string values."""

        DEGRADED = "degraded"
        DISABLED = "disabled"
        HEALTHY = "healthy"
        UNAVAILABLE = "unavailable"

    class Risk:
        """Risk level string values."""

        CRITICAL = "critical"
        HIGH = "high"
        LOW = "low"
        MEDIUM = "medium"

    class SchemaType:
        """JSON schema ``type`` string values used in MCP descriptors."""

        OBJECT = "object"
        STRING = "string"

    class JsonRpc:
        """JSON-RPC protocol version constant."""

        VERSION = "2.0"

    class JsonRpcError:
        """JSON-RPC error code numeric constants."""

        METHOD_NOT_FOUND = -32601

    class JsonRpcMethod:
        """JSON-RPC method name constants for MCP calls."""

        INITIALIZE = "initialize"
        INITIALIZED = "notifications/initialized"
        CALL_TOOL = "tools/call"
        LIST_RESOURCES = "resources/list"
        LIST_TOOLS = "tools/list"

    class McpClientInfo:
        """Advertised client identity values sent in the MCP handshake."""

        NAME = "enterprise-search-ai-backend"
        PROTOCOL_VERSION = "2025-06-18"
        VERSION = "0.1.0"

    class Placeholder:
        """Fallback name values used when a server descriptor omits required fields."""

        RESOURCE_NAME = "mcp_resource"
        TOOL_NAME = "mcp_tool"

    class Mime:
        """MIME type string values."""

        OCTET_STREAM = "application/octet-stream"

    class Route:
        """Internal API route templates."""

        INTERNAL_MCP_RPC = "/internal/v1/mcp/servers/{server_id}/rpc"

    class Transport:
        """MCP transport string values."""

        HTTP = "http"
        SSE = "sse"
        STDIO = "stdio"

    class ToolName:
        """Canonical tool names used by MCP middleware."""

        AUTH_MCP = "auth_mcp"
        CALL_MCP_TOOL = "call_mcp_tool"
        LOAD_MCP_SERVER = "load_mcp_server"

    class UriScheme:
        """Allowed resource URI scheme strings."""

        HTTPS = "https"
        MCP = "mcp"
        URN = "urn"

    class WarningCode:
        """Non-fatal warning code string values."""

        SERVER_DEGRADED = "server_degraded"


class Limits:
    """Validation limits for compact cards and MCP descriptors."""

    CARD_DESCRIPTION_MAX_LENGTH = 240
    DESCRIPTOR_DESCRIPTION_MAX_LENGTH = 4_000
    LOAD_COST_MAX = 100_000
    MCP_SCHEMA_MAX_BYTES = 16_384
    METADATA_LATENCY_MAX_MS = 600_000
    RESOURCE_NAME_MAX_LENGTH = 120
    MIME_TYPE_MAX_LENGTH = 200
    SAFE_MESSAGE_MAX_LENGTH = 500


class Defaults:
    """Default runtime limits for MCP loading."""

    MAX_RESOURCE_DESCRIPTORS = 100
    MAX_TOOL_DESCRIPTORS = 100
    TIMEOUT_SECONDS = 30


class Patterns:
    """Compiled validators for stable IDs, slugs, and permission scopes."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    SCOPE = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)*$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Centralized safe validation and public error messages."""

    class Middleware:
        """Tool description strings for MCP middleware tools."""

        AUTH_MCP_TOOL_DESCRIPTION = _AUTH_MCP_TOOL_DESCRIPTION
        CALL_MCP_TOOL_DESCRIPTION = _CALL_MCP_TOOL_DESCRIPTION
        LOAD_MCP_SERVER_TOOL_DESCRIPTION = _LOAD_MCP_SERVER_TOOL_DESCRIPTION

    class Registry:
        """Safe error messages emitted by the MCP registry."""

        CARDS_LOAD_FAILED = "MCP server cards could not be loaded."
        DUPLICATE_SERVER_NAME = (
            "Multiple MCP servers are registered with the same name."
        )
        INVALID_CONTEXT = "Runtime context is invalid."
        INVALID_SERVER_CARD = "MCP server card metadata is invalid."
        MISSING_CREATE_CLIENT = "MCP provider is missing create_client()."
        MISSING_LIST_SERVER_CARDS = "MCP provider is missing list_server_cards()."
        REQUESTED_SERVER_DISABLED = "Requested MCP server is disabled."
        REQUESTED_SERVER_DUPLICATE = (
            "Requested MCP server name is registered more than once."
        )
        REQUESTED_SERVER_UNAVAILABLE = "Requested MCP server is unavailable."
        REQUESTED_SERVER_UNKNOWN = "Requested MCP server is not available."
        REQUESTED_TOOL_UNKNOWN = "Requested MCP tool is not available on this server."

    class Loader:
        """Safe error and warning messages emitted by the MCP loader."""

        AUTH_FAILED = "MCP server authentication failed."
        CONNECTION_FAILED = "The MCP server could not be reached."
        DESCRIPTORS_INVALID = "The MCP server returned invalid descriptors."
        DESCRIPTORS_LOAD_FAILED = (
            "The MCP server descriptors could not be loaded safely."
        )
        DUPLICATE_RESOURCE_NAMES = "The MCP server returned duplicate resource names."
        DUPLICATE_TOOL_NAMES = "The MCP server returned duplicate tool names."
        INVALID_CONNECTION_METADATA = (
            "The MCP server returned invalid connection metadata."
        )
        LOAD_FAILED = "The MCP server could not be loaded right now."
        LOCAL_TOOL_COLLISION = (
            "The MCP server returned a tool name that collides with a local tool."
        )
        LOCAL_TOOL_NAMES_INVALID = "MCP local tool names are invalid."
        RESOURCE_BUDGET_EXCEEDED = (
            "The MCP server returned too many resources to load safely."
        )
        SERVER_DEGRADED = "The MCP server is degraded and may be slower than usual."
        STABLE_SERVER_NAME_REQUIRED = "MCP servers must be requested by stable name."
        TIMEOUT = "The MCP server did not respond in time."
        TOOL_BUDGET_EXCEEDED = "The MCP server returned too many tools to load safely."
        UNAUTHORIZED_SERVER = "You do not have access to this MCP server."
        UNSUPPORTED_TRANSPORT = "Requested MCP server uses an unsupported transport."

    class Validation:
        """Safe validation error messages and factory methods."""

        EXACTLY_ONE_LOAD_OUTCOME = "mcp load result must contain exactly one outcome"
        UNSUPPORTED_RESOURCE_SCHEME = "uri uses an unsupported resource scheme"

        @classmethod
        def explicit_permission_scopes(cls, field_name: str) -> str:
            """Return a validation message for missing explicit permission scopes."""
            return f"{field_name} must contain explicit permission scopes"

        @classmethod
        def id_contains_unsupported_characters(cls, field_name: str) -> str:
            """Return a validation message for IDs with unsupported characters."""
            return f"{field_name} contains unsupported characters"

        @classmethod
        def iterable_not_string(cls, field_name: str) -> str:
            """Return a validation message when a string is passed where an iterable is required."""
            return f"{field_name} must be an iterable, not a string"

        @classmethod
        def iterable_required(cls, field_name: str) -> str:
            """Return a validation message when a non-iterable is passed."""
            return f"{field_name} must be an iterable"

        @classmethod
        def json_schema_object(cls, field_name: str) -> str:
            """Return a validation message for non-dict schema fields."""
            return f"{field_name} must be a JSON schema object"

        @classmethod
        def json_serializable(cls, field_name: str) -> str:
            """Return a validation message for non-JSON-serialisable values."""
            return f"{field_name} must be JSON serializable"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            """Return a validation message for empty or missing strings."""
            return f"{field_name} must not be empty"

        @classmethod
        def schema_size_exceeded(cls, field_name: str) -> str:
            """Return a validation message for schemas that exceed the byte limit."""
            return f"{field_name} exceeds the configured schema size"

        @classmethod
        def schema_type_required(cls, field_name: str) -> str:
            """Return a validation message when a schema is missing a type key."""
            return f"{field_name} must include a JSON schema type"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            """Return a validation message for values that are not stable slugs."""
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            """Return a validation message for non-string values."""
            return f"{field_name} must be a string"
