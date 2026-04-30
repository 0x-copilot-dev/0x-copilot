"""Pydantic contracts for the runtime foundation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
import re
from typing import Any, TypeAlias
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
)

from agent_runtime.execution.ports import (
    McpRegistry,
    MemoryBackendFactory,
    StreamNormalizer,
    SubagentCatalog,
    ToolRegistry,
)
from agent_runtime.observability.constants import Keys as ObservabilityKeys
from agent_runtime.observability.constants import Messages as ObservabilityMessages
from agent_runtime.observability.constants import Patterns as ObservabilityPatterns
from agent_runtime.observability.constants import Values as ObservabilityValues
from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.observability.tracing import TraceContext
from agent_runtime.capabilities.skills.sources import SkillSourceConfig

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)*$")

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | dict[str, JsonScalar] | list[JsonScalar]
JsonObject: TypeAlias = dict[str, JsonValue]


class RuntimeContract(BaseModel):
    """Base model for typed runtime boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class FeatureFlag(StrEnum):
    """Feature gates known to the runtime roadmap."""

    DYNAMIC_TOOL_LOADING = "dynamic_tool_loading"
    SKILLS_MIDDLEWARE = "skills_middleware"
    DYNAMIC_MCP_LOADING = "dynamic_mcp_loading"
    CONTEXT_MEMORY = "context_memory"
    SUBAGENTS = "subagents"
    STREAMING_OBSERVABILITY = "streaming_observability"


class RuntimeErrorCode(StrEnum):
    """Typed error classes safe for API and stream surfaces."""

    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    CAPABILITY_NOT_FOUND = "capability_not_found"
    CAPABILITY_LOAD_ERROR = "capability_load_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    CONFIGURATION_ERROR = "configuration_error"
    DEPENDENCY_ERROR = "dependency_error"
    RUNTIME_FACTORY_ERROR = "runtime_factory_error"


