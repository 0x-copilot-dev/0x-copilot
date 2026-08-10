"""Pre-fire gate for routines: permission intersection + auto-pause flow.

This module is a thin coordinator. P5-A2 owns the main scheduler /
trigger loop; this file contributes ONE function -- the pre-fire hook
that P5-A2's loop and P5-A3's webhook handler call before submitting a
routine run.

Responsibilities at the gate (per routines-prd §7.4 + cross-audit §9.7
Q4/Q5/Q11):

1. Compute INTERSECT(routine, owner, project) via
   :func:`check_routine_permissions`.
2. If the routine is satisfied -> return ``allow=True`` (caller fires).
3. If not -> drive the auto-pause flow:
   a. Transition the routine to ``status="paused"`` with the right
      ``pause_reason`` via the injected pause port (service-token
      boundary owned by P5-A1's CRUD).
   b. Emit one Inbox item with a re-authorize CTA via P4-A2's producer
      interface (we depend on the port shape; the concrete adapter is
      wired by the runtime container at startup).
   c. Write a ``routine.auto_paused`` audit row with the full
      ``context.missing`` list.
4. Return ``allow=False`` so the caller skips the fire entirely.

No-auto-resume rule (cross-audit §9.7 Q5) is enforced by the absence of
the inverse path -- nothing in this module re-activates a routine when
permissions are restored. Resume is owner-driven through the normal
routine PATCH endpoint.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import ClassVar, Protocol

from pydantic import Field

from agent_runtime.api.routine_permission_check import (
    RoutinePauseReason,
    RoutinePermissionCheckResult,
    RoutinePermissionContext,
    check_routine_permissions,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.http_logging import LoggingConfigurator


class RoutineInboxItemKind:
    """Inbox item ``kind`` values used by the routine pause flow.

    ``ROUTINE_PAUSED`` is the target wire value once P4-A1's InboxItemKind
    union adds it. Until then, callers may translate it to the fallback
    ``AGENT_QUESTION`` in their adapter -- see ``WIRE_SHAPE_NOTE`` below.
    Keeping the ideal value here so the producer signature reflects the
    target shape and the fallback is an adapter concern, not a domain one.
    """

    ROUTINE_PAUSED: ClassVar[str] = "routine_paused"
    AGENT_QUESTION: ClassVar[str] = "agent_question"

    # WIRE_SHAPE_NOTE (cross-merge with P4-A1):
    # P4-A1's InboxItemKind union currently lacks "routine_paused". The
    # producer adapter at integration time must either (a) extend the
    # union to add ROUTINE_PAUSED -- the preferred path, captured as
    # a merge-time extension request -- or (b) downshift to
    # AGENT_QUESTION temporarily. Domain code emits ROUTINE_PAUSED.


class RoutineInboxLinkKind:
    """Stable link kinds for Inbox CTA target refs."""

    ROUTINE: ClassVar[str] = "routine"
    AGENT: ClassVar[str] = "agent"


class RoutineInboxSenderKind:
    """Sender ref kinds for Inbox items emitted by the routine system."""

    AGENT: ClassVar[str] = "agent"


class RoutineInboxItemRef(RuntimeContract):
    """A typed ref to a related resource on an Inbox item."""

    kind: str
    id: str


class RoutineInboxItemSpec(RuntimeContract):
    """The wire-shape contract the InboxProducer adapter consumes.

    Mirrors the P4-A2 InboxProducer interface without importing it -- we
    don't yet have a merged P4-A2 module to import from, and over-coupling
    on a not-yet-stable type would block this PR. The adapter is wired at
    integration time and converts this spec to the concrete InboxItem
    record format.
    """

    kind: str
    title: str
    body_ref: RoutineInboxItemRef
    sender_ref: RoutineInboxItemRef
    links: tuple[RoutineInboxItemRef, ...] = Field(default_factory=tuple)
    priority: str = "high"
    org_id: str
    owner_user_id: str


class RoutineToPauseSummary(RuntimeContract):
    """Minimum routine fields needed to drive the pause flow.

    Decoupled from P5-A1's full ``Routine`` record so this module can ship
    independently of that schema. The pause port adapter does the
    cross-walk on call.
    """

    routine_id: str
    org_id: str
    owner_user_id: str
    agent_id: str | None = None
    project_id: str | None = None


class RoutinePauseDecision(RuntimeContract):
    """Caller-facing outcome from :meth:`RoutinePreFireGate.evaluate`.

    - ``allow_fire`` -- if true the caller may submit the routine run;
      everything else is empty.
    - ``pause_reason`` / ``missing_scopes`` -- populated only when
      ``allow_fire`` is false. The scheduler / webhook handler does not
      reach into the check result directly; it consumes this shape.
    """

    allow_fire: bool
    pause_reason: RoutinePauseReason | None = None
    missing_scopes: tuple[str, ...] = Field(default_factory=tuple)
    effective_scopes: tuple[str, ...] = Field(default_factory=tuple)


class RoutinePausePort(Protocol):
    """Service-token boundary into P5-A1's routine CRUD.

    The pre-fire gate doesn't own routine state; it asks P5-A1 to do the
    transition. The adapter is the only place that holds the
    service-token credentials needed to PATCH a routine owned by another
    user.
    """

    async def mark_routine_paused(
        self,
        *,
        routine_id: str,
        org_id: str,
        pause_reason: RoutinePauseReason,
        missing_scopes: Sequence[str],
        occurred_at: datetime,
    ) -> None:
        """Transition the routine to ``status="paused"`` with the given reason."""


class RoutineInboxProducerPort(Protocol):
    """P4-A2's InboxProducer surface, narrowed to what this flow needs.

    The integration adapter validates the spec, hashes any sensitive
    references, and writes the inbox row. We expose only the publish
    method so this module never accumulates inbox state.
    """

    async def publish_routine_paused_item(
        self,
        spec: RoutineInboxItemSpec,
    ) -> None:
        """Persist + fan-out an Inbox item for an auto-paused routine."""


class RoutineAuditPort(Protocol):
    """The audit emission surface used by the gate.

    Implemented in production by an adapter that wraps
    :class:`runtime_worker.audit.WorkerAuditEmitter` -- the adapter adds
    the routine-specific ``emit_routine_auto_paused`` method while keeping
    the WorkerAuditEmitter free of P5-specific knowledge until the rest of
    P5 lands.
    """

    async def emit_routine_auto_paused(
        self,
        *,
        routine: RoutineToPauseSummary,
        pause_reason: RoutinePauseReason,
        missing_scopes: Sequence[str],
        effective_scopes: Sequence[str],
    ) -> None:
        """Emit one ``routine.auto_paused`` audit row with the full context."""


class RoutinePreFireGate:
    """Pre-fire gate: run the permission check and drive auto-pause on miss.

    Wired into P5-A2's scheduler loop as the LAST step before submitting a
    queued routine run, and into P5-A3's webhook handler immediately
    after secret verification. Both call sites pass the same context
    shape; both consume the same :class:`RoutinePauseDecision`.

    Idempotency: a routine that's already paused will not be re-paused by
    this gate (the scheduler is expected to skip non-active routines
    before calling this), but the adapter implementations of the pause +
    inbox + audit ports should be idempotent on routine_id + reason so a
    retry never produces duplicate inbox items.
    """

    _LOGGER_NAME: ClassVar[str] = "runtime_worker.jobs.routine_scheduler"

    _INBOX_TITLES: ClassVar[dict[RoutinePauseReason, str]] = {
        RoutinePauseReason.PERMISSION_SHRINKAGE: ("Routine paused: permissions shrunk"),
        RoutinePauseReason.OWNER_OFFBOARDED: ("Routine paused: owner offboarded"),
        RoutinePauseReason.CRITICAL_CONNECTOR_DISCONNECTED: (
            "Routine paused: critical connector disconnected"
        ),
    }

    def __init__(
        self,
        *,
        pause_port: RoutinePausePort,
        inbox_port: RoutineInboxProducerPort,
        audit_port: RoutineAuditPort,
        clock: "Clock | None" = None,
    ) -> None:
        self._pause_port = pause_port
        self._inbox_port = inbox_port
        self._audit_port = audit_port
        self._clock = clock or _SystemClock()
        self._logger = LoggingConfigurator.get_logger(self._LOGGER_NAME)

    async def evaluate(
        self,
        *,
        routine: RoutineToPauseSummary,
        permission_context: RoutinePermissionContext,
    ) -> RoutinePauseDecision:
        """Run the gate; on miss, drive the auto-pause side effects.

        Side effects fire in the order: pause -> inbox -> audit. The audit
        row is last so a SIEM consumer sees the pause + the user-visible
        notification as already-committed by the time the audit row
        arrives. Each port is expected to be best-effort durable; we do
        not gate the audit row on inbox success so a producer outage does
        not silently swallow the auto_paused record.
        """
        result = check_routine_permissions(permission_context)
        if result.is_satisfied:
            return RoutinePauseDecision(
                allow_fire=True,
                pause_reason=None,
                missing_scopes=(),
                effective_scopes=result.effective_scopes,
            )
        # Defensive: a satisfied=False result must have a pause_reason --
        # treat its absence as permission_shrinkage so we never silently
        # skip the audit row. The check module sets this for every failure
        # branch today.
        pause_reason = result.pause_reason or RoutinePauseReason.PERMISSION_SHRINKAGE
        await self._drive_auto_pause(
            routine=routine,
            check_result=result,
            pause_reason=pause_reason,
        )
        return RoutinePauseDecision(
            allow_fire=False,
            pause_reason=pause_reason,
            missing_scopes=result.missing_scopes,
            effective_scopes=result.effective_scopes,
        )

    async def _drive_auto_pause(
        self,
        *,
        routine: RoutineToPauseSummary,
        check_result: RoutinePermissionCheckResult,
        pause_reason: RoutinePauseReason,
    ) -> None:
        """Execute the three-step auto-pause cascade: pause -> inbox -> audit."""
        now = self._clock.now()
        await self._pause_port.mark_routine_paused(
            routine_id=routine.routine_id,
            org_id=routine.org_id,
            pause_reason=pause_reason,
            missing_scopes=check_result.missing_scopes,
            occurred_at=now,
        )
        await self._inbox_port.publish_routine_paused_item(
            self._build_inbox_spec(routine=routine, pause_reason=pause_reason)
        )
        await self._audit_port.emit_routine_auto_paused(
            routine=routine,
            pause_reason=pause_reason,
            missing_scopes=check_result.missing_scopes,
            effective_scopes=check_result.effective_scopes,
        )
        # Structured log: NO scope strings, NO routine name, NO instructions.
        # Only ids + enum values -- the missing scope list lives in the audit
        # row only (it can contain connector/skill identifiers that we treat
        # as sensitive in app logs).
        self._logger.info(
            "routine_auto_paused",
            metadata={
                "routine_id": routine.routine_id,
                "org_id": routine.org_id,
                "owner_user_id": routine.owner_user_id,
                "pause_reason": pause_reason.value,
                "missing_count": len(check_result.missing_scopes),
            },
        )

    def _build_inbox_spec(
        self,
        *,
        routine: RoutineToPauseSummary,
        pause_reason: RoutinePauseReason,
    ) -> RoutineInboxItemSpec:
        """Assemble the Inbox item spec for the re-authorize CTA.

        ``body_ref`` points at the routine detail page; the user lands on
        the Routine destination's detail view and the re-auth CTA is
        rendered inline by the surface (no auto-action -- manual resume
        per cross-audit §9.7 Q5).
        """
        links: list[RoutineInboxItemRef] = [
            RoutineInboxItemRef(
                kind=RoutineInboxLinkKind.ROUTINE,
                id=routine.routine_id,
            )
        ]
        if routine.agent_id is not None:
            links.append(
                RoutineInboxItemRef(
                    kind=RoutineInboxLinkKind.AGENT,
                    id=routine.agent_id,
                )
            )
        return RoutineInboxItemSpec(
            kind=RoutineInboxItemKind.ROUTINE_PAUSED,
            title=self._INBOX_TITLES.get(
                pause_reason,
                self._INBOX_TITLES[RoutinePauseReason.PERMISSION_SHRINKAGE],
            ),
            body_ref=RoutineInboxItemRef(
                kind=RoutineInboxLinkKind.ROUTINE,
                id=routine.routine_id,
            ),
            sender_ref=RoutineInboxItemRef(
                kind=RoutineInboxSenderKind.AGENT,
                id=routine.agent_id or _Values.SYSTEM_AGENT_ID,
            ),
            links=tuple(links),
            priority=_Values.INBOX_PRIORITY_HIGH,
            org_id=routine.org_id,
            owner_user_id=routine.owner_user_id,
        )


class _Values:
    """Stable enum-y constants used by the gate's Inbox spec."""

    INBOX_PRIORITY_HIGH: ClassVar[str] = "high"
    SYSTEM_AGENT_ID: ClassVar[str] = "system:routines"


