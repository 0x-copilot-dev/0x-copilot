"""Postgres-backed ``ShareStorePort``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from agent_runtime.persistence.records import (
    ShareRecipientRecord,
    ShareRecord,
    ShareViewAccess,
)


_SHARES_TABLE = "conversation_shares"
_RECIPIENTS_TABLE = "conversation_share_recipients"


class PostgresShareStore:
    """Postgres adapter for conversation shares + recipients."""

    def __init__(self, parent: object) -> None:
        # ``parent`` is :class:`PostgresRuntimeApiStore` exposing
        # ``_tenant_connection`` and ``_pool``. We only use already-defined
        # internals (the same pattern PostgresDraftStore uses).
        self._parent = parent

    # -- create -------------------------------------------------------------

    async def insert_share(
        self,
        *,
        share: ShareRecord,
        recipients: Sequence[ShareRecipientRecord],
    ) -> ShareRecord:
        """Persist a new share row and its initial recipients in one transaction."""
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=share.org_id
        ) as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        INSERT INTO {_SHARES_TABLE}
                            (share_id, org_id, conversation_id,
                             created_by_user_id, view_access,
                             sources_visible_to_viewer,
                             share_token_hash, share_token_prefix,
                             snapshot_at, expires_at, revoked_at, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            share.share_id,
                            share.org_id,
                            share.conversation_id,
                            share.created_by_user_id,
                            share.view_access.value,
                            share.sources_visible_to_viewer,
                            share.share_token_hash,
                            share.share_token_prefix,
                            share.snapshot_at,
                            share.expires_at,
                            share.revoked_at,
                            share.created_at,
                        ),
                    )
                    if recipients:
                        await cur.executemany(
                            f"""
                            INSERT INTO {_RECIPIENTS_TABLE}
                                (share_id, user_id, granted_at)
                            VALUES (%s, %s, %s)
                            """,
                            [
                                (
                                    recipient.share_id,
                                    recipient.user_id,
                                    recipient.granted_at,
                                )
                                for recipient in recipients
                            ],
                        )
        return share

    # -- read ---------------------------------------------------------------

    async def get_by_id(self, *, org_id: str, share_id: str) -> ShareRecord | None:
        """Return a share scoped by org, or ``None`` if not found."""
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    self._select_columns("WHERE org_id = %s AND share_id = %s LIMIT 1"),
                    (org_id, share_id),
                )
                row = await cur.fetchone()
        return self._row_to_record(row) if row is not None else None

    async def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        include_revoked: bool,
    ) -> Sequence[ShareRecord]:
        """Return shares for a conversation, newest first; optionally include revoked rows."""
        clause = "WHERE org_id = %s AND conversation_id = %s"
        if not include_revoked:
            clause += " AND revoked_at IS NULL"
        clause += " ORDER BY created_at DESC"
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    self._select_columns(clause), (org_id, conversation_id)
                )
                rows = await cur.fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    async def find_by_token_hash(self, *, share_token_hash: str) -> ShareRecord | None:
        """Return the share matching a token hash, or ``None`` if not found.

        Org-agnostic — the recipient presents a token before the server knows
        the tenant, so no RLS session var is stamped on this connection.
        """
        async with self._parent._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    self._select_columns("WHERE share_token_hash = %s LIMIT 1"),
                    (share_token_hash,),
                )
                row = await cur.fetchone()
        return self._row_to_record(row) if row is not None else None

    async def list_recipients(
        self, *, org_id: str, share_id: str
    ) -> Sequence[ShareRecipientRecord]:
        """Return recipients for a share, ordered by grant time."""
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT share_id, user_id, granted_at
                      FROM {_RECIPIENTS_TABLE}
                     WHERE share_id = %s
                     ORDER BY granted_at ASC
                    """,
                    (share_id,),
                )
                rows = await cur.fetchall()
        return tuple(
            ShareRecipientRecord(
                share_id=str(row[0]),
                user_id=str(row[1]),
                granted_at=row[2]
                if isinstance(row[2], datetime)
                else datetime.now(timezone.utc),
            )
            for row in rows
        )

    # -- mutate -------------------------------------------------------------

    async def replace_recipients(
        self,
        *,
        org_id: str,
        share_id: str,
        recipients: Sequence[ShareRecipientRecord],
    ) -> tuple[Sequence[str], Sequence[str]]:
        """Atomically replace the recipient list; returns (added_user_ids, removed_user_ids)."""
        new_ids = {recipient.user_id for recipient in recipients}
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT user_id
                          FROM {_RECIPIENTS_TABLE}
                         WHERE share_id = %s
                        """,
                        (share_id,),
                    )
                    existing_ids = {str(row[0]) for row in await cur.fetchall()}
                    removed = tuple(sorted(existing_ids - new_ids))
                    added = tuple(sorted(new_ids - existing_ids))
                    if removed:
                        await cur.execute(
                            f"""
                            DELETE FROM {_RECIPIENTS_TABLE}
                             WHERE share_id = %s AND user_id = ANY(%s)
                            """,
                            (share_id, list(removed)),
                        )
                    if added:
                        await cur.executemany(
                            f"""
                            INSERT INTO {_RECIPIENTS_TABLE}
                                (share_id, user_id, granted_at)
                            VALUES (%s, %s, NOW())
                            """,
                            [(share_id, user_id) for user_id in added],
                        )
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
        """Apply a partial update; returns the refreshed record or ``None`` if not found."""
        sets: list[str] = []
        params: list[object] = []
        if sources_visible_to_viewer is not None:
            sets.append("sources_visible_to_viewer = %s")
            params.append(sources_visible_to_viewer)
        if clear_expires_at:
            sets.append("expires_at = NULL")
        elif expires_at is not None:
            sets.append("expires_at = %s")
            params.append(expires_at)
        if not sets:
            return await self.get_by_id(org_id=org_id, share_id=share_id)
        params.extend([org_id, share_id])
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE {_SHARES_TABLE}
                       SET {", ".join(sets)}
                     WHERE org_id = %s AND share_id = %s
                    """,
                    params,
                )
        return await self.get_by_id(org_id=org_id, share_id=share_id)

    async def revoke_share(
        self, *, org_id: str, share_id: str, now: datetime
    ) -> ShareRecord | None:
        """Stamp ``revoked_at``; idempotent if already revoked. Returns the updated record."""
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE {_SHARES_TABLE}
                       SET revoked_at = COALESCE(revoked_at, %s)
                     WHERE org_id = %s AND share_id = %s
                    """,
                    (now, org_id, share_id),
                )
        return await self.get_by_id(org_id=org_id, share_id=share_id)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _select_columns(clause: str) -> str:
        """Build a SELECT statement for the shares table with the given WHERE/ORDER clause."""
        return f"""
            SELECT share_id, org_id, conversation_id, created_by_user_id,
                   view_access, sources_visible_to_viewer,
                   share_token_hash, share_token_prefix,
                   snapshot_at, expires_at, revoked_at, created_at
              FROM {_SHARES_TABLE}
             {clause}
        """

    @staticmethod
    def _row_to_record(row: tuple[object, ...]) -> ShareRecord:
        """Unpack a raw Postgres tuple into a :class:`ShareRecord`."""
        (
            share_id,
            org_id,
            conversation_id,
            created_by_user_id,
            view_access,
            sources_visible_to_viewer,
            share_token_hash,
            share_token_prefix,
            snapshot_at,
            expires_at,
            revoked_at,
            created_at,
        ) = row
        return ShareRecord(
            share_id=str(share_id),
            org_id=str(org_id),
            conversation_id=str(conversation_id),
            created_by_user_id=str(created_by_user_id),
            view_access=ShareViewAccess(str(view_access)),
            sources_visible_to_viewer=bool(sources_visible_to_viewer),
            share_token_hash=str(share_token_hash) if share_token_hash else None,
            share_token_prefix=str(share_token_prefix) if share_token_prefix else None,
            snapshot_at=snapshot_at
            if isinstance(snapshot_at, datetime)
            else datetime.now(timezone.utc),
            expires_at=expires_at if isinstance(expires_at, datetime) else None,
            revoked_at=revoked_at if isinstance(revoked_at, datetime) else None,
            created_at=created_at
            if isinstance(created_at, datetime)
            else datetime.now(timezone.utc),
        )
