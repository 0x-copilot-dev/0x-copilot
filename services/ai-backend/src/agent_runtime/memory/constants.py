"""Constants, limits, and messages for context and memory management."""

from __future__ import annotations

import re


class Keys:
    """Stable field names used by memory contracts."""

    class Field:
        AFTER_TOKENS = "after_tokens"
        APPROVAL_REQUIRED = "approval_required"
        ARTIFACTS = "artifacts"
        ASSISTANT_ID = "assistant_id"
        BEFORE_TOKENS = "before_tokens"
        CONTENT = "content"
        DECISIONS = "decisions"
        EXPECTED_VERSION = "expected_version"
        FALLBACK_TRIGGER = "fallback_trigger"
        FILES_WRITTEN = "files_written"
        MAX_INPUT_TOKENS = "max_input_tokens"
        METADATA = "metadata"
        NAMESPACE = "namespace"
        NEXT_STEPS = "next_steps"
        OBJECTIVE = "objective"
        ORG_ID = "org_id"
        PATH = "path"
        PATH_PREFIX = "path_prefix"
        READ_ROLES = "read_roles"
        RECENT_CONTEXT_RATIO = "recent_context_ratio"
        REFERENCE = "reference"
        SCOPE_TYPE = "scope_type"
        SHARED = "shared"
        STRATEGY = "strategy"
        SUMMARY_THRESHOLD_RATIO = "summary_threshold_ratio"
        TRACE_ID = "trace_id"
        USER_ID = "user_id"
        WRITE_ROLES = "write_roles"


class Values:
    """Stable enum values exposed by memory contracts."""

    class ActorRole:
        APPLICATION = "application"
        ASSISTANT = "assistant"
        USER = "user"

    class CompressionStrategy:
        FALLBACK_SUMMARY = "fallback_summary"
        INLINE = "inline"
        OFFLOAD = "offload"
        SUMMARIZE = "summarize"

    class FallbackTrigger:
        CONTEXT_OVERFLOW = "context_overflow"
        SUMMARIZATION_FAILURE = "summarization_failure"

    class Operation:
        READ = "read"
        WRITE = "write"

    class Path:
        MEMORIES = "/memories/"
        POLICIES = "/policies/"
        SKILLS = "/skills/"

    class ScopeType:
        AGENT = "agent"
        ORGANIZATION = "organization"
        USER = "user"


class Defaults:
    """Default budget and routing values."""

    ASSISTANT_ID = "default"
    MAX_INPUT_TOKENS = 128_000
    RECENT_CONTEXT_RATIO = 0.25
    SUMMARY_THRESHOLD_RATIO = 0.85


class Limits:
    """Validation limits for memory paths, summaries, and metrics."""

    MEMORY_PATH_MAX_LENGTH = 500
    SUMMARY_FIELD_MAX_LENGTH = 4_000
    TRACE_ID_MAX_LENGTH = 200


class Patterns:
    """Compiled validators for stable IDs, paths, and sensitive content."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    MEMORY_PATH = re.compile(r"^/[A-Za-z0-9._:/-]+$")
    NAMESPACE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    PATH_PREFIX = re.compile(r"^/[A-Za-z0-9._-]+/$")
    SENSITIVE_KEY = re.compile(r"(api[_-]?key|authorization|password|secret|token)", re.I)
    SENSITIVE_VALUE = re.compile(
        r"(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+",
        re.I,
    )


class Messages:
    """Centralized validation and public messages for memory management."""

    class Errors:
        CONCURRENT_WRITE = "Memory was updated concurrently. Reload and retry the write."
        INVALID_CONTEXT_SUMMARY = "Context summary was empty or invalid."
        MEMORY_POLICY_DENIED = "Memory access was denied by policy."
        PROMPT_INJECTION_REJECTED = "Memory write was rejected by policy."

    class Validation:
        NAMESPACE_REQUIRED = "namespace must contain stable scope identifiers"
        PATH_TRAVERSAL_UNSUPPORTED = "memory paths must not contain traversal segments"
        USER_SCOPE_REQUIRES_USER_ID = "user memory scope requires user_id"
        AGENT_SCOPE_REQUIRES_ASSISTANT_ID = "agent memory scope requires assistant_id"

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
        def memory_path(cls, field_name: str) -> str:
            return f"{field_name} must be an absolute memory path"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            return f"{field_name} must not be empty"

        @classmethod
        def path_prefix(cls, field_name: str) -> str:
            return f"{field_name} must be an absolute path prefix ending in /"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            return f"{field_name} must be a string"
