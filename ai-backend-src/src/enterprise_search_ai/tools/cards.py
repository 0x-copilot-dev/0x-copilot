"""Pydantic contracts for dynamic tool cards and loaded tool specs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
import json
import re
from typing import Any, TypeAlias
from uuid import uuid4

from pydantic import (
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, RuntimeContract

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)*$")

JsonSchema: TypeAlias = Mapping[str, Any]

MAX_CARD_DESCRIPTION_LENGTH = 240
MAX_TOOL_DESCRIPTION_LENGTH = 4_000
MAX_TOOL_SCHEMA_BYTES = 16_384


class ToolRiskLevel(StrEnum):
    """Risk bands used before exposing capabilities to the model."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolSideEffect(StrEnum):
    """Side-effect classes disclosed by a loaded tool spec."""

    READ = "read"
    WRITE = "write"
    EXTERNAL_CALL = "external_call"
    DELETE = "delete"


class ToolLoadErrorCode(StrEnum):
    """Typed dynamic-loading failures safe for public surfaces."""

    INVALID_TOOL_NAME = "invalid_tool_name"
    UNKNOWN_TOOL = "unknown_tool"
    DUPLICATE_TOOL_NAME = "duplicate_tool_name"
    TOOL_DISABLED = "tool_disabled"
    PERMISSION_DENIED = "permission_denied"
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    MALFORMED_TOOL_SPEC = "malformed_tool_spec"


class ToolCard(RuntimeContract):
    """Compact model-visible summary used before loading a full tool spec."""

    name: str
    display_name: str = Field(min_length=1, max_length=120)
    short_description: str = Field(min_length=1, max_length=MAX_CARD_DESCRIPTION_LENGTH)
    connector: str
    tags: frozenset[str] = Field(default_factory=frozenset)
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    load_cost: PositiveInt = Field(le=100_000)
    enabled: bool = True

    @field_validator("name", "connector")
    @classmethod
    def _normalize_slug_field(cls, value: object, info: ValidationInfo) -> str:
        return normalize_slug(value, info.field_name)

    @field_validator("display_name", "short_description")
    @classmethod
    def _normalize_label(cls, value: str, info: ValidationInfo) -> str:
        return normalize_nonempty_string(value, info.field_name)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> frozenset[str]:
        return normalize_slug_set(value, "tags")

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        return normalize_scope_set(value, "required_scopes")


class ToolPermissionPolicy(RuntimeContract):
    """Authorization metadata rechecked when a full tool spec is loaded."""

    connector: str
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    requires_confirmation: bool = False

    @field_validator("connector")
    @classmethod
    def _normalize_connector(cls, value: object) -> str:
        return normalize_slug(value, "connector")

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        return normalize_scope_set(value, "required_scopes")

    @model_validator(mode="after")
    def _risky_tools_require_confirmation(self) -> "ToolPermissionPolicy":
        if self.risk_level in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL}:
            if not self.requires_confirmation:
                msg = "high-risk tools must require explicit confirmation"
                raise ValueError(msg)
        return self


class LoadedToolSpec(RuntimeContract):
    """Full validated tool contract loaded only after explicit selection."""

    name: str
    description: str = Field(min_length=1, max_length=MAX_TOOL_DESCRIPTION_LENGTH)
    args_schema: JsonSchema
    return_schema: JsonSchema
    side_effects: frozenset[ToolSideEffect] = Field(
        default_factory=lambda: frozenset({ToolSideEffect.READ})
    )
    timeout_ms: PositiveInt = Field(le=600_000)
    permission_policy: ToolPermissionPolicy

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return normalize_slug(value, "name")

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str) -> str:
        return normalize_nonempty_string(value, "description")

    @field_validator("args_schema", "return_schema")
    @classmethod
    def _validate_json_schema(cls, value: JsonSchema, info: ValidationInfo) -> JsonSchema:
        return validate_json_schema(value, info.field_name)


class ToolLoadRequest(RuntimeContract):
    """Request to resolve a selected compact card into a full spec."""

    tool_name: str
    runtime_context: AgentRuntimeContext

    @field_validator("tool_name")
    @classmethod
    def _normalize_tool_name(cls, value: object) -> str:
        return normalize_slug(value, "tool_name")


class ToolLoadError(RuntimeContract):
    """Safe, typed error returned by the dynamic tool loader."""

    code: ToolLoadErrorCode
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    tool_name: str | None = None
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator("safe_message")
    @classmethod
    def _normalize_safe_message(cls, value: str) -> str:
        return normalize_nonempty_string(value, "safe_message")

    @field_validator("tool_name")
    @classmethod
    def _normalize_optional_tool_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug(value, "tool_name")


class ToolLoadResult(RuntimeContract):
    """Result envelope containing either a loaded spec or a typed error."""

    loaded_spec: LoadedToolSpec | None = None
    error: ToolLoadError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> "ToolLoadResult":
        if (self.loaded_spec is None) == (self.error is None):
            msg = "tool load result must contain exactly one outcome"
            raise ValueError(msg)
        return self

    @classmethod
    def ok(cls, loaded_spec: LoadedToolSpec) -> "ToolLoadResult":
        return cls(loaded_spec=loaded_spec)

    @classmethod
    def fail(
        cls,
        code: ToolLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        tool_name: str | None = None,
        correlation_id: str | None = None,
    ) -> "ToolLoadResult":
        return cls(
            error=ToolLoadError(
                code=code,
                safe_message=safe_message,
                retryable=retryable,
                tool_name=tool_name,
                correlation_id=correlation_id or uuid4().hex,
            )
        )

    @property
    def succeeded(self) -> bool:
        return self.loaded_spec is not None


def normalize_slug(value: object, field_name: str) -> str:
    normalized = normalize_nonempty_string(value, field_name).lower()
    if not _SLUG_PATTERN.fullmatch(normalized):
        msg = f"{field_name} must be a stable slug"
        raise ValueError(msg)
    return normalized


def normalize_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        msg = f"{field_name} must be a string"
        raise ValueError(msg)
    normalized = value.strip()
    if not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    return normalized


def normalize_slug_set(value: object, field_name: str) -> frozenset[str]:
    values = _coerce_iterable(value, field_name)
    return frozenset(normalize_slug(item, field_name) for item in values)


def normalize_scope_set(value: object, field_name: str) -> frozenset[str]:
    values = _coerce_iterable(value, field_name)
    return frozenset(normalize_scope(item, field_name) for item in values)


def normalize_scope(value: object, field_name: str) -> str:
    normalized = normalize_nonempty_string(value, field_name).lower()
    if not _SCOPE_PATTERN.fullmatch(normalized):
        msg = f"{field_name} must contain explicit permission scopes"
        raise ValueError(msg)
    return normalized


def validate_json_schema(value: JsonSchema, field_name: str) -> JsonSchema:
    if not isinstance(value, Mapping):
        msg = f"{field_name} must be a JSON schema object"
        raise ValueError(msg)
    if "type" not in value:
        msg = f"{field_name} must include a JSON schema type"
        raise ValueError(msg)
    try:
        encoded = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        msg = f"{field_name} must be JSON serializable"
        raise ValueError(msg) from exc
    if len(encoded.encode("utf-8")) > MAX_TOOL_SCHEMA_BYTES:
        msg = f"{field_name} exceeds the configured schema size"
        raise ValueError(msg)
    return dict(value)


def _coerce_iterable(value: object, field_name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        msg = f"{field_name} must be an iterable, not a string"
        raise ValueError(msg)
    if not isinstance(value, Iterable):
        msg = f"{field_name} must be an iterable"
        raise ValueError(msg)
    return tuple(value)
