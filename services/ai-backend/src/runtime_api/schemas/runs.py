"""Run lifecycle API schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import (
    ConfigDict,
    Field,
    NonNegativeInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    JsonObject,
    ModelReasoningConfig,
    RuntimeContract,
    RuntimeErrorEnvelope,
)
from agent_runtime.api.constants import Keys, Values
from agent_runtime.observability.redactor import JsonObjectCoercer
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import AgentRunStatus


class _Fields:
    PROVIDER = "provider"
    MODEL_NAME = "model_name"
    CONNECTOR_SCOPES = "connector_scopes"
    CONTEXT = "context"
    TRACE_METADATA = "trace_metadata"
    CONTENT_FORMAT = "content_format"
    REQUEST_OPTIONS = "request_options"
    USER_MESSAGE_ID = "user_message_id"


class ModelSelectionRequest(RuntimeContract):
    """Request-level model selection and safe runtime overrides."""

    provider: str | None = None
    model_name: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    max_input_tokens: int | None = Field(default=None, gt=0, le=2_000_000)
    supports_streaming: bool | None = None
    reasoning: ModelReasoningConfig | None = None

    @field_validator(_Fields.PROVIDER, mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, _Fields.PROVIDER)

    @field_validator(_Fields.MODEL_NAME, mode="before")
    @classmethod
    def _normalize_model_name(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, _Fields.MODEL_NAME)


class ModelCatalogItem(RuntimeContract):
    """Frontend-selectable model metadata."""

    id: str
    provider: str
    model_name: str
    name: str
    description: str | None = None
    configured: bool
    supports_streaming: bool = True
    supports_attachments: bool = False
    supports_reasoning: bool = False
    reasoning: JsonObject | None = None


class ModelCatalogResponse(RuntimeContract):
    """Available model profiles for the chat model selector."""

    default_model_id: str
    models: tuple[ModelCatalogItem, ...]


class FlexibleRuntimePayload(RuntimeContract):
    """Typed known fields while preserving client/runtime extension metadata."""

    model_config = ConfigDict(extra="allow", frozen=True, validate_assignment=True)


class RunContentPartRequest(FlexibleRuntimePayload):
    """Assistant UI content part sent with a run submission."""

    type: str
    text: str | None = None
    image: str | None = None
    data: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    name: str | None = None
    size: NonNegativeInt | None = None
    file_id: str | None = None
    url: str | None = None
    content: object | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RunAttachmentRequest(FlexibleRuntimePayload):
    """Attachment metadata and serialized content sent from the composer."""

    id: str
    type: str
    name: str
    content_type: str | None = None
    size: NonNegativeInt | None = None
    file_id: str | None = None
    url: str | None = None
    content: tuple[RunContentPartRequest, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)


class RunQuoteRequest(FlexibleRuntimePayload):
    """Selected text quote metadata included with a run submission."""

    text: str | None = None
    message_id: str | None = None
    part_index: NonNegativeInt | None = None
    start_index: NonNegativeInt | None = None
    end_index: NonNegativeInt | None = None
    source: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RunBranchMetadataRequest(FlexibleRuntimePayload):
    """Branch/edit/regenerate metadata for Assistant UI message actions."""

    branch_id: str | None = None
    parent_message_id: str | None = None
    source_message_id: str | None = None
    regenerate_from_message_id: str | None = None
    replace_from_message_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RuntimeRequestContext(RuntimeContract):
    """Optional request context used to build an internal runtime context."""

    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: JsonObject = Field(default_factory=dict)
    # PR 4.4.6.2 — server_ids the user explicitly paused for this run.
    # Populated by ``_apply_conversation_scope_fallback`` from the
    # conversation column's null entries; downstream
    # ``_request_with_runtime_context`` threads it onto
    # ``AgentRuntimeContext.paused_connectors`` so MCP gates see the
    # signal at every layer (visibility, load, call). Without the field
    # declared here, ``RuntimeContract.extra='forbid'`` would silently
    # drop it on ``model_copy(update=...)``, which was the original
    # leak.
    paused_connectors: tuple[str, ...] = ()
    context: JsonObject = Field(default_factory=dict)
    trace_metadata: JsonObject = Field(default_factory=dict)
    feature_flags: tuple[str, ...] = ()

    @field_validator(
        _Fields.CONNECTOR_SCOPES,
        _Fields.CONTEXT,
        _Fields.TRACE_METADATA,
        mode="before",
    )
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)

    @field_validator("paused_connectors", mode="before")
    @classmethod
    def _normalize_paused_connectors(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("paused_connectors must be an iterable")
        try:
            iterable = list(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("paused_connectors must be an iterable") from exc
        normalized: list[str] = []
        for item in iterable:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("paused_connectors items must be non-empty strings")
            normalized.append(item.strip())
        return tuple(normalized)


class CreateRunRequest(RuntimeContract):
    """Request to create a queued runtime run for one user message."""

    conversation_id: str
    org_id: str | None = None
    user_id: str | None = None
    user_input: str
    content_format: str = Values.DEFAULT_CONTENT_FORMAT
    idempotency_key: str | None = None
    model: ModelSelectionRequest | None = None
    content: tuple[RunContentPartRequest, ...] = ()
    attachments: tuple[RunAttachmentRequest, ...] = ()
    quote: RunQuoteRequest | None = None
    parent_message_id: str | None = None
    source_message_id: str | None = None
    regenerate_from_message_id: str | None = None
    branch_id: str | None = None
    branch: RunBranchMetadataRequest | None = None
    request_context: RuntimeRequestContext = Field(
        default_factory=RuntimeRequestContext
    )
    runtime_context: AgentRuntimeContext | None = Field(default=None, exclude=True)
    request_options: JsonObject = Field(default_factory=dict)

    @field_validator(Keys.Field.CONVERSATION_ID)
    @classmethod
    def _normalize_conversation_id(cls, value: object) -> str:
        return ValueNormalizer.normalize_id(value, Keys.Field.CONVERSATION_ID)

    @field_validator(Keys.Field.ORG_ID, Keys.Field.USER_ID, mode="before")
    @classmethod
    def _normalize_optional_identity(
        cls, value: object, info: ValidationInfo
    ) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(Keys.Field.USER_INPUT, _Fields.CONTENT_FORMAT)
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator(_Fields.REQUEST_OPTIONS, mode="before")
    @classmethod
    def _redact_request_options(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)

    @model_validator(mode="after")
    def _require_identity_or_context(self) -> "CreateRunRequest":
        if self.runtime_context is not None:
            raise ValueError(
                "runtime_context is server-owned and cannot be supplied by clients"
            )
        if self.org_id is None or self.user_id is None:
            raise ValueError(
                "org_id and user_id are required when runtime_context is omitted"
            )
        return self

    def quote_payload(self) -> JsonObject | None:
        """Return quote metadata as JSON for persistence and trace context."""

        if self.quote is None:
            return None
        return self.quote.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )

    def branch_payload(self) -> JsonObject | None:
        """Return branch metadata as JSON for persistence and trace context."""

        if self.branch is None:
            return None
        return self.branch.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )


class RunRecord(RuntimeContract):
    """Persisted runtime run state."""

    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    user_message_id: str
    idempotency_key: str | None = None
    trace_id: str
    status: AgentRunStatus = AgentRunStatus.QUEUED
    model_provider: str
    model_name: str
    runtime_context: AgentRuntimeContext
    request_options: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    safe_error: RuntimeErrorEnvelope | None = None
    latest_sequence_no: NonNegativeInt = 0

    @field_validator(
        Keys.Field.RUN_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.ORG_ID,
        Keys.Field.USER_ID,
        _Fields.USER_MESSAGE_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    def to_response(self) -> "RunStatusResponse":
        """Return the public run status shape."""

        return RunStatusResponse(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            user_id=self.user_id,
            status=self.status,
            trace_id=self.trace_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
            cancelled_at=self.cancelled_at,
            safe_error=self.safe_error,
            latest_sequence_no=self.latest_sequence_no,
        )


class CreateRunResponse(RuntimeContract):
    """Run handle returned after producer transaction commits."""

    run_id: str
    conversation_id: str
    user_message_id: str
    trace_id: str
    status: AgentRunStatus
    stream_url: str
    events_url: str
    created_at: datetime
    # Prior run ids in this conversation chain whose events feed cross-turn
    # context. Surfaced on the response so on-call can correlate a turn back
    # to the runs whose tool/subagent observations shaped its prompt.
    prior_run_ids: tuple[str, ...] = ()


class RunStatusResponse(RuntimeContract):
    """Current run status returned by run inspection and cancellation."""

    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: AgentRunStatus
    trace_id: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    safe_error: RuntimeErrorEnvelope | None = None
    latest_sequence_no: NonNegativeInt = 0


class CancelRunRequest(RuntimeContract):
    """Request to cancel long-running work."""

    reason: str | None = None
    requested_by_user_id: str

    @field_validator(Keys.Field.REQUESTED_BY_USER_ID)
    @classmethod
    def _normalize_requested_by_user_id(cls, value: object) -> str:
        return ValueNormalizer.normalize_id(value, Keys.Field.REQUESTED_BY_USER_ID)

    @field_validator(Keys.Field.REASON, mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, Keys.Field.REASON)


class CancelRunResponse(RuntimeContract):
    """Cancellation request result."""

    run_id: str
    status: AgentRunStatus
    cancel_requested_at: datetime | None = None
    latest_sequence_no: NonNegativeInt
