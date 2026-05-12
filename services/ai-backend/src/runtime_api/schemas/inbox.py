"""Per-user inbox SSE wire schemas for approval-assigned and approval-resolved events."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract


class InboxEventEnvelopeSchema(RuntimeContract):
    """Wire schema for an envelope on the per-user inbox SSE channel.

    The FE consumes this through the same SSE parser as
    ``RuntimeEventEnvelope``; the data line is the JSON-serialised form
    of this model.
    """

    sequence_no: int = Field(ge=1)
    event_type: Literal["approval_assigned", "approval_resolved"]
    approval_id: str
    status: str
    org_id: str
    conversation_id: str
    actor_user_id: str
    emitted_at: datetime
