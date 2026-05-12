"""C8 retention sweeper loop.

Runs alongside the runtime worker (similar lifecycle as
``UsageRollupLoop`` from B4). Every ``RETENTION_SWEEP_INTERVAL_SECONDS``
(default 600) it:

  1. ``list_retention_orgs()`` — distinct org_ids in the affected tables.
  2. Per org: load policies, build a ``RetentionPolicyResolver``
     (Phase 4: used only for CHECKPOINTS; other kinds read ``retention_until``).
  3. For each kind, dispatch to ``sweep_retention_kind()``.
  4. Phase 4 (``RETENTION_SWEEP_USE_RETENTION_UNTIL=true``, default on):
     loop until the adapter returns 0 rows for the chunk; resolver is
     bypassed for all kinds except CHECKPOINTS (which still needs ttl).
  5. Emit per-tenant tally to OTel + write ``runtime_deletion_evidence``
     row on every non-empty outcome (Phase 1).

Disabled by default (``RETENTION_SWEEP_ENABLED=true`` to opt in) so
existing deployments don't start tombstoning rows on upgrade.

Phase 4 flag: ``RETENTION_SWEEP_USE_RETENTION_UNTIL`` (default ``true``).
Set to ``false`` for one release to fall back to the legacy
``created_at + ttl < NOW()`` unbounded sweep during a cautious rollout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from agent_runtime.api.ports import PersistencePort
from agent_runtime.observability.retention_metrics import RetentionMetrics
from agent_runtime.persistence.records.retention import (
    RetentionDeletionEvidenceRecord,
    RetentionKind,
    RetentionSweepOutcome,
)
from agent_runtime.retention import (
    DEPLOYMENT_DEFAULT_TTL_SECONDS,
    RetentionPolicyResolver,
)


_LOGGER = logging.getLogger(__name__)


class RetentionSweeperLoopEnv:
    """Env-var keys + defaults for the retention sweeper."""

    INTERVAL_SECONDS = "RETENTION_SWEEP_INTERVAL_SECONDS"
    ENABLED = "RETENTION_SWEEP_ENABLED"
    DRY_RUN = "RETENTION_SWEEP_DRY_RUN"
    # Phase 4: switch sweep SQL to retention_until-based chunked CTE.
    USE_RETENTION_UNTIL = "RETENTION_SWEEP_USE_RETENTION_UNTIL"
    # Rows per chunk per (org, kind) call. 0 disables chunking (legacy path).
    CHUNK_SIZE = "RETENTION_SWEEP_CHUNK"
    # Phase 5: grace period before hard-deleting tombstoned rows.
    # 0 (default) = skip the second-pass hard-delete entirely (safe default).
    GRACE_DAYS_MESSAGES = "RETENTION_TOMBSTONE_GRACE_DAYS_MESSAGES"
    GRACE_DAYS_EVENTS = "RETENTION_TOMBSTONE_GRACE_DAYS_EVENTS"
    GRACE_DAYS_MEMORY_ITEMS = "RETENTION_TOMBSTONE_GRACE_DAYS_MEMORY_ITEMS"

    DEFAULT_INTERVAL_SECONDS = 600.0
    DEFAULT_CHUNK_SIZE = 500

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            v = int(raw)
            return v if v > 0 else default
        except ValueError:
            return default


class RetentionSweeperLoop:
    """Periodic per-tenant retention sweep."""

    _SWEEP_KINDS: tuple[RetentionKind, ...] = (
        RetentionKind.CONTEXT_PAYLOADS,
        RetentionKind.CHECKPOINTS,
        RetentionKind.MESSAGES,
        RetentionKind.EVENTS,
        RetentionKind.MEMORY_ITEMS,
        # Phase 5: second-pass hard-delete after grace period.
        # Run AFTER the first-pass tombstones so rows have status='deleted'
        # before the hard-delete query looks for them.
        RetentionKind.MESSAGES_TOMBSTONED,
        RetentionKind.EVENTS_TOMBSTONED,
        RetentionKind.MEMORY_ITEMS_TOMBSTONED,
    )

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        interval_seconds: float | None = None,
        dry_run: bool | None = None,
        metrics: RetentionMetrics | None = None,
        use_retention_until: bool | None = None,
        chunk_size: int | None = None,
        grace_days_messages: int | None = None,
        grace_days_events: int | None = None,
        grace_days_memory_items: int | None = None,
    ) -> None:
        self._persistence = persistence
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else RetentionSweeperLoopEnv.env_float(
                RetentionSweeperLoopEnv.INTERVAL_SECONDS,
                RetentionSweeperLoopEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._dry_run = (
            dry_run
            if dry_run is not None
            else RetentionSweeperLoopEnv.env_bool(
                RetentionSweeperLoopEnv.DRY_RUN, False
            )
        )
        self._use_retention_until = (
            use_retention_until
            if use_retention_until is not None
            else RetentionSweeperLoopEnv.env_bool(
                RetentionSweeperLoopEnv.USE_RETENTION_UNTIL, True
            )
        )
        self._chunk_size = (
            chunk_size
            if chunk_size is not None
            else RetentionSweeperLoopEnv.env_int(
                RetentionSweeperLoopEnv.CHUNK_SIZE,
                RetentionSweeperLoopEnv.DEFAULT_CHUNK_SIZE,
            )
        )
        self._grace_days: dict[RetentionKind, int] = {
            RetentionKind.MESSAGES_TOMBSTONED: (
                grace_days_messages
                if grace_days_messages is not None
                else RetentionSweeperLoopEnv.env_int(
                    RetentionSweeperLoopEnv.GRACE_DAYS_MESSAGES, 0
                )
            ),
            RetentionKind.EVENTS_TOMBSTONED: (
                grace_days_events
                if grace_days_events is not None
                else RetentionSweeperLoopEnv.env_int(
                    RetentionSweeperLoopEnv.GRACE_DAYS_EVENTS, 0
                )
            ),
            RetentionKind.MEMORY_ITEMS_TOMBSTONED: (
                grace_days_memory_items
                if grace_days_memory_items is not None
                else RetentionSweeperLoopEnv.env_int(
                    RetentionSweeperLoopEnv.GRACE_DAYS_MEMORY_ITEMS, 0
                )
            ),
        }
        self._metrics = metrics if metrics is not None else RetentionMetrics()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="retention-sweeper-loop")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            try:
                await self.sweep_once()
            except Exception:
                _LOGGER.warning("retention_sweep_failed", exc_info=True)

    async def sweep_once(self) -> tuple[RetentionSweepOutcome, ...]:
        """Run one full pass across every org × kind. Returns tally per call."""

        outcomes: list[RetentionSweepOutcome] = []
        org_ids = await self._persistence.list_retention_orgs()
        for org_id in org_ids:
            policies = await self._persistence.list_retention_policies(org_id=org_id)
            resolver = RetentionPolicyResolver(
                org_id=org_id,
                policies=policies,
                deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
            )
            for kind in self._SWEEP_KINDS:
                if self._use_retention_until:
                    outcome = await self._sweep_kind_chunked(
                        org_id=org_id, kind=kind, resolver=resolver
                    )
                else:
                    outcome = await self._sweep_kind_legacy(
                        org_id=org_id, kind=kind, resolver=resolver
                    )
                if outcome is not None:
                    outcomes.append(outcome)
        return tuple(outcomes)

    async def _sweep_kind_chunked(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        resolver: RetentionPolicyResolver,
    ) -> RetentionSweepOutcome | None:
        """Phase 4 path: retention_until-driven, loop until 0 rows per chunk.

        CHECKPOINTS still resolves ttl_seconds (its keep-N logic is not
        expressible via retention_until). All other kinds are column-driven
        and do not touch the resolver.

        Dry-run: executes one chunk in a force-rollback transaction and
        returns immediately — looping would re-see the same rows every time.
        """
        ttl_seconds = 0
        if kind is RetentionKind.CHECKPOINTS:
            resolved = resolver.resolve(kind=kind)
            if resolved.ttl_seconds is None:
                return None
            ttl_seconds = resolved.ttl_seconds

        total = RetentionSweepOutcome(org_id=org_id, kind=kind)
        t0 = time.monotonic()
        while True:
            try:
                chunk = await self._persistence.sweep_retention_kind(
                    org_id=org_id,
                    kind=kind,
                    ttl_seconds=ttl_seconds,
                    dry_run=self._dry_run,
                    chunk_size=self._chunk_size,
                )
            except Exception:
                _LOGGER.warning(
                    "retention_sweep_kind_failed",
                    extra={"metadata": {"org_id": org_id, "kind": kind.value}},
                    exc_info=True,
                )
                break
            total = total.model_copy(
                update={
                    "tombstoned": total.tombstoned + chunk.tombstoned,
                    "deleted": total.deleted + chunk.deleted,
                    "skipped_legal_hold": (
                        total.skipped_legal_hold + chunk.skipped_legal_hold
                    ),
                }
            )
            # Dry-run: one chunk only (force-rollback means rows never
            # disappear, so looping would never converge).
            if self._dry_run or chunk.tombstoned + chunk.deleted == 0:
                break

        elapsed = time.monotonic() - t0
        self._metrics.record_sweep_duration(kind=kind.value, elapsed_seconds=elapsed)
        _LOGGER.info(
            "retention_swept",
            extra={
                "metadata": {
                    "org_id": org_id,
                    "kind": kind.value,
                    "tombstoned": total.tombstoned,
                    "deleted": total.deleted,
                    "skipped_legal_hold": total.skipped_legal_hold,
                    "dry_run": self._dry_run,
                }
            },
        )
        await self._record_metrics_and_evidence(total)
        return total

    async def _sweep_kind_legacy(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        resolver: RetentionPolicyResolver,
    ) -> RetentionSweepOutcome | None:
        """Pre-Phase-4 path: single-pass created_at+ttl sweep (flag=false)."""

        resolved = resolver.resolve(kind=kind)
        if resolved.ttl_seconds is None and kind is not RetentionKind.CONTEXT_PAYLOADS:
            return None
        t0 = time.monotonic()
        try:
            outcome = await self._persistence.sweep_retention_kind(
                org_id=org_id,
                kind=kind,
                ttl_seconds=resolved.ttl_seconds or 0,
                dry_run=self._dry_run,
                chunk_size=0,
            )
        except Exception:
            _LOGGER.warning(
                "retention_sweep_kind_failed",
                extra={"metadata": {"org_id": org_id, "kind": kind.value}},
                exc_info=True,
            )
            return None
        elapsed = time.monotonic() - t0
        self._metrics.record_sweep_duration(kind=kind.value, elapsed_seconds=elapsed)
        _LOGGER.info(
            "retention_swept",
            extra={
                "metadata": {
                    "org_id": org_id,
                    "kind": kind.value,
                    "tombstoned": outcome.tombstoned,
                    "deleted": outcome.deleted,
                    "skipped_legal_hold": outcome.skipped_legal_hold,
                    "dry_run": self._dry_run,
                }
            },
        )
        await self._record_metrics_and_evidence(outcome)
        return outcome

    async def _record_metrics_and_evidence(
        self, outcome: RetentionSweepOutcome
    ) -> None:
        """Emit OTel counters and write a deletion evidence row when non-empty.

        Called after every successful sweep call. Evidence rows are written
        even for dry-run sweeps (tagged ``dry_run=True``) so operators can
        verify "what would have been swept" without reading logs.
        """

        kind_str = outcome.kind.value
        if outcome.tombstoned:
            self._metrics.record_swept_rows(
                kind=kind_str,
                action="tombstone",
                count=outcome.tombstoned,
                dry_run=self._dry_run,
            )
        if outcome.deleted:
            self._metrics.record_swept_rows(
                kind=kind_str,
                action="delete",
                count=outcome.deleted,
                dry_run=self._dry_run,
            )
        if outcome.tombstoned or outcome.deleted or outcome.skipped_legal_hold:
            evidence = RetentionDeletionEvidenceRecord(
                org_id=outcome.org_id,
                kind=outcome.kind,
                tombstoned=outcome.tombstoned,
                deleted=outcome.deleted,
                skipped_legal_hold=outcome.skipped_legal_hold,
                dry_run=self._dry_run,
            )
            try:
                await self._persistence.insert_retention_deletion_evidence(evidence)
            except Exception:
                _LOGGER.warning(
                    "retention_evidence_insert_failed",
                    extra={"metadata": {"org_id": outcome.org_id, "kind": kind_str}},
                    exc_info=True,
                )
