"""Durable Postgres adapter for A5 effect execution claims.

Claims are the durable boundary immediately before an effect executor performs
an external mutation.  The adapter deliberately borrows the runtime store's
``worker`` connection: claims are written and recovered only by worker-owned
coordination paths, never by a tenant request handler.  A composite unique key
on ``(org_id, executor, idempotency_key)`` makes acquisition atomic across
workers while the stored immutable request facts let a retry distinguish a
legitimate replay from an idempotency-key collision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimAcquisition,
    EffectClaimConflict,
    EffectClaimInvalidTransition,
    EffectClaimNotFound,
    EffectClaimStorageError,
    normalize_persisted_effect_claim_payload,
    require_persistable_effect_claim,
    validate_claim_transition,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


_TABLE = "runtime_effect_claims"
_WORKER_ROLE = "worker"
_CLAIM_COLUMNS = """
    org_id, run_id, stage_id, revision, claim_id, idempotency_key, executor,
    proposal_digest, target_digest, state, attempt, prepared_ref, receipt_ref,
    outcome, result_digest, safe_message, target_ref, proposal_ref,
    proposal_content_ref, actor, decision_ledger_id, created_at, updated_at
"""


class PostgresEffectClaimStore:
    """``EffectClaimStore`` backed by ``runtime_effect_claims``.

    ``store`` is intentionally typed as ``object`` so this adapter can borrow
    the runtime store's worker-role connection without making a second pool or
    introducing a domain dependency on the concrete Postgres store.
    """

    def __init__(self, store: object) -> None:
        self._store = store

    async def claim(self, *, claim: EffectClaim) -> EffectClaimAcquisition:
        """Create one claim atomically or return its semantic replay.

        ``ON CONFLICT DO NOTHING`` waits on a racing transaction's unique-key
        decision.  The following select therefore observes the durable winner
        under PostgreSQL's read-committed semantics before comparing all
        immutable request facts.
        """

        require_persistable_effect_claim(claim)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_TABLE} ({_CLAIM_COLUMNS})
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (org_id, executor, idempotency_key)
                        DO NOTHING
                        RETURNING {_CLAIM_COLUMNS}
                        """,
                        _claim_values(claim),
                    )
                    row = await cursor.fetchone()
                    if row is not None:
                        return EffectClaimAcquisition(
                            created=True, claim=_claim_from_row(row)
                        )

                    existing = await _load_by_key(
                        conn,
                        org_id=claim.org_id,
                        executor=claim.executor,
                        idempotency_key=claim.idempotency_key,
                    )
                    if existing is None:
                        raise EffectClaimStorageError(
                            "The claimed effect could not be read safely."
                        )
                    if not existing.same_request_as(claim):
                        raise EffectClaimConflict()
                    return EffectClaimAcquisition(created=False, claim=existing)
        except (EffectClaimConflict, EffectClaimStorageError):
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise EffectClaimStorageError() from exc

    async def get(
        self,
        *,
        org_id: str,
        executor: EffectExecutorKind,
        idempotency_key: str,
    ) -> EffectClaim | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                return await _load_by_key(
                    conn,
                    org_id=org_id,
                    executor=executor,
                    idempotency_key=idempotency_key,
                )
        except EffectClaimStorageError:
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise EffectClaimStorageError() from exc

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT {_CLAIM_COLUMNS}
                      FROM {_TABLE}
                     WHERE org_id = %s AND claim_id = %s
                    """,
                    (org_id, claim_id),
                )
                row = await cursor.fetchone()
            return _claim_from_row(row) if row is not None else None
        except EffectClaimStorageError:
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise EffectClaimStorageError() from exc

    async def update(self, *, claim: EffectClaim) -> EffectClaim:
        """Persist only a validated monotonic state transition.

        A row lock serializes competing reconciliation outcomes.  Immutable
        request facts are never overwritten by this update; the transition
        validator proves the replacement refers to exactly the same claim.
        """

        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        SELECT {_CLAIM_COLUMNS}
                          FROM {_TABLE}
                         WHERE org_id = %s
                           AND executor = %s
                           AND idempotency_key = %s
                         FOR UPDATE
                        """,
                        (claim.org_id, claim.executor.value, claim.idempotency_key),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise EffectClaimNotFound()
                    previous = _claim_from_row(row)
                    if previous.claim_id != claim.claim_id:
                        raise EffectClaimNotFound()
                    validate_claim_transition(previous=previous, replacement=claim)

                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET state = %s,
                               attempt = %s,
                               prepared_ref = %s,
                               receipt_ref = %s,
                               outcome = %s,
                               result_digest = %s,
                               safe_message = %s,
                               updated_at = %s
                         WHERE org_id = %s
                           AND executor = %s
                           AND idempotency_key = %s
                           AND claim_id = %s
                        RETURNING {_CLAIM_COLUMNS}
                        """,
                        (
                            claim.state.value,
                            claim.attempt,
                            claim.prepared_ref,
                            claim.receipt_ref,
                            claim.outcome.value if claim.outcome is not None else None,
                            claim.result_digest,
                            claim.safe_message,
                            claim.updated_at,
                            claim.org_id,
                            claim.executor.value,
                            claim.idempotency_key,
                            claim.claim_id,
                        ),
                    )
                    stored = await cursor.fetchone()
                    if stored is None:
                        raise EffectClaimNotFound()
                    return _claim_from_row(stored)
        except (
            EffectClaimInvalidTransition,
            EffectClaimNotFound,
            EffectClaimStorageError,
        ):
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise EffectClaimStorageError() from exc

    async def list_incomplete(
        self, *, org_id: str | None = None, limit: int = 100
    ) -> Sequence[EffectClaim]:
        if limit < 1:
            return ()
        try:
            where = "state IN ('claimed', 'indeterminate')"
            params: tuple[Any, ...]
            if org_id is None:
                params = (limit,)
            else:
                where += " AND org_id = %s"
                params = (org_id, limit)
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT {_CLAIM_COLUMNS}
                      FROM {_TABLE}
                     WHERE {where}
                     ORDER BY created_at ASC, claim_id ASC
                     LIMIT %s
                    """,
                    params,
                )
                rows = await cursor.fetchall()
            return tuple(_claim_from_row(row) for row in rows)
        except EffectClaimStorageError:
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise EffectClaimStorageError() from exc


