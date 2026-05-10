"""C8 retention sweeper loop.

Runs alongside the runtime worker (similar lifecycle as
``UsageRollupLoop`` from B4). Every ``RETENTION_SWEEP_INTERVAL_SECONDS``
(default 600) it:

  1. ``list_retention_orgs()`` — distinct org_ids in the affected tables.
  2. Per org: load policies, build a ``RetentionPolicyResolver``.
  3. For each kind, resolve org-scope TTL (most-specific within the org;
     conversation/user-scope policies still bite when the per-row
     handler reaches them via the WHERE clauses).
  4. ``sweep_retention_kind(...)`` — adapter-side SQL (per-kind strategy).
  5. Emit per-tenant tally to OTel + ``runtime_audit_log``.

Disabled by default (``RETENTION_SWEEP_ENABLED=true`` to opt in) so
existing deployments don't start tombstoning rows on upgrade.
"""

from __future__ import annotations

import asyncio
import logging
import os

from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records.retention import (
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

    DEFAULT_INTERVAL_SECONDS = 600.0

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


class RetentionSweeperLoop:
    """Periodic per-tenant retention sweep."""

    _SWEEP_KINDS: tuple[RetentionKind, ...] = (
        RetentionKind.CONTEXT_PAYLOADS,
        RetentionKind.CHECKPOINTS,
        RetentionKind.MESSAGES,
        RetentionKind.EVENTS,
        RetentionKind.MEMORY_ITEMS,
    )

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        interval_seconds: float | None = None,
        dry_run: bool | None = None,
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
                resolved = resolver.resolve(kind=kind)
                # ``context_payloads`` is driven by the schema's
                # ``retention_until`` column rather than a TTL — the
                # adapter ignores ttl for that kind, so we still call it
                # even when resolved.ttl_seconds is None.
                if (
                    resolved.ttl_seconds is None
                    and kind is not RetentionKind.CONTEXT_PAYLOADS
                ):
                    continue
                try:
                    outcome = await self._persistence.sweep_retention_kind(
                        org_id=org_id,
                        kind=kind,
                        ttl_seconds=resolved.ttl_seconds or 0,
                        dry_run=self._dry_run,
                    )
                except Exception:
                    _LOGGER.warning(
                        "retention_sweep_kind_failed",
                        extra={"metadata": {"org_id": org_id, "kind": kind.value}},
                        exc_info=True,
                    )
                    continue
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
                outcomes.append(outcome)
        return tuple(outcomes)
