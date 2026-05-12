"""Pydantic contracts for the runtime foundation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import TypeAlias
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.execution.ports import (
    McpRegistry,
    MemoryBackendFactory,
    SubagentCatalog,
    ToolRegistry,
)
from agent_runtime.observability.constants import Keys as ObservabilityKeys
from agent_runtime.observability.redactor import JsonObjectCoercer
from agent_runtime.observability.tracing import TraceContext
from agent_runtime.capabilities.skills.sources import SkillSourceConfig

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | dict[str, object] | list[object]
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
    RUN_WORKER_LOST = "run_worker_lost"


class RuntimeRunStatus(StrEnum):
    """Product-visible status for request-scoped runtime runs."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StreamEventSource(StrEnum):
    """Runtime subsystem that produced a stream event."""

    MAIN_AGENT = "main_agent"
    SUBAGENT = "subagent"
    TOOL = "tool"
    MCP = "mcp"
    SUMMARIZATION = "summarization"
    SYSTEM = "system"
    RUNTIME = "runtime"
    MODEL = "model"


class StreamEventType(StrEnum):
    """User-safe event types emitted by the runtime."""

    PROGRESS = "progress"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CUSTOM = "custom"
    LIFECYCLE = "lifecycle"
    SUBAGENT_UPDATE = "subagent_update"
    OBSERVATION = "observation"
    ERROR = "error"
    FINAL = "final"
    FINAL_RESPONSE = "final_response"


StreamSource = StreamEventSource


