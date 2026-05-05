"""``/internal/v1/workspace/billing`` — read-only billing digest (PR 4.2).

Composes:

  - **Plan**: read from environment (``BILLING_PLAN_TIER`` /
    ``BILLING_PLAN_DISPLAY_NAME`` / ``BILLING_PLAN_BILLING_CONTACT``). v1
    treats billing as **managed externally**; the real Stripe integration
    is its own PR.
  - **Seats**: ``COUNT(*) FROM organization_members WHERE removed_at IS NULL``,
    plus a static ``BILLING_SEAT_LIMIT`` ceiling per deploy.
  - **Current period**: a calendar-month window in UTC for v1.
  - **Budgets**: v1 returns ``[]`` here; the FE pulls live budgets directly
    from the ai-backend ``GET /v1/budgets/me`` endpoint (already shipped).
    This avoids backend ↔ ai-backend HTTP coupling for the read path.
  - **Invoices**: ``[]`` placeholder.

The route is admin-only.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


class PlanInfo(BaseModel):
    tier: str
    display_name: str
    managed_externally: bool
    billing_contact: str | None


class SeatsInfo(BaseModel):
    used: int
    limit: int
    removed_in_period: int


class CurrentPeriod(BaseModel):
    start: str
    end: str


class BudgetSummaryStub(BaseModel):
    """Mirrors ai-backend ``BudgetSummary`` (subset). v1 leaves this empty;
    the FE merges in live budgets via a separate call."""

    scope: str
    period: str
    limit_micro_usd: int | None = None
    current_spend_micro_usd: int | None = None


class InvoiceStub(BaseModel):
    invoice_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    amount_micro_usd: int | None = None
    status: str | None = None


class BillingDigestResponse(BaseModel):
    plan: PlanInfo
    seats: SeatsInfo
    current_period: CurrentPeriod
    budgets: list[BudgetSummaryStub] = Field(default_factory=list)
    invoices: list[InvoiceStub] = Field(default_factory=list)


def register_billing_routes(app: FastAPI) -> None:
    @app.get(
        "/internal/v1/workspace/billing",
        response_model=BillingDigestResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def get_billing(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> BillingDigestResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store
        org = store.get_organization(org_id=identity.org_id)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")

        members = store.list_members(org_id=identity.org_id)
        used_seats = sum(1 for m in members if m.removed_at is None)
        # ``removed_in_period`` is "during the current calendar month". v1
        # filters in Python because the in-memory store doesn't track the
        # period; once we're sized for it we move to a SQL count.
        period_start, period_end = _current_period()
        removed_in_period = sum(
            1
            for m in members
            if m.removed_at is not None and period_start <= m.removed_at < period_end
        )

        return BillingDigestResponse(
            plan=PlanInfo(
                tier=os.environ.get("BILLING_PLAN_TIER", "developer"),
                display_name=os.environ.get(
                    "BILLING_PLAN_DISPLAY_NAME", "Atlas — Developer"
                ),
                managed_externally=True,
                billing_contact=os.environ.get("BILLING_CONTACT_EMAIL") or None,
            ),
            seats=SeatsInfo(
                used=used_seats,
                limit=int(os.environ.get("BILLING_SEAT_LIMIT", "25")),
                removed_in_period=removed_in_period,
            ),
            current_period=CurrentPeriod(
                start=_iso(period_start),
                end=_iso(period_end),
            ),
            budgets=[],
            invoices=[],
        )


def _current_period() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["register_billing_routes"]
