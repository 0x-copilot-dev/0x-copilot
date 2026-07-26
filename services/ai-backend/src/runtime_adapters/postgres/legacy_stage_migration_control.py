"""Postgres-native E2 D5 source reservation and legacy queue CAS ports."""

from __future__ import annotations

from agent_runtime.api.legacy_stage_migration_runtime import (
    LegacyCanonicalStageEvidence,
    LegacyQueueInventoryState,
    legacy_stage_source_digest,
)
from agent_runtime.surfaces_v2.legacy_stage_materialization import (
    LegacyStageReconciliationRecord,
    LegacyStageReconciliationState,
)
from agent_runtime.api.legacy_stage_migration_service import (
    LegacyQueueNeutralizationOutcome,
    LegacySourceFenceOutcome,
)
from agent_runtime.surfaces_v2.staging import StagedWriteFold


_RESERVATIONS = "runtime_e2_legacy_stage_reservations"
_OUTBOX = "runtime_outbox_events"
_STAGE_COMMIT = "stage_commit_requested"


class PostgresLegacyStageReservationStore:
    """DB-backed ``reserved → staged → mapped`` materialization state."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def verify_and_reserve(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        idempotency_key: str,
        canonical_stage_id: str,
    ) -> LegacySourceFenceOutcome:
        try:
            async with self._store._role_connection("worker") as conn:  # noqa: SLF001
                async with conn.transaction():
                    if not await _source_matches(
                        conn,
                        org_id=org_id,
                        run_id=run_id,
                        legacy_stage_id=legacy_stage_id,
                        source_digest=expected_source_digest,
                        lock_run=True,
                    ):
                        return LegacySourceFenceOutcome.SOURCE_CHANGED
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_RESERVATIONS}
                            (org_id, run_id, legacy_stage_id, source_digest, idempotency_key,
                             canonical_stage_id, materialization_state, revision)
                        VALUES (%s, %s, %s, %s, %s, %s, 'reserved', 0)
                        ON CONFLICT (org_id, run_id, legacy_stage_id) DO NOTHING
                        RETURNING materialization_state
                        """,
                        (
                            org_id,
                            run_id,
                            legacy_stage_id,
                            expected_source_digest,
                            idempotency_key,
                            canonical_stage_id,
                        ),
                    )
                    if await cursor.fetchone() is not None:
                        return LegacySourceFenceOutcome.RESERVED
                    cursor = await conn.execute(
                        f"""
                        SELECT source_digest, idempotency_key, canonical_stage_id,
                               materialization_state
                          FROM {_RESERVATIONS}
                         WHERE org_id = %s AND run_id = %s AND legacy_stage_id = %s
                         FOR UPDATE
                        """,
                        (org_id, run_id, legacy_stage_id),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        return LegacySourceFenceOutcome.SOURCE_CHANGED
                    values = dict(row)
                    if (
                        values.get("source_digest") != expected_source_digest
                        or values.get("idempotency_key") != idempotency_key
                        or values.get("canonical_stage_id") != canonical_stage_id
                    ):
                        return LegacySourceFenceOutcome.SOURCE_CHANGED
                    state = values.get("materialization_state")
                    if state == "reserved":
                        return LegacySourceFenceOutcome.ALREADY_RESERVED
                    if state in {"staged", "mapped"}:
                        return LegacySourceFenceOutcome.STAGED
                    return LegacySourceFenceOutcome.SOURCE_CHANGED
        except Exception as exc:  # pragma: no cover - driver boundary
            raise RuntimeError("legacy stage reservation is unavailable") from exc

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        try:
            async with self._store._role_connection("worker") as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_RESERVATIONS}
                           SET materialization_state = 'mapped', revision = revision + 1,
                               updated_at = now()
                         WHERE org_id = %s AND run_id = %s AND legacy_stage_id = %s
                           AND source_digest = %s AND canonical_stage_id = %s
                           AND materialization_state IN ('staged', 'mapped')
                        RETURNING materialization_state
                        """,
                        (
                            org_id,
                            run_id,
                            legacy_stage_id,
                            expected_source_digest,
                            canonical_stage_id,
                        ),
                    )
                    if await cursor.fetchone() is None:
                        raise RuntimeError("legacy materialization cannot be mapped")
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - driver boundary
            raise RuntimeError("legacy stage reservation is unavailable") from exc

    async def quarantine(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> None:
        try:
            async with self._store._role_connection("worker") as conn:  # noqa: SLF001
                async with conn.transaction():
                    await conn.execute(
                        f"""
                        UPDATE {_RESERVATIONS}
                           SET materialization_state = 'quarantined', revision = revision + 1,
                               updated_at = now()
                         WHERE org_id = %s AND run_id = %s AND legacy_stage_id = %s
                           AND source_digest = %s AND materialization_state = 'reserved'
                        """,
                        (org_id, run_id, legacy_stage_id, expected_source_digest),
                    )
        except Exception as exc:  # pragma: no cover - driver boundary
            raise RuntimeError("legacy stage reservation is unavailable") from exc

    async def load_candidate_evidence(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
    ) -> LegacyCanonicalStageEvidence | None:
        try:
            async with self._store._role_connection("worker") as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    """
                    SELECT candidate_json, proof_digest
                      FROM runtime_e2_legacy_stage_evidence
                     WHERE org_id = %s AND run_id = %s AND legacy_stage_id = %s
                       AND source_digest = %s
                    """,
                    (org_id, run_id, legacy_stage_id, source_digest),
                )
                row = await cursor.fetchone()
            if row is None:
                return None
            candidate_json = dict(row["candidate_json"] or {})
            return LegacyCanonicalStageEvidence.model_validate(
                {
                    **candidate_json,
                    "proof_digest": str(row["proof_digest"]),
                }
            )
        except (TypeError, ValueError):
            return None
        except Exception as exc:  # pragma: no cover - driver boundary
            raise RuntimeError("legacy stage evidence is unavailable") from exc

    async def checkpoint_reconciliation(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        state: LegacyStageReconciliationState,
        operator_ref: str,
        migration_job_id: str,
    ) -> LegacyStageReconciliationRecord:
        try:
            async with self._store._role_connection("worker") as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        """
                        INSERT INTO runtime_e2_legacy_stage_reconciliations
                            (org_id, run_id, legacy_stage_id, source_digest, status,
                             checkpoint_revision, reassessed_at, terminal_at,
                             operator_ref, migration_job_id)
                        VALUES (%s, %s, %s, %s, %s, 0, now(),
                                CASE WHEN %s = 'frozen' THEN NULL ELSE now() END,
                                %s, %s)
                        ON CONFLICT (org_id, run_id, legacy_stage_id)
                        DO UPDATE SET
                            source_digest = EXCLUDED.source_digest,
                            status = EXCLUDED.status,
                            checkpoint_revision = runtime_e2_legacy_stage_reconciliations.checkpoint_revision + 1,
                            reassessed_at = now(),
                            terminal_at = CASE WHEN EXCLUDED.status = 'frozen' THEN NULL ELSE now() END,
                            operator_ref = EXCLUDED.operator_ref,
                            migration_job_id = EXCLUDED.migration_job_id
                        RETURNING org_id, run_id, legacy_stage_id, source_digest, status,
                                  checkpoint_revision, reassessed_at, terminal_at,
                                  operator_ref, migration_job_id
                        """,
                        (
                            org_id,
                            run_id,
                            legacy_stage_id,
                            source_digest,
                            state.value,
                            state.value,
                            operator_ref,
                            migration_job_id,
                        ),
                    )
                    row = await cursor.fetchone()
            values = dict(row)
            return LegacyStageReconciliationRecord(
                org_id=str(values["org_id"]),
                run_id=str(values["run_id"]),
                legacy_stage_id=str(values["legacy_stage_id"]),
                source_digest=str(values["source_digest"]),
                state=LegacyStageReconciliationState(str(values["status"])),
                checkpoint_revision=int(values["checkpoint_revision"]),
                reassessed_at=values["reassessed_at"],
                terminal_at=values["terminal_at"],
                operator_ref=str(values["operator_ref"]),
                migration_job_id=str(values["migration_job_id"]),
            )
        except Exception as exc:  # pragma: no cover - driver boundary
            raise RuntimeError("legacy reconciliation is unavailable") from exc


class PostgresLegacyStageQueueControl:
    """One SQL compare-and-set neutralizes only an exact pending old command."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def state_for_stage(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> str:
        async with self._store._role_connection("worker") as conn:  # noqa: SLF001
            cursor = await conn.execute(
                f"""
                SELECT status
                  FROM {_OUTBOX}
                 WHERE org_id = %s
                   AND aggregate_id = %s
                   AND event_type = %s
                   AND payload_json->>'stage_id' = %s
                """,
                (org_id, run_id, _STAGE_COMMIT, legacy_stage_id),
            )
            rows = await cursor.fetchall()
        if not rows:
            return LegacyQueueInventoryState.NONE
        statuses = {str(dict(row).get("status")) for row in rows}
        if "claimed" in statuses:
            return LegacyQueueInventoryState.CLAIMED
        if statuses & {"pending", "retry"}:
            return LegacyQueueInventoryState.UNCLAIMED
        return LegacyQueueInventoryState.NONE

    async def cancel_unclaimed(
        self, *, org_id: str, run_id: str, legacy_stage_id: str, source_digest: str
    ) -> LegacyQueueNeutralizationOutcome:
        async with self._store._role_connection("worker") as conn:  # noqa: SLF001
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    SELECT id
                      FROM agent_runs
                     WHERE id = %s AND org_id = %s
                     FOR UPDATE
                    """,
                    (run_id, org_id),
                )
                if await cursor.fetchone() is None:
                    return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
                cursor = await conn.execute(
                    """
                    SELECT event_type, sequence_no, payload_json_redacted
                      FROM runtime_events
                     WHERE org_id = %s AND run_id = %s
                     ORDER BY sequence_no ASC
                     FOR SHARE
                    """,
                    (org_id, run_id),
                )
                rows = await cursor.fetchall()
                raw_events = tuple(
                    {
                        "event_type": row["event_type"],
                        "sequence_no": row["sequence_no"],
                        "payload": dict(row["payload_json_redacted"] or {}),
                    }
                    for row in rows
                )
                state = StagedWriteFold.fold_raw(raw_events).get(legacy_stage_id)
                if (
                    state is None
                    or legacy_stage_source_digest(run_id=run_id, state=state)
                    != source_digest
                ):
                    return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
                cursor = await conn.execute(
                    f"""
                    SELECT status
                      FROM {_OUTBOX}
                     WHERE org_id = %s
                       AND aggregate_id = %s
                       AND event_type = %s
                       AND payload_json->>'stage_id' = %s
                     FOR UPDATE
                    """,
                    (org_id, run_id, _STAGE_COMMIT, legacy_stage_id),
                )
                rows = await cursor.fetchall()
                if not rows:
                    return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
                statuses = {str(dict(row).get("status")) for row in rows}
                if "claimed" in statuses:
                    return LegacyQueueNeutralizationOutcome.CLAIMED
                if statuses <= {"cancelled"}:
                    return LegacyQueueNeutralizationOutcome.ALREADY_CANCELLED
                if not statuses <= {"pending", "retry", "cancelled"}:
                    return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
                cursor = await conn.execute(
                    f"""
                    UPDATE {_OUTBOX}
                       SET status = 'cancelled', locked_by = NULL,
                           lock_expires_at = NULL, updated_at = now()
                     WHERE org_id = %s AND aggregate_id = %s AND event_type = %s
                       AND payload_json->>'stage_id' = %s
                       AND status IN ('pending', 'retry')
                    RETURNING id
                    """,
                    (org_id, run_id, _STAGE_COMMIT, legacy_stage_id),
                )
                cancelled = await cursor.fetchall()
                return (
                    LegacyQueueNeutralizationOutcome.CANCELLED
                    if cancelled
                    else LegacyQueueNeutralizationOutcome.ALREADY_CANCELLED
                )


async def _source_matches(
    conn: object,
    *,
    org_id: str,
    run_id: str,
    legacy_stage_id: str,
    source_digest: str,
    lock_run: bool,
) -> bool:
    """Fold the source under the same DB transaction as its state transition."""

    lock = " FOR UPDATE" if lock_run else ""
    cursor = await conn.execute(  # type: ignore[attr-defined]
        f"SELECT id FROM agent_runs WHERE id = %s AND org_id = %s{lock}",
        (run_id, org_id),
    )
    if await cursor.fetchone() is None:
        return False
    cursor = await conn.execute(  # type: ignore[attr-defined]
        """
        SELECT event_type, sequence_no, payload_json_redacted
          FROM runtime_events
         WHERE org_id = %s AND run_id = %s
         ORDER BY sequence_no ASC
         FOR SHARE
        """,
        (org_id, run_id),
    )
    rows = await cursor.fetchall()
    raw_events = tuple(
        {
            "event_type": row["event_type"],
            "sequence_no": row["sequence_no"],
            "payload": dict(row["payload_json_redacted"] or {}),
        }
        for row in rows
    )
    state = StagedWriteFold.fold_raw(raw_events).get(legacy_stage_id)
    return (
        state is not None
        and legacy_stage_source_digest(run_id=run_id, state=state) == source_digest
    )


__all__ = [
    "PostgresLegacyStageQueueControl",
    "PostgresLegacyStageReservationStore",
]
