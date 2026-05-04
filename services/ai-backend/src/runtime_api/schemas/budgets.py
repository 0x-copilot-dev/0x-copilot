"""Public response + request schemas for /v1/budgets/* (B7).

The persistence record :class:`BudgetRecord` is the source of truth. The
request/response shapes here are intentionally narrow — they expose only
the mutable fields admins can set via the admin endpoints. Status
transitions go through ``PATCH`` on a single ``status`` field; deletion
is a ``DELETE`` (cascades to state + reservations).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.records import (
    BudgetEnforcement,
    BudgetPeriod,
    BudgetScope,
    BudgetStatus,
)


class BudgetCreateRequest(RuntimeContract):
    """Body for ``POST /v1/budgets``."""

    user_id: str | None = None  # None when scope='org'
    scope: BudgetScope
    period: BudgetPeriod
    enforcement: BudgetEnforcement
    limit_micro_usd: int | None = Field(default=None, ge=0)
    limit_tokens: int | None = Field(default=None, ge=0)


class BudgetUpdateRequest(RuntimeContract):
    """Body for ``PATCH /v1/budgets/{id}``. All fields optional."""

    enforcement: BudgetEnforcement | None = None
    limit_micro_usd: int | None = Field(default=None, ge=0)
    limit_tokens: int | None = Field(default=None, ge=0)
    status: BudgetStatus | None = None


class BudgetView(RuntimeContract):
    """Read shape for one budget."""

    id: str
    org_id: str
    user_id: str | None
    scope: BudgetScope
    period: BudgetPeriod
    enforcement: BudgetEnforcement
    limit_micro_usd: int | None
    limit_tokens: int | None
    status: BudgetStatus
    created_at: datetime
    updated_at: datetime
    created_by_user_id: str


class BudgetListResponse(RuntimeContract):
    """Response for ``GET /v1/budgets``."""

    budgets: tuple[BudgetView, ...] = ()


class BudgetMeRow(RuntimeContract):
    """One budget that currently applies to the caller, with remaining headroom.

    ``remaining_micro_usd`` / ``remaining_tokens`` are integers computed
    server-side. The ``period_start`` / ``period_end`` fields disambiguate
    which window the row reports against.
    """

    id: str
    scope: BudgetScope
    period: BudgetPeriod
    enforcement: BudgetEnforcement
    status: BudgetStatus
    limit_micro_usd: int | None
    limit_tokens: int | None
    current_micro_usd: int
    current_tokens: int
    remaining_micro_usd: int | None
    remaining_tokens: int | None
    period_start: date
    period_end: date


class BudgetMeResponse(RuntimeContract):
    """Response for ``GET /v1/budgets/me``."""

    currency: Literal["USD"] = "USD"
    budgets: tuple[BudgetMeRow, ...] = ()
