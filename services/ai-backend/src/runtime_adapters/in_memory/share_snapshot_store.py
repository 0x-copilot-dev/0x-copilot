"""In-memory ``ShareSnapshotPort`` for tests and local development."""

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
        """Mark a share token as revoked; subsequent lookups return ``None``."""
        self._revoked.add(token)

    async def resolve_by_token(self, share_token: str) -> ShareSnapshot | None:
        """Return the snapshot for a token, or ``None`` if revoked or expired."""
        if share_token in self._revoked:
            return None
        snapshot = self._by_token.get(share_token)
        if snapshot is None:
            return None
        expires_at = self._expires_at.get(share_token)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return None
        return snapshot