class RuntimeRunStatus(StrEnum):
    """Product-visible status for request-scoped runtime runs."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StreamEventSource(StrEnum):
    """Runtime subsystem that produced a stream event."""

    MAIN_AGENT = ObservabilityValues.Source.MAIN_AGENT
    SUBAGENT = ObservabilityValues.Source.SUBAGENT
    TOOL = ObservabilityValues.Source.TOOL
    MCP = ObservabilityValues.Source.MCP
    SUMMARIZATION = ObservabilityValues.Source.SUMMARIZATION
    SYSTEM = ObservabilityValues.Source.SYSTEM
    RUNTIME = ObservabilityValues.Source.RUNTIME
    MODEL = ObservabilityValues.Source.MODEL


class StreamEventType(StrEnum):
    """User-safe event types emitted by the runtime."""

    PROGRESS = ObservabilityValues.EventType.PROGRESS
    TOOL_CALL = ObservabilityValues.EventType.TOOL_CALL
    TOOL_RESULT = ObservabilityValues.EventType.TOOL_RESULT
    CUSTOM = ObservabilityValues.EventType.CUSTOM
    LIFECYCLE = ObservabilityValues.EventType.LIFECYCLE
    SUBAGENT_UPDATE = ObservabilityValues.EventType.SUBAGENT_UPDATE
    OBSERVATION = ObservabilityValues.EventType.OBSERVATION
    ERROR = ObservabilityValues.EventType.ERROR
    FINAL = ObservabilityValues.EventType.FINAL
    FINAL_RESPONSE = ObservabilityValues.EventType.FINAL_RESPONSE


StreamSource = StreamEventSource


class ModelConfig(RuntimeContract):
    """Model settings selected before the runtime is constructed."""

    provider: str
    model_name: str = Field(min_length=1, max_length=200)
    max_input_tokens: PositiveInt = Field(le=2_000_000)
    timeout_seconds: float = Field(gt=0, le=600)
    temperature: float = Field(ge=0, le=2)
    supports_streaming: bool = True

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _normalize_slug(value, "provider")

    @field_validator("model_name")
    @classmethod
    def _normalize_model_name(cls, value: str) -> str:
        normalized = _normalize_nonempty_string(value, "model_name")
        if len(normalized) > 200:
            msg = "model_name must be at most 200 characters"
            raise ValueError(msg)
        return normalized


class RuntimeRunContext(RuntimeContract):
    """Product-owned IDs propagated through LangGraph, traces, and logs."""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_trace_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("request_id", "run_id", "trace_id", mode="before")
    @classmethod
    def _normalize_required_id(cls, value: object, info: ValidationInfo) -> str:
        return _normalize_runtime_id(value, info.field_name)

    @field_validator("parent_trace_id", mode="before")
    @classmethod
    def _normalize_optional_parent_trace_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_id(value, "parent_trace_id")

    @field_validator("metadata", mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)  # type: ignore[return-value]


class RuntimeRunHandle(RuntimeContract):
    """Small response returned once a product-owned run has been created."""

    request_id: str
    run_id: str
    trace_id: str
    status: RuntimeRunStatus = RuntimeRunStatus.ACCEPTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("request_id", "run_id", "trace_id")
    @classmethod
    def _normalize_handle_id(cls, value: object, info: ValidationInfo) -> str:
        return _normalize_runtime_id(value, info.field_name)

    @classmethod
    def from_context(
        cls,
        context: "AgentRuntimeContext",
        *,
        status: RuntimeRunStatus = RuntimeRunStatus.ACCEPTED,
    ) -> "RuntimeRunHandle":
        return cls(
            request_id=context.request_id,
            run_id=context.run_id,
            trace_id=context.trace_id,
            status=status,
            created_at=context.started_at,
        )


class AgentRuntimeContext(RuntimeContract):
    """Request-level identity, authorization, model, and trace context."""

    user_id: str
    org_id: str
    roles: frozenset[str]
    permission_scopes: frozenset[str] = Field(default_factory=frozenset)
    connector_scopes: dict[str, frozenset[str]] = Field(default_factory=dict)
    model_profile: ModelConfig
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_trace_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_metadata: JsonObject = Field(default_factory=dict)
    feature_flags: frozenset[FeatureFlag] = Field(default_factory=frozenset)

    @field_validator("user_id", "org_id")
    @classmethod
    def _normalize_identity(cls, value: str, info: ValidationInfo) -> str:
        normalized = _normalize_nonempty_string(value, info.field_name)
        if not _ID_PATTERN.fullmatch(normalized):
            msg = f"{info.field_name} contains unsupported characters"
            raise ValueError(msg)
        return normalized

    @field_validator("roles", mode="before")
    @classmethod
    def _normalize_roles(cls, value: object) -> frozenset[str]:
        return _normalize_slug_set(value, "roles", require_non_empty=True)

    @field_validator("permission_scopes", mode="before")
    @classmethod
    def _normalize_permission_scopes(cls, value: object) -> frozenset[str]:
        return _normalize_scope_set(value, "permission_scopes")

    @field_validator("connector_scopes", mode="before")
    @classmethod
    def _normalize_connector_scopes(cls, value: object) -> dict[str, frozenset[str]]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = "connector_scopes must be a mapping of connector slug to scopes"
            raise ValueError(msg)

        normalized: dict[str, frozenset[str]] = {}
        for connector, scopes in value.items():
            connector_slug = _normalize_slug(connector, "connector_scopes key")
            normalized[connector_slug] = _normalize_scope_set(
                scopes,
                f"connector_scopes.{connector_slug}",
            )
        return normalized

    @field_validator("request_id", "run_id", "trace_id", mode="before")
    @classmethod
    def _normalize_runtime_identifier(cls, value: object, info: ValidationInfo) -> str:
        return _normalize_runtime_id(value, info.field_name)

    @field_validator("parent_trace_id", mode="before")
    @classmethod
    def _normalize_parent_trace_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_id(value, "parent_trace_id")

    @field_validator("trace_metadata", mode="before")
    @classmethod
    def _redact_trace_metadata(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)  # type: ignore[return-value]

    @property
    def run_context(self) -> RuntimeRunContext:
        """Return the request/run ID bundle used by graph config and observability."""

        return RuntimeRunContext(
            request_id=self.request_id,
            run_id=self.run_id,
            trace_id=self.trace_id,
            parent_trace_id=self.parent_trace_id,
            started_at=self.started_at,
            metadata=self.trace_metadata,
        )


class RuntimeDependencies(RuntimeContract):
    """Dependency-injected runtime ports.

    Concrete connectors, stores, MCP clients, and runners live behind these
    protocols so unit tests can use fakes and the runtime avoids vendor SDKs.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    tool_registry: ToolRegistry
    mcp_registry: McpRegistry
    skill_source_config: SkillSourceConfig
    skill_registry: object | None = None
    memory_backend_factory: MemoryBackendFactory
    subagent_catalog: SubagentCatalog
    stream_normalizer: StreamNormalizer

    @field_validator(
        "tool_registry",
        "mcp_registry",
        "memory_backend_factory",
        "subagent_catalog",
        "stream_normalizer",
    )
    @classmethod
    def _validate_protocol(cls, value: object, info: ValidationInfo) -> object:
        method_by_field = {
            "tool_registry": "list_available_tools",
            "mcp_registry": "list_available_servers",
            "memory_backend_factory": "create",
            "subagent_catalog": "list_available_subagents",
            "stream_normalizer": "normalize",
        }
        method_name = method_by_field[info.field_name]
        if not callable(getattr(value, method_name, None)):
            msg = f"{info.field_name} must provide callable {method_name}()"
            raise ValueError(msg)
        return value


