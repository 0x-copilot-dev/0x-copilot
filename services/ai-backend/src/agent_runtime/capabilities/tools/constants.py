"""Shared keys, limits, patterns, and public messages for dynamic tools."""

from __future__ import annotations

import re

from agent_runtime.prompts.tools import (
    LOAD_TOOL_SPEC_DESCRIPTION as _LOAD_TOOL_SPEC_DESCRIPTION,
)


class Keys:
    """Stable string keys used by the dynamic tool-loading package."""

    class Builtin:
        """Built-in tool name constants."""

        LOAD_TOOL_SPEC = "load_tool_spec"

    class Encoding:
        """Supported text encoding labels."""

        UTF_8 = "utf-8"

    class Fields:
        """Canonical field name strings for Pydantic validators and serialization."""

        ARGS_SCHEMA = "args_schema"
        CONNECTOR = "connector"
        DESCRIPTION = "description"
        DISPLAY_NAME = "display_name"
        NAME = "name"
        REQUIRED_SCOPES = "required_scopes"
        RETURN_SCHEMA = "return_schema"
        SAFE_MESSAGE = "safe_message"
        SHORT_DESCRIPTION = "short_description"
        TAGS = "tags"
        TOOL_NAME = "tool_name"

    class Methods:
        """Method name constants checked via ``getattr`` on provider instances."""

        LIST_TOOL_CARDS = "list_tool_cards"
        LOAD_TOOL_SPEC = "load_tool_spec"

    class Schema:
        """JSON schema field name constants."""

        TYPE = "type"

    class Serialization:
        """Pydantic serialization mode tokens."""

        JSON = "json"


class Limits:
    """Validation limits for model-visible tool metadata."""

    CARD_DESCRIPTION_MAX_LENGTH = 240
    TOOL_DESCRIPTION_MAX_LENGTH = 4_000
    TOOL_SCHEMA_MAX_BYTES = 16_384
    TOOL_NAME_MAX_LENGTH = 120
    TOOL_LOAD_COST_MAX = 100_000
    TOOL_TIMEOUT_MAX_MS = 600_000
    PUBLIC_ERROR_MAX_LENGTH = 500


class Patterns:
    """Compiled patterns for normalized identifiers and scopes."""

    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    SCOPE = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)*$")


class Messages:
    """Centralized public and validation messages for dynamic tools."""

    class Builtin:
        """Built-in tool description strings sourced from the prompts package."""

        LOAD_TOOL_SPEC_DESCRIPTION = _LOAD_TOOL_SPEC_DESCRIPTION

    class Errors:
        """Safe public error messages for tool-loading failures."""

        CONNECTOR_LOAD_FAILED = "The connector could not load this tool right now."
        DUPLICATE_TOOL_REGISTRATION = (
            "Multiple tools are registered with the same name."
        )
        PROVIDER_MISSING_LIST_TOOL_CARDS = "Tool provider is missing list_tool_cards()."
        PROVIDER_MISSING_LOAD_TOOL_SPEC = "Tool provider is missing load_tool_spec()."
        REQUESTED_TOOL_DISABLED = "Requested tool is disabled."
        REQUESTED_TOOL_DUPLICATE = "Requested tool name is registered more than once."
        REQUESTED_TOOL_UNAVAILABLE = "Requested tool is not available."
        RUNTIME_CONTEXT_INVALID = "Runtime context is invalid."
        TOOL_CARD_METADATA_INVALID = "Tool card metadata is invalid."
        TOOL_CARDS_LOAD_FAILED = "Tool cards could not be loaded."
        TOOL_NAME_REQUIRED = "Tools must be requested by stable name."
        TOOL_PERMISSION_DENIED = "You do not have access to this tool."
        TOOL_SPEC_INVALID = "The selected tool has an invalid specification."

    class SpecMismatch:
        """Spec-mismatch messages emitted when a provider returns inconsistent data."""

        CONNECTOR = "The selected tool returned mismatched connector metadata."
        NAME = "The selected tool returned a mismatched specification."
        PERMISSIONS = "The selected tool returned mismatched permission metadata."
        RISK = "The selected tool returned mismatched risk metadata."

    class Validation:
        """Validation failure messages used by Pydantic field and model validators."""

        HIGH_RISK_CONFIRMATION_REQUIRED = (
            "high-risk tools must require explicit confirmation"
        )
        TOOL_LOAD_RESULT_EXACTLY_ONE_OUTCOME = (
            "tool load result must contain exactly one outcome"
        )

        @classmethod
        def explicit_permission_scopes(cls, field_name: str) -> str:
            """Return the explicit-scopes validation message for ``field_name``."""
            return f"{field_name} must contain explicit permission scopes"

        @classmethod
        def iterable_required(cls, field_name: str) -> str:
            """Return the iterable-required validation message for ``field_name``."""
            return f"{field_name} must be an iterable"

        @classmethod
        def iterable_not_string(cls, field_name: str) -> str:
            """Return the non-string-iterable validation message for ``field_name``."""
            return f"{field_name} must be an iterable, not a string"

        @classmethod
        def json_schema_object(cls, field_name: str) -> str:
            """Return the JSON-schema-object validation message for ``field_name``."""
            return f"{field_name} must be a JSON schema object"

        @classmethod
        def json_serializable(cls, field_name: str) -> str:
            """Return the JSON-serialisable validation message for ``field_name``."""
            return f"{field_name} must be JSON serializable"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            """Return the non-empty-string validation message for ``field_name``."""
            return f"{field_name} must not be empty"

        @classmethod
        def schema_size_exceeded(cls, field_name: str) -> str:
            """Return the schema-size-exceeded validation message for ``field_name``."""
            return f"{field_name} exceeds the configured schema size"

        @classmethod
        def schema_type_required(cls, field_name: str) -> str:
            """Return the schema-type-required validation message for ``field_name``."""
            return f"{field_name} must include a JSON schema type"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            """Return the stable-slug validation message for ``field_name``."""
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            """Return the string-required validation message for ``field_name``."""
            return f"{field_name} must be a string"
