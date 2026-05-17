"""Pure scoring helpers for the Home destination.

Kept free of DB calls so the policies under-test are deterministic.
``compute_focus_score`` collapses three independent signals (overdue,
priority, age) into a single monotonic urgency value the FE sorts on.

When new signals land (e.g. routine fire imminence), add a single
weighted axis here; the route layer composes the inputs and stays
thin.
"""

from __future__ import annotations

from datetime import datetime, timezone


# Weights are normalised so the maximum reachable score is 1.0 even
# when every signal saturates. Tunable; keep the sum == 1.0 so the
# value remains an interpretable [0, 1] urgency.
_W_OVERDUE = 0.55
_W_PRIORITY = 0.25
_W_AGE = 0.20

# Priority labels coming from todos/inbox/approvals tables. Lower-case
# keys; the route layer normalises before passing them in.
_PRIORITY_WEIGHTS: dict[str, float] = {
    "p0": 1.0,
    "urgent": 1.0,
    "p1": 0.75,
    "high": 0.75,
    "p2": 0.45,
    "normal": 0.45,
    "medium": 0.45,
    "p3": 0.15,
    "low": 0.15,
}


def compute_focus_score(
    *,
    now: datetime,
    due_at: datetime | None,
    priority: str | None,
    created_at: datetime,
) -> float:
    """Combine three signals into a single [0, 1] urgency value.

    * Overdue — saturates at 1.0 when ``due_at`` is in the past, ramps
      from 0 to 1 over a 24-hour horizon as the deadline approaches.
    * Priority — discrete labels map to [0, 1] via ``_PRIORITY_WEIGHTS``;
      unknown priority → 0.45 (treated as "normal").
    * Age — older items get a small bump; saturates at 1.0 after 14 days
      so a low-priority item that's been sitting for a fortnight still
      surfaces above brand-new same-priority items.

    Deterministic: same inputs always produce the same float; no
    randomness, no clock reads beyond the explicit ``now`` argument.
    """

    overdue = _overdue_score(now=now, due_at=due_at)
    pri = _priority_score(priority)
    age = _age_score(now=now, created_at=created_at)
    return round(_W_OVERDUE * overdue + _W_PRIORITY * pri + _W_AGE * age, 6)


def _overdue_score(*, now: datetime, due_at: datetime | None) -> float:
    if due_at is None:
        return 0.0
    now_utc = _as_utc(now)
    due_utc = _as_utc(due_at)
    delta_seconds = (due_utc - now_utc).total_seconds()
    if delta_seconds <= 0:
        return 1.0
    # Ramp 0 → 1 over a 24-hour horizon. Items >24h out contribute 0
    # so they don't crowd out genuine deadlines.
    horizon = 24 * 60 * 60
    if delta_seconds >= horizon:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (delta_seconds / horizon)))


def _priority_score(priority: str | None) -> float:
    if priority is None:
        return _PRIORITY_WEIGHTS["normal"]
    return _PRIORITY_WEIGHTS.get(priority.strip().lower(), _PRIORITY_WEIGHTS["normal"])


def _age_score(*, now: datetime, created_at: datetime) -> float:
    now_utc = _as_utc(now)
    created_utc = _as_utc(created_at)
    age_seconds = (now_utc - created_utc).total_seconds()
    if age_seconds <= 0:
        return 0.0
    horizon = 14 * 24 * 60 * 60
    return max(0.0, min(1.0, age_seconds / horizon))


def _as_utc(value: datetime) -> datetime:
    """Coerce a datetime to UTC. Naive values are treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["compute_focus_score"]
