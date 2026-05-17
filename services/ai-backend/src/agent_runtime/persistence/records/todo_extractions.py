"""Persisted todo-extraction proposal records.

The extractor worker job (see ``runtime_worker/jobs/todo_extractor.py``) scans
a completed run's transcript for actionable items and persists a proposal row
per candidate. Proposals are never inserted into the public ``todos`` table —
the user must accept or reject each one. Accept calls the backend service's
public ``POST /v1/todos`` over the internal service-token path (P3-A1 owns
that wiring); reject simply transitions the row to ``rejected``.

This module owns the Pydantic v2 contract; storage adapters live under
``runtime_adapters/{in_memory,postgres}/todo_extraction_store.py``.

Field semantics:

- ``id`` — opaque proposal identifier returned to the client and used in
  accept/reject URLs.
- ``run_id`` / ``conversation_id`` — the source run that produced the
  proposal. Both are required so the UI can deep-link back to the chat
  excerpt the proposal came from.
- ``owner_user_id`` / ``org_id`` — owner-only authorization scope. Only the
  caller whose identity matches ``(org_id, owner_user_id)`` can list/accept/
  reject. Cross-tenant queries are guarded by the store's tenant-first
  index ordering.
- ``proposed_text`` — the action-item text the model produced. Treated as
  untrusted content; never logged.
- ``suggested_due`` — optional ISO date suggestion (no time component).
- ``suggested_project_id`` — optional project hint; ``None`` is the default.
- ``source_message_id`` — the assistant message id the excerpt came from.
- ``confidence_score`` — 0..1 model-reported confidence. Higher = stronger
  signal that the action belongs to the caller. The frontend may use this
  to default-check high-confidence rows in the preview sheet.
- ``state`` — lifecycle: ``pending`` (default) → ``accepted`` | ``rejected``.
- ``resolved_at`` — set once the row leaves ``pending``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, NonNegativeFloat, field_validator

from agent_runtime.execution.contracts import RuntimeContract


class TodoExtractionState(StrEnum):
    """Lifecycle states for a proposal row."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TodoExtractionRecord(RuntimeContract):
    """One persisted action-item proposal produced by the extractor job.

    Frozen and validate-on-assignment via :class:`RuntimeContract`; every
    mutation goes through the store's ``update_state`` which constructs a
    new instance.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    proposed_text: str = Field(min_length=1, max_length=2000)
    suggested_due: str | None = Field(default=None, max_length=10)
    suggested_project_id: str | None = Field(default=None, max_length=64)
    source_message_id: str | None = Field(default=None, max_length=128)
    confidence_score: NonNegativeFloat = Field(default=0.0, le=1.0)
    state: TodoExtractionState = TodoExtractionState.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None

    @field_validator("suggested_due")
    @classmethod
    def _validate_due(cls, value: str | None) -> str | None:
        """Enforce ``YYYY-MM-DD`` shape on the optional date suggestion."""
        if value is None:
            return value
        # We accept the date alone; full ISO-8601 datetimes are rejected
        # so the UI can render this as a calendar day without timezone
        # ambiguity. The user retimes on accept if they want a deadline.
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("suggested_due must be YYYY-MM-DD") from exc
        return value
