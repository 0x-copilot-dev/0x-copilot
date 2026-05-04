"""C9 exporter interface + normalized event shape."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class SiemExportSource(StrEnum):
    """Which audit source a normalized event came from."""

    MCP_AUDIT = "mcp_audit"
    IDENTITY_AUDIT = "identity_audit"
    RUNTIME_AUDIT_REMOTE = "runtime_audit_remote"


class NormalizedEvent(BaseModel):
    """Common shape exporters speak.

    The ``composite_id`` is what customers de-dup on (``{org_id}:{event_id}``).
    Per-source raw payloads ride in ``raw`` so SIEM operators can write
    custom parsers without us baking exporter-specific schemas into the
    normalizer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    composite_id: str
    source: SiemExportSource
    org_id: str | None
    user_id: str | None = None
    event_type: str
    timestamp: datetime
    severity: str = "INFO"
    payload: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class SendOutcome(StrEnum):
    """One of three terminal results for a single batch attempt."""

    OK = "ok"
    DEAD_LETTER = "dead_letter"  # 4xx-class — operator should inspect.
    RETRY = "retry"  # 5xx-class or transient — pump backs off.


class SendResult(BaseModel):
    """What an exporter returns from ``send(...)``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: SendOutcome
    last_error: str | None = None
    accepted_event_ids: tuple[str, ...] = ()
    rejected_event_ids: tuple[str, ...] = ()


class SiemExporter(Protocol):
    """Protocol every exporter implements."""

    name: str

    async def send(self, events: tuple[NormalizedEvent, ...]) -> SendResult:
        """Forward a batch. Must be idempotent on ``composite_id``.

        Implementations distinguish:

          - ``OK``           — entire batch was accepted (2xx).
          - ``DEAD_LETTER``  — 4xx-class; cursor advances past these events
                               and they're written to ``siem_export_dead_letters``.
          - ``RETRY``        — 5xx-class or transport error; cursor stays.
        """
