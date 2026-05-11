"""Constants and public messages for streaming observability."""

from __future__ import annotations


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


class UserContentKeys:
    """Payload keys whose values carry user-visible content.

    Strings under these keys (and any nested values inside them) bypass the
    `MAX_STREAM_FIELD_LENGTH` cap so chat replies, tool outputs, reasoning,
    and approval payloads render in full. Sensitive-key and sensitive-value
    scrubbing still apply.
    """

    KEYS = frozenset(
        {
            "message",
            "delta",
            "summary",
            "reason",
            "output",
            "content",
            "arguments",
            "args",
            "description",
        }
    )


# ``Patterns.SENSITIVE_KEY`` and ``Patterns.SENSITIVE_VALUE`` were
# removed in P11.2 (see docs/refactor/01b-redaction-exact-match-deny-keys.md).
# Key matching now uses an exact-match deny set in
# ``agent_runtime.observability.redactor.DENY_KEYS``. Value pattern
# scrubbing is gone entirely — sensitivity is tagged per-field via the
# ``Sensitive[]`` annotation (P11.3), not detected by content shape.
