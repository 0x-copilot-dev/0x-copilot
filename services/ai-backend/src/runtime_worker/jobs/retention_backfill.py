"""One-shot startup job that back-fills ``retention_until`` on rows written before the column existed."""

from __future__ import annotations

import logging
import os

from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records.retention import RetentionKind
from agent_runtime.retention import (
    DEPLOYMENT_DEFAULT_TTL_SECONDS,
    RetentionPolicyResolver,
)


_LOGGER = logging.getLogger(__name__)

# CONTEXT_PAYLOADS already have retention_until; CHECKPOINTS use a keep-latest-N rule, not TTL.
_BACKFILL_KINDS: tuple[RetentionKind, ...] = (
    RetentionKind.MESSAGES,
    RetentionKind.EVENTS,
    RetentionKind.MEMORY_ITEMS,
)


class RetentionBackfillJobEnv:
    """Env-var keys + defaults for the backfill job."""

    ENABLED = "RETENTION_BACKFILL_ENABLED"
    CHUNK_SIZE = "RETENTION_BACKFILL_CHUNK"

    DEFAULT_CHUNK_SIZE = 10_000

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        """Read ``name`` from the environment as a positive int, returning ``default`` on miss or invalid value."""
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            v = int(raw)
            return v if v > 0 else default
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        """Read ``name`` from the environment as a boolean, returning ``default`` on miss."""
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


class RetentionBackfillJob:
    """One-shot backfill: stamp ``retention_until`` on all unset rows.

    Call ``await job.run()`` once at startup. The returned dict maps
    ``"<org_id>:<kind>"`` to the total row count updated for each
    (org, kind) pair — useful for startup logging.

    Idempotent: rows with ``retention_until`` already set are skipped.
    Safe to run multiple times; each run picks up where the last left off
    (rows updated in previous runs are filtered by ``retention_until IS NULL``).
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        chunk_size: int | None = None,
    ) -> None:
        self._persistence = persistence
        self._chunk_size = (
            chunk_size
            if chunk_size is not None
            else RetentionBackfillJobEnv.env_int(
                RetentionBackfillJobEnv.CHUNK_SIZE,
                RetentionBackfillJobEnv.DEFAULT_CHUNK_SIZE,
            )
        )

    async def run(self) -> dict[str, int]:
        """Backfill all orgs × kinds. Returns total rows stamped per key."""

        totals: dict[str, int] = {}
        org_ids = await self._persistence.list_retention_orgs()
        for org_id in org_ids:
            policies = await self._persistence.list_retention_policies(org_id=org_id)
            resolver = RetentionPolicyResolver(
                org_id=org_id,
                policies=policies,
                deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
            )
            for kind in _BACKFILL_KINDS:
                resolved = resolver.resolve(kind=kind)
                if resolved.ttl_seconds is None:
                    continue
                key = f"{org_id}:{kind.value}"
                total = await self._backfill_kind(
                    org_id=org_id,
                    kind=kind,
                    ttl_seconds=resolved.ttl_seconds,
                )
                totals[key] = total
                if total:
                    _LOGGER.info(
                        "retention_backfill_kind_complete",
                        extra={
                            "metadata": {
                                "org_id": org_id,
                                "kind": kind.value,
                                "rows_stamped": total,
                                "ttl_seconds": resolved.ttl_seconds,
                            }
                        },
                    )
        return totals

    async def _backfill_kind(
        self, *, org_id: str, kind: RetentionKind, ttl_seconds: int
    ) -> int:
        """Loop until all unset rows for (org_id, kind) are stamped; returns total rows updated."""
        total = 0
        while True:
            count = await self._persistence.backfill_retention_until(
                org_id=org_id,
                kind=kind,
                ttl_seconds=ttl_seconds,
                chunk_size=self._chunk_size,
            )
            total += count
            if count == 0:
                break
        return total
