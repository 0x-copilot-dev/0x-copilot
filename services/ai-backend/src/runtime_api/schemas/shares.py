"""HTTP IO schemas for the conversation sharing lifecycle.

Two surfaces in this module:

- **Creator surface** — what the share-creator (chat owner / admin) sees.
  Tokens come back exactly once in the create response and are not stored in
  plain text.
- **Recipient surface** — what an authorised viewer sees on the
  ``/share/:token`` page. Read-only snapshot of the conversation; source
  restriction is applied by the service before payloads land in this envelope.
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agent_runtime.persistence.records import ShareViewAccess
from runtime_api.schemas.conversations import ConversationResponse, MessageResponse
from runtime_api.schemas.drafts import Draft
from runtime_api.schemas.events import RuntimeEventEnvelope
from runtime_api.schemas.workspace import SourceEntry, SubagentEntry


__all__ = [
    "ConversationShare",
    "CreateShareRequest",
    "CreateShareResponse",
    "ListSharesResponse",
    "RecipientPreview",
    "SharedByUser",
    "SharedConversationSummary",
    "SharedConversationView",
    "ShareViewAccess",
    "UpdateShareRequest",
]


_RECIPIENT_LIMIT = 200
"""Hard cap on ``recipient_user_ids`` per share. Beyond this we hint admins
to switch to ``view_access='workspace'``."""


class ConversationShare(BaseModel):
    """One share row as the creator sees it (no token plaintext)."""

    model_config = ConfigDict(extra="forbid")

    share_id: str = Field(min_length=1)
    share_token_prefix: str | None = None
    view_access: ShareViewAccess
    recipient_user_ids: tuple[str, ...] = ()
    sources_visible_to_viewer: bool
    snapshot_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    created_by_user_id: str
    created_at: AwareDatetime
    # Best-effort viewer count derived from audit (no row write per view
    # — the share row stays mutation-free for view events).
    view_count: int = Field(default=0, ge=0)


class CreateShareRequest(BaseModel):
    """``POST /v1/agent/conversations/{id}/share`` body."""

    model_config = ConfigDict(extra="forbid")

    view_access: ShareViewAccess = ShareViewAccess.WORKSPACE
    recipient_user_ids: tuple[str, ...] = ()
    sources_visible_to_viewer: bool = False
    expires_at: AwareDatetime | None = None
    include_link: bool = True

    @field_validator("recipient_user_ids", mode="before")
    @classmethod
    def _coerce_recipient_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            normalized: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("recipient_user_ids must be strings")
                stripped = item.strip()
                if not stripped:
                    raise ValueError("recipient_user_ids must be non-empty strings")
                normalized.append(stripped)
            return tuple(dict.fromkeys(normalized))  # de-dup, preserve order
        raise ValueError("recipient_user_ids must be a list of strings")

    @model_validator(mode="after")
    def _enforce_view_access_invariants(self) -> "CreateShareRequest":
        if self.view_access is ShareViewAccess.SPECIFIC:
            if not self.recipient_user_ids:
                raise ValueError(
                    "recipient_user_ids is required when view_access='specific'"
                )
        else:
            if self.recipient_user_ids:
                raise ValueError(
                    "recipient_user_ids must be empty when view_access='workspace'"
                )
        if len(self.recipient_user_ids) > _RECIPIENT_LIMIT:
            raise ValueError(
                f"recipient_user_ids exceeds the limit of {_RECIPIENT_LIMIT}"
            )
        return self


class CreateShareResponse(ConversationShare):
    """Same shape as :class:`ConversationShare` plus the **one-time** token + URL."""

    model_config = ConfigDict(extra="forbid")

    share_token: str = Field(
        min_length=1,
        description=(
            "Plaintext bearer token. Returned exactly ONCE at create time; "
            "the server only stores sha256(plaintext)."
        ),
    )
    share_url: str


class ListSharesResponse(BaseModel):
    """``GET /v1/agent/conversations/{id}/shares`` response."""

    model_config = ConfigDict(extra="forbid")

    shares: tuple[ConversationShare, ...] = ()


class UpdateShareRequest(BaseModel):
    """``PATCH /v1/agent/shares/{share_id}`` body — RFC 7396 merge-patch.

    Each field is optional; omit to leave the value untouched. Send ``null``
    on ``expires_at`` to explicitly clear the expiry. ``view_access`` is
    immutable (rotate by creating a new share).
    """

    model_config = ConfigDict(extra="forbid")

    sources_visible_to_viewer: bool | None = None
    expires_at: AwareDatetime | None = None
    recipient_user_ids: tuple[str, ...] | None = None
    # When ``True`` and ``expires_at`` is omitted, the existing expiry is
    # cleared. Distinguishes "leave alone" (omit) from "explicitly null"
    # — Pydantic can't tell those apart on a single nullable field.
    clear_expires_at: bool = False

    @field_validator("recipient_user_ids", mode="before")
    @classmethod
    def _coerce_recipient_ids(cls, value: object) -> tuple[str, ...] | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            normalized: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("recipient_user_ids must be strings")
                stripped = item.strip()
                if not stripped:
                    raise ValueError("recipient_user_ids must be non-empty strings")
                normalized.append(stripped)
            return tuple(dict.fromkeys(normalized))
        raise ValueError("recipient_user_ids must be a list of strings")

    @model_validator(mode="after")
    def _enforce_recipient_limit(self) -> "UpdateShareRequest":
        if (
            self.recipient_user_ids is not None
            and len(self.recipient_user_ids) > _RECIPIENT_LIMIT
        ):
            raise ValueError(
                f"recipient_user_ids exceeds the limit of {_RECIPIENT_LIMIT}"
            )
        return self


class SharedByUser(BaseModel):
    """Display chip for the share creator on the recipient view."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str | None = None


class SharedConversationSummary(BaseModel):
    """Compact share descriptor on the recipient page header."""

    model_config = ConfigDict(extra="forbid")

    share_id: str
    view_access: ShareViewAccess
    sources_visible_to_viewer: bool
    snapshot_at: AwareDatetime
    shared_by: SharedByUser


class RecipientPreview(BaseModel):
    """Resolved-but-not-yet-mounted share state surfaced before the recipient
    opens the heavy snapshot read.

    Returned by ``GET /v1/agent/shares/{share_token}/preview`` (light-weight
    endpoint that the FE hits before painting the full read-only thread —
    e.g. when deciding whether to show a "this share has been revoked"
    toast on a stale link).
    """

    model_config = ConfigDict(extra="forbid")

    share: SharedConversationSummary
    can_view: bool
    reason: Literal[
        "ok",
        "revoked",
        "expired",
        "foreign_org",
        "not_recipient",
        "share_not_found",
    ] = "ok"


class SharedConversationView(BaseModel):
    """Recipient-side payload — read-only snapshot of one conversation."""

    model_config = ConfigDict(extra="forbid")

    share: SharedConversationSummary
    conversation: ConversationResponse
    messages: tuple[MessageResponse, ...] = ()
    events_by_run_id: dict[str, tuple[RuntimeEventEnvelope, ...]] = Field(
        default_factory=dict
    )
    sources: tuple[SourceEntry, ...] = ()
    drafts: tuple[Draft, ...] = ()
    subagents: tuple[SubagentEntry, ...] = ()