class RuntimeErrorEnvelope(RuntimeContract):
    """User-safe serialized runtime error."""

    code: RuntimeErrorCode
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool
    correlation_id: str

    @field_validator("safe_message")
    @classmethod
    def _normalize_safe_message(cls, value: str) -> str:
        return _normalize_nonempty_string(value, "safe_message")

    @field_validator("correlation_id")
    @classmethod
    def _normalize_correlation_id(cls, value: str) -> str:
        normalized = _normalize_nonempty_string(value, "correlation_id")
        if not _ID_PATTERN.fullmatch(normalized):
            msg = "correlation_id contains unsupported characters"
            raise ValueError(msg)
        return normalized

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        *,
        correlation_id: str | None = None,
        default_code: RuntimeErrorCode = RuntimeErrorCode.RUNTIME_FACTORY_ERROR,
        default_message: str = "The runtime could not complete the request safely.",
        retryable: bool = False,
    ) -> "RuntimeErrorEnvelope":
        from agent_runtime.execution.errors import AgentRuntimeError

        if isinstance(exc, AgentRuntimeError):
            return exc.to_envelope(correlation_id=correlation_id)

        return cls(
            code=default_code,
            safe_message=default_message,
            retryable=retryable,
            correlation_id=correlation_id or uuid4().hex,
        )


