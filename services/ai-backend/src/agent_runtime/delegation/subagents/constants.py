"""Constants and safe messages for subagent delegation."""

from __future__ import annotations

import re


class Keys:
    """Stable keys used at subagent validation and lifecycle boundaries."""

    class Field:
        ALLOWED_SKILLS = "allowed_skills"
        ALLOWED_TOOLS = "allowed_tools"
        ARTIFACT_TYPE = "artifact_type"
        ARTIFACTS = "artifacts"
        CONSTRAINTS = "constraints"
        CONCURRENCY_LIMIT = "concurrency_limit"
        CORRELATION_ID = "correlation_id"
        CREATED_AT = "created_at"
        DEADLINE_AT = "deadline_at"
        DESCRIPTION = "description"
        ENABLED = "enabled"
        ERROR = "error"
        EXECUTION_SUMMARY = "execution_summary"
        FORMAT = "format"
        GRAPH_ID = "graph_id"
        JSON_SCHEMA = "json_schema"
        NAME = "name"
        OBJECTIVE = "objective"
        OUTPUT_CONTRACT = "output_contract"
        PERMISSION_SCOPES = "permission_scopes"
        PLAN_SUMMARY = "plan_summary"
        RECENT_MESSAGES = "recent_messages"
        REFERENCE = "reference"
        RELEVANT_SUMMARY = "relevant_summary"
        REQUIRED_FIELDS = "required_fields"
        REQUIRED_SCOPES = "required_scopes"
        RESPONSE = "response"
        RUN_ID = "run_id"
        RUNTIME_CONTEXT_REF = "runtime_context_ref"
        SAFE_MESSAGE = "safe_message"
        SKILLS = "skills"
        STATUS = "status"
        SUBAGENT_NAME = "subagent_name"
        TASK_ID = "task_id"
        THREAD_ID = "thread_id"
        TIMEOUT_SECONDS = "timeout_seconds"
        TOOLS = "tools"
        TRACE_ID = "trace_id"
        TRANSPORT = "transport"
        UPDATED_AT = "updated_at"
        USER_ID = "user_id"
        ORG_ID = "org_id"

    class Method:
        LIST_SUBAGENT_DEFINITIONS = "list_subagent_definitions"


class Values:
    """Stable string values exposed by subagent contracts and tests."""

    class ErrorCode:
        CANCELLED = "cancelled"
        CONCURRENCY_LIMIT_EXCEEDED = "concurrency_limit_exceeded"
        MALFORMED_RESULT = "malformed_result"
        OVERSIZED_RESULT = "oversized_result"
        RUNNER_ERROR = "runner_error"
        STALE_TASK_ID = "stale_task_id"
        SUBAGENT_UNAVAILABLE = "subagent_unavailable"
        TIMEOUT = "timeout"
        VALIDATION_ERROR = "validation_error"

    class OutputFormat:
        TEXT = "text"

    class Status:
        CANCELLED = "cancelled"
        FAILED = "failed"
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        TIMED_OUT = "timed_out"

    class Transport:
        ASGI = "asgi"
        HTTP = "http"


class Defaults:
    """Default task and lifecycle limits."""

    OUTPUT_FORMAT = Values.OutputFormat.TEXT
    SUBAGENT_TIMEOUT_SECONDS = 120
    SUBAGENT_CONCURRENCY_LIMIT = 2


class Limits:
    """Validation limits for model-visible subagent metadata and results."""

    ARTIFACTS_MAX_COUNT = 20
    DESCRIPTION_MAX_LENGTH = 500
    DESCRIPTION_MIN_LENGTH = 20
    ID_MAX_LENGTH = 200
    RECENT_MESSAGE_MAX_LENGTH = 2_000
    RECENT_MESSAGES_MAX_COUNT = 10
    RESULT_RESPONSE_MAX_LENGTH = 12_000
    SAFE_MESSAGE_MAX_LENGTH = 500
    SUMMARY_MAX_LENGTH = 4_000
    TASK_TEXT_MAX_LENGTH = 4_000
    TIMEOUT_MAX_SECONDS = 3_600
    CONCURRENCY_LIMIT_MAX = 100


class Patterns:
    """Compiled validators for stable IDs, slugs, and permission scopes."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    SCOPE = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)*$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Centralized public and validation messages for subagent delegation."""

    class Catalog:
        DEFINITIONS_LOAD_FAILED = "Subagent definitions could not be loaded."
        DUPLICATE_SUBAGENT_NAME = (
            "Multiple subagents are registered with the same name."
        )
        INVALID_CONTEXT = "Runtime context is invalid."
        INVALID_DEFINITION = "Subagent definition metadata is invalid."
        MISSING_LIST_DEFINITIONS = (
            "Subagent provider is missing list_subagent_definitions()."
        )
        REQUESTED_SUBAGENT_DISABLED = "Requested subagent is disabled."
        REQUESTED_SUBAGENT_DUPLICATE = (
            "Requested subagent name is registered more than once."
        )
        REQUESTED_SUBAGENT_UNKNOWN = "Requested subagent is not available."

    class Lifecycle:
        CANCELLED_TASK = "The subagent task has been cancelled."
        MALFORMED_RESULT = "The subagent returned an invalid result."
        OVERSIZED_RESULT = "The subagent returned too much output."
        RUNNER_ERROR = "The subagent task could not be updated right now."
        STALE_TASK_ID = "Subagent task ID is stale or unknown."
        SUBAGENT_UNAVAILABLE = "Requested subagent is not available."
        TASK_TIMEOUT = "The subagent task timed out."

    class Validation:
        EXACTLY_ONE_RESULT_OUTCOME = "subagent result must contain exactly one outcome"
        EXACTLY_ONE_LIFECYCLE_OUTCOME = (
            "lifecycle result must contain exactly one outcome"
        )
        RESULT_SUMMARIES_REQUIRED = (
            "successful subagent results require execution and plan summaries"
        )

        @classmethod
        def explicit_permission_scopes(cls, field_name: str) -> str:
            return f"{field_name} must contain explicit permission scopes"

        @classmethod
        def id_contains_unsupported_characters(cls, field_name: str) -> str:
            return f"{field_name} contains unsupported characters"

        @classmethod
        def iterable_not_string(cls, field_name: str) -> str:
            return f"{field_name} must be an iterable, not a string"

        @classmethod
        def iterable_required(cls, field_name: str) -> str:
            return f"{field_name} must be an iterable"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            return f"{field_name} must not be empty"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            return f"{field_name} must be a string"
