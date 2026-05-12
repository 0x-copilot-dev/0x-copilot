"""Backfill job that re-encrypts existing ``encryption_version=0`` rows under the active envelope adapter."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from agent_runtime.persistence.encryption import (
    EncryptionUnavailableError,
    FieldCodec,
    FieldEncryption,
    NullFieldEncryption,
)
from agent_runtime.persistence.encryption_metrics import FieldEncryptionMetrics

import json

from psycopg.types.json import Jsonb


_LOGGER = logging.getLogger("ai_backend.field_encryption_backfill")


@dataclass(frozen=True)
class BackfillTarget:
    """One (table, column) covered by the backfill loop."""

    table: str
    column: str
    column_type: str  # "text" or "json"


# Default target set. Tables without an active write path are excluded
# intentionally to avoid rewriting empty tables on every run.
_DEFAULT_TARGETS: tuple[BackfillTarget, ...] = (
    BackfillTarget(table="agent_messages", column="content_text", column_type="text"),
    BackfillTarget(table="agent_messages", column="content_json", column_type="json"),
    BackfillTarget(table="agent_messages", column="metadata_json", column_type="json"),
    BackfillTarget(
        table="runtime_audit_log",
        column="metadata_json_redacted",
        column_type="json",
    ),
    BackfillTarget(
        table="runtime_events",
        column="payload_json_redacted",
        column_type="json",
    ),
    BackfillTarget(
        table="runtime_events",
        column="metadata_json_redacted",
        column_type="json",
    ),
)


# Back-compat alias for earlier callers that referenced the narrower name.
_PHASE_1_TARGETS = _DEFAULT_TARGETS


class FieldEncryptionBackfill:
    """Rate-limited per-table re-encryption loop.

    Reads `encryption_version=0` rows in batches, encrypts each covered
    column under the configured adapter, and UPDATEs the row to v1 in a
    single transaction per batch. ``RUNTIME_ENCRYPTION_BACKFILL_BATCH``
    bounds batch size; ``RUNTIME_ENCRYPTION_BACKFILL_SLEEP_MS`` paces
    successive batches.
    """

    def __init__(
        self,
        *,
        database_url: str,
        field_encryption: FieldEncryption,
        targets: tuple[BackfillTarget, ...] = _PHASE_1_TARGETS,
        batch_size: int | None = None,
        sleep_ms: int | None = None,
    ) -> None:
        if isinstance(field_encryption, NullFieldEncryption):
            raise RuntimeError(
                "Backfill requires an envelope-capable adapter; "
                "RUNTIME_FIELD_ENCRYPTION must be set to 'envelope_v1'."
            )
        self._database_url = database_url
        self._field_encryption = field_encryption
        # The codec wraps JSONB columns with the ``{"$enc": "v1:..."}`` envelope so
        # Postgres doesn't reject a raw envelope string as invalid JSONB.
        self._codec = FieldCodec(field_encryption)
        self._targets = targets
        self._batch_size = batch_size or int(
            os.environ.get("RUNTIME_ENCRYPTION_BACKFILL_BATCH", "100")
        )
        self._sleep_ms = sleep_ms or int(
            os.environ.get("RUNTIME_ENCRYPTION_BACKFILL_SLEEP_MS", "200")
        )
        self._metrics = FieldEncryptionMetrics.recorder()

    async def run(self) -> dict[str, int]:
        """Run the backfill across every target until each is exhausted.

        Returns a per-table tally of rows rewritten so operators can fold
        the result into deployment audit logs.
        """

        totals: dict[str, int] = {}
        for target in self._targets:
            totals[target.table] = await self._run_target(target)
        return totals

    async def _run_target(self, target: BackfillTarget) -> int:
        """Process one target table/column until all v0 rows are rewritten; returns the total count."""
        rewritten = 0
        loop = asyncio.get_running_loop()
        while True:
            batch = await loop.run_in_executor(None, self._rewrite_batch, target)
            if batch == 0:
                break
            rewritten += batch
            self._metrics.record_backfill_rows(table=target.table, count=batch)
            _LOGGER.info(
                "field_encryption_backfill table=%s column=%s rewrote=%d",
                target.table,
                target.column,
                batch,
            )
            await asyncio.sleep(self._sleep_ms / 1000.0)
        return rewritten

    def _rewrite_batch(self, target: BackfillTarget) -> int:
        """Encrypt one batch of v0 rows for ``target`` in a single transaction; returns the row count."""
        select_sql = (
            f"SELECT id, org_id, {target.column} AS payload "
            f"FROM {target.table} "
            f"WHERE encryption_version = 0 "
            f"  AND {target.column} IS NOT NULL "
            f"ORDER BY id ASC "
            f"LIMIT %(batch)s "
            f"FOR UPDATE SKIP LOCKED"
        )
        update_sql = (
            f"UPDATE {target.table} "
            f"   SET {target.column} = %(value)s, encryption_version = 1 "
            f" WHERE id = %(id)s AND encryption_version = 0"
        )
        with psycopg.connect(
            self._database_url, autocommit=False, row_factory=dict_row
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(select_sql, {"batch": self._batch_size})
                rows = list(cur.fetchall())
                if not rows:
                    return 0
                count = 0
                for row in rows:
                    org_id = str(row["org_id"])
                    try:
                        stored_value = self._encrypt_for_target(
                            target, row["payload"], org_id=org_id
                        )
                    except EncryptionUnavailableError:
                        # KMS unavailable — abort the batch; the outer loop retries next pass.
                        conn.rollback()
                        raise
                    cur.execute(
                        update_sql,
                        {
                            "value": stored_value,
                            "id": row["id"],
                        },
                    )
                    count += 1
                conn.commit()
        return count

    def _encrypt_for_target(
        self, target: BackfillTarget, value: Any, *, org_id: str
    ) -> Any:
        """Encrypt ``value`` for the given target's column type; skips rows already encrypted."""
        if target.column_type == "json":
            # Defensive guard against partial backfills: already-encrypted rows show up as
            # ``{"$enc": "..."}`` which the WHERE clause should exclude, but skip them anyway.
            if isinstance(value, dict) and len(value) == 1 and "$enc" in value:
                return Jsonb(value)
            encrypted = self._codec.encrypt_jsonb(
                value, table=target.table, column=target.column, org_id=org_id
            )
            return Jsonb(encrypted)
        # Text column.
        if isinstance(value, str) and value.startswith("v1:"):
            return value
        plaintext = self._coerce_text_to_bytes(value)
        return self._codec.encrypt_text(
            plaintext.decode("utf-8"),
            table=target.table,
            column=target.column,
            org_id=org_id,
        )

    @staticmethod
    def _coerce_text_to_bytes(value: Any) -> bytes:
        """Coerce a text column value to bytes for encryption; JSON-encodes non-string/bytes values."""
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # Back-compat alias for existing tests and operator scripts.
    _coerce_to_bytes = _coerce_text_to_bytes
