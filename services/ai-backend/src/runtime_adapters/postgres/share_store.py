"""Postgres-backed ``ShareStorePort`` (PR 6.1).

Borrows the parent :class:`PostgresRuntimeApiStore`'s pool and
``_tenant_connection`` helper so RLS session vars are stamped on every
access.

Token lookups (the recipient endpoint) are intentionally org-agnostic —
the recipient submits a token before the server knows which tenant it
belongs to. The lookup runs through ``_pool.connection()`` directly
(no ``app.current_org_id`` set), and the Postgres row's ``org_id``
column is the source of truth that the API service uses to set the GUC
for *subsequent* reads in the same request.
"""

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
        # Org-agnostic: the recipient hasn't proven membership yet, so the
        # service hasn't set ``app.current_org_id``. The unique partial
        # index makes this O(1).
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