class ModelReasoningEffort(StrEnum):
    """Provider-neutral reasoning effort controls."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ModelReasoningSummary(StrEnum):
    """OpenAI reasoning summary output controls."""

    AUTO = "auto"
    CONCISE = "concise"
    DETAILED = "detailed"


class ModelReasoningDisplay(StrEnum):
    """Anthropic thinking display controls."""

    OMITTED = "omitted"
    SUMMARIZED = "summarized"


class ModelThinkingMode(StrEnum):
    """Anthropic thinking mode controls."""

    ENABLED = "enabled"
    ADAPTIVE = "adaptive"


class ModelReasoningConfig(RuntimeContract):
    """Provider-neutral reasoning and thinking controls."""

    enabled: bool = True
    effort: ModelReasoningEffort | None = None
    summary: ModelReasoningSummary | None = None
    display: ModelReasoningDisplay | None = None
    budget_tokens: PositiveInt | None = Field(default=None, le=2_000_000)
    include_encrypted_content: bool = False
    thinking_mode: ModelThinkingMode | None = None

    @model_validator(mode="after")
    def _validate_thinking_budget(self) -> "ModelReasoningConfig":
        if (
            self.thinking_mode is ModelThinkingMode.ADAPTIVE
            and self.budget_tokens is not None
        ):
            msg = "budget_tokens cannot be set when thinking_mode is adaptive"
            raise ValueError(msg)
        return self


class ModelConfig(RuntimeContract):
    """Model settings selected before the runtime is constructed."""

    provider: str
    model_name: str = Field(min_length=1, max_length=200)
    max_input_tokens: PositiveInt = Field(le=2_000_000)
    timeout_seconds: float = Field(gt=0, le=600)
    temperature: float = Field(ge=0, le=2)
    supports_streaming: bool = True
    reasoning: ModelReasoningConfig | None = None

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
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
        return JsonObjectCoercer.coerce(value)  # type: ignore[return-value]


class RuntimeRunHandle(RuntimeContract):
    """Small response returned once a product-owned run has been created."""

    request_id: str
    run_id: str
    trace_id: str
    status: RuntimeRunStatus = RuntimeRunStatus.ACCEPTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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


class CatalogSuggestionCard(RuntimeContract):
    """PR 4.4.7 Phase 2 — one catalog entry the agent may suggest.

    Materialised at run-create from
    ``backend.McpRegistryService.list_suggestible_connectors``. Tight
    Pydantic shape so the system prompt and discovery tool see the
    same fields without re-parsing the wire payload.
    """

    slug: str
    display_name: str
    description: str = ""
    scopes_summary: str | None = None
    brand_color: str | None = None
    # PR 4.4.7 follow-up — when True, install requires the user to
    # paste a pre-registered OAuth client first (vendor doesn't expose
    # RFC 8414 metadata or RFC 7591 dynamic client registration). The
    # discovery card stamps this onto the wire payload so the FE
    # routes Connect to the credentials form instead of running a
    # 1-click install + redirect.
    requires_pre_registered_client: bool = False

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        normalized = _normalize_nonempty_string(value, "slug").lower()
        if not normalized:
            msg = "slug must be a non-empty string"
            raise ValueError(msg)
        return normalized

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: str) -> str:
        return _normalize_nonempty_string(value, "display_name")


class AgentRuntimeContext(RuntimeContract):
    """Request-level identity, authorization, model, and trace context."""

    user_id: str
    org_id: str
    roles: frozenset[str]
    permission_scopes: frozenset[str] = Field(default_factory=frozenset)
    connector_scopes: dict[str, frozenset[str]] = Field(default_factory=dict)
    # PR 4.4.6.2 — server_ids the user explicitly paused for this run
    # (popover toggle ⇒ ``enabled_connectors[server_id] = null``). Kept
    # separate from ``connector_scopes`` because the latter only carries
    # *active* entries: an empty ``connector_scopes`` is ambiguous
    # between "no per-chat override" and "everything paused". This set
    # gives MCP permission gates and the call-tool path an explicit
    # signal so a paused MCP server is invisible AND unloadable AND
    # uncallable for the duration of this run.
    paused_connectors: frozenset[str] = Field(default_factory=frozenset)
    # PR 4.4.7 Phase 2 (Slice B) — catalog entries the agent may
    # surface as progressive-discovery suggestions in this run. These
    # are *uninstalled* connectors filtered server-side by
    # ``backend.McpRegistryService.list_suggestible_connectors`` (paused
    # excluded, user mutes excluded, catalog-level
    # ``discoverable=false`` excluded unless user overrode). Suggesting
    # a slug that isn't in this tuple is a no-op via the discovery
    # service's permission gate. Empty tuple ⇒ system prompt skips the
    # section and the agent doesn't surface any suggestions.
    suggested_connectors: tuple["CatalogSuggestionCard", ...] = Field(
        default_factory=tuple
    )
    model_profile: ModelConfig
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_trace_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    max_parallel_tasks: PositiveInt = Field(default=4, le=100)
    trace_metadata: JsonObject = Field(default_factory=dict)
    feature_flags: frozenset[FeatureFlag] = Field(default_factory=frozenset)
    # PR 4.3 — workspace-policy knobs resolved at run-create. Persisted
    # in ``agent_runs.runtime_context_json`` like every other field on
    # this context. Stored as a generic ``JsonObject`` here (rather than
    # a typed ``WorkspaceBehaviorOverrides``) because ``contracts`` lives
    # in ``agent_runtime/execution`` and importing the runtime_api
    # schema would invert the layering. Consumers (citation middleware,
    # safety middleware, model-call middleware) downcast to the typed
    # model via ``WorkspaceBehaviorOverrides.model_validate``.
    workspace_behavior_overrides: JsonObject = Field(default_factory=dict)
    # PR 8.0.5 — per-(org, user) runtime policies resolved at run-create
    # from backend's ``/internal/v1/policies/runtime`` aggregate route.
    # Same JsonObject pattern as ``workspace_behavior_overrides`` so
    # consumers downcast via the typed snapshot classes
    # (``ToolUsePolicySnapshot.from_response`` /
    # ``PrivacySettingsSnapshot.from_response``). Empty dict ⇒ "use
    # deployment defaults" — every consumer's snapshot factory accepts
    # absent keys without raising.
    user_policies_json: JsonObject = Field(default_factory=dict)

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
        # Keys here are connector ids from
        # ``ConversationConnectorScopes`` (e.g. ``"seed:linear"``) — the
        # conversation column accepts any non-empty trimmed string and
        # the runtime registry validates *existence*, not lexical
        # form. Lowercase + trim to match the historic case-insensitive
        # semantics, but accept colons / other characters that the
        # strict slug pattern would reject so an active per-chat scope
        # override for a catalog-seeded server actually materialises
        # onto the context instead of failing pydantic validation.
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = "connector_scopes must be a mapping of connector id to scopes"
            raise ValueError(msg)

        normalized: dict[str, frozenset[str]] = {}
        for connector, scopes in value.items():
            if not isinstance(connector, str) or not connector.strip():
                msg = "connector_scopes keys must be non-empty strings"
                raise ValueError(msg)
            connector_id = connector.strip().lower()
            normalized[connector_id] = _normalize_scope_set(
                scopes,
                f"connector_scopes.{connector_id}",
            )
        return normalized

    @field_validator("suggested_connectors", mode="before")
    @classmethod
    def _normalize_suggested_connectors(
        cls, value: object
    ) -> tuple["CatalogSuggestionCard", ...]:
        # Accept either a sequence of ``CatalogSuggestionCard`` (Python
        # caller path) or a sequence of dicts (HTTP-deserialised path).
        # ``None`` collapses to empty so older request shapes still
        # validate.
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            msg = "suggested_connectors must be an iterable of cards"
            raise ValueError(msg)
        try:
            iterable = list(value)  # type: ignore[arg-type]
        except TypeError as exc:
            msg = "suggested_connectors must be an iterable of cards"
            raise ValueError(msg) from exc
        cards: list[CatalogSuggestionCard] = []
        for item in iterable:
            if isinstance(item, CatalogSuggestionCard):
                cards.append(item)
            elif isinstance(item, Mapping):
                cards.append(CatalogSuggestionCard.model_validate(dict(item)))
            else:
                msg = "suggested_connectors items must be cards or dicts"
                raise ValueError(msg)
        return tuple(cards)

    @field_validator("paused_connectors", mode="before")
    @classmethod
    def _normalize_paused_connectors(cls, value: object) -> frozenset[str]:
        # Accept the conversation column's connector-id shape (any
        # non-empty trimmed string) rather than the strict
        # ``[a-z0-9_-]`` slug — server_ids include a ``seed:`` prefix
        # ("seed:linear"), and ``ConversationConnectorScopes`` is the
        # source of truth here per
        # ``ConnectorScopeValidator._coerce_connector_id``. Validating
        # connector existence is the runtime registry's job, not this
        # contract's.
        if value is None:
            return frozenset()
        if isinstance(value, (str, bytes)):
            msg = "paused_connectors must be an iterable of connector ids"
            raise ValueError(msg)
        try:
            iterable = list(value)  # type: ignore[arg-type]
        except TypeError as exc:
            msg = "paused_connectors must be an iterable of connector ids"
            raise ValueError(msg) from exc
        normalized: list[str] = []
        for item in iterable:
            if not isinstance(item, str) or not item.strip():
                msg = "paused_connectors items must be non-empty strings"
                raise ValueError(msg)
            normalized.append(item.strip())
        return frozenset(normalized)

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
        return JsonObjectCoercer.coerce(value)  # type: ignore[return-value]

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
    prior_tool_result_loader: object | None = None
    memory_backend_factory: MemoryBackendFactory
    subagent_catalog: SubagentCatalog
    # Optional read-only Deep Agents backend that exposes per-subagent execution
    # traces under `/subagents/<task_id>/`. Constructed by the run handler from
    # the event store + persistence ports; the factory wraps it with deepagents'
    # `CompositeBackend` so the supervisor's `ls` and `read_file` tools can read
    # it without affecting `/memories/`, `/skills/`, etc.
    subagent_artifacts_backend: object | None = None
    # Optional Deep Agents backend that captures `/drafts/<uuid>.md` writes
    # and persists them as versioned `runtime_drafts` rows. Constructed by the
    # run handler from the draft store + event producer; the factory routes
    # the `/drafts/` prefix to it so the agent's existing `write_file` /
    # `edit_file` tools produce Workspace-pane drafts without a new tool.
    drafts_backend: object | None = None

    @field_validator(
        "tool_registry",
        "mcp_registry",
        "memory_backend_factory",
        "subagent_catalog",
    )
    @classmethod
    def _validate_protocol(cls, value: object, info: ValidationInfo) -> object:
        method_by_field = {
            "tool_registry": "list_available_tools",
            "mcp_registry": "list_available_servers",
            "memory_backend_factory": "create",
            "subagent_catalog": "list_available_subagents",
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


class StreamEvent(RuntimeContract):
    """Normalized, redacted event emitted by the runtime."""

    event_id: str = Field(default_factory=TraceContext.event_id)
    source: StreamEventSource
    event_type: StreamEventType
    trace_id: str
    parent_task_id: str | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(ObservabilityKeys.Field.EVENT_ID, ObservabilityKeys.Field.TRACE_ID)
    @classmethod
    def _normalize_event_id(cls, value: object, info: ValidationInfo) -> str:
        return StreamValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(ObservabilityKeys.Field.PARENT_TASK_ID)
    @classmethod
    def _normalize_parent_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return StreamValueNormalizer.normalize_id(
            value, ObservabilityKeys.Field.PARENT_TASK_ID
        )

    @field_validator(
        ObservabilityKeys.Field.PAYLOAD,
        ObservabilityKeys.Field.METADATA,
        mode="before",
    )
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)  # type: ignore[return-value]


class StreamValueNormalizer:
    """Normalization helpers used by streaming Pydantic validators.

    All common methods delegate to the shared ``ValueNormalizer``.
    """

    from agent_runtime.validation import ValueNormalizer as _V

    normalize_nonempty_string = _V.normalize_nonempty_string
    normalize_id = _V.normalize_id
    normalize_slug = _V.normalize_slug

    del _V


def _normalize_nonempty_string(value: object, field_name: str) -> str:
    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_nonempty_string(value, field_name)


def _normalize_runtime_id(value: object, field_name: str) -> str:
    if value is None:
        return uuid4().hex
    normalized = _normalize_nonempty_string(value, field_name)
    if not _ID_PATTERN.fullmatch(normalized):
        msg = f"{field_name} contains unsupported characters"
        raise ValueError(msg)
    return normalized


def _normalize_slug(value: object, field_name: str) -> str:
    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_slug(value, field_name)


def _normalize_slug_set(
    value: object,
    field_name: str,
    *,
    require_non_empty: bool = False,
) -> frozenset[str]:
    from agent_runtime.validation import ValueNormalizer

    normalized = ValueNormalizer.normalize_slug_set(value, field_name)
    if require_non_empty and not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    return normalized


def _normalize_scope_set(value: object, field_name: str) -> frozenset[str]:
    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_scope_set(value, field_name)


def _normalize_scope(value: object, field_name: str) -> str:
    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_scope(value, field_name)


def _coerce_iterable(value: object, field_name: str) -> tuple[object, ...]:
    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.coerce_iterable(value, field_name)
