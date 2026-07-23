"""HTTP IO schemas for the Workspace-pane draft artifact."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_runtime.persistence.records import DraftStatus

__all__ = [
    "Draft",
    "DraftSection",
    "DraftListResponse",
    "DraftPatchRequest",
    "DraftSendRequest",
    "DraftSendResponse",
    "DraftDiscardRequest",
    "DraftStatus",
]


class DraftSection(BaseModel):
    """One section parsed out of a draft's markdown body for the FE renderer."""

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(default="", max_length=240)
    body: str = Field(default="")


class Draft(BaseModel):
    """One draft version returned to API callers.

    The wire shape is the projection of one ``runtime_drafts`` row plus the
    parsed ``sections`` array — kept here so the FE doesn't have to parse
    Markdown in the worker thread that handles every SSE frame.
    """

    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=32, max_length=32)
    version: PositiveInt
    conversation_id: str
    run_id: str | None = None
    user_id: str
    title: str = ""
    content_text: str = ""
    sections: tuple[DraftSection, ...] = ()
    target_connector: str | None = None
    target_metadata: dict[str, Any] | None = None
    citation_ids: tuple[str, ...] = ()
    status: DraftStatus = DraftStatus.DRAFT
    created_at: datetime


class DraftListResponse(BaseModel):
    """Latest version of every draft scoped to one conversation."""

    model_config = ConfigDict(extra="forbid")

    drafts: tuple[Draft, ...] = ()


class DraftPatchRequest(BaseModel):
    """User-edited replacement of a draft's body via edit-in-place."""

    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveInt
    content_text: str
    title: str | None = Field(default=None, max_length=240)


class DraftSendRequest(BaseModel):
    """Request to send a draft through a connector, gated by approval."""

    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveInt
    target_connector: str = Field(min_length=1, max_length=64)
    target_metadata: dict[str, Any] = Field(default_factory=dict)


class DraftSendResponse(BaseModel):
    """Response from a send-draft request — points the FE at the approval card."""

    model_config = ConfigDict(extra="forbid")

    draft: Draft
    # The approval emission lives behind the existing approval primitive;
    # surface its id so the FE can scroll to the inline ApprovalTool card.
    approval_id: str | None = None
    run_id: str | None = None
    # PRD-D1 (Generative Surfaces v2): when the flag is on, a send stages a write
    # instead of an approval row; ``stage_id`` binds the FE to the staged-draft
    # surface. ``None`` on the v1 path (byte-identical when the flag is off).
    stage_id: str | None = None


class DraftDiscardRequest(BaseModel):
    """Request to mark a draft as discarded (final). Soft-delete only."""

    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveInt
