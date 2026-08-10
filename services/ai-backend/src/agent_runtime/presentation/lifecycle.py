"""Pure Canvas lifecycle projection over production runtime events (PRD-B3).

This is deliberately a structural fold: it accepts the same persisted event
shape served by replay/SSE and imports neither ``runtime_api`` nor React-facing
types.  The TypeScript twin in ``packages/chat-surface`` follows the exact
subject identity, ordering, and terminal-state rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


class CanvasLifecycleState(StrEnum):
    """What the canvas can say about itself — and ONLY about itself.

    There is deliberately no ``FAILED``. The canvas answers one question: "is
    there something to look at, and if not, why not?" A run-level verdict is not
    an answer to that question, and rendering one here put a second, often
    contradictory opinion beside the chat pane. Step failures belong on the step
    that failed; a terminal run failure belongs in the chat stream.

    Kept byte-equivalent with the client twin in ``canvasLifecycle.ts``; the
    differential corpus asserts both folds agree.
    """

    ASSEMBLING = "assembling"
    PRESENTING = "presenting"
    CHAT_ONLY = "chat_only"
    PARKED = "parked"
    COMPLETE_EMPTY = "complete_empty"


class CanvasSubjectKind(StrEnum):
    ARTIFACT = "artifact"
    SURFACE = "surface"
    EFFECT = "effect"
    RECEIPT = "receipt"


@dataclass(frozen=True, slots=True)
class CanvasSubject:
    """A stable presentation subject, not a renderer-specific URI."""

    key: str
    kind: CanvasSubjectKind
    subject_id: str
    title: str
    revision: int | None
    last_sequence_no: int
    priority: int
    renderer_hint: str | None = None


@dataclass(frozen=True, slots=True)
class CanvasProjection:
    lifecycle: CanvasLifecycleState
    tabs: tuple[CanvasSubject, ...]
    active_subject_key: str | None
    pending_subject_keys: tuple[str, ...]
    terminal_receipt: CanvasSubject | None
    activity_count: int
    #: Most recent failure text anywhere in the run, including a step the agent
    #: recovered from. Exposed for presentation; does NOT steer ``lifecycle``.
    failure: str | None
    has_final_response: bool
    terminal: bool
    #: The run's own terminal status, or ``None`` while it is still running.
    #: This — not ``failure`` — distinguishes a run that died from one that hit
    #: a bad step and carried on.
    terminal_status: str | None = None


@dataclass(slots=True)
class _SubjectAccumulator:
    key: str
    kind: CanvasSubjectKind
    subject_id: str
    title: str
    revision: int | None
    last_sequence_no: int
    priority: int
    renderer_hint: str | None

    def frozen(self) -> CanvasSubject:
        return CanvasSubject(
            key=self.key,
            kind=self.kind,
            subject_id=self.subject_id,
            title=self.title,
            revision=self.revision,
            last_sequence_no=self.last_sequence_no,
            priority=self.priority,
            renderer_hint=self.renderer_hint,
        )


class CanvasLifecycleProjection:
    """Fold all canvas-relevant shapes from the durable event stream.

    A tab only enters this projection after an explicit presentation fact:
    artifact decisions, a ``surface.created`` subject, or a staged effect. A
    read/tool object by itself remains activity. This is the core guard against
    the historic "mapping output ⇒ canvas" coupling.
    """

    _TERMINAL_RUN_STATUSES = frozenset(
        {"completed", "failed", "cancelled", "timed_out"}
    )
    _ACTIVITY_EVENTS = frozenset(
        {
            LedgerEventType.OPERATION_REQUESTED.value,
            LedgerEventType.OPERATION_CLASSIFIED.value,
            LedgerEventType.OPERATION_COMPLETED.value,
            LedgerEventType.OPERATION_FAILED.value,
            LedgerEventType.READ_EXECUTED.value,
            "tool_result",
            "tool_call_started",
        }
    )
    _FAILURE_EVENTS = frozenset({LedgerEventType.OPERATION_FAILED.value, "run_failed"})

    @classmethod
    def fold(cls, events: Iterable[Mapping[str, object] | object]) -> CanvasProjection:
        subjects: dict[str, _SubjectAccumulator] = {}
        artifact_keys: dict[str, str] = {}
        artifact_candidates: dict[str, _SubjectAccumulator] = {}
        artifact_decisions: dict[str, str] = {}
        surface_keys: dict[str, str] = {}
        effect_keys: dict[str, str] = {}
        open_gates: set[str] = set()
        pending_stages: set[str] = set()
        terminal_receipt: CanvasSubject | None = None
        activity_count = 0
        final_response = False
        terminal = False
        terminal_status: str | None = None
        failure: str | None = None

        for event in sorted(events, key=cls._sequence_no):
            event_type, payload, seq = cls._fields(event)
            if event_type is None:
                continue
            if event_type in cls._ACTIVITY_EVENTS:
                activity_count += 1
            if event_type == "final_response":
                final_response = True
            if event_type in cls._FAILURE_EVENTS:
                failure = (
                    cls._safe_failure(payload)
                    or failure
                    or "This run could not finish."
                )
            if event_type == "tool_result" and cls._is_failed(payload):
                failure = (
                    cls._safe_failure(payload) or failure or "This operation failed."
                )
            if event_type == "run_completed":
                terminal = True
                terminal_status = cls._text(payload.get("status"))
            elif event_type in {"run_failed", "run_cancelled", "run_timed_out"}:
                terminal = True
                terminal_status = event_type.removeprefix("run_")

            if event_type == LedgerEventType.ARTIFACT_CREATED.value:
                artifact_id = cls._text(payload.get("artifact_id"))
                kind = cls._text(payload.get("kind")) or "file"
                revision = cls._positive_int(payload.get("revision"))
                if artifact_id is None or revision is None:
                    continue
                key = f"artifact:{artifact_id}"
                artifact_keys[artifact_id] = key
                candidate = _SubjectAccumulator(
                    key=key,
                    kind=CanvasSubjectKind.ARTIFACT,
                    subject_id=artifact_id,
                    title=f"{kind} artifact",
                    revision=revision,
                    last_sequence_no=seq,
                    priority=300,
                    renderer_hint=f"artifact-{kind}",
                )
                artifact_candidates[artifact_id] = candidate
                # Creation is not presentation. Only the canonical decision
                # event below may put this candidate onto the canvas.
                if artifact_decisions.get(artifact_id) == "canvas":
                    subjects[key] = candidate
                continue
            if event_type == LedgerEventType.ARTIFACT_REVISED.value:
                artifact_id = cls._text(payload.get("artifact_id"))
                revision = cls._positive_int(payload.get("revision"))
                key = artifact_keys.get(artifact_id or "")
                if key is not None and revision is not None:
                    candidate = artifact_candidates.get(artifact_id or "")
                    if candidate is not None:
                        candidate.revision = revision
                        candidate.last_sequence_no = seq
                    subject = subjects.get(key)
                    if subject is not None:
                        subject.revision = revision
                        subject.last_sequence_no = seq
                continue
            if event_type == LedgerEventType.ARTIFACT_PRESENTATION_DECIDED.value:
                artifact_id = cls._text(payload.get("artifact_id"))
                decision = cls._text(payload.get("decision"))
                key = artifact_keys.get(artifact_id or "")
                if artifact_id is not None and decision is not None:
                    artifact_decisions[artifact_id] = decision
                # A decision can arrive before creation on a replay edge. The
                # decision is retained, but no synthetic subject is invented;
                # the later create establishes the durable candidate.
                if key is not None:
                    if decision == "canvas" and artifact_id in artifact_candidates:
                        subjects[key] = artifact_candidates[artifact_id]
                    elif decision != "canvas":
                        subjects.pop(key, None)
                continue
            if event_type == LedgerEventType.SURFACE_CREATED.value:
                surface_id = cls._text(payload.get("surface_id"))
                if surface_id is None:
                    continue
                kind = cls._text(payload.get("kind")) or "raw"
                key = f"surface:{surface_id}"
                surface_keys[surface_id] = key
                # A receipt is a rail/export artifact, not an automatically
                # selected canvas tab.  ``receipt.emitted`` retains it as a
                # terminal card so an explicit user action can open it later.
                if kind == "receipt":
                    continue
                subjects[key] = _SubjectAccumulator(
                    key=key,
                    kind=CanvasSubjectKind.SURFACE,
                    subject_id=surface_id,
                    title=cls._text(payload.get("title")) or "Untitled result",
                    revision=None,
                    last_sequence_no=seq,
                    priority=200,
                    renderer_hint=kind,
                )
                continue
            if event_type == LedgerEventType.VIEW_DERIVED.value:
                surface_id = cls._text(payload.get("surface_id"))
                key = surface_keys.get(surface_id or "")
                if key is not None:
                    subjects[key].last_sequence_no = seq
                continue
            if event_type in {
                LedgerEventType.WRITE_STAGED.value,
                LedgerEventType.EFFECT_STAGED.value,
            }:
                stage_id = cls._text(payload.get("stage_id"))
                if stage_id is None:
                    continue
                key = f"effect:{stage_id}"
                effect_keys[stage_id] = key
                pending_stages.add(key)
                subjects[key] = _SubjectAccumulator(
                    key=key,
                    kind=CanvasSubjectKind.EFFECT,
                    subject_id=stage_id,
                    title=cls._text(payload.get("display_target")) or "Proposed change",
                    revision=cls._positive_int(payload.get("revision")),
                    last_sequence_no=seq,
                    priority=400,
                    renderer_hint="effect-stage",
                )
                continue
            if event_type == LedgerEventType.EFFECT_REVISED.value:
                stage_id = cls._text(payload.get("stage_id"))
                key = effect_keys.get(stage_id or "")
                if key is not None:
                    subject = subjects.get(key)
                    if subject is not None:
                        revision = cls._positive_int(payload.get("revision"))
                        if revision is not None:
                            subject.revision = revision
                        title = cls._text(payload.get("display_target"))
                        if title is not None:
                            subject.title = title
                        subject.last_sequence_no = seq
                continue
            if event_type in {
                LedgerEventType.DECISION_RECORDED.value,
                LedgerEventType.EFFECT_DECISION_RECORDED.value,
            }:
                stage_id = cls._text(payload.get("stage_id"))
                key = effect_keys.get(stage_id or "")
                # A rejected or cancelled proposal no longer awaits a user
                # decision, but remains a reviewable terminal stage tab.
                if key is not None and cls._text(payload.get("decision")) in {
                    "reject",
                    "cancel",
                }:
                    pending_stages.discard(key)
                    subject = subjects.get(key)
                    if subject is not None:
                        subject.last_sequence_no = seq
                continue
            if event_type in {
                LedgerEventType.EFFECT_APPLIED.value,
                LedgerEventType.EFFECT_INDETERMINATE.value,
                LedgerEventType.EFFECT_RECONCILED.value,
                LedgerEventType.WRITE_APPLIED.value,
            }:
                stage_id = cls._text(payload.get("stage_id"))
                key = effect_keys.get(stage_id or "")
                if key is not None:
                    pending_stages.discard(key)
                    subjects[key].last_sequence_no = seq
                continue
            if event_type in {
                LedgerEventType.GATE_OPENED.value,
                LedgerEventType.GATE_OPENED_V2.value,
            }:
                gate_id = cls._text(payload.get("gate_id"))
                if gate_id is not None:
                    open_gates.add(gate_id)
                continue
            if event_type in {
                LedgerEventType.GATE_RESOLVED.value,
                LedgerEventType.GATE_RESOLVED_V2.value,
            }:
                gate_id = cls._text(payload.get("gate_id"))
                if gate_id is not None:
                    open_gates.discard(gate_id)
                continue
            if event_type == LedgerEventType.RECEIPT_EMITTED.value:
                surface_id = cls._text(payload.get("surface_id"))
                if surface_id is not None:
                    key = surface_keys.get(surface_id) or f"receipt:{surface_id}"
                    subject = subjects.get(key)
                    terminal_receipt = (
                        subject.frozen()
                        if subject is not None
                        else CanvasSubject(
                            key=key,
                            kind=CanvasSubjectKind.RECEIPT,
                            subject_id=surface_id,
                            title="Run receipt",
                            revision=None,
                            last_sequence_no=seq,
                            priority=10,
                            renderer_hint="receipt",
                        )
                    )

        ordered = tuple(
            subject.frozen()
            for subject in sorted(
                subjects.values(),
                key=lambda subject: (
                    -subject.priority,
                    -subject.last_sequence_no,
                    subject.key,
                ),
            )
        )
        # Receipts never steal active work. They are recommended only when they
        # are the sole canvas subject; a card/rail can always open them later.
        active = next(
            (item for item in ordered if item.kind is not CanvasSubjectKind.RECEIPT),
            None,
        )
        if active is None:
            active = ordered[0] if ordered else None
        pending = tuple(
            sorted((*pending_stages, *(f"gate:{gate}" for gate in open_gates)))
        )
        lifecycle = cls._lifecycle(
            terminal=terminal,
            final_response=final_response,
            has_subject=bool(ordered),
            has_pending=bool(pending),
        )
        return CanvasProjection(
            lifecycle=lifecycle,
            tabs=ordered,
            active_subject_key=active.key if active is not None else None,
            pending_subject_keys=pending,
            terminal_receipt=terminal_receipt,
            activity_count=activity_count,
            failure=failure,
            has_final_response=final_response,
            terminal=terminal,
            terminal_status=terminal_status,
        )

    @staticmethod
    def _lifecycle(
        *,
        terminal: bool,
        final_response: bool,
        has_subject: bool,
        has_pending: bool,
    ) -> CanvasLifecycleState:
        if has_pending:
            return CanvasLifecycleState.PARKED
        if has_subject:
            return CanvasLifecycleState.PRESENTING
        if not terminal:
            return CanvasLifecycleState.ASSEMBLING
        # ``failure`` / ``terminal_status`` are deliberately NOT consulted.
        # Whether a step (or the run) failed says nothing about whether the
        # canvas has a subject, and reading them here is what let one failed
        # tool call — including a recovered one — repaint the canvas as an
        # alarm. Both stay EXPOSED on the projection for presentation.
        if final_response:
            return CanvasLifecycleState.CHAT_ONLY
        return CanvasLifecycleState.COMPLETE_EMPTY

    @staticmethod
    def _fields(
        event: Mapping[str, object] | object,
    ) -> tuple[str | None, Mapping[str, object], int]:
        if isinstance(event, Mapping):
            event_type = event.get("event_type")
            payload = event.get("payload")
            sequence_no = event.get("sequence_no")
        else:
            event_type = getattr(event, "event_type", None)
            payload = getattr(event, "payload", None)
            sequence_no = getattr(event, "sequence_no", None)
        value = getattr(event_type, "value", None)
        text = (
            value
            if isinstance(value, str)
            else event_type
            if isinstance(event_type, str)
            else None
        )
        return (
            text,
            payload if isinstance(payload, Mapping) else {},
            CanvasLifecycleProjection._positive_int(sequence_no) or 0,
        )

    @staticmethod
    def _sequence_no(event: Mapping[str, object] | object) -> int:
        return CanvasLifecycleProjection._fields(event)[2]

    @staticmethod
    def _positive_int(value: object) -> int | None:
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else None
        )

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _is_failed(payload: Mapping[str, object]) -> bool:
        return CanvasLifecycleProjection._text(payload.get("status")) in {
            "failed",
            "error",
        }

    @staticmethod
    def _safe_failure(payload: Mapping[str, object]) -> str | None:
        for key in ("safe_message", "error_message", "failure_code"):
            value = CanvasLifecycleProjection._text(payload.get(key))
            if value is not None:
                return value[:240]
        return None


__all__ = (
    "CanvasLifecycleProjection",
    "CanvasLifecycleState",
    "CanvasProjection",
    "CanvasSubject",
    "CanvasSubjectKind",
)
