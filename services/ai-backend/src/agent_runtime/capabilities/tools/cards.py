"""Pydantic contracts for dynamic tool cards and loaded tool specs."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import json
from typing import Any, TypeAlias
from uuid import uuid4

from pydantic import (
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.capabilities.tools.constants import Keys, Limits, Messages

JsonSchema: TypeAlias = Mapping[str, Any]


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


class ToolDisplayTemplate(RuntimeContract):
    """Deterministic title/summary templates rendered without an LLM call.

    Authors register these on `ToolCard` (and on `McpServerCard` /
    `McpToolDescriptor` for MCP tools) so the presentation layer can build
    user-facing activity cards from payload fields alone, skipping the
    presentation LLM entirely. Placeholders use Python `str.format` syntax
    against the safe event payload (e.g. ``"Searching {connector} for {query}"``).
    Missing placeholders fall back to the generator's static default.

    `result_preview_path` and `result_preview_row` declare how to project
    rows out of the result payload for the card body. Without them, the
    presentation layer falls back to heuristic field-name matching on
    common shapes — see ``PayloadProjector``.
    """

    title_template: str = Field(min_length=1, max_length=240)
    summary_template: str | None = Field(default=None, max_length=480)
    result_title_template: str | None = Field(default=None, max_length=240)
    result_summary_template: str | None = Field(default=None, max_length=480)
    result_preview_path: str | None = Field(default=None, max_length=120)
    result_preview_row: dict[str, str] | None = Field(default=None)
    # ``True`` when produced by a synthesis path (e.g. ``DisplayMetadataMiddleware``).
    # Agent-supplied ``_display_*`` overrides are applied only when this flag is set;
    # author-written templates always win.
    synthetic: bool = False


class ToolCard(RuntimeContract):
    """Compact model-visible summary used before loading a full tool spec."""

    name: str
    display_name: str = Field(min_length=1, max_length=Limits.TOOL_NAME_MAX_LENGTH)
    short_description: str = Field(
        min_length=1,
        max_length=Limits.CARD_DESCRIPTION_MAX_LENGTH,
    )
    connector: str
    tags: frozenset[str] = Field(default_factory=frozenset)
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    load_cost: PositiveInt = Field(le=Limits.TOOL_LOAD_COST_MAX)
    enabled: bool = True
    display: ToolDisplayTemplate | None = None

    @field_validator(Keys.Fields.NAME, Keys.Fields.CONNECTOR)
    @classmethod
    def _normalize_slug_field(cls, value: object, info: ValidationInfo) -> str:
        """Coerce slug fields to lowercase stable identifiers."""
        return ToolValueNormalizer.normalize_slug(value, info.field_name)

    @field_validator(Keys.Fields.DISPLAY_NAME, Keys.Fields.SHORT_DESCRIPTION)
    @classmethod
    def _normalize_label(cls, value: str, info: ValidationInfo) -> str:
        """Strip and validate non-empty display label fields."""
        return ToolValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(Keys.Fields.TAGS, mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> frozenset[str]:
        """Coerce tag input to a frozenset of lowercase slug strings."""
        return ToolValueNormalizer.normalize_slug_set(value, Keys.Fields.TAGS)

    @field_validator(Keys.Fields.REQUIRED_SCOPES, mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        """Coerce required-scopes input to a frozenset of valid scope strings."""
        return ToolValueNormalizer.normalize_scope_set(
            value, Keys.Fields.REQUIRED_SCOPES
        )


class ToolPermissionPolicy(RuntimeContract):
    """Authorization metadata rechecked when a full tool spec is loaded."""

    connector: str
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    requires_confirmation: bool = False

    @field_validator(Keys.Fields.CONNECTOR)
    @classmethod
    def _normalize_connector(cls, value: object) -> str:
        """Coerce connector to a stable slug identifier."""
        return ToolValueNormalizer.normalize_slug(value, Keys.Fields.CONNECTOR)

    @field_validator(Keys.Fields.REQUIRED_SCOPES, mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        """Coerce required-scopes input to a frozenset of valid scope strings."""
        return ToolValueNormalizer.normalize_scope_set(
            value, Keys.Fields.REQUIRED_SCOPES
        )

    @model_validator(mode="after")
    def _risky_tools_require_confirmation(self) -> "ToolPermissionPolicy":
        """Reject high-risk tool specs that omit explicit confirmation."""
        if self.risk_level in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL}:
            if not self.requires_confirmation:
                msg = Messages.Validation.HIGH_RISK_CONFIRMATION_REQUIRED
                raise ValueError(msg)
        return self


class LoadedToolSpec(RuntimeContract):
    """Full validated tool contract loaded only after explicit selection."""

    name: str
    description: str = Field(
        min_length=1, max_length=Limits.TOOL_DESCRIPTION_MAX_LENGTH
    )
    args_schema: JsonSchema
    return_schema: JsonSchema
    side_effects: frozenset[ToolSideEffect] = Field(
        default_factory=lambda: frozenset({ToolSideEffect.READ})
    )
    timeout_ms: PositiveInt = Field(le=Limits.TOOL_TIMEOUT_MAX_MS)
    permission_policy: ToolPermissionPolicy

    @field_validator(Keys.Fields.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        """Coerce tool name to a stable slug identifier."""
        return ToolValueNormalizer.normalize_slug(value, Keys.Fields.NAME)

    @field_validator(Keys.Fields.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: str) -> str:
        """Strip and validate the tool description."""
        return ToolValueNormalizer.normalize_nonempty_string(
            value, Keys.Fields.DESCRIPTION
        )

    @field_validator(Keys.Fields.ARGS_SCHEMA, Keys.Fields.RETURN_SCHEMA)
    @classmethod
    def _validate_json_schema(
        cls, value: JsonSchema, info: ValidationInfo
    ) -> JsonSchema:
        """Validate that schema fields are JSON-serialisable mappings with a ``type`` key."""
        return ToolSchemaValidator.validate_json_schema(value, info.field_name)


class ToolLoadRequest(RuntimeContract):
    """Request to resolve a selected compact card into a full spec."""

    tool_name: str
    runtime_context: AgentRuntimeContext

    @field_validator(Keys.Fields.TOOL_NAME)
    @classmethod
    def _normalize_tool_name(cls, value: object) -> str:
        """Coerce the requested tool name to a stable slug."""
        return ToolValueNormalizer.normalize_slug(value, Keys.Fields.TOOL_NAME)


class ToolLoadError(RuntimeContract):
    """Safe, typed error returned by the dynamic tool loader."""

    code: ToolLoadErrorCode
    safe_message: str = Field(min_length=1, max_length=Limits.PUBLIC_ERROR_MAX_LENGTH)
    retryable: bool = False
    tool_name: str | None = None
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator(Keys.Fields.SAFE_MESSAGE)
    @classmethod
    def _normalize_safe_message(cls, value: str) -> str:
        """Strip and validate the public-facing error message."""
        return ToolValueNormalizer.normalize_nonempty_string(
            value, Keys.Fields.SAFE_MESSAGE
        )

    @field_validator(Keys.Fields.TOOL_NAME)
    @classmethod
    def _normalize_optional_tool_name(cls, value: str | None) -> str | None:
        """Coerce optional tool name to a stable slug, or pass through ``None``."""
        if value is None:
            return None
        return ToolValueNormalizer.normalize_slug(value, Keys.Fields.TOOL_NAME)


class ToolLoadResult(RuntimeContract):
    """Result envelope containing either a loaded spec or a typed error."""

    loaded_spec: LoadedToolSpec | None = None
    error: ToolLoadError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> "ToolLoadResult":
        """Enforce that exactly one of ``loaded_spec`` or ``error`` is set."""
        if (self.loaded_spec is None) == (self.error is None):
            msg = Messages.Validation.TOOL_LOAD_RESULT_EXACTLY_ONE_OUTCOME
            raise ValueError(msg)
        return self

    @classmethod
    def ok(cls, loaded_spec: LoadedToolSpec) -> "ToolLoadResult":
        """Return a successful load result wrapping ``loaded_spec``."""
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
        """Return a failure load result with a typed ``ToolLoadError``."""
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
        """Return ``True`` when the load completed without error."""
        return self.loaded_spec is not None


class ToolValueNormalizer:
    """Normalization helpers used by Pydantic validators.

    All common methods delegate to the shared ``ValueNormalizer``.
    """

    from agent_runtime.validation import ValueNormalizer as _V

    normalize_nonempty_string = _V.normalize_nonempty_string
    normalize_slug = _V.normalize_slug
    normalize_slug_set = _V.normalize_slug_set
    normalize_scope = _V.normalize_scope
    normalize_scope_set = _V.normalize_scope_set
    coerce_iterable = _V.coerce_iterable

    del _V


class ToolSchemaValidator:
    """JSON-schema compatibility validation for loaded tool specs."""

    @classmethod
    def validate_json_schema(cls, value: JsonSchema, field_name: str) -> JsonSchema:
        """Validate that ``value`` is a JSON-serialisable mapping with a ``type`` key."""
        if not isinstance(value, Mapping):
            msg = Messages.Validation.json_schema_object(field_name)
            raise ValueError(msg)
        if Keys.Schema.TYPE not in value:
            msg = Messages.Validation.schema_type_required(field_name)
            raise ValueError(msg)
        try:
            encoded = json.dumps(value, sort_keys=True)
        except (TypeError, ValueError) as exc:
            msg = Messages.Validation.json_serializable(field_name)
            raise ValueError(msg) from exc
        if len(encoded.encode(Keys.Encoding.UTF_8)) > Limits.TOOL_SCHEMA_MAX_BYTES:
            msg = Messages.Validation.schema_size_exceeded(field_name)
            raise ValueError(msg)
        return dict(value)