class Clock(Protocol):
    """Tiny clock surface so tests can pin the audit ``occurred_at``."""

    def now(self) -> datetime: ...


class _SystemClock:
    """Default UTC clock implementation."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers exposed for adapters that translate domain types <-> wire types.
# ---------------------------------------------------------------------------


def build_permission_context_from_scope_lists(
    *,
    routine_id: str,
    routine_declared: Iterable[str] | None,
    routine_required: Iterable[str] | None,
    owner_scopes: Iterable[str] | None,
    project_scopes: Iterable[str] | None,
    owner_disabled: bool = False,
    disconnected_connectors: Iterable[str] | None = None,
) -> RoutinePermissionContext:
    """Convenience builder for callers that hold plain scope-string lists.

    Project-scope handling: pass ``None`` for routines not filed under a
    project. Passing an empty iterable signals "project gate applies but
    currently grants nothing" which is a distinct outcome (the
    intersection collapses to empty).
    """
    from agent_runtime.api.routine_permission_check import RoutinePermissionScope

    project = (
        RoutinePermissionScope.from_iterable(project_scopes)
        if project_scopes is not None
        else None
    )
    return RoutinePermissionContext(
        routine_id=routine_id,
        routine_declared_scopes=RoutinePermissionScope.from_iterable(routine_declared),
        routine_required_scopes=RoutinePermissionScope.from_iterable(routine_required),
        owner_scopes=RoutinePermissionScope.from_iterable(owner_scopes),
        project_scopes=project,
        owner_disabled=owner_disabled,
        disconnected_connectors=tuple(disconnected_connectors or ()),
    )
