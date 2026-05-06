"""In-memory ``ShareStorePort`` for tests and local development (PR 6.1)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import RLock

from agent_runtime.persistence.records import ShareRecipientRecord, ShareRecord


class InMemoryShareStore:
    """Process-local conversation-share store with RLock-guarded mutations.

    Tests assert against ``self.shares`` / ``self.recipients`` directly when
    they need to inspect raw state.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        # share_id -> ShareRecord
        self.shares: dict[str, ShareRecord] = {}
        # share_id -> {user_id: ShareRecipientRecord}
        self.recipients: dict[str, dict[str, ShareRecipientRecord]] = {}

    async def insert_share(
        self,
        *,
        share: ShareRecord,
        recipients: Sequence[ShareRecipientRecord],
    ) -> ShareRecord:
        with self._lock:
            if share.share_id in self.shares:
                raise ValueError(f"share {share.share_id} already exists")
            if share.share_token_hash is not None:
                for existing in self.shares.values():
                    if existing.share_token_hash == share.share_token_hash:
                        raise ValueError(
                            f"share_token_hash collision on {share.share_id}"
                        )
            self.shares[share.share_id] = share
            self.recipients[share.share_id] = {
                recipient.user_id: recipient for recipient in recipients
            }
            return share

    async def get_by_id(self, *, org_id: str, share_id: str) -> ShareRecord | None:
        with self._lock:
            record = self.shares.get(share_id)
            if record is None or record.org_id != org_id:
                return None
            return record

    async def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        include_revoked: bool,
    ) -> Sequence[ShareRecord]:
        with self._lock:
            results = [
                share
                for share in self.shares.values()
                if share.org_id == org_id
                and share.conversation_id == conversation_id
                and (include_revoked or share.revoked_at is None)
            ]
            results.sort(key=lambda share: share.created_at, reverse=True)
            return tuple(results)

    async def find_by_token_hash(self, *, share_token_hash: str) -> ShareRecord | None:
        with self._lock:
            for share in self.shares.values():
                if share.share_token_hash == share_token_hash:
                    return share
            return None

    async def list_recipients(
        self, *, org_id: str, share_id: str
    ) -> Sequence[ShareRecipientRecord]:
        with self._lock:
            share = self.shares.get(share_id)
            if share is None or share.org_id != org_id:
                return ()
            recipients = self.recipients.get(share_id, {})
            return tuple(sorted(recipients.values(), key=lambda r: r.granted_at))

    async def replace_recipients(
        self,
        *,
        org_id: str,
        share_id: str,
        recipients: Sequence[ShareRecipientRecord],
    ) -> tuple[Sequence[str], Sequence[str]]:
        with self._lock:
            share = self.shares.get(share_id)
            if share is None or share.org_id != org_id:
                return ((), ())
            current = self.recipients.setdefault(share_id, {})
            new_ids = {recipient.user_id for recipient in recipients}
            removed = tuple(sorted(set(current.keys()) - new_ids))
            added = tuple(sorted(new_ids - set(current.keys())))
            self.recipients[share_id] = {
                recipient.user_id: recipient for recipient in recipients
            }
            return (added, removed)

    async def update_share(
        self,
        *,
        org_id: str,
        share_id: str,
        sources_visible_to_viewer: bool | None = None,
        expires_at: datetime | None = None,
        clear_expires_at: bool = False,
    ) -> ShareRecord | None:
        with self._lock:
            share = self.shares.get(share_id)
            if share is None or share.org_id != org_id:
                return None
            updates: dict[str, object] = {}
            if sources_visible_to_viewer is not None:
                updates["sources_visible_to_viewer"] = sources_visible_to_viewer
            if clear_expires_at:
                updates["expires_at"] = None
            elif expires_at is not None:
                updates["expires_at"] = expires_at
            if not updates:
                return share
            updated = share.model_copy(update=updates)
            self.shares[share_id] = updated
            return updated

    async def revoke_share(
        self, *, org_id: str, share_id: str, now: datetime
    ) -> ShareRecord | None:
        with self._lock:
            share = self.shares.get(share_id)
            if share is None or share.org_id != org_id:
                return None
            if share.revoked_at is not None:
                return share
            updated = share.model_copy(update={"revoked_at": now})
            self.shares[share_id] = updated
            return updated
