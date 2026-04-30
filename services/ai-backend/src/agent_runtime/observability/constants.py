"""Constants and public messages for streaming observability."""

from __future__ import annotations

import re


class Keys:
    """Stable field names used by stream and observation contracts."""

    class Field:
        API_EVENT_TYPE = "api_event_type"
        ARGS = "args"
        CALL_ID = "call_id"
        EVENT_ID = "event_id"
        EVENT_TYPE = "event_type"
        METADATA = "metadata"
        METRIC_NAME = "metric_name"
        PARENT_TASK_ID = "parent_task_id"
        PAYLOAD = "payload"
        SOURCE = "source"
        STATUS = "status"
        SUBAGENT_NAME = "subagent_name"
        SUMMARY = "summary"
        TAGS = "tags"
        TASK_ID = "task_id"
        TOOL_NAME = "tool_name"
        TRACE_ID = "trace_id"
        VALUE = "value"

    class Raw:
        ARGS = "args"
        CHUNK = "chunk"
        CONTENT = "content"
        DATA = "data"
        EVENT = "event"
        EVENT_TYPE = "event_type"
        ID = "id"
        MESSAGE = "message"
        MESSAGES = "messages"
        METADATA = "metadata"
        MODE = "mode"
        NAME = "name"
        NAMESPACE = "namespace"
        NS = "ns"
        PARENT_TASK_ID = "parent_task_id"
        STATUS = "status"
        TASK_ID = "task_id"
        TOOL_CALLS = "tool_calls"
        TOOL_NAME = "tool_name"
        TRACE_ID = "trace_id"
        TYPE = "type"


class Values:
    """Stable enum values exposed by stream contracts."""

    class Source:
        MAIN_AGENT = "main_agent"
        MCP = "mcp"
        MODEL = "model"
        RUNTIME = "runtime"
        SUBAGENT = "subagent"
        SUMMARIZATION = "summarization"
        SYSTEM = "system"
        TOOL = "tool"

    class EventType:
        CUSTOM = "custom"
        ERROR = "error"
        FINAL = "final"
        FINAL_RESPONSE = "final_response"
        LIFECYCLE = "lifecycle"
        OBSERVATION = "observation"
        PROGRESS = "progress"
        SUBAGENT_UPDATE = "subagent_update"
        TOOL_CALL = "tool_call"
        TOOL_RESULT = "tool_result"

    class ApiEventType:
        MCP_AUTH_REQUIRED = "mcp_auth_required"
        REASONING_SUMMARY = "reasoning_summary"
        REASONING_SUMMARY_DELTA = "reasoning_summary_delta"
        SUBAGENT_COMPLETED = "subagent_completed"
        SUBAGENT_PROGRESS = "subagent_progress"
        SUBAGENT_STARTED = "subagent_started"
        TOOL_CALL_COMPLETED = "tool_call_completed"
        TOOL_CALL_DELTA = "tool_call_delta"
        TOOL_CALL_STARTED = "tool_call_started"
        TOOL_RESULT = "tool_result"

    class StreamMode:
        CUSTOM = "custom"
        DEBUG = "debug"
        MESSAGES = "messages"
        UPDATES = "updates"
        VALUES = "values"

    class Status:
        COMPLETED = "completed"
        EMITTED = "emitted"
        FAILED = "failed"
        PENDING = "pending"
        RECEIVED = "received"
        STARTED = "started"
        TRUNCATED = "truncated"
        UNKNOWN = "unknown"


class Defaults:
    """Default limits and placeholders for stream projection."""

    MAX_STREAM_FIELD_LENGTH = 2_000
    MAX_STREAM_PAYLOAD_CHARS = 4_000
    REDACTED = "[redacted]"
    TRUNCATED = "[truncated]"
    UNKNOWN_MODE = "unknown"


class Patterns:
    """Compiled validators for stable IDs, names, and sensitive fields."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    METRIC = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    SENSITIVE_KEY = re.compile(
        r"(api[_-]?key|authorization|credential|password|secret|token)",
        re.I,
    )
    SENSITIVE_VALUE = re.compile(
        r"(api[_-]?key|authorization|credential|password|secret|token)\s*[:=]\s*\S+",
        re.I,
    )


class Messages:
    """Centralized public and validation messages for stream observability."""

    class Events:
        MALFORMED_CHUNK = "A runtime stream event could not be normalized safely."
        UNKNOWN_STREAM_MODE = "Runtime emitted an unsupported stream mode."

    class Validation:
        @classmethod
        def id_contains_unsupported_characters(cls, field_name: str) -> str:
            return f"{field_name} contains unsupported characters"

        @classmethod
        def mapping_required(cls, field_name: str) -> str:
            return f"{field_name} must be a mapping"

        @classmethod
        def metric_name(cls, field_name: str) -> str:
            return f"{field_name} must be a stable metric name"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            return f"{field_name} must not be empty"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            return f"{field_name} must be a string"
