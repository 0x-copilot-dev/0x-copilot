"""B7 budget enforcement: period math, estimation, preflight, atomic charge.

The package is split so each module is independently testable:

- ``period``       — pure UTC window calculation per :class:`BudgetPeriod`.
- ``estimator``    — pre-run token + cost estimate; conservative.
- ``enforcer``     — preflight: lookup → reserve → Allow / Warn / Deny.
- ``charger``      — post-run charge: CAS UPDATE + idempotency on run_id.
- ``reservations`` — reserve / release / consume / reaper. Optional path
                     activated when concurrent runs share a budget.

The worker handler in ``runtime_worker/handlers/run.py`` calls
:meth:`BudgetEnforcer.preflight` at the top and
:meth:`BudgetCharger.charge_run` after ``_record_run_usage``.
"""

from agent_runtime.budgets.charger import BudgetCharger
from agent_runtime.budgets.enforcer import (
    BudgetDecision,
    BudgetEnforcer,
    BudgetPreflightAllow,
    BudgetPreflightDeny,
    BudgetPreflightWarn,
)
from agent_runtime.budgets.estimator import BudgetEstimate, BudgetEstimator
from agent_runtime.budgets.period import BudgetPeriodCalculator
from agent_runtime.budgets.reservations import BudgetReservationManager

__all__ = [
    "BudgetCharger",
    "BudgetDecision",
    "BudgetEnforcer",
    "BudgetEstimate",
    "BudgetEstimator",
    "BudgetPeriodCalculator",
    "BudgetPreflightAllow",
    "BudgetPreflightDeny",
    "BudgetPreflightWarn",
    "BudgetReservationManager",
]
