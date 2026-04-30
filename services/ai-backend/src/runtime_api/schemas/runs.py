"""Run lifecycle API schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field, NonNegativeInt, ValidationInfo, field_validator

from agent_runtime.agent.contracts import AgentRuntimeContext, JsonObject, RuntimeContract, RuntimeErrorEnvelope
from agent_runtime.api.constants import Keys, Values
from runtime_api.schemas.common import AgentRunStatus, RuntimeApiValueNormalizer


class CreateRunRequest(RuntimeContract):
    """Request to create a queued runtime run for one user message."""

    conversation_id: str
    user_input: str
    content_format: str = Values.DEFAULT_CONTENT_FORMAT
    idempotency_key: str | None = None
    runtime_context: AgentRuntimeContext
    request_options: JsonObject = Field(default_factory=dict)

    @field_validator(Keys.Field.CONVERSATION_ID)
    @classmethod
    def _normalize_conversation_id(cls, value: object) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, Keys.Field.CONVERSATION_ID)

    @field_validator(Keys.Field.USER_INPUT, "content_format")
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator("request_options", mode="before")
    @classmethod
    def _redact_request_options(cls, value: object) -> JsonObject:
        return RuntimeApiValueNormalizer.redact_json_object(value)



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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
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
        "user_message_id",
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

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
        return RuntimeApiValueNormalizer.normalize_id(value, Keys.Field.REQUESTED_BY_USER_ID)

    @field_validator(Keys.Field.REASON, mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, Keys.Field.REASON)



class CancelRunResponse(RuntimeContract):
    """Cancellation request result."""

    run_id: str
    status: AgentRunStatus
    cancel_requested_at: datetime | None = None
    latest_sequence_no: NonNegativeInt
