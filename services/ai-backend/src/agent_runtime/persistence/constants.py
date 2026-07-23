"""Constants for durable runtime persistence contracts and migrations."""

from __future__ import annotations

import re


class Keys:
    """Stable field names shared by persistence contracts and adapters."""

    class Field:
        ACTION = "action"
        ACTOR_TYPE = "actor_type"
        AGGREGATE_ID = "aggregate_id"
        AGGREGATE_TYPE = "aggregate_type"
        APPROVAL_ID = "approval_id"
        ARTIFACTS = "artifacts"
        ASSISTANT_ID = "assistant_id"
        ATTEMPTS = "attempts"
        AVAILABLE_AT = "available_at"
        BYTE_SIZE = "byte_size"
        CAPABILITY_NAME = "capability_name"
        CAPABILITY_TYPE = "capability_type"
        CHECKPOINT_BLOB_REF = "checkpoint_blob_ref"
        CHECKPOINT_NAMESPACE = "checkpoint_namespace"
        CHECKPOINT_VERSION = "checkpoint_version"
        CHECKSUM = "checksum"
        COMMAND_ID = "command_id"
        CONSUMER_NAME = "consumer_name"
        CONTENT_REF = "content_ref"
        CONTENT_SUMMARY = "content_summary"
        CONVERSATION_ID = "conversation_id"
        CREATED_BY_RUN_ID = "created_by_run_id"
        EVENT_ID = "event_id"
        EVENT_TYPE = "event_type"
        EXTERNAL_REF = "external_ref"
        ID = "id"
        KIND = "kind"
        LOCK_EXPIRES_AT = "lock_expires_at"
        LOCKED_BY = "locked_by"
        METADATA = "metadata"
        MIME_TYPE = "mime_type"
        NAMESPACE_HASH = "namespace_hash"
        ORG_ID = "org_id"
        OUTCOME = "outcome"
        PATH = "path"
        PAYLOAD = "payload"
        PAYLOAD_REFS = "payload_refs"
        POLICY_ID = "policy_id"
        RESOURCE_ID = "resource_id"
        RESOURCE_TYPE = "resource_type"
        RUN_ID = "run_id"
        SCOPE_ID = "scope_id"
        SHA256 = "sha256"
        STORAGE_BACKEND = "storage_backend"
        STORAGE_URI = "storage_uri"
        SUBAGENT_NAME = "subagent_name"
        SUMMARY = "summary"
        TASK_ID = "task_id"
        THREAD_ID = "thread_id"
        TOOL_INVOCATION_ID = "tool_invocation_id"
        TOOL_NAME = "tool_name"
        TRACE_ID = "trace_id"
        UPDATED_BY_RUN_ID = "updated_by_run_id"
        USER_ID = "user_id"
        WORKER_ID = "worker_id"


class Values:
    """Known persistence values that should remain stable across adapters."""

    MIGRATION_ID = "0001_agent_runtime_persistence"
    SCHEMA_VERSION = 1

    class AggregateType:
        AGENT_RUN = "agent_run"
        APPROVAL = "approval"

    class EventType:
        APPROVAL_RESOLVED = "approval_resolved"
        RUN_CANCEL_REQUESTED = "run_cancel_requested"
        RUN_REQUESTED = "run_requested"
        # PRD-D2 — the durable command a stage approve enqueues; the worker-side
        # CommitEngine handler is its only consumer. The commit never runs inline
        # in the API (mirrors approval-resolution's "resume is never inline").
        STAGE_COMMIT_REQUESTED = "stage_commit_requested"


class Patterns:
    """Compiled validators for stable persistence identifiers."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    HASH = re.compile(r"^[a-f0-9]{64}$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Safe validation messages for persistence contracts."""

    class Validation:
        @classmethod
        def id_contains_unsupported_characters(cls, field_name: str) -> str:
            return f"{field_name} contains unsupported characters"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            return f"{field_name} must not be empty"

        @classmethod
        def sha256(cls, field_name: str) -> str:
            return f"{field_name} must be a lowercase sha256 hex digest"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            return f"{field_name} must be a string"
