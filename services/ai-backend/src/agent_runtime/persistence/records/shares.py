"""Persisted conversation share records.

A share is one row in ``conversation_shares`` granting an org's members
(workspace mode) or a named recipient set (specific mode) read-only access
to a conversation snapshot.

The bearer token is stored as ``sha256(plaintext)`` (same pattern as
``scim_tokens.token_hash`` on the backend service) — the plaintext is
returned to the creator exactly once at create time and never persisted.

Snapshot semantics: ``snapshot_at`` is immutable; the recipient endpoint
clamps message / event / citation reads to ``created_at <= snapshot_at``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import RuntimeContract


class ShareViewAccess(StrEnum):
    """Who can open a share."""

    WORKSPACE = "workspace"
    """Any member of the share's org. Recipients table is empty."""

    SPECIFIC = "specific"
    """Only listed users. ``conversation_share_recipients`` carries the allow-list."""


class ShareRecord(RuntimeContract):
    """One persisted conversation share row.

    Mutable state: ``revoked_at`` (set on revoke; idempotent), recipient
    membership (managed via the join table), ``expires_at`` /
    ``sources_visible_to_viewer`` (PATCH).

    Immutable: ``share_id``, ``snapshot_at``, ``share_token_hash`` /
    ``share_token_prefix`` (rotating the token = new share row).
    """

    share_id: str = Field(min_length=1, max_length=64)
    org_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    created_by_user_id: str = Field(min_length=1)
    view_access: ShareViewAccess = ShareViewAccess.WORKSPACE
    sources_visible_to_viewer: bool = False
    # ``share_token_hash`` is sha256(plaintext) — see
    # ``agent_runtime.api.share_token``. NULL on people-only shares
    # (no copy-link). ``share_token_prefix`` is the UI hint; both are
    # set or both are null (constraint enforced at the SQL layer).
    share_token_hash: str | None = None
    share_token_prefix: str | None = None
    snapshot_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("share_token_prefix", mode="after")
    @classmethod
    def _hash_prefix_consistency(cls, value: str | None, info: object) -> str | None:
        # Normalise empty strings to None so the SQL CHECK constraint never
        # sees an empty string. Cross-field consistency (token_hash ↔ prefix)
        # is enforced at the SQL layer, not here.
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def has_link(self) -> bool:
        """Return ``True`` when a copy-link bearer token is set on this share."""
        return self.share_token_hash is not None

    def is_revoked(self) -> bool:
        """Return ``True`` when ``revoked_at`` has been stamped."""
        return self.revoked_at is not None

    def is_expired(self, *, now: datetime) -> bool:
        """Return ``True`` when ``expires_at`` is set and ``now`` has passed it."""
        return self.expires_at is not None and now >= self.expires_at

    def is_active(self, *, now: datetime) -> bool:
        """Return ``True`` when the share is neither revoked nor expired."""
        return not self.is_revoked() and not self.is_expired(now=now)


class ShareRecipientRecord(RuntimeContract):
    """One row in ``conversation_share_recipients``."""

    share_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    granted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
