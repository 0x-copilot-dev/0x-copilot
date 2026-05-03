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


class Defaults:
    """Default limits and placeholders for stream projection."""

    MAX_STREAM_FIELD_LENGTH = 2_000
    REDACTED = "[redacted]"
    TRUNCATED = "[truncated]"


class Patterns:
    """Compiled validators for sensitive fields."""

    SENSITIVE_KEY = re.compile(
        r"(api[_-]?key|authorization|credential|password|secret|token)",
        re.I,
    )
    SENSITIVE_VALUE = re.compile(
        r"(api[_-]?key|authorization|credential|password|secret|token)\s*[:=]\s*\S+",
        re.I,
    )
