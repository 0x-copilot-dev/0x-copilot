"""C8 records: per-tenant retention policies + sweep evidence.

Each row in ``retention_policies`` is one (scope, resource_id, kind) policy
with a TTL in seconds. Most-specific policy wins at resolution time:
conversation > assistant > user > org > deployment-default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract


class RetentionScope(StrEnum):
    ORG = "org"
    USER = "user"
    CONVERSATION = "conversation"
    ASSISTANT = "assistant"


class RetentionKind(StrEnum):
    MESSAGES = "messages"
    EVENTS = "events"
    CONTEXT_PAYLOADS = "context_payloads"
    CHECKPOINTS = "checkpoints"
    MEMORY_ITEMS = "memory_items"


class RetentionPolicyRecord(RuntimeContract):
    """One retention policy row.

    ``resource_id`` is None when ``scope=ORG`` (the policy is tenant-wide).
    For ``USER`` / ``CONVERSATION`` / ``ASSISTANT`` it identifies the row
    in ``users`` / ``agent_conversations`` / the assistant slug.
    """

    id: str = Field(default_factory=lambda: f"rp_{uuid4().hex}")
    org_id: str
    scope: RetentionScope
    resource_id: str | None = None
    kind: RetentionKind
    ttl_seconds: PositiveInt
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RetentionSweepOutcome(RuntimeContract):
    """Per-kind tally returned by one sweeper pass for one org."""

    org_id: str
    kind: RetentionKind
    tombstoned: int = 0
    deleted: int = 0
    skipped_legal_hold: int = 0
