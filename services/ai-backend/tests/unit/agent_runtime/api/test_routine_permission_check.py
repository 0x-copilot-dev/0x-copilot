"""Tests for the routine fire-time permission intersection.

Covers routines-prd §7.4 and cross-audit §9.7 Q4/Q5/Q11:

- INTERSECT(routine, owner, project) set semantics (overlapping, disjoint,
  empty layers, missing project layer).
- ``missing_scopes`` carries every required scope that shrunk away.
- ``pause_reason`` enum is set on every failure branch
  (permission_shrinkage / owner_offboarded / critical_connector_disconnected)
  so the audit row's ``context`` field is populated.
- Connector disconnection short-circuits before generic shrinkage so the
  Inbox CTA can route to the right repair flow.
- NO auto-resume: re-granting permissions does NOT change any persisted
  state -- the check is pure; the policy of "manual resume only" is
  enforced by the absence of any resume codepath in the gate.

LLM provider imports: none. This module is pure permission math.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.routine_permission_check import (
    RoutinePauseReason,
    RoutinePermissionChecker,
    RoutinePermissionContext,
    RoutinePermissionScope,
    check_routine_permissions,
)


class RoutinePermissionContextMixin:
    """Helpers to build contexts with sane defaults so each test is one delta."""

    DEFAULT_ROUTINE_ID = "rt_briefing"

    @classmethod
    def make_context(
        cls,
        *,
        routine_id: str | None = None,
        routine_declared: list[str] | None = None,
        routine_required: list[str] | None = None,
        owner_scopes: list[str] | None = None,
        project_scopes: list[str] | None | object = "MISSING",
        owner_disabled: bool = False,
        disconnected_connectors: tuple[str, ...] = (),
    ) -> RoutinePermissionContext:
        routine_declared = (
            routine_declared
            if routine_declared is not None
            else ["connector:slack:read", "connector:slack:write"]
        )
        routine_required = (
            routine_required if routine_required is not None else list(routine_declared)
        )
        owner_scopes = (
            owner_scopes if owner_scopes is not None else list(routine_declared)
        )
        if project_scopes == "MISSING":
            project = None
        else:
            project = RoutinePermissionScope.from_iterable(project_scopes)  # type: ignore[arg-type]
        return RoutinePermissionContext(
            routine_id=routine_id or cls.DEFAULT_ROUTINE_ID,
            routine_declared_scopes=RoutinePermissionScope.from_iterable(
                routine_declared
            ),
            routine_required_scopes=RoutinePermissionScope.from_iterable(
                routine_required
            ),
            owner_scopes=RoutinePermissionScope.from_iterable(owner_scopes),
            project_scopes=project,
            owner_disabled=owner_disabled,
            disconnected_connectors=disconnected_connectors,
        )


class TestRoutinePermissionScopeNormalisation(RoutinePermissionContextMixin):
    def test_scope_set_dedupes_and_sorts(self) -> None:
        scope = RoutinePermissionScope.from_iterable(["b", "a", "a", "c"])
        assert scope.scopes == ("a", "b", "c")

    def test_scope_set_handles_none(self) -> None:
        scope = RoutinePermissionScope.from_iterable(None)
        assert scope.scopes == ()

    def test_scope_set_ignores_non_strings(self) -> None:
        scope = RoutinePermissionScope.from_iterable(["a", 7, None, "b"])  # type: ignore[list-item]
        assert scope.scopes == ("a", "b")


class TestIntersectionHappyPath(RoutinePermissionContextMixin):
    def test_fully_overlapping_scopes_satisfy(self) -> None:
        result = check_routine_permissions(self.make_context())
        assert result.is_satisfied is True
        assert result.missing_scopes == ()
        assert result.pause_reason is None
        assert set(result.effective_scopes) == {
            "connector:slack:read",
            "connector:slack:write",
        }

    def test_project_scope_present_and_aligned(self) -> None:
        ctx = self.make_context(
            project_scopes=[
                "connector:slack:read",
                "connector:slack:write",
                "project:proj_42:read",
            ],
        )
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is True

    def test_owner_grants_superset_of_required(self) -> None:
        ctx = self.make_context(
            owner_scopes=[
                "connector:slack:read",
                "connector:slack:write",
                "connector:gmail:read",
            ],
        )
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is True
        # Effective is INTERSECT(declared, owner) so the extra owner scope
        # is intentionally dropped -- routine cannot exceed declared.
        assert "connector:gmail:read" not in result.effective_scopes


class TestPermissionShrinkage(RoutinePermissionContextMixin):
    def test_owner_lost_one_scope_returns_missing(self) -> None:
        ctx = self.make_context(owner_scopes=["connector:slack:read"])
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is False
        assert result.missing_scopes == ("connector:slack:write",)
        assert result.pause_reason is RoutinePauseReason.PERMISSION_SHRINKAGE

    def test_project_access_lost_returns_missing(self) -> None:
        # Project gate present but empty -> intersection collapses.
        ctx = self.make_context(project_scopes=[])
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is False
        assert set(result.missing_scopes) == {
            "connector:slack:read",
            "connector:slack:write",
        }
        assert result.pause_reason is RoutinePauseReason.PERMISSION_SHRINKAGE

    def test_disjoint_owner_returns_all_required_missing(self) -> None:
        ctx = self.make_context(
            owner_scopes=["connector:gmail:read"],
        )
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is False
        # Effective is empty (no overlap).
        assert result.effective_scopes == ()
        assert set(result.missing_scopes) == {
            "connector:slack:read",
            "connector:slack:write",
        }

    def test_required_is_subset_of_declared_and_owner_has_only_subset(
        self,
    ) -> None:
        # Routine declared two scopes but at-fire only one is required.
        # Owner still grants that one -> satisfied.
        ctx = self.make_context(
            routine_declared=[
                "connector:slack:read",
                "connector:slack:write",
            ],
            routine_required=["connector:slack:read"],
            owner_scopes=["connector:slack:read"],
        )
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is True


class TestOwnerOffboarded(RoutinePermissionContextMixin):
    def test_owner_disabled_short_circuits_to_offboarded(self) -> None:
        ctx = self.make_context(owner_disabled=True)
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is False
        assert result.pause_reason is RoutinePauseReason.OWNER_OFFBOARDED
        # All required scopes are listed missing even though owner.scopes
        # was technically fine -- offboarded owner means no perms.
        assert set(result.missing_scopes) == {
            "connector:slack:read",
            "connector:slack:write",
        }
        assert result.effective_scopes == ()


class TestConnectorDisconnected(RoutinePermissionContextMixin):
    def test_disconnected_connector_pauses_with_specific_reason(self) -> None:
        ctx = self.make_context(
            disconnected_connectors=("slack",),
        )
        result = check_routine_permissions(ctx)
        assert result.is_satisfied is False
        assert result.pause_reason is RoutinePauseReason.CRITICAL_CONNECTOR_DISCONNECTED
        assert set(result.missing_scopes) == {
            "connector:slack:read",
            "connector:slack:write",
        }

    def test_disconnected_connector_takes_priority_over_generic_shrinkage(
        self,
    ) -> None:
        # Both: owner lost a scope AND connector disconnected. Connector
        # reason wins so the Inbox CTA can route to the repair flow.
        ctx = self.make_context(
            owner_scopes=["connector:slack:read"],  # generic shrinkage too
            disconnected_connectors=("slack",),
        )
        result = check_routine_permissions(ctx)
        assert result.pause_reason is RoutinePauseReason.CRITICAL_CONNECTOR_DISCONNECTED

    def test_disconnected_unrelated_connector_falls_through(self) -> None:
        # The disconnected connector is not in the required scope set.
        ctx = self.make_context(
            disconnected_connectors=("gmail",),
        )
        result = check_routine_permissions(ctx)
        # No connector-related miss; routine is otherwise satisfied.
        assert result.is_satisfied is True


class TestNoAutoResume(RoutinePermissionContextMixin):
    """Cross-audit §9.7 Q5: re-granting perms does NOT auto-resume.

    The check is pure -- it has no notion of routine state transitions on
    its own. The "no auto-resume" rule is enforced by:

      a) the check returning ``is_satisfied=True`` does NOT trigger any
         side effect (the gate only side-effects on miss);
      b) no codepath in this module flips a paused routine back to active.

    We assert (a) here by calling check twice -- once shrunk, once
    restored -- and confirming the result reflects only the input
    intersection, never a memory of prior state.
    """

    def test_check_is_stateless_across_calls(self) -> None:
        shrunk = self.make_context(owner_scopes=["connector:slack:read"])
        restored = self.make_context()  # full grants

        first = check_routine_permissions(shrunk)
        assert first.is_satisfied is False

        second = check_routine_permissions(restored)
        # Same checker, scope restored. No memory of previous miss.
        assert second.is_satisfied is True
        # And critically: a second `restored` call still says "satisfied"
        # rather than "now allowed to resume" -- the resume decision is
        # not in this module's vocabulary.
        third = check_routine_permissions(restored)
        assert third.is_satisfied is True
        assert third.pause_reason is None

    def test_no_method_exists_to_auto_resume(self) -> None:
        # The pure module exposes ONE entry point. Anything that looked
        # like ``maybe_resume`` would be auto-resume in disguise.
        public_callables = {
            name for name in dir(RoutinePermissionChecker) if not name.startswith("_")
        }
        # Only `check` should be in the public surface.
        assert public_callables == {"check"}


class TestPydanticContractBoundary(RoutinePermissionContextMixin):
    def test_result_is_frozen(self) -> None:
        result = check_routine_permissions(self.make_context())
        with pytest.raises(Exception):
            # RuntimeContract sets frozen=True; mutation must fail.
            result.is_satisfied = False  # type: ignore[misc]

    def test_context_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            RoutinePermissionContext(  # type: ignore[call-arg]
                routine_id="rt_x",
                routine_declared_scopes=RoutinePermissionScope.from_iterable([]),
                routine_required_scopes=RoutinePermissionScope.from_iterable([]),
                owner_scopes=RoutinePermissionScope.from_iterable([]),
                unexpected_extra_field="oops",
            )
