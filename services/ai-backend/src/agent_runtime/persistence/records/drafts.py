"""Persisted draft artifact records.

Drafts are versioned, append-only artifacts that the agent produces by writing
to ``/drafts/{draft_id}.md`` through deepagents' built-in ``write_file`` /
``edit_file`` tools. The :class:`DraftBackend` adapter (under
``agent_runtime.capabilities.backends``) routes those writes here, persists a
new ``DraftRecord``, and emits a ``DRAFT_UPDATED`` runtime event.

The user can also patch a draft directly through the HTTP edit-in-place path;
those rows have ``run_id`` set to ``None`` and ``user_id`` set to the editor.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
)

from agent_runtime.execution.contracts import RuntimeContract


class DraftStatus(StrEnum):
    """Lifecycle states for a draft version."""

    DRAFT = "draft"
    SEND_PENDING_APPROVAL = "send_pending_approval"
    SENT = "sent"
    DISCARDED = "discarded"
    SEND_FAILED = "send_failed"


class DraftPath:
    """Constants and validators for the ``/drafts/{draft_id}.md`` path scheme."""

    PREFIX = "/drafts/"
    SUFFIX = ".md"
    DRAFT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
    PATH_RE = re.compile(r"^/drafts/([0-9a-f]{32})\.md$")

    @classmethod
    def parse_draft_id(cls, file_path: str) -> str | None:
        """Return the draft_id encoded in ``/drafts/{uuid}.md`` or ``None``."""

        match = cls.PATH_RE.match(file_path)
        return match.group(1) if match else None

    @classmethod
    def for_draft_id(cls, draft_id: str) -> str:
        """Return the canonical filesystem path for a given ``draft_id``."""

        return f"{cls.PREFIX}{draft_id}{cls.SUFFIX}"


class DraftRecord(RuntimeContract):
    """One persisted version of a draft artifact.

    Append-only: every successful ``awrite``/``aedit``/``patch``/``send`` /
    ``discard`` produces one new row with ``version = max_existing + 1``.
    Readers always select the row with the largest ``version`` for a given
    ``(org_id, draft_id)``.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    draft_id: str = Field(min_length=32, max_length=36)
    version: PositiveInt
    org_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    run_id: str | None = None
    user_id: str = Field(min_length=1)
    title: str = Field(default="", max_length=240)
    content_text: str = ""
    target_connector: str | None = None
    target_metadata: dict[str, object] = Field(default_factory=dict)
    citation_ids: tuple[str, ...] = ()
    status: DraftStatus = DraftStatus.DRAFT
    encryption_version: NonNegativeInt = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("draft_id", mode="before")
    @classmethod
    def _validate_draft_id(cls, value: str) -> str:
        # Accept any hex-uuid (with or without dashes) and normalize to 32-hex.
        try:
            normalized = UUID(value).hex
        except ValueError as exc:  # pragma: no cover - exercised in tests
            raise ValueError("draft_id must be a hex UUID") from exc
        return normalized

    @field_validator("citation_ids", mode="before")
    @classmethod
    def _coerce_citation_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise ValueError("citation_ids must be a list/tuple of strings")
