"""Pydantic contracts for subagent definitions, handoffs, results, and lifecycle state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeAlias
from uuid import uuid4

from pydantic import Field, PositiveInt, ValidationInfo, field_validator, model_validator

from agent_runtime.agent.contracts import AgentRuntimeContext, JsonScalar, RuntimeContract
from agent_runtime.subagents.constants import Defaults, Keys, Limits, Messages, Patterns, Values

OutputSchema: TypeAlias = Mapping[str, Any]
ResultMetadata: TypeAlias = Mapping[str, JsonScalar]


class SubagentTransport(StrEnum):
    """Supported subagent transport registrations."""

    ASGI = Values.Transport.ASGI
    HTTP = Values.Transport.HTTP


class AsyncTaskStatus(StrEnum):
    """Observable lifecycle states for async subagent tasks."""

    QUEUED = Values.Status.QUEUED
    RUNNING = Values.Status.RUNNING
    SUCCEEDED = Values.Status.SUCCEEDED
    FAILED = Values.Status.FAILED
    CANCELLED = Values.Status.CANCELLED
    TIMED_OUT = Values.Status.TIMED_OUT


class SubagentErrorCode(StrEnum):
    """Typed subagent failures safe for API and stream surfaces."""

    SUBAGENT_UNAVAILABLE = Values.ErrorCode.SUBAGENT_UNAVAILABLE
    CONCURRENCY_LIMIT_EXCEEDED = Values.ErrorCode.CONCURRENCY_LIMIT_EXCEEDED
    TIMEOUT = Values.ErrorCode.TIMEOUT
    CANCELLED = Values.ErrorCode.CANCELLED
    STALE_TASK_ID = Values.ErrorCode.STALE_TASK_ID
    MALFORMED_RESULT = Values.ErrorCode.MALFORMED_RESULT
    OVERSIZED_RESULT = Values.ErrorCode.OVERSIZED_RESULT
    RUNNER_ERROR = Values.ErrorCode.RUNNER_ERROR
    VALIDATION_ERROR = Values.ErrorCode.VALIDATION_ERROR


class RuntimeContextReference(RuntimeContract):
    """Compact context reference passed to a subagent instead of the full runtime context."""

    user_id: str
    org_id: str
    trace_id: str
    permission_scopes: frozenset[str] = Field(default_factory=frozenset)

    @field_validator(Keys.Field.USER_ID, Keys.Field.ORG_ID, Keys.Field.TRACE_ID)
    @classmethod
    def _normalize_id(cls, value: object, info: ValidationInfo) -> str:
        return SubagentValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.PERMISSION_SCOPES, mode="before")
    @classmethod
    def _normalize_permission_scopes(cls, value: object) -> frozenset[str]:
        return SubagentValueNormalizer.normalize_scope_set(value, Keys.Field.PERMISSION_SCOPES)

    @classmethod
    def from_context(cls, context: AgentRuntimeContext) -> "RuntimeContextReference":
        """Create a compact reference from the full request context."""

        return cls(
            user_id=context.user_id,
            org_id=context.org_id,
            trace_id=context.trace_id,
            permission_scopes=context.permission_scopes,
        )


class SubagentDefinition(RuntimeContract):
    """Model-visible subagent metadata used for supervisor selection."""

    name: str
    description: str = Field(
        min_length=Limits.DESCRIPTION_MIN_LENGTH,
        max_length=Limits.DESCRIPTION_MAX_LENGTH,
    )
    graph_id: str
    transport: SubagentTransport = SubagentTransport.ASGI
    tools: frozenset[str] = Field(default_factory=frozenset)
    skills: frozenset[str] = Field(default_factory=frozenset)
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    timeout_seconds: PositiveInt = Field(
        default=Defaults.SUBAGENT_TIMEOUT_SECONDS,
        le=Limits.TIMEOUT_MAX_SECONDS,
    )
    concurrency_limit: PositiveInt = Field(
        default=Defaults.SUBAGENT_CONCURRENCY_LIMIT,
        le=Limits.CONCURRENCY_LIMIT_MAX,
    )
    enabled: bool = True

    @field_validator(Keys.Field.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_slug(value, Keys.Field.NAME)

    @field_validator(Keys.Field.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_nonempty_string(value, Keys.Field.DESCRIPTION)

    @field_validator(Keys.Field.GRAPH_ID)
    @classmethod
    def _normalize_graph_id(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_id(value, Keys.Field.GRAPH_ID)

    @field_validator(
        Keys.Field.TOOLS,
        Keys.Field.SKILLS,
        mode="before",
    )
    @classmethod
    def _normalize_slug_set(cls, value: object, info: ValidationInfo) -> frozenset[str]:
        return SubagentValueNormalizer.normalize_slug_set(value, info.field_name)

    @field_validator(Keys.Field.REQUIRED_SCOPES, mode="before")
    @classmethod
    def _normalize_required_scopes(cls, value: object) -> frozenset[str]:
        return SubagentValueNormalizer.normalize_scope_set(value, Keys.Field.REQUIRED_SCOPES)


class SubagentOutputContract(RuntimeContract):
    """Small output contract included in a compact handoff."""

    format: str = Defaults.OUTPUT_FORMAT
    required_fields: frozenset[str] = Field(
        default_factory=lambda: frozenset(
            {
                Keys.Field.RESPONSE,
                Keys.Field.EXECUTION_SUMMARY,
                Keys.Field.PLAN_SUMMARY,
            }
        )
    )
    json_schema: OutputSchema | None = None

    @field_validator(Keys.Field.FORMAT)
    @classmethod
    def _normalize_format(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_slug(value, Keys.Field.FORMAT)

    @field_validator(Keys.Field.REQUIRED_FIELDS, mode="before")
    @classmethod
    def _normalize_required_fields(cls, value: object) -> frozenset[str]:
        return SubagentValueNormalizer.normalize_slug_set(value, Keys.Field.REQUIRED_FIELDS)

    @field_validator(Keys.Field.JSON_SCHEMA)
    @classmethod
    def _validate_json_schema(cls, value: OutputSchema | None) -> OutputSchema | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("json_schema must be a mapping")
        return value


class SubagentTask(RuntimeContract):
    """Compact subagent handoff that intentionally excludes raw conversation history."""

    objective: str = Field(min_length=1, max_length=Limits.TASK_TEXT_MAX_LENGTH)
    relevant_summary: str = Field(min_length=1, max_length=Limits.TASK_TEXT_MAX_LENGTH)
    constraints: tuple[str, ...] = Field(default_factory=tuple)
    runtime_context_ref: RuntimeContextReference
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    allowed_skills: frozenset[str] = Field(default_factory=frozenset)
    output_contract: SubagentOutputContract = Field(default_factory=SubagentOutputContract)

    @field_validator(Keys.Field.OBJECTIVE, Keys.Field.RELEVANT_SUMMARY)
    @classmethod
    def _normalize_task_text(cls, value: object, info: ValidationInfo) -> str:
        return SubagentValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(Keys.Field.CONSTRAINTS, mode="before")
    @classmethod
    def _normalize_constraints(cls, value: object) -> tuple[str, ...]:
        return tuple(
            SubagentValueNormalizer.normalize_nonempty_string(item, Keys.Field.CONSTRAINTS)
            for item in SubagentValueNormalizer.coerce_iterable(value, Keys.Field.CONSTRAINTS)
        )

    @field_validator(Keys.Field.ALLOWED_TOOLS, Keys.Field.ALLOWED_SKILLS, mode="before")
    @classmethod
    def _normalize_allowed_slugs(cls, value: object, info: ValidationInfo) -> frozenset[str]:
        return SubagentValueNormalizer.normalize_slug_set(value, info.field_name)


class SubagentArtifact(RuntimeContract):
    """Reference to a subagent-produced artifact without embedding large content."""

    name: str
    artifact_type: str = "text"
    reference: str

    @field_validator(Keys.Field.NAME, Keys.Field.ARTIFACT_TYPE)
    @classmethod
    def _normalize_slug_field(cls, value: object, info: ValidationInfo) -> str:
        return SubagentValueNormalizer.normalize_slug(value, info.field_name)

    @field_validator(Keys.Field.REFERENCE)
    @classmethod
    def _normalize_reference(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_nonempty_string(value, Keys.Field.REFERENCE)


class SubagentError(RuntimeContract):
    """Safe subagent error returned by lifecycle APIs and failed results."""

    code: SubagentErrorCode
    safe_message: str = Field(min_length=1, max_length=Limits.SAFE_MESSAGE_MAX_LENGTH)
    retryable: bool = False
    task_id: str | None = None
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator(Keys.Field.SAFE_MESSAGE)
    @classmethod
    def _normalize_safe_message(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_nonempty_string(value, Keys.Field.SAFE_MESSAGE)

    @field_validator(Keys.Field.TASK_ID)
    @classmethod
    def _normalize_optional_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return SubagentValueNormalizer.normalize_id(value, Keys.Field.TASK_ID)

    @field_validator(Keys.Field.CORRELATION_ID)
    @classmethod
    def _normalize_correlation_id(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_id(value, Keys.Field.CORRELATION_ID)


class SubagentResult(RuntimeContract):
    """Validated subagent output with both answer and execution summaries."""

    response: str | None = Field(default=None, max_length=Limits.RESULT_RESPONSE_MAX_LENGTH)
    execution_summary: str | None = Field(default=None, max_length=Limits.SUMMARY_MAX_LENGTH)
    plan_summary: str | None = Field(default=None, max_length=Limits.SUMMARY_MAX_LENGTH)
    artifacts: tuple[SubagentArtifact, ...] = Field(default_factory=tuple)
    recent_messages: tuple[str, ...] = Field(default_factory=tuple)
    error: SubagentError | None = None

    @field_validator(
        Keys.Field.RESPONSE,
        Keys.Field.EXECUTION_SUMMARY,
        Keys.Field.PLAN_SUMMARY,
    )
    @classmethod
    def _normalize_optional_text(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        return SubagentValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(Keys.Field.RECENT_MESSAGES, mode="before")
    @classmethod
    def _normalize_recent_messages(cls, value: object) -> tuple[str, ...]:
        messages = tuple(
            SubagentValueNormalizer.normalize_nonempty_string(
                item,
                Keys.Field.RECENT_MESSAGES,
            )
            for item in SubagentValueNormalizer.coerce_iterable(
                value,
                Keys.Field.RECENT_MESSAGES,
            )
        )
        if len(messages) > Limits.RECENT_MESSAGES_MAX_COUNT:
            raise ValueError("recent_messages exceeds the configured limit")
        for message in messages:
            if len(message) > Limits.RECENT_MESSAGE_MAX_LENGTH:
                raise ValueError("recent_messages contains an oversized message")
        return messages

    @model_validator(mode="after")
    def _validate_result_shape(self) -> "SubagentResult":
        if len(self.artifacts) > Limits.ARTIFACTS_MAX_COUNT:
            raise ValueError("artifacts exceeds the configured limit")
        has_response = self.response is not None
        has_error = self.error is not None
        if has_response == has_error:
            raise ValueError(Messages.Validation.EXACTLY_ONE_RESULT_OUTCOME)
        if has_response and (self.execution_summary is None or self.plan_summary is None):
            raise ValueError(Messages.Validation.RESULT_SUMMARIES_REQUIRED)
        return self

    @classmethod
    def ok(
        cls,
        *,
        response: str,
        execution_summary: str,
        plan_summary: str,
        artifacts: tuple[SubagentArtifact, ...] = (),
        recent_messages: tuple[str, ...] = (),
    ) -> "SubagentResult":
        """Create a successful result with required summaries."""

        return cls(
            response=response,
            execution_summary=execution_summary,
            plan_summary=plan_summary,
            artifacts=artifacts,
            recent_messages=recent_messages,
        )

    @classmethod
    def fail(
        cls,
        code: SubagentErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> "SubagentResult":
        """Create a failed result without exposing raw runner details."""

        return cls(
            error=SubagentError(
                code=code,
                safe_message=safe_message,
                retryable=retryable,
                task_id=task_id,
                correlation_id=correlation_id or uuid4().hex,
            )
        )


class AsyncSubagentLaunch(RuntimeContract):
    """Runner launch metadata used to create external async task state."""

    thread_id: str
    run_id: str
    status: AsyncTaskStatus = AsyncTaskStatus.RUNNING

    @field_validator(Keys.Field.THREAD_ID, Keys.Field.RUN_ID)
    @classmethod
    def _normalize_launch_id(cls, value: object, info: ValidationInfo) -> str:
        return SubagentValueNormalizer.normalize_id(value, info.field_name)

    @model_validator(mode="after")
    def _validate_launch_status(self) -> "AsyncSubagentLaunch":
        if self.status not in {AsyncTaskStatus.QUEUED, AsyncTaskStatus.RUNNING}:
            raise ValueError("launch status must be queued or running")
        return self


class AsyncTaskState(RuntimeContract):
    """Dedicated lifecycle metadata stored outside model-visible message history."""

    task_id: str
    subagent_name: str
    thread_id: str
    run_id: str
    status: AsyncTaskStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline_at: datetime | None = None

    @field_validator(Keys.Field.TASK_ID, Keys.Field.THREAD_ID, Keys.Field.RUN_ID)
    @classmethod
    def _normalize_state_id(cls, value: object, info: ValidationInfo) -> str:
        return SubagentValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.SUBAGENT_NAME)
    @classmethod
    def _normalize_subagent_name(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_slug(value, Keys.Field.SUBAGENT_NAME)

    @model_validator(mode="after")
    def _validate_timestamps(self) -> "AsyncTaskState":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be after created_at")
        if self.deadline_at is not None and self.deadline_at <= self.created_at:
            raise ValueError("deadline_at must be after created_at")
        return self


class AsyncTaskLifecycleResult(RuntimeContract):
    """Envelope for start/check/update/cancel/list lifecycle operations."""

    state: AsyncTaskState | None = None
    result: SubagentResult | None = None
    tasks: tuple[AsyncTaskState, ...] | None = None
    error: SubagentError | None = None

    @model_validator(mode="after")
    def _require_one_lifecycle_outcome(self) -> "AsyncTaskLifecycleResult":
        has_state = self.state is not None
        has_tasks = self.tasks is not None
        has_error = self.error is not None
        if sum((has_state, has_tasks, has_error)) != 1:
            raise ValueError(Messages.Validation.EXACTLY_ONE_LIFECYCLE_OUTCOME)
        if self.result is not None and not has_state:
            raise ValueError("lifecycle result payloads require task state")
        return self

    @classmethod
    def from_state(
        cls,
        state: AsyncTaskState,
        *,
        result: SubagentResult | None = None,
    ) -> "AsyncTaskLifecycleResult":
        """Create a state-bearing lifecycle response."""

        return cls(state=state, result=result)

    @classmethod
    def from_tasks(cls, tasks: tuple[AsyncTaskState, ...]) -> "AsyncTaskLifecycleResult":
        """Create a task-list lifecycle response."""

        return cls(tasks=tasks)

    @classmethod
    def fail(
        cls,
        code: SubagentErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> "AsyncTaskLifecycleResult":
        """Create a failed lifecycle response."""

        return cls(
            error=SubagentError(
                code=code,
                safe_message=safe_message,
                retryable=retryable,
                task_id=task_id,
                correlation_id=correlation_id or uuid4().hex,
            )
        )


class SubagentValueNormalizer:
    """Normalization helpers used by subagent Pydantic validators."""

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if len(normalized) > Limits.ID_MAX_LENGTH or not Patterns.ID.fullmatch(normalized):
            raise ValueError(Messages.Validation.id_contains_unsupported_characters(field_name))
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
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.SLUG.fullmatch(normalized):
            raise ValueError(Messages.Validation.stable_slug(field_name))
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
    def coerce_iterable(cls, value: object, field_name: str) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError(Messages.Validation.iterable_not_string(field_name))
        if not isinstance(value, Iterable):
            raise ValueError(Messages.Validation.iterable_required(field_name))
        return tuple(value)
