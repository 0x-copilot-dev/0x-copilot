"""Persistence records for spend budgets.

Three records map directly to the three tables created in migration
0009: ``usage_budgets`` (config), ``usage_budget_state`` (per-period
running spend), and ``usage_budget_reservations`` (pre-flight estimates
that gate concurrent runs from both passing the same headroom check).

All cost amounts are micro-USD integers (1 USD = 1_000_000 micro_usd).
The budget can also be denominated purely in tokens (``limit_tokens``)
for single-tenant deploys without seeded pricing — the enforcer falls
back to that path automatically when ``limit_micro_usd`` is None.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, NonNegativeInt

from agent_runtime.execution.contracts import RuntimeContract


class BudgetScope(StrEnum):
    """Whether the budget applies to the whole org or to a single user."""

    ORG = "org"
    USER = "user"


class BudgetPeriod(StrEnum):
    """Rolling-window granularity. UTC boundaries (midnight, first-of-month)."""

    DAY = "day"
    MONTH = "month"


class BudgetEnforcement(StrEnum):
    """``soft`` warns and continues; ``hard`` denies the run."""

    SOFT = "soft"
    HARD = "hard"


class BudgetStatus(StrEnum):
    """Active budgets are enforced; disabled budgets short-circuit to Allow."""

    ACTIVE = "active"
    DISABLED = "disabled"


class BudgetRecord(RuntimeContract):
    """One configured spend budget for an org or user."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str | None = None
    scope: BudgetScope
    period: BudgetPeriod
    enforcement: BudgetEnforcement
    limit_micro_usd: int | None = None
    limit_tokens: int | None = None
    status: BudgetStatus = BudgetStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_user_id: str


class BudgetStateRecord(RuntimeContract):
    """Running spend for a single (budget, period) slot.

    ``row_version`` is the CAS guard. ``last_charged_run_id`` provides
    idempotency: a worker retry that re-charges the same run hits the
    ``IS DISTINCT FROM`` clause and writes zero rows, returning a
    no-op outcome to the caller.
    """

    budget_id: str
    period_start: date
    period_end: date
    current_spend_micro_usd: int = 0
    current_spend_tokens: int = 0
    row_version: int = 1
    last_charged_run_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BudgetReservationRecord(RuntimeContract):
    """Pre-flight reservation: an estimate that prevents two concurrent
    runs from each passing the same headroom check and then both being
    charged into a negative spend.

    Reservations have a TTL (default 60s, configurable). The reaper in
    ``usage_rollup_loop`` purges expired rows on every tick — covered by
    the partial index ``idx_usage_budget_reservations_expiring``.
    """

    reservation_id: str = Field(default_factory=lambda: uuid4().hex)
    budget_id: str
    period_start: date
    run_id: str
    reserved_micro_usd: NonNegativeInt = 0
    reserved_tokens: NonNegativeInt = 0
    expires_at: datetime
    consumed_at: datetime | None = None


class BudgetWithState(RuntimeContract):
    """Convenience join used by the enforcer to make one fetch instead of two.

    The state row may be absent (period just rolled over and no run has
    charged yet); callers treat ``state is None`` as "spend is zero in
    the current window" and INSERT a new row inside the charge txn.
    """

    budget: BudgetRecord
    state: BudgetStateRecord | None = None


class ChargeOutcome(StrEnum):
    """Discriminator for ``charge_budget`` results."""

    APPLIED = "applied"
    IDEMPOTENT_NOOP = "idempotent_noop"  # last_charged_run_id matched
    EXHAUSTED_RETRIES = "exhausted_retries"
