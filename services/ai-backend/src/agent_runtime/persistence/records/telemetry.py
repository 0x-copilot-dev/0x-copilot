"""Compression and capability telemetry records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, NonNegativeInt

from agent_runtime.execution.contracts import JsonObject, RuntimeContract


class CompressionEventRecord(RuntimeContract):
    """Redacted context compression telemetry."""

    compression_event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    before_tokens: NonNegativeInt
    after_tokens: NonNegativeInt
    strategy: str
    payload_refs: JsonObject = Field(default_factory=dict)
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CapabilitySnapshotRecord(RuntimeContract):
    """Model-visible capability summary available during a run."""

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    capability_type: str
    capability_name: str
    capability_version: str | None = None
    scopes: JsonObject = Field(default_factory=dict)
    risk_class: str | None = None
    summary: str
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
