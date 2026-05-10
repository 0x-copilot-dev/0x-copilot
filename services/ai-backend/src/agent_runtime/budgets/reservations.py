"""Pre-flight budget reservations.

When two concurrent runs each estimate $0.60 against a $1.00 remaining
budget, both can pass an unprotected preflight and then both get charged,
busting the cap. The reservation row holds the estimate against the
budget for ``ttl_seconds`` (default 60) so the second run's headroom
calculation accounts for the first one's pending estimate.

Lifecycle:

  reserve(budget_id, period_start, run_id, micro_usd, tokens, ttl)
    └── INSERT ... ON CONFLICT (budget_id, run_id) WHERE consumed_at IS NULL
        DO NOTHING   ← idempotent on retry
  consume(reservation_id)
    └── UPDATE ... SET consumed_at = now()  ← charger calls after CAS
  release(reservation_id)
    └── same as consume; called when run is denied / fails before charge
  reap(now)
    └── DELETE ... WHERE expires_at < now AND consumed_at IS NULL

The reaper is wired into the existing ``usage_rollup_loop`` task so the
worker doesn't need a new daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agent_runtime.persistence.records import BudgetReservationRecord


_DEFAULT_TTL_SECONDS = 60


class _Port:
    """Type-only marker for the persistence subset we need.

    Importing the full :class:`PersistencePort` here would create
    an import cycle (records → budgets → ports → records). The reservations
    module declares only what it uses.
    """


@dataclass(frozen=True)
class ReservationOutcome:
    """Result of a reserve call.

    ``reservation`` is None when the call could not reserve (budget is
    already at or beyond limit including existing reservations). The
    enforcer reads this to decide between ``Allow`` and ``Deny``.
    """

    reservation: BudgetReservationRecord | None
    reason: str | None = None


class BudgetReservationManager:
    """Coordinates reserve / consume / release / reap on top of the port.

    Stateless wrapper around the persistence port — every method is async
    and routes to a port method. Lives here (not on the port directly)
    so the TTL default and the "budget would be exceeded" arithmetic
    sit beside each other instead of split across the SQL adapter and
    the enforcer.
    """

    def __init__(self, persistence: object, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._persistence = persistence
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @classmethod
    def expires_at(cls, *, now: datetime | None = None, ttl_seconds: int) -> datetime:
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now + timedelta(seconds=ttl_seconds)