class ToolCallEvent(RuntimeContract):
    """Redacted details for a model-requested tool invocation."""

    tool_name: str
    call_id: str
    args: JsonObject = Field(default_factory=dict)
    status: str = ObservabilityValues.Status.PENDING

    @field_validator(ObservabilityKeys.Field.TOOL_NAME)
    @classmethod
    def _normalize_tool_name(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_slug(value, ObservabilityKeys.Field.TOOL_NAME)

    @field_validator(ObservabilityKeys.Field.CALL_ID)
    @classmethod
    def _normalize_call_id(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_id(value, ObservabilityKeys.Field.CALL_ID)

    @field_validator(ObservabilityKeys.Field.STATUS)
    @classmethod
    def _normalize_status(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_nonempty_string(
            value,
            ObservabilityKeys.Field.STATUS,
        )

    @field_validator(ObservabilityKeys.Field.ARGS, mode="before")
    @classmethod
    def _redact_args(cls, value: object) -> JsonObject:
        return StreamValueNormalizer.redact_json_object(value)


class ToolResultEvent(RuntimeContract):
    """Redacted tool result summary safe to expose in streams."""

    tool_name: str
    call_id: str
    status: str = ObservabilityValues.Status.COMPLETED
    output: JsonObject = Field(default_factory=dict)

    @field_validator(ObservabilityKeys.Field.TOOL_NAME)
    @classmethod
    def _normalize_tool_name(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_slug(value, ObservabilityKeys.Field.TOOL_NAME)

    @field_validator(ObservabilityKeys.Field.CALL_ID)
    @classmethod
    def _normalize_call_id(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_id(value, ObservabilityKeys.Field.CALL_ID)

    @field_validator(ObservabilityKeys.Field.STATUS)
    @classmethod
    def _normalize_status(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_nonempty_string(
            value,
            ObservabilityKeys.Field.STATUS,
        )

    @field_validator("output", mode="before")
    @classmethod
    def _redact_output(cls, value: object) -> JsonObject:
        return StreamValueNormalizer.redact_json_object(value)


class SubagentLifecycleEvent(RuntimeContract):
    """Subagent lifecycle update correlated to a parent supervisor task."""

    task_id: str
    subagent_name: str
    status: str
    summary: str | None = None

    @field_validator(ObservabilityKeys.Field.TASK_ID)
    @classmethod
    def _normalize_task_id(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_id(value, ObservabilityKeys.Field.TASK_ID)

    @field_validator(ObservabilityKeys.Field.SUBAGENT_NAME)
    @classmethod
    def _normalize_subagent_name(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_slug(
            value,
            ObservabilityKeys.Field.SUBAGENT_NAME,
        )

    @field_validator(ObservabilityKeys.Field.STATUS)
    @classmethod
    def _normalize_lifecycle_status(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_nonempty_string(
            value,
            ObservabilityKeys.Field.STATUS,
        )

    @field_validator(ObservabilityKeys.Field.SUMMARY)
    @classmethod
    def _normalize_optional_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return StreamValueNormalizer.normalize_nonempty_string(
            value,
            ObservabilityKeys.Field.SUMMARY,
        )


class ObservationEvent(RuntimeContract):
    """Trace-correlated metric or diagnostic observation."""

    metric_name: str
    value: float
    trace_id: str
    tags: JsonObject = Field(default_factory=dict)

    @field_validator(ObservabilityKeys.Field.METRIC_NAME)
    @classmethod
    def _normalize_metric_name(cls, value: object) -> str:
        normalized = StreamValueNormalizer.normalize_nonempty_string(
            value,
            ObservabilityKeys.Field.METRIC_NAME,
        ).lower()
        if not ObservabilityPatterns.METRIC.fullmatch(normalized):
            msg = ObservabilityMessages.Validation.metric_name(
                ObservabilityKeys.Field.METRIC_NAME,
            )
            raise ValueError(msg)
        return normalized

    @field_validator(ObservabilityKeys.Field.TRACE_ID)
    @classmethod
    def _normalize_trace_id(cls, value: object) -> str:
        return StreamValueNormalizer.normalize_id(value, ObservabilityKeys.Field.TRACE_ID)

    @field_validator(ObservabilityKeys.Field.TAGS, mode="before")
    @classmethod
    def _redact_tags(cls, value: object) -> JsonObject:
        return StreamValueNormalizer.redact_json_object(value)


class StreamEvent(RuntimeContract):
    """Normalized, redacted event emitted by the runtime."""

    event_id: str = Field(default_factory=TraceContext.event_id)
    source: StreamEventSource
    event_type: StreamEventType
    trace_id: str
    parent_task_id: str | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(ObservabilityKeys.Field.EVENT_ID, ObservabilityKeys.Field.TRACE_ID)
    @classmethod
    def _normalize_event_id(cls, value: object, info: ValidationInfo) -> str:
        return StreamValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(ObservabilityKeys.Field.PARENT_TASK_ID)
    @classmethod
    def _normalize_parent_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return StreamValueNormalizer.normalize_id(value, ObservabilityKeys.Field.PARENT_TASK_ID)

    @field_validator(
        ObservabilityKeys.Field.PAYLOAD,
        ObservabilityKeys.Field.METADATA,
        mode="before",
    )
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return StreamValueNormalizer.redact_json_object(value)


class StreamValueNormalizer:
    """Normalization helpers used by streaming Pydantic validators."""

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if not _ID_PATTERN.fullmatch(normalized):
            msg = f"{field_name} contains unsupported characters"
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_nonempty_string(cls, value: object, field_name: str) -> str:
        return _normalize_nonempty_string(value, field_name)

    @classmethod
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not ObservabilityPatterns.SLUG.fullmatch(normalized):
            msg = f"{field_name} must be a stable slug"
            raise ValueError(msg)
        return normalized

    @classmethod
    def redact_json_object(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)  # type: ignore[return-value]


def _normalize_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        msg = f"{field_name} must be a string"
        raise ValueError(msg)
    normalized = value.strip()
    if not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    return normalized


def _normalize_runtime_id(value: object, field_name: str) -> str:
    if value is None:
        return uuid4().hex
    normalized = _normalize_nonempty_string(value, field_name)
    if not _ID_PATTERN.fullmatch(normalized):
        msg = f"{field_name} contains unsupported characters"
        raise ValueError(msg)
    return normalized


def _normalize_slug(value: object, field_name: str) -> str:
    normalized = _normalize_nonempty_string(value, field_name).lower()
    if not _SLUG_PATTERN.fullmatch(normalized):
        msg = f"{field_name} must be a stable slug"
        raise ValueError(msg)
    return normalized


def _normalize_slug_set(
    value: object,
    field_name: str,
    *,
    require_non_empty: bool = False,
) -> frozenset[str]:
    values = _coerce_iterable(value, field_name)
    normalized = frozenset(_normalize_slug(item, field_name) for item in values)
    if require_non_empty and not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    return normalized


def _normalize_scope_set(value: object, field_name: str) -> frozenset[str]:
    values = _coerce_iterable(value, field_name)
    normalized = frozenset(_normalize_scope(item, field_name) for item in values)
    return normalized


def _normalize_scope(value: object, field_name: str) -> str:
    normalized = _normalize_nonempty_string(value, field_name).lower()
    if not _SCOPE_PATTERN.fullmatch(normalized):
        msg = f"{field_name} must contain explicit permission scopes"
        raise ValueError(msg)
    return normalized


def _coerce_iterable(value: object, field_name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        msg = f"{field_name} must be an iterable, not a string"
        raise ValueError(msg)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        msg = f"{field_name} must be an iterable"
        raise ValueError(msg) from exc
