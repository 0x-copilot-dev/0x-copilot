"""Permission intersection at routine fire time.

Implements cross-audit §9.7 Q4 / Q5 / Q11 + routines-prd §7.4:

    At fire time, before invoking the run:
        effective = INTERSECT(routine.permissions, owner.current, project.current if filed)
        if effective does not satisfy routine.permissions:
            1. Do NOT fire.
            2. routine.status := "paused", pause_reason := "permission_shrinkage"
            3. Inbox item (re-authorize CTA) -> owner
            4. Audit row: routine.auto_paused with context={reason, missing:[...]}

Hard rules baked into this module:

- Pure set semantics on permission scope strings (no auto-edit-down).
- No auto-resume on permission restoration -- this check has no concept of
  "the user got the perm back, restart the routine"; resume is owner-driven.
- No raw routine content / instructions / model output flows through here:
  inputs are typed permission shapes only, outputs are scope-string lists.

This module is pure: no I/O, no logging, no LLM/provider imports. The
caller (the routine scheduler / webhook handler) is responsible for the
auto-pause side effects -- this contract just tells them whether to fire
and what shrunk.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import ClassVar

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract


class RoutinePauseReason(StrEnum):
    """``pause_reason`` enum aligned with routines-prd §7.4 / cross-audit §9.7 Q4.

    Stable wire values -- audit consumers + SIEM dashboards filter on these.
    """

    PERMISSION_SHRINKAGE = "permission_shrinkage"
    OWNER_OFFBOARDED = "owner_offboarded"
    CRITICAL_CONNECTOR_DISCONNECTED = "critical_connector_disconnected"


class RoutinePermissionScope(RuntimeContract):
    """Permission scopes declared by a routine, an owner, or a project.

    A scope is the smallest checkable unit and is opaque to this module
    (e.g. ``"connector:slack:read"``, ``"project:proj_42:read"``,
    ``"skill:weekly_briefing:execute"``). The check operates on string set
    intersection -- the scope grammar is defined by the surrounding system.
    """

    scopes: tuple[str, ...] = Field(default_factory=tuple)

    @classmethod
    def from_iterable(cls, scopes: Iterable[str] | None) -> RoutinePermissionScope:
        """Build a scope set from any iterable; ``None`` becomes the empty set.

        Order is normalised (sorted) so equality + audit context is stable
        across calls. Duplicates are collapsed.
        """
        if scopes is None:
            return cls(scopes=())
        unique = tuple(sorted({s for s in scopes if isinstance(s, str)}))
        return cls(scopes=unique)

    def as_set(self) -> frozenset[str]:
        """Return the scope set as a frozenset for intersection / difference math."""
        return frozenset(self.scopes)


class RoutinePermissionCheckResult(RuntimeContract):
    """Output of :meth:`RoutinePermissionChecker.check`.

    - ``effective_scopes`` is the INTERSECT of routine + owner + (project
      if scoped). Sorted, deduplicated.
    - ``is_satisfied`` is true iff the effective set contains every scope
      the routine declared it required.
    - ``missing_scopes`` lists which required scopes shrunk away. Empty
      when ``is_satisfied`` is true.
    - ``pause_reason`` is set only when the check fails -- the caller
      reads this directly into the audit + Inbox item shape.
    """

    effective_scopes: tuple[str, ...] = Field(default_factory=tuple)
    is_satisfied: bool
    missing_scopes: tuple[str, ...] = Field(default_factory=tuple)
    pause_reason: RoutinePauseReason | None = None


class RoutinePermissionContext(RuntimeContract):
    """Immutable bundle of inputs to the intersection check.

    The routine carries two scope sets: the ones it *declared* (the
    superset granted when the user authored the routine) and the ones it
    *requires* to actually fire (which is normally the same set, but a
    subset is allowed for advanced routines that gate optional scopes).

    ``project_scopes`` is ``None`` when the routine is not project-scoped.
    A ``None`` here means "the project gate does not apply"; an empty
    scope set means "the project gate applies but currently grants
    nothing".

    ``owner_disabled`` short-circuits the check: if the owner is
    offboarded the intersection is empty regardless of scope math, and
    the result carries ``pause_reason=OWNER_OFFBOARDED``.
    """

    routine_id: str
    routine_declared_scopes: RoutinePermissionScope
    routine_required_scopes: RoutinePermissionScope
    owner_scopes: RoutinePermissionScope
    project_scopes: RoutinePermissionScope | None = None
    owner_disabled: bool = False
    disconnected_connectors: tuple[str, ...] = Field(default_factory=tuple)


class RoutinePermissionChecker:
    """Pure intersection logic for routine fire-time permission gating.

    The class wraps a single static method so callers reference the
    capability by name and not by raw module function (matches the
    "no module-level helpers" rule from services/ai-backend/CLAUDE.md).
    """

    _RESULT_CLASS: ClassVar[type[RoutinePermissionCheckResult]] = (
        RoutinePermissionCheckResult
    )

    @classmethod
    def check(cls, context: RoutinePermissionContext) -> RoutinePermissionCheckResult:
        """Return the intersection check result for ``context``.

        Order of resolution (each layer can fail-fast the check):

        1. ``owner_disabled`` -> empty effective, pause=owner_offboarded.
        2. ``disconnected_connectors`` overlap with required ->
           pause=critical_connector_disconnected, missing lists the
           disconnected scopes (suffixed by connector id so the Inbox CTA
           can route to the right repair flow).
        3. INTERSECT(routine_declared, owner, project?) and compare
           against routine_required. Missing -> pause=permission_shrinkage.
        """
        required = context.routine_required_scopes.as_set()

        if context.owner_disabled:
            return cls._RESULT_CLASS(
                effective_scopes=(),
                is_satisfied=False,
                missing_scopes=tuple(sorted(required)),
                pause_reason=RoutinePauseReason.OWNER_OFFBOARDED,
            )

        disconnected_hits = cls._connector_misses(
            required=required,
            disconnected_connectors=context.disconnected_connectors,
        )
        if disconnected_hits:
            return cls._RESULT_CLASS(
                effective_scopes=tuple(sorted(required - frozenset(disconnected_hits))),
                is_satisfied=False,
                missing_scopes=tuple(sorted(disconnected_hits)),
                pause_reason=RoutinePauseReason.CRITICAL_CONNECTOR_DISCONNECTED,
            )

        effective = cls._intersect_layers(
            routine_declared=context.routine_declared_scopes.as_set(),
            owner=context.owner_scopes.as_set(),
            project=(
                context.project_scopes.as_set()
                if context.project_scopes is not None
                else None
            ),
        )
        missing = required - effective
        if missing:
            return cls._RESULT_CLASS(
                effective_scopes=tuple(sorted(effective)),
                is_satisfied=False,
                missing_scopes=tuple(sorted(missing)),
                pause_reason=RoutinePauseReason.PERMISSION_SHRINKAGE,
            )
        return cls._RESULT_CLASS(
            effective_scopes=tuple(sorted(effective)),
            is_satisfied=True,
            missing_scopes=(),
            pause_reason=None,
        )

    @staticmethod
    def _intersect_layers(
        *,
        routine_declared: frozenset[str],
        owner: frozenset[str],
        project: frozenset[str] | None,
    ) -> frozenset[str]:
        """Compose the layered intersection.

        Routine is the outer cap (cannot exceed what was originally
        authorised). Owner is the current owner's grant. Project, when
        present, narrows further. ``None`` project = layer skipped.
        """
        effective = routine_declared & owner
        if project is not None:
            effective = effective & project
        return effective

    @staticmethod
    def _connector_misses(
        *,
        required: frozenset[str],
        disconnected_connectors: Sequence[str],
    ) -> frozenset[str]:
        """Return required scopes that mention a disconnected connector.

        Scope grammar: ``"connector:<id>:<verb>"``. A connector id appears
        in the second colon-separated segment. Anything that doesn't fit
        that shape is ignored here -- this is the *connector* gate only.
        """
        if not disconnected_connectors:
            return frozenset()
        broken = set(disconnected_connectors)
        hits: set[str] = set()
        for scope in required:
            parts = scope.split(":")
            if len(parts) >= 2 and parts[0] == "connector" and parts[1] in broken:
                hits.add(scope)
        return frozenset(hits)


def check_routine_permissions(
    context: RoutinePermissionContext,
) -> RoutinePermissionCheckResult:
    """Thin functional wrapper around :class:`RoutinePermissionChecker`.

    Kept as the public callsite name so future callers (scheduler,
    webhook handler) read naturally. The class carries the behaviour;
    this wrapper is the documented entry point named in the task spec.
    """
    return RoutinePermissionChecker.check(context)