async def _load_by_key(
    conn: object,
    *,
    org_id: str,
    executor: EffectExecutorKind,
    idempotency_key: str,
) -> EffectClaim | None:
    cursor = await conn.execute(
        f"""
        SELECT {_CLAIM_COLUMNS}
          FROM {_TABLE}
         WHERE org_id = %s
           AND executor = %s
           AND idempotency_key = %s
        """,
        (org_id, executor.value, idempotency_key),
    )
    row = await cursor.fetchone()
    return _claim_from_row(row) if row is not None else None


def _claim_values(claim: EffectClaim) -> tuple[object, ...]:
    return (
        claim.org_id,
        claim.run_id,
        claim.stage_id,
        claim.revision,
        claim.claim_id,
        claim.idempotency_key,
        claim.executor.value,
        claim.proposal_digest,
        claim.target_digest,
        claim.state.value,
        claim.attempt,
        claim.prepared_ref,
        claim.receipt_ref,
        claim.outcome.value if claim.outcome is not None else None,
        claim.result_digest,
        claim.safe_message,
        claim.target_ref,
        claim.proposal_ref,
        claim.proposal_content_ref,
        claim.actor.value,
        claim.decision_ledger_id,
        claim.created_at,
        claim.updated_at,
    )


def _claim_from_row(row: Mapping[str, object]) -> EffectClaim:
    payload = dict(row)
    for field in ("created_at", "updated_at"):
        value = payload.get(field)
        if isinstance(value, datetime):
            payload[field] = value.isoformat()
    try:
        return EffectClaim.model_validate(
            normalize_persisted_effect_claim_payload(payload)
        )
    except Exception as exc:
        raise EffectClaimStorageError("A stored effect claim is invalid.") from exc


__all__ = ["PostgresEffectClaimStore"]
