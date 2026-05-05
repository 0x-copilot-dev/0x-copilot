"""Approval expiry + membership-cascade sweeper (PR 1.4.1).

Runs as a periodic loop alongside the runtime worker's other jobs
(``RetentionSweeperLoop``, ``UsageRollupLoop``). Each tick:

  1. Time-expiry pass: find pending approvals whose ``expires_at`` is
     past ``now()`` and enqueue a synthetic
     ``RuntimeApprovalResolvedCommand`` with ``decision=REJECTED`` +
     ``reason='expired'`` + ``decided_by_user_id=Values.SYSTEM_USER_ID``.
     The existing approval handler resolves the run with the standard
     reject path; the audit emitter promotes ``actor_type=system``.

  2. Membership-cascade pass: for the remaining pending rows, ask the
     :class:`WorkspaceMembershipResolver` whether each recipient is
     still active. Inactive recipients trigger the same synthetic
     rejection with ``reason='recipient_membership_revoked'``.

This is bookkeeping — no new resolution path. The sweeper enqueues
through the existing worker queue; the handler does the rest. DRY anchor:
the sweeper is to expiry what the resolution handler is to user-driven
decisions.

Disabled by default (``RUNTIME_APPROVAL_EXPIRY_SWEEP_ENABLED=true`` to
opt in) so existing deployments don't start auto-rejecting on upgrade.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from agent_runtime.api.async_ports import AsyncPersistencePort, AsyncRuntimeQueuePort
from agent_runtime.api.constants import Messages, Values
from agent_runtime.api.membership import (
    MembershipResolverUnavailable,
    WorkspaceMembershipResolver,
)
from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalRequestRecord,
    RuntimeApprovalResolvedCommand,
)


_LOGGER = logging.getLogger(__name__)


class ApprovalExpirySweeperEnv:
    """Env-var keys + defaults for the sweeper."""

    INTERVAL_SECONDS = "RUNTIME_APPROVAL_EXPIRY_TICK_SECONDS"
    ENABLED = "RUNTIME_APPROVAL_EXPIRY_SWEEP_ENABLED"
    BATCH_SIZE = "RUNTIME_APPROVAL_EXPIRY_BATCH_SIZE"
    MEMBERSHIP_BATCH_SIZE = "RUNTIME_APPROVAL_MEMBERSHIP_BATCH_SIZE"

    DEFAULT_INTERVAL_SECONDS = 30.0
    DEFAULT_BATCH_SIZE = 200
    DEFAULT_MEMBERSHIP_BATCH_SIZE = 500

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
    def env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


class ApprovalExpirySweeper:
    """Periodic two-pass sweep: expiry + membership cascade."""

    def __init__(
        self,
        *,
        persistence: AsyncPersistencePort,
        queue: AsyncRuntimeQueuePort,
        membership_resolver: WorkspaceMembershipResolver,
        interval_seconds: float | None = None,
        batch_size: int | None = None,
        membership_batch_size: int | None = None,
        clock: callable | None = None,
    ) -> None:
        self._persistence = persistence
        self._queue = queue
        self._membership_resolver = membership_resolver
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else ApprovalExpirySweeperEnv.env_float(
                ApprovalExpirySweeperEnv.INTERVAL_SECONDS,
                ApprovalExpirySweeperEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._batch_size = (
            batch_size
            if batch_size is not None
            else ApprovalExpirySweeperEnv.env_int(
                ApprovalExpirySweeperEnv.BATCH_SIZE,
                ApprovalExpirySweeperEnv.DEFAULT_BATCH_SIZE,
            )
        )
        self._membership_batch_size = (
            membership_batch_size
            if membership_batch_size is not None
            else ApprovalExpirySweeperEnv.env_int(
                ApprovalExpirySweeperEnv.MEMBERSHIP_BATCH_SIZE,
                ApprovalExpirySweeperEnv.DEFAULT_MEMBERSHIP_BATCH_SIZE,
            )
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name="approval-expiry-sweeper-loop"
        )

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
                _LOGGER.warning("approval_expiry_sweep_failed", exc_info=True)

    async def sweep_once(self) -> tuple[int, int]:
        """Run one full pass — expiry pass + membership cascade pass.

        Returns ``(expired_count, membership_revoked_count)``. Used by
        unit tests for deterministic assertions; production logs
        per-pass counts at INFO.
        """

        expired = await self._sweep_expiry_pass()
        revoked = await self._sweep_membership_pass()
        return expired, revoked

    async def _sweep_expiry_pass(self) -> int:
        now = self._clock()
        rows = await self._persistence.list_pending_expired_approvals(
            now=now, limit=self._batch_size
        )
        for row in rows:
            await self._enqueue_synthetic_rejection(
                approval=row,
                reason=Messages.Audit.APPROVAL_REASON_EXPIRED,
            )
        if rows:
            _LOGGER.info(
                "approval_expiry_swept",
                extra={"metadata": {"count": len(rows)}},
            )
        return len(rows)

    async def _sweep_membership_pass(self) -> int:
        rows = await self._persistence.list_pending_approvals_for_membership_audit(
            limit=self._membership_batch_size
        )
        revoked = 0
        for row in rows:
            try:
                is_active = await self._membership_resolver.is_active_member(
                    org_id=row.org_id, user_id=row.user_id
                )
            except MembershipResolverUnavailable:
                # Identity backend transiently down — skip this row this
                # tick; we'll pick it up next interval. Don't reject on
                # uncertainty.
                _LOGGER.warning(
                    "approval_membership_audit_unavailable",
                    extra={"metadata": {"approval_id": row.approval_id}},
                )
                continue
            if is_active:
                continue
            await self._enqueue_synthetic_rejection(
                approval=row,
                reason=Messages.Audit.APPROVAL_REASON_RECIPIENT_REVOKED,
            )
            revoked += 1
        if revoked:
            _LOGGER.info(
                "approval_membership_cascade_swept",
                extra={"metadata": {"count": revoked}},
            )
        return revoked

    async def _enqueue_synthetic_rejection(
        self,
        *,
        approval: ApprovalRequestRecord,
        reason: str,
    ) -> None:
        command = RuntimeApprovalResolvedCommand(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            org_id=approval.org_id,
            decision=ApprovalDecision.REJECTED,
            decided_by_user_id=Values.SYSTEM_USER_ID,
            reason=reason,
        )
        await self._queue.enqueue_approval_resolved(command)


def build_default_sweeper(
    *,
    persistence: AsyncPersistencePort,
    queue: AsyncRuntimeQueuePort,
    membership_resolver: WorkspaceMembershipResolver,
) -> ApprovalExpirySweeper | None:
    """Construct the sweeper iff the env flag is on.

    Returns ``None`` when ``RUNTIME_APPROVAL_EXPIRY_SWEEP_ENABLED`` is
    unset/false so existing deployments don't auto-reject on upgrade.
    """

    if not ApprovalExpirySweeperEnv.env_bool(ApprovalExpirySweeperEnv.ENABLED, False):
        return None
    return ApprovalExpirySweeper(
        persistence=persistence,
        queue=queue,
        membership_resolver=membership_resolver,
    )
