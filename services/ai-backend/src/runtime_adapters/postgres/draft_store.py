"""Postgres-backed ``DraftStorePort``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone

from agent_runtime.persistence.encryption import FieldCodec
from agent_runtime.persistence.ports import OptimisticConflict
from agent_runtime.persistence.records import DraftRecord, DraftStatus

_TABLE = "runtime_drafts"


class PostgresDraftStore:
    """Postgres-backed draft persistence. Borrows the parent store's pool."""

    def __init__(self, parent: object) -> None:
        # ``parent`` is a :class:`PostgresRuntimeApiStore` exposing the helpers
        # we need without forcing a circular import or a public surface
        # change. We only call already-public methods.
        self._parent = parent

    @property
    def _codec(self) -> FieldCodec:
        """Return the parent store's FieldCodec for encryption/decryption."""
        return self._parent._codec  # type: ignore[attr-defined]

    async def insert_version(self, record: DraftRecord) -> DraftRecord:
        """Insert one new draft version; raises :class:`OptimisticConflict` on duplicate (draft_id, version)."""

        codec = self._codec
        title_encrypted = codec.encrypt_text(
            record.title, table=_TABLE, column="title", org_id=record.org_id
        )
        content_encrypted = codec.encrypt_text(
            record.content_text,
            table=_TABLE,
            column="content_text",
            org_id=record.org_id,
        )
        metadata_encrypted = codec.encrypt_jsonb(
            record.target_metadata or None,
            table=_TABLE,
            column="target_metadata",
            org_id=record.org_id,
        )
        async with self._parent._tenant_connection(org_id=record.org_id) as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        f"""
                        INSERT INTO {_TABLE}
                            (id, draft_id, version, org_id, conversation_id,
                             run_id, user_id, title, content_text,
                             target_connector, target_metadata, citation_ids,
                             status, encryption_version, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            record.id,
                            record.draft_id,
                            record.version,
                            record.org_id,
                            record.conversation_id,
                            record.run_id,
                            record.user_id,
                            (title_encrypted or "").encode("utf-8")
                            if title_encrypted
                            else b"",
                            (content_encrypted or "").encode("utf-8")
                            if content_encrypted
                            else b"",
                            record.target_connector,
                            json.dumps(metadata_encrypted).encode("utf-8")
                            if metadata_encrypted is not None
                            else None,
                            list(record.citation_ids),
                            record.status.value,
                            codec.write_version,
                            record.created_at,
                        ),
                    )
                except Exception as exc:
                    # UNIQUE (org_id, draft_id, version) collision means a
                    # concurrent writer raced us; surface as the typed
                    # conflict so callers can re-fetch and retry.
                    if (
                        "duplicate key" in str(exc).lower()
                        or "unique" in str(exc).lower()
                    ):
                        latest = await self.latest(
                            org_id=record.org_id, draft_id=record.draft_id
                        )
                        raise OptimisticConflict(
                            draft_id=record.draft_id,
                            expected_version=record.version,
                            actual_version=latest.version if latest else 0,
                        ) from exc
                    raise
        return record

    async def latest(self, *, org_id: str, draft_id: str) -> DraftRecord | None:
        """Return the highest-versioned draft row, or ``None`` if the draft does not exist."""
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT id, draft_id, version, org_id, conversation_id,
                           run_id, user_id, title, content_text,
                           target_connector, target_metadata, citation_ids,
                           status, encryption_version, created_at
                      FROM {_TABLE}
                     WHERE org_id = %s AND draft_id = %s
                     ORDER BY version DESC
                     LIMIT 1
                    """,
                    (org_id, draft_id),
                )
                row = await cur.fetchone()
        return self._row_to_record(row) if row is not None else None

    async def get_version(
        self, *, org_id: str, draft_id: str, version: int
    ) -> DraftRecord | None:
        """Return a specific version of a draft, or ``None`` if not found."""
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT id, draft_id, version, org_id, conversation_id,
                           run_id, user_id, title, content_text,
                           target_connector, target_metadata, citation_ids,
                           status, encryption_version, created_at
                      FROM {_TABLE}
                     WHERE org_id = %s AND draft_id = %s AND version = %s
                    """,
                    (org_id, draft_id, version),
                )
                row = await cur.fetchone()
        return self._row_to_record(row) if row is not None else None

    async def latest_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> Sequence[DraftRecord]:
        """Return the highest-versioned row for each draft in a conversation."""
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT DISTINCT ON (draft_id)
                           id, draft_id, version, org_id, conversation_id,
                           run_id, user_id, title, content_text,
                           target_connector, target_metadata, citation_ids,
                           status, encryption_version, created_at
                      FROM {_TABLE}
                     WHERE org_id = %s AND conversation_id = %s
                     ORDER BY draft_id, version DESC
                    """,
                    (org_id, conversation_id),
                )
                rows = await cur.fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    async def expect_status(
        self,
        *,
        org_id: str,
        draft_id: str,
        expected_version: int,
        expected_status: DraftStatus | None = None,
    ) -> DraftRecord:
        """Return the draft if version and status match; raises :class:`OptimisticConflict` otherwise."""
        latest = await self.latest(org_id=org_id, draft_id=draft_id)
        if latest is None:
            raise KeyError(draft_id)
        if latest.version != expected_version or (
            expected_status is not None and latest.status != expected_status
        ):
            raise OptimisticConflict(
                draft_id=draft_id,
                expected_version=expected_version,
                actual_version=latest.version,
            )
        return latest

    def _row_to_record(self, row: tuple[object, ...]) -> DraftRecord:
        """Decrypt and unpack a raw Postgres tuple into a :class:`DraftRecord`."""
        codec = self._codec
        (
            row_id,
            draft_id,
            version,
            org_id,
            conversation_id,
            run_id,
            user_id,
            title_blob,
            content_blob,
            target_connector,
            target_metadata_blob,
            citation_ids,
            status,
            encryption_version,
            created_at,
        ) = row

        org_id_str = str(org_id)
        title = codec.decrypt_text(
            title_blob.decode("utf-8")
            if isinstance(title_blob, (bytes, bytearray))
            else title_blob,
            encryption_version=int(encryption_version),
            table=_TABLE,
            column="title",
            org_id=org_id_str,
        )
        content_text = codec.decrypt_text(
            content_blob.decode("utf-8")
            if isinstance(content_blob, (bytes, bytearray))
            else content_blob,
            encryption_version=int(encryption_version),
            table=_TABLE,
            column="content_text",
            org_id=org_id_str,
        )
        target_metadata: dict[str, object]
        if target_metadata_blob is None:
            target_metadata = {}
        else:
            blob_raw = (
                target_metadata_blob.decode("utf-8")
                if isinstance(target_metadata_blob, (bytes, bytearray))
                else target_metadata_blob
            )
            decoded = codec.decrypt_jsonb(
                json.loads(blob_raw) if isinstance(blob_raw, str) else blob_raw,
                encryption_version=int(encryption_version),
                table=_TABLE,
                column="target_metadata",
                org_id=org_id_str,
            )
            target_metadata = decoded or {}

        return DraftRecord(
            id=str(row_id),
            draft_id=str(draft_id),
            version=int(version),
            org_id=org_id_str,
            conversation_id=str(conversation_id),
            run_id=str(run_id) if run_id is not None else None,
            user_id=str(user_id),
            title=title or "",
            content_text=content_text or "",
            target_connector=str(target_connector)
            if target_connector is not None
            else None,
            target_metadata=target_metadata,
            citation_ids=tuple(str(cid) for cid in (citation_ids or ())),
            status=DraftStatus(str(status)),
            encryption_version=int(encryption_version),
            created_at=created_at
            if isinstance(created_at, datetime)
            else datetime.now(timezone.utc),
        )
