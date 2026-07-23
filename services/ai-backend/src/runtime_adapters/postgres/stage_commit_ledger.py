"""Durable Postgres idempotency ledger for the v2 CommitEngine (PRD-D2).

The claim is the atomic ``INSERT ... ON CONFLICT (commit_key) DO NOTHING RETURNING``
primitive: exactly one concurrent worker's insert returns a row (it won the claim);
every other returns nothing (a prior / racing attempt already claimed it, so the
side effect must NOT fire again). Written *before* the connector send, the row
survives a worker crash, so a redelivered command replays inert instead of
double-sending — at-most-once for irreversible actions.

The table is keyed solely by ``commit_key`` (``stage_id:rev:decision_seq``), which
is uuid4-derived and globally unique. It holds only claim state + a small connector
receipt (``ConnectorCommitResult``), never tenant-readable content; ``org_id`` is
stored for audit joins. The claim is issued on the operator ``worker`` role
connection (like the outbox claim), so the ledger cannot be reached by a tenant
request path.
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult
from agent_runtime.surfaces_v2.commit_engine import StageCommitLedgerEntry

_TABLE = "runtime_stage_commit_ledger"
_WORKER_ROLE = "worker"


class PostgresStageCommitLedger:
    """Postgres ``StageCommitLedgerPort`` over the runtime store's connection pool."""

    def __init__(self, store: object, *, org_id: str | None = None) -> None:
        # ``store`` is the AsyncPostgresRuntimeApiStore; we reuse its worker-role
        # connection helper rather than opening a second pool.
        self._store = store
        # Optional org stamp for the audit column (claims key on commit_key only).
        self._org_id = org_id

    async def load(self, *, commit_key: str) -> StageCommitLedgerEntry | None:
        async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
            cur = await conn.execute(
                f"SELECT committed, result_json FROM {_TABLE} WHERE commit_key = %s",
                (commit_key,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        result_raw = row[1]
        result = (
            ConnectorCommitResult.model_validate(result_raw)
            if isinstance(result_raw, dict)
            else None
        )
        return StageCommitLedgerEntry(
            commit_key=commit_key, committed=bool(row[0]), result=result
        )

    async def claim(self, *, commit_key: str) -> bool:
        async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
            async with conn.transaction():
                cur = await conn.execute(
                    f"""
                    INSERT INTO {_TABLE} (commit_key, org_id, committed, created_at)
                    VALUES (%s, %s, false, now())
                    ON CONFLICT (commit_key) DO NOTHING
                    RETURNING commit_key
                    """,
                    (commit_key, self._org_id),
                )
                row = await cur.fetchone()
        return row is not None

    async def complete(self, *, commit_key: str, result: ConnectorCommitResult) -> None:
        async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
            async with conn.transaction():
                await conn.execute(
                    f"""
                    UPDATE {_TABLE}
                    SET committed = true, result_json = %s, updated_at = now()
                    WHERE commit_key = %s
                    """,
                    (Jsonb(result.model_dump(mode="json")), commit_key),
                )


__all__ = ["PostgresStageCommitLedger"]
