"""In-memory :class:`ShareSnapshotPort` (PR 6.2).

Lives here, not under ``runtime_adapters.in_memory.runtime_api_store``,
because the share table is owned by PR 6.1 and a thin standalone
adapter avoids coupling the conversation store to a not-yet-shipped
table. Tests for the fork service inject this directly.

When PR 6.1 lands its ``conversation_shares`` writer it will populate
real ``ShareSnapshot`` rows; until then the test harness uses
``register`` / ``revoke`` to wire deterministic fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone

from runtime_api.schemas import ShareSnapshot


class InMemoryShareSnapshotStore:
    """Token-keyed lookup with revocation + expiry filtering."""

    def __init__(self) -> None:
        self._by_token: dict[str, ShareSnapshot] = {}
        self._revoked: set[str] = set()
        self._expires_at: dict[str, datetime] = {}

    def register(
        self,
        *,
        token: str,
        snapshot: ShareSnapshot,
        expires_at: datetime | None = None,
    ) -> None:
        """Wire a ``share_token -> ShareSnapshot`` entry for tests."""

        self._by_token[token] = snapshot
        if expires_at is not None:
            self._expires_at[token] = expires_at

    def revoke(self, token: str) -> None:
        self._revoked.add(token)

    async def resolve_by_token(self, share_token: str) -> ShareSnapshot | None:
        if share_token in self._revoked:
            return None
        snapshot = self._by_token.get(share_token)
        if snapshot is None:
            return None
        expires_at = self._expires_at.get(share_token)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return None
        return snapshot
