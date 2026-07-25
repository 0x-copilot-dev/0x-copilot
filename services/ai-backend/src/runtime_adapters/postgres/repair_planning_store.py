"""Postgres-backed durable state for D12 repair-planning snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from agent_runtime.effects.claims import EffectClaimScanCursor
from agent_runtime.surfaces_v2.repair_planning import (
    RepairPlanningSnapshot,
    RepairPlanningSnapshotState,
    RepairPlanningStateError,
    validate_repair_plan_page,
)
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairDecision,
    RepairPlan,
    RepairSnapshotRecord,
)


_SNAPSHOTS_TABLE = "runtime_repair_planning_snapshots"
_OUTCOMES_TABLE = "runtime_repair_planning_outcomes"
_SCAN_STATE_TABLE = "runtime_repair_planning_scan_state"
_EFFECT_CLAIM_SCAN_SOURCE = "effect_claims"
_WORKER_ROLE = "worker"


class PostgresRepairPlanningSnapshotStore:
    """CAS-backed worker-owned D12 planning state over the runtime pool."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def load_or_create(
        self, *, snapshot: RepairPlanningSnapshot
    ) -> RepairPlanningSnapshotState:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_SNAPSHOTS_TABLE} (
                            org_id, snapshot_id, snapshot_digest, as_of,
                            source_complete, records_json, cursor_after_candidate_id,
                            completed, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, NULL, false, now(), now())
                        ON CONFLICT (org_id, snapshot_id) DO NOTHING
                        RETURNING org_id, snapshot_id, snapshot_digest, as_of,
                                  source_complete, records_json,
                                  cursor_after_candidate_id, completed
                        """,
                        _snapshot_values(snapshot),
                    )
                    row = await cursor.fetchone()
                    if row is not None:
                        return _state_from_row(row)
                    existing = await _load_state(
                        conn,
                        tenant_id=snapshot.tenant_id,
                        snapshot_id=snapshot.snapshot_id,
                        for_update=True,
                    )
                    if (
                        existing is None
                        or not existing.snapshot.same_persisted_snapshot_as(snapshot)
                    ):
                        raise RepairPlanningStateError()
                    return existing
        except RepairPlanningStateError:
            raise
        except Exception as exc:  # pragma: no cover - broken database driver
            raise RepairPlanningStateError() from exc

    async def load(
        self, *, tenant_id: str, snapshot_id: str
    ) -> RepairPlanningSnapshotState | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                return await _load_state(
                    conn, tenant_id=tenant_id, snapshot_id=snapshot_id
                )
        except RepairPlanningStateError:
            raise
        except Exception as exc:  # pragma: no cover - broken database driver
            raise RepairPlanningStateError() from exc

    async def advance(
        self,
        *,
        tenant_id: str,
        snapshot_id: str,
        expected_after_candidate_id: str | None,
        plan: RepairPlan,
    ) -> bool:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    state = await _load_state(
                        conn,
                        tenant_id=tenant_id,
                        snapshot_id=snapshot_id,
                        for_update=True,
                    )
                    if state is None:
                        raise RepairPlanningStateError()
                    if state.after_candidate_id != expected_after_candidate_id:
                        return False
                    validate_repair_plan_page(
                        state=state,
                        expected_after_candidate_id=expected_after_candidate_id,
                        plan=plan,
                    )
                    for decision in plan.decisions:
                        await _insert_or_verify_outcome(
                            conn,
                            tenant_id=tenant_id,
                            snapshot_id=snapshot_id,
                            decision=decision,
                        )
                    next_after = (
                        plan.next_cursor.after_candidate_id
                        if plan.next_cursor is not None
                        else (
                            plan.decisions[-1].candidate_id if plan.decisions else None
                        )
                    )
                    await conn.execute(
                        f"""
                        UPDATE {_SNAPSHOTS_TABLE}
                           SET cursor_after_candidate_id = %s,
                               completed = %s,
                               updated_at = now()
                         WHERE org_id = %s AND snapshot_id = %s
                        """,
                        (next_after, not plan.has_more, tenant_id, snapshot_id),
                    )
                    return True
        except RepairPlanningStateError:
            raise
        except Exception as exc:  # pragma: no cover - broken database driver
            raise RepairPlanningStateError() from exc

    async def list_outcomes(
        self, *, tenant_id: str, snapshot_id: str
    ) -> Sequence[RepairDecision]:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT candidate_id, state, action, reasons_json
                      FROM {_OUTCOMES_TABLE}
                     WHERE org_id = %s AND snapshot_id = %s
                     ORDER BY candidate_id ASC
                    """,
                    (tenant_id, snapshot_id),
                )
                rows = await cursor.fetchall()
            return tuple(_decision_from_row(row) for row in rows)
        except RepairPlanningStateError:
            raise
        except Exception as exc:  # pragma: no cover - broken database driver
            raise RepairPlanningStateError() from exc

    async def load_effect_claim_scan_cursor(self) -> EffectClaimScanCursor | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT after_created_at, after_org_id, after_claim_id
                      FROM {_SCAN_STATE_TABLE}
                     WHERE source = %s
                    """,
                    (_EFFECT_CLAIM_SCAN_SOURCE,),
                )
                row = await cursor.fetchone()
            return _scan_cursor_from_row(row) if row is not None else None
        except RepairPlanningStateError:
            raise
        except Exception as exc:  # pragma: no cover - broken database driver
            raise RepairPlanningStateError() from exc

    async def advance_effect_claim_scan_cursor(
        self,
        *,
        expected: EffectClaimScanCursor | None,
        next_cursor: EffectClaimScanCursor | None,
    ) -> bool:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await conn.execute(
                        f"""
                        INSERT INTO {_SCAN_STATE_TABLE} (
                            source, after_created_at, after_org_id,
                            after_claim_id, updated_at
                        ) VALUES (%s, NULL, NULL, NULL, now())
                        ON CONFLICT (source) DO NOTHING
                        """,
                        (_EFFECT_CLAIM_SCAN_SOURCE,),
                    )
                    cursor = await conn.execute(
                        f"""
                        SELECT after_created_at, after_org_id, after_claim_id
                          FROM {_SCAN_STATE_TABLE}
                         WHERE source = %s
                         FOR UPDATE
                        """,
                        (_EFFECT_CLAIM_SCAN_SOURCE,),
                    )
                    current_row = await cursor.fetchone()
                    if current_row is None:
                        raise RepairPlanningStateError()
                    if _scan_cursor_from_row(current_row) != expected:
                        return False
                    await conn.execute(
                        f"""
                        UPDATE {_SCAN_STATE_TABLE}
                           SET after_created_at = %s,
                               after_org_id = %s,
                               after_claim_id = %s,
                               updated_at = now()
                         WHERE source = %s
                        """,
                        (
                            (
                                next_cursor.after_created_at
                                if next_cursor is not None
                                else None
                            ),
                            next_cursor.after_org_id
                            if next_cursor is not None
                            else None,
                            (
                                next_cursor.after_claim_id
                                if next_cursor is not None
                                else None
                            ),
                            _EFFECT_CLAIM_SCAN_SOURCE,
                        ),
                    )
                    return True
        except RepairPlanningStateError:
            raise
        except Exception as exc:  # pragma: no cover - broken database driver
            raise RepairPlanningStateError() from exc


def _snapshot_values(snapshot: RepairPlanningSnapshot) -> tuple[object, ...]:
    return (
        snapshot.tenant_id,
        snapshot.snapshot_id,
        snapshot.snapshot_digest,
        snapshot.as_of,
        snapshot.source_complete,
        Jsonb([record.model_dump(mode="json") for record in snapshot.records]),
    )


async def _load_state(
    conn: object,
    *,
    tenant_id: str,
    snapshot_id: str,
    for_update: bool = False,
) -> RepairPlanningSnapshotState | None:
    lock = " FOR UPDATE" if for_update else ""
    cursor = await conn.execute(
        f"""
        SELECT org_id, snapshot_id, snapshot_digest, as_of, source_complete,
               records_json, cursor_after_candidate_id, completed
          FROM {_SNAPSHOTS_TABLE}
         WHERE org_id = %s AND snapshot_id = %s{lock}
        """,
        (tenant_id, snapshot_id),
    )
    row = await cursor.fetchone()
    return _state_from_row(row) if row is not None else None


async def _insert_or_verify_outcome(
    conn: object,
    *,
    tenant_id: str,
    snapshot_id: str,
    decision: RepairDecision,
) -> None:
    cursor = await conn.execute(
        f"""
        INSERT INTO {_OUTCOMES_TABLE} (
            org_id, snapshot_id, candidate_id, state, action, reasons_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (org_id, snapshot_id, candidate_id) DO NOTHING
        RETURNING candidate_id
        """,
        (
            tenant_id,
            snapshot_id,
            decision.candidate_id,
            decision.state.value,
            decision.action.value if decision.action is not None else None,
            Jsonb([reason.value for reason in decision.reasons]),
        ),
    )
    if await cursor.fetchone() is not None:
        return
    cursor = await conn.execute(
        f"""
        SELECT candidate_id, state, action, reasons_json
          FROM {_OUTCOMES_TABLE}
         WHERE org_id = %s AND snapshot_id = %s AND candidate_id = %s
        """,
        (tenant_id, snapshot_id, decision.candidate_id),
    )
    row = await cursor.fetchone()
    if row is None or _decision_from_row(row) != decision:
        raise RepairPlanningStateError()


def _state_from_row(row: Mapping[str, object]) -> RepairPlanningSnapshotState:
    try:
        records_raw = row["records_json"]
        if not isinstance(records_raw, list):
            raise ValueError
        as_of = row["as_of"]
        if not isinstance(as_of, datetime):
            raise ValueError
        snapshot = RepairPlanningSnapshot(
            tenant_id=str(row["org_id"]),
            snapshot_id=str(row["snapshot_id"]),
            snapshot_digest=str(row["snapshot_digest"]),
            as_of=as_of,
            source_complete=bool(row["source_complete"]),
            records=tuple(
                RepairSnapshotRecord.model_validate(record) for record in records_raw
            ),
        )
        return RepairPlanningSnapshotState(
            snapshot=snapshot,
            after_candidate_id=(
                str(row["cursor_after_candidate_id"])
                if row["cursor_after_candidate_id"] is not None
                else None
            ),
            completed=bool(row["completed"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairPlanningStateError() from exc


def _decision_from_row(row: Mapping[str, Any]) -> RepairDecision:
    try:
        reasons = row["reasons_json"]
        if not isinstance(reasons, list):
            raise ValueError
        return RepairDecision.model_validate(
            {
                "candidate_id": row["candidate_id"],
                "state": row["state"],
                "action": row["action"],
                "reasons": reasons,
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairPlanningStateError() from exc


def _scan_cursor_from_row(row: Mapping[str, object]) -> EffectClaimScanCursor | None:
    try:
        values = (
            row["after_created_at"],
            row["after_org_id"],
            row["after_claim_id"],
        )
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise ValueError
        return EffectClaimScanCursor(
            after_created_at=values[0],
            after_org_id=str(values[1]),
            after_claim_id=str(values[2]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairPlanningStateError() from exc


__all__ = ("PostgresRepairPlanningSnapshotStore",)
