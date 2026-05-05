"""Persisted citation records (PR 1.1).

A :class:`CitationRecord` is the durable mirror of the wire ``CitationSourceRef``
emitted on ``source_ingested`` runtime events. The :class:`CitationLedger`
owns insertion through :class:`agent_runtime.persistence.ports.CitationStorePort`
and is the single seam for tools, provider adapters, and replay paths.

Idempotency is keyed on ``(run_id, source_connector, source_doc_id)``: a tool
that returns the same document twice in one run gets back the same
``citation_id`` without re-emitting an event or inserting a row.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract


class CitationRecord(RuntimeContract):
    """One durable citation row (denormalized for Sources-tab and ACL reads)."""

    citation_id: str = Field(min_length=2, max_length=16)
    run_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    ordinal: PositiveInt
    source_connector: str = Field(min_length=1, max_length=64)
    source_doc_id: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    snippet: str | None = Field(default=None, max_length=1024)
    freshness_at: datetime | None = None
    source_tool_call_id: str | None = Field(default=None, max_length=128)
    encryption_version: NonNegativeInt = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_wire_payload(self) -> dict[str, object]:
        """Return the JSON-serializable shape sent to the FE in event payloads."""

        return {
            "citation_id": self.citation_id,
            "source_connector": self.source_connector,
            "source_doc_id": self.source_doc_id,
            "source_url": self.source_url,
            "title": self.title,
            "snippet": self.snippet,
            "freshness_at": (
                self.freshness_at.isoformat() if self.freshness_at is not None else None
            ),
            "source_tool_call_id": self.source_tool_call_id,
            "ordinal": self.ordinal,
        }
