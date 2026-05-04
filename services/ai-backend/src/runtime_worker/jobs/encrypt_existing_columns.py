"""C7 backfill job: re-encrypt existing v0 rows under the active envelope adapter.

Scoped per (table, column) so an operator can resume after a partial run
or run only a subset under load. The job batches rows, rate-limits between
batches, and is idempotent — re-running advances the cursor on
``encryption_version=0`` without rewriting v1 rows.

Phase 1 ships the framework + a single demo column (``agent_messages.
content_text``) so we have a working test surface; phase 2 adds the
remaining columns from the C7 spec table.
"""

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
    FieldEncryption,
    NullFieldEncryption,
)
from agent_runtime.persistence.encryption_metrics import FieldEncryptionMetrics


_LOGGER = logging.getLogger("ai_backend.field_encryption_backfill")


@dataclass(frozen=True)
class BackfillTarget:
    """One (table, column) covered by the backfill loop."""

    table: str
    column: str
    column_type: str  # "text" or "json"


# Phase 1 ships one canonical target so the framework has a tested surface.
# Phase 2 adds the rest from the C7 spec; gated on operator readiness.
_PHASE_1_TARGETS: tuple[BackfillTarget, ...] = (
    BackfillTarget(table="agent_messages", column="content_text", column_type="text"),
)


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
                    plaintext = self._coerce_to_bytes(row["payload"])
                    try:
                        ciphertext = self._field_encryption.encrypt(
                            plaintext,
                            table=target.table,
                            column=target.column,
                            org_id=str(row["org_id"]),
                        )
                    except EncryptionUnavailableError:
                        # KMS is wedged — abort the batch; outer loop will
                        # retry on the next pass.
                        conn.rollback()
                        raise
                    cur.execute(
                        update_sql,
                        {
                            "value": ciphertext,
                            "id": row["id"],
                        },
                    )
                    count += 1
                conn.commit()
        return count

    @staticmethod
    def _coerce_to_bytes(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        # JSONB / dict: serialize stably so re-encrypted bytes are
        # decryptable as the same logical structure.
        import json

        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
