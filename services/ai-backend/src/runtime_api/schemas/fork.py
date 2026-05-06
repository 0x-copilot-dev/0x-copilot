"""HTTP IO + share-snapshot contracts for the conversation fork mechanic (PR 6.2).

The fork endpoint (``POST /v1/agent/shares/{share_token}/fork``) consumes
a *share snapshot* — the resolved view of one ``conversation_shares`` row
plus the recipient gate's permitted user set. PR 6.1 owns the share
table + token resolution adapter; PR 6.2 ships the consumer contract
(:class:`ShareSnapshot` + :class:`ShareSnapshotPort`) so the fork can
land independently and the postgres adapter can wire in when 6.1 ships.

Three reasons this lives in a dedicated module rather than
``runtime_api/schemas/conversations.py``:

  1. The fork wire shape is small and self-contained — keeping it
     separate keeps ``conversations.py`` focused on the conversation
     row contracts.
  2. PR 6.1 will add a ``runtime_api/schemas/shares.py`` for the full
     share lifecycle; the share-snapshot Pydantic shape already named
     here is the shape PR 6.1 produces (it can re-export from this
     module so consumers stay stable across the two PRs).
  3. The :class:`ShareSnapshotPort` Protocol is a thin contract — one
     async method — that PR 6.1 implements without depending on
     anything PR 6.2 ships.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ForkRequest",
    "ForkResponse",
    "SelfForkRequest",
    "ShareSnapshot",
    "ShareSnapshotPort",
    "FORK_TITLE_MAX_LENGTH",
    "FORK_FOLDER_MAX_LENGTH",
    "RUNTIME_FORK_MAX_MESSAGES_DEFAULT",
]


# Same caps the ``UpdateConversationRequest`` (PR 1.6) enforces — keep
# the fork endpoint's validation aligned with the conversation patch
# semantics so a forked title that the recipient renames inside the
# chat surface validates identically.
FORK_TITLE_MAX_LENGTH = 240
FORK_FOLDER_MAX_LENGTH = 64

# Default cap on how many messages a single fork can copy. Bounded so a
# rogue 50k-message chat can't blow the request budget on copy. Operators
# tune via ``RUNTIME_FORK_MAX_MESSAGES`` env (read by the fork service).
RUNTIME_FORK_MAX_MESSAGES_DEFAULT = 500


class ForkRequest(BaseModel):
    """Body for ``POST /v1/agent/shares/{share_token}/fork``.

    Both fields are optional. ``title`` defaults to
    ``f"Forked from {source_title}"`` when omitted; ``folder`` defaults
    to NULL (no folder).
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=FORK_TITLE_MAX_LENGTH)
    folder: str | None = Field(default=None, max_length=FORK_FOLDER_MAX_LENGTH)


class ForkResponse(BaseModel):
    """Response shape for both the share-fork and self-fork endpoints.

    Carries the new conversation's id (the FE navigates to
    ``/?conversationId={conversation_id}``) plus enough context for the
    post-fork toast and the audit row the FE renders against the user
    inbox.

    Lineage is explicit but disjoint: share-forks set
    ``forked_from_share_id`` and leave ``forked_from_message_id`` NULL;
    self-forks (PR A3) do the inverse.
    """

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    parent_conversation_id: str
    forked_from_share_id: str | None = None
    forked_from_message_id: str | None = None
    fork_message_count: int = Field(ge=0)
    title: str | None = None
    folder: str | None = None
    created_at: datetime
    user_id: str


class SelfForkRequest(BaseModel):
    """Body for ``POST /v1/agent/conversations/{conversation_id}/fork`` (PR A3).

    The owner of a conversation forks from a specific message in their
    own thread (the "Retry from here" affordance). ``from_message_id``
    caps the message-slice copied into the new conversation; it MUST
    belong to the source conversation. ``title`` and ``folder`` follow
    the same shape as :class:`ForkRequest`.
    """

    model_config = ConfigDict(extra="forbid")

    from_message_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=FORK_TITLE_MAX_LENGTH)
    folder: str | None = Field(default=None, max_length=FORK_FOLDER_MAX_LENGTH)


class ShareSnapshot(BaseModel):
    """The minimum view of a ``conversation_shares`` row the fork needs.

    PR 6.1 owns the share row and its full lifecycle (create, list,
    revoke, recipient view). The fork only needs the audit pointer
    (``share_id``), the source location (``conversation_id``), the
    snapshot bound (``snapshot_at``), the org scope (``org_id``), and
    the recipient gate (``view_access`` + ``recipient_user_ids``).

    Cross-PR sequencing: PR 6.1 will re-export this exact name from
    ``runtime_api.schemas.shares`` (or wherever the full share IO lands)
    so the fork service's import doesn't shift.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    share_id: str
    org_id: str
    conversation_id: str
    snapshot_at: datetime
    view_access: str = Field(pattern=r"^(workspace|specific)$")
    recipient_user_ids: tuple[str, ...] = ()
    sources_visible_to_viewer: bool = False
    created_by_user_id: str


@runtime_checkable
class ShareSnapshotPort(Protocol):
    """Resolve a share by its bearer token.

    Implementations live in ``runtime_adapters/{in_memory,postgres}/``
    once PR 6.1 ships the ``conversation_shares`` table + token store.
    Until then, the in-memory adapter (this PR ships the in-memory
    impl in ``runtime_adapters.in_memory.share_snapshot_store``)
    provides a deterministic test harness for the fork service.

    The lookup is *org-agnostic*: a share token is a global secret;
    cross-org refusal is enforced at the service layer after the org
    scope on the snapshot row is known. Implementations MUST return
    ``None`` (not raise) for unknown / revoked / expired tokens — the
    fork service maps that uniformly to a 404.
    """

    async def resolve_by_token(self, share_token: str) -> ShareSnapshot | None:
        """Return the active share for ``share_token`` or ``None``."""
