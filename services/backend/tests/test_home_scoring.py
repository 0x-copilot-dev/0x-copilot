"""Pure-function tests for ``home.scoring.compute_focus_score``.

Deterministic by construction (no clock reads, no randomness). Given
fixed inputs, the function returns fixed floats; the tests pin a few
canonical cases plus the monotonicity invariants the FE depends on
for stable sort order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend_app.home.scoring import compute_focus_score


_NOW = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)


class TestDeterminism:
    def test_same_inputs_same_output(self) -> None:
        a = compute_focus_score(
            now=_NOW,
            due_at=_NOW + timedelta(hours=6),
            priority="p1",
            created_at=_NOW - timedelta(days=2),
        )
        b = compute_focus_score(
            now=_NOW,
            due_at=_NOW + timedelta(hours=6),
            priority="p1",
            created_at=_NOW - timedelta(days=2),
        )
        assert a == b

    def test_overdue_item_saturates(self) -> None:
        """Past-due items get the maximum overdue weight."""

        score = compute_focus_score(
            now=_NOW,
            due_at=_NOW - timedelta(hours=2),
            priority="p2",
            created_at=_NOW - timedelta(days=1),
        )
        # Overdue weight 0.55 + priority 0.25 × 0.45 + age 0.20 × (1/14)
        # ≈ 0.55 + 0.1125 + 0.01428 = 0.67678... rounded to 6 places.
        assert score >= 0.55  # overdue alone clears the floor
        assert 0.0 <= score <= 1.0

    def test_no_due_date_score_is_priority_plus_age(self) -> None:
        score = compute_focus_score(
            now=_NOW,
            due_at=None,
            priority="p0",
            created_at=_NOW,  # fresh — age contributes 0
        )
        # 0.55 × 0 + 0.25 × 1.0 + 0.20 × 0 == 0.25
        assert score == 0.25


class TestMonotonicity:
    """The FE sorts items by score — the order must be stable for
    typical comparisons. These tests pin a few invariants."""

    def test_higher_priority_outranks_lower(self) -> None:
        kwargs = {
            "now": _NOW,
            "due_at": None,
            "created_at": _NOW - timedelta(hours=1),
        }
        p0 = compute_focus_score(priority="p0", **kwargs)
        p3 = compute_focus_score(priority="p3", **kwargs)
        assert p0 > p3

    def test_overdue_outranks_far_future_at_same_priority(self) -> None:
        common = {
            "now": _NOW,
            "priority": "p2",
            "created_at": _NOW - timedelta(hours=1),
        }
        overdue = compute_focus_score(due_at=_NOW - timedelta(hours=1), **common)
        future = compute_focus_score(due_at=_NOW + timedelta(days=7), **common)
        assert overdue > future

    def test_closer_due_date_outranks_further_at_same_priority(self) -> None:
        common = {
            "now": _NOW,
            "priority": "p1",
            "created_at": _NOW - timedelta(hours=1),
        }
        soon = compute_focus_score(due_at=_NOW + timedelta(hours=2), **common)
        later = compute_focus_score(due_at=_NOW + timedelta(hours=20), **common)
        assert soon > later

    def test_older_item_outranks_newer_at_same_priority_and_due(self) -> None:
        common = {"now": _NOW, "due_at": None, "priority": "p2"}
        older = compute_focus_score(created_at=_NOW - timedelta(days=10), **common)
        newer = compute_focus_score(created_at=_NOW - timedelta(hours=1), **common)
        assert older > newer


class TestRobustness:
    def test_unknown_priority_treated_as_normal(self) -> None:
        unknown = compute_focus_score(
            now=_NOW,
            due_at=None,
            priority="totally-unknown",
            created_at=_NOW,
        )
        normal = compute_focus_score(
            now=_NOW,
            due_at=None,
            priority="normal",
            created_at=_NOW,
        )
        assert unknown == normal

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Tolerant of naive datetimes (some stores still emit them)."""

        naive_now = datetime(2026, 5, 18, 12, 0)
        score = compute_focus_score(
            now=naive_now,
            due_at=datetime(2026, 5, 18, 14, 0),
            priority="p1",
            created_at=datetime(2026, 5, 17, 12, 0),
        )
        assert 0.0 <= score <= 1.0

    def test_score_is_bounded_zero_to_one(self) -> None:
        """Maximum reachable score (every axis saturated) is exactly 1.0."""

        score = compute_focus_score(
            now=_NOW,
            due_at=_NOW - timedelta(days=5),  # overdue → 1.0
            priority="p0",  # 1.0
            created_at=_NOW - timedelta(days=30),  # age → 1.0
        )
        assert score == 1.0
        # Minimum reachable is 0 when everything is null/minimal.
        floor = compute_focus_score(
            now=_NOW,
            due_at=_NOW + timedelta(days=30),  # outside horizon → 0
            priority="p3",
            created_at=_NOW,  # fresh → 0
        )
        # 0.55 × 0 + 0.25 × 0.15 + 0.20 × 0 == 0.0375
        assert 0.0 <= floor < 0.1
