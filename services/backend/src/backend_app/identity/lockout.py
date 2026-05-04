"""Account-lockout service (A8): sliding window + active-lockout gate.

The lockout machinery is wired between every login path (A3 OIDC, A4 local
password, future A5 SAML, A6 MFA) and the actual credential verify. The
service is intentionally framework-agnostic — routes catch ``AccountLocked``
and translate it to HTTP 423; tests can drive it directly without spinning
up FastAPI.

Two-phase rollout: ``LockoutPolicyRecord.enforce_lockout`` defaults to
``False`` so the migration ships without breaking any existing login. The
service still RECORDS attempts in ``login_attempts`` (existing behavior
from A1/A4) and STILL writes ``account_lockouts`` rows when thresholds
trip — operators get the telemetry first, then flip enforcement on per-org
once the failure curve is well-understood.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend_app.contracts import (
    AccountLockoutRecord,
    IdentityAuditEventRecord,
    LockoutPolicyRecord,
    LoginAttemptOutcome,
)
from backend_app.identity.lockout_store import LockoutStore
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountLocked(RuntimeError):
    """Raised by ``LockoutService.check_or_raise`` when the user is locked.

    ``retry_after_seconds`` lets the route handler set a ``Retry-After``
    header (RFC 7231 §7.1.3). Zero means "manual unlock required".
    """

    org_id: str
    user_id: str
    retry_after_seconds: int
    reason: str

    def __str__(self) -> str:
        return f"account locked: {self.reason}"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


# Outcome strings counted as "failed login" for the sliding-window check.
# Mirrors the spec list at docs/roadmap/17-a8-lockout.md §2.4.
_FAILURE_OUTCOMES = (
    LoginAttemptOutcome.BAD_PASSWORD,
    LoginAttemptOutcome.MFA_FAILED,
    LoginAttemptOutcome.PROVIDER_REJECTED,
)


class LockoutService:
    """Per-org sliding-window lockout enforcement.

    Threading model: stateless. The active-lockout row + policy row are
    the source of truth, so multiple workers concurrently calling
    ``record_failure`` converge on the same outcome thanks to the
    partial-unique index on ``account_lockouts``.

    Reusable: every login path (A3..A6) holds a ``LockoutService`` and
    calls the same three methods around the credential verify:

      service.check_or_raise(...)        # before verify
      ... do verify ...
      service.record_failure(...)        # on bad credentials
      service.record_success(...)        # on good credentials
    """

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        lockout_store: LockoutStore,
    ) -> None:
        self._identity_store = identity_store
        self._lockout_store = lockout_store

    # Policy ------------------------------------------------------------
    def policy_for(self, *, org_id: str) -> LockoutPolicyRecord:
        existing = self._lockout_store.get_policy(org_id=org_id)
        if existing is not None:
            return existing
        # Default policy: telemetry-only (enforce_lockout=False) so the
        # service may be added to a hot path without changing behavior.
        return LockoutPolicyRecord(org_id=org_id)

    # Pre-check ---------------------------------------------------------
    def check_or_raise(
        self,
        *,
        org_id: str,
        user_id: str | None,
    ) -> None:
        """Raise ``AccountLocked`` when an active lockout exists for the
        user. Routes call this BEFORE password / token verify so a locked
        user with the right credentials still 423s — the spec is explicit
        about this (§2.5: "lockout supersedes password check").

        For pre-account flows (unknown email, OIDC subject not yet
        provisioned), pass ``user_id=None`` and the check no-ops; the
        sliding-window count still grows on each failure so when the user
        DOES provision, the lockout pre-check immediately trips.
        """

        if user_id is None:
            return
        policy = self.policy_for(org_id=org_id)
        if not policy.enforce_lockout:
            return
        active = self._lockout_store.get_active_lockout(org_id=org_id, user_id=user_id)
        if active is None:
            return
        if active.auto_unlock_at is None:
            # Permanent lockout — admin unlock required.
            raise AccountLocked(
                org_id=org_id,
                user_id=user_id,
                retry_after_seconds=0,
                reason=active.lock_reason,
            )
        retry_after = max(0, int((active.auto_unlock_at - _now()).total_seconds()))
        if retry_after <= 0:
            # The auto-unlock window has elapsed but the row is still
            # active. Treat the next request as an opportunity to clear
            # it lazily so the user isn't held by stale state.
            self._lockout_store.unlock(
                org_id=org_id,
                user_id=user_id,
                unlocked_by_user_id=None,
                reason="auto_unlock_window_elapsed",
            )
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=None,
                    subject_user_id=user_id,
                    action="lockout.auto_unlocked",
                    metadata={"lockout_id": active.lockout_id},
                )
            )
            return
        raise AccountLocked(
            org_id=org_id,
            user_id=user_id,
            retry_after_seconds=retry_after,
            reason=active.lock_reason,
        )

    # Failure / success post-write -------------------------------------
    def record_failure(
        self,
        *,
        org_id: str,
        user_id: str | None,
        email: str | None,
    ) -> AccountLockoutRecord | None:
        """Tally one failure into the sliding window. If the threshold is
        crossed AND a known user is in scope, create the lockout row.

        Routes call this AFTER they've already appended to
        ``login_attempts`` (so the count includes the just-attempted
        failure). Returns the new lockout record when the call crosses
        the threshold, or None otherwise.
        """

        if user_id is None:
            # Pre-account failure (unknown email, etc.). No lockout row to
            # create — the sliding window builds in ``login_attempts`` and
            # the next failure with a real user_id will see the count.
            return None
        policy = self.policy_for(org_id=org_id)
        recent = self._failure_count_for_window(
            org_id=org_id,
            user_id=user_id,
            email=email,
            window_seconds=policy.failure_window_seconds,
        )
        if recent < policy.max_failures:
            return None
        return self._lock(org_id=org_id, user_id=user_id, policy=policy)

    def record_success(self, *, org_id: str, user_id: str) -> None:
        """Best-effort: clear an active auto-unlock row on a successful
        verify so a user who solved the lockout window doesn't keep
        bumping into it on the next failure inside the same minute. We
        leave the historical lockout intact — only ``unlocked_at`` is
        stamped — so SIEM still sees the timeline.
        """

        active = self._lockout_store.get_active_lockout(org_id=org_id, user_id=user_id)
        if active is None:
            return
        unlocked = self._lockout_store.unlock(
            org_id=org_id,
            user_id=user_id,
            unlocked_by_user_id=None,
            reason="successful_login",
        )
        if unlocked is not None:
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=user_id,
                    subject_user_id=user_id,
                    action="lockout.cleared_on_success",
                    metadata={"lockout_id": active.lockout_id},
                )
            )

    # Admin unlock ------------------------------------------------------
    def force_unlock(
        self,
        *,
        org_id: str,
        user_id: str,
        unlocked_by_user_id: str,
        reason: str | None,
    ) -> AccountLockoutRecord | None:
        unlocked = self._lockout_store.unlock(
            org_id=org_id,
            user_id=user_id,
            unlocked_by_user_id=unlocked_by_user_id,
            reason=reason or "admin_unlock",
        )
        self._identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org_id,
                actor_user_id=unlocked_by_user_id,
                subject_user_id=user_id,
                action="lockout.admin_unlocked",
                metadata={
                    "lockout_id": unlocked.lockout_id if unlocked else None,
                    "reason": reason or "admin_unlock",
                },
            )
        )
        return unlocked

    # Internals ---------------------------------------------------------
    def _failure_count_for_window(
        self,
        *,
        org_id: str,
        user_id: str,
        email: str | None,
        window_seconds: int,
    ) -> int:
        # The IdentityStore's ``list_login_attempts`` already filters by
        # outcome via the in-memory predicate / Postgres query. We pull
        # by user_id (which matches both login flavours after auth has
        # resolved the subject) and union with the email-only failures
        # that happened before user_id was known.
        cutoff = _now() - timedelta(seconds=max(1, window_seconds))
        attempts = self._identity_store.list_login_attempts(
            org_id=org_id, user_id=user_id, limit=1024
        )
        count = sum(
            1
            for a in attempts
            if a.outcome in _FAILURE_OUTCOMES and a.created_at >= cutoff
        )
        if email:
            email_attempts = self._identity_store.list_login_attempts(
                org_id=org_id, email=email, limit=1024
            )
            count += sum(
                1
                for a in email_attempts
                if a.user_id is None  # avoid double-counting user-keyed rows
                and a.outcome in _FAILURE_OUTCOMES
                and a.created_at >= cutoff
            )
        return count

    def _lock(
        self,
        *,
        org_id: str,
        user_id: str,
        policy: LockoutPolicyRecord,
    ) -> AccountLockoutRecord | None:
        auto_unlock = (
            _now() + timedelta(seconds=policy.lockout_duration_seconds)
            if policy.lockout_duration_seconds > 0
            else None
        )
        # Permanent lockout escalation: if the user has been locked
        # ``permanent_after_n_lockouts`` times within a generous window,
        # leave ``auto_unlock_at=None`` so only an admin can clear it.
        if policy.permanent_after_n_lockouts > 0:
            since = _now() - timedelta(days=30)
            prior = self._lockout_store.count_lockouts_since(
                org_id=org_id, user_id=user_id, since=since
            )
            if prior + 1 >= policy.permanent_after_n_lockouts:
                auto_unlock = None
        candidate = AccountLockoutRecord(
            org_id=org_id,
            user_id=user_id,
            lock_reason=(
                "permanent_after_repeat_lockouts"
                if auto_unlock is None
                else "max_failures_exceeded"
            ),
            auto_unlock_at=auto_unlock,
        )
        created = self._lockout_store.create_lockout(candidate)
        if created is not None:
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=None,
                    subject_user_id=user_id,
                    action="lockout.locked",
                    metadata={
                        "lockout_id": created.lockout_id,
                        "auto_unlock_at": (
                            created.auto_unlock_at.isoformat()
                            if created.auto_unlock_at
                            else None
                        ),
                        "lock_reason": created.lock_reason,
                    },
                )
            )
            _LOGGER.info(
                "account_locked org_id=%s user_id=%s reason=%s",
                org_id,
                user_id,
                created.lock_reason,
            )
        return created


__all__ = [
    "AccountLocked",
    "LockoutService",
]
