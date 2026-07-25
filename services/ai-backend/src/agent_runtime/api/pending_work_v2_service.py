"""Authorized, bounded aggregate for canonical v2.1 pending work.

The pure :mod:`agent_runtime.surfaces_v2.pending_work_v2` projection owns the
meaning of one run's ledger.  This module owns the *query boundary*: it obtains
only the caller's runs through the persistence port, reads each authorised
ledger, and returns a deliberately small public read model.

No proposal, target, workspace path, reason, reference, conversation title, or
event body crosses this boundary.  A corrupt or unreadable individual run is
omitted with a safe ``{run_id, status}`` warning; a failed candidate-run scan
fails closed rather than pretending that no work is pending.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)
from agent_runtime.surfaces_v2.pending_work_v2 import (
    PendingWorkItemV2,
    PendingWorkProjectionV2,
)


class PendingWorkV2Values:
    """Explicit run-page bounds for the fold-on-read aggregate."""

    DEFAULT_RUN_LIMIT = 20
    MAX_RUN_LIMIT = 50
    CURSOR_MAX_LENGTH = 512


class PendingWorkV2WarningStatus(StrEnum):
    """A safe statement that an authorised run was deliberately omitted."""

    OMITTED = "omitted"


class PendingWorkV2RunWarning(RuntimeContract):
    """No-detail omission marker for one caller-owned candidate run."""

    run_id: str
    status: Literal[PendingWorkV2WarningStatus.OMITTED] = (
        PendingWorkV2WarningStatus.OMITTED
    )


class PendingWorkV2Response(RuntimeContract):
    """A page of safe canonical v2.1 pending-work state."""

    v: Literal[2] = 2
    items: tuple[PendingWorkItemV2, ...] = ()
    warnings: tuple[PendingWorkV2RunWarning, ...] = ()
    next_cursor: str | None = None
    has_more: bool = False


class PendingWorkV2Error(RuntimeError):
    """Base error with intentionally safe public mapping."""


class PendingWorkV2InvalidCursor(PendingWorkV2Error):
    """A caller supplied a malformed pending-work-v2 cursor."""


class PendingWorkV2Unavailable(PendingWorkV2Error):
    """The candidate-run source is unavailable; never render a false empty queue."""


class PendingWorkV2Cursor:
    """Namespaced opaque keyset cursor for ``(created_at, run_id)`` pages.

    It is not an authorisation token: every page still queries through the
    persistence port with the verified ``(org_id, user_id)`` scope.  Namespace
    validation prevents a run-history cursor from accidentally being accepted by
    this independently versioned endpoint.
    """

    _PREFIX = "pending-work-v2"
    _SEPARATOR = "|"

    @classmethod
    def encode(cls, created_at: datetime, run_id: str) -> str:
        if not cls._is_safe_run_id(run_id):
            raise PendingWorkV2InvalidCursor
        if created_at.tzinfo is None:
            raise PendingWorkV2InvalidCursor
        raw = cls._SEPARATOR.join((cls._PREFIX, created_at.isoformat(), run_id))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, token: str | None) -> tuple[datetime, str] | None:
        if token is None:
            return None
        if (
            not isinstance(token, str)
            or not token
            or len(token) > PendingWorkV2Values.CURSOR_MAX_LENGTH
        ):
            raise PendingWorkV2InvalidCursor
        try:
            raw = base64.b64decode(
                token.encode("ascii"), altchars=b"-_", validate=True
            ).decode("utf-8")
            prefix, created_at_text, run_id = raw.split(cls._SEPARATOR, 2)
            created_at = datetime.fromisoformat(created_at_text)
        except (UnicodeError, ValueError, binascii.Error) as exc:
            raise PendingWorkV2InvalidCursor from exc
        if (
            prefix != cls._PREFIX
            or created_at.tzinfo is None
            or not cls._is_safe_run_id(run_id)
        ):
            raise PendingWorkV2InvalidCursor
        return created_at, run_id

    @staticmethod
    def _is_safe_run_id(value: object) -> bool:
        if not isinstance(value, str):
            return False
        # Reuse the projection's canonical opaque-id rule rather than duplicate
        # its regex.  An empty fold has no event-derived state to obscure this
        # validation result.
        return PendingWorkProjectionV2.fold_raw(value, ()).run_id == value


_PENDING_LEDGER_EVENT_TYPES = frozenset(
    {
        LedgerEventType.EFFECT_STAGED.value,
        LedgerEventType.EFFECT_REVISED.value,
        LedgerEventType.EFFECT_DECISION_RECORDED.value,
        LedgerEventType.EFFECT_CLAIMED.value,
        LedgerEventType.EFFECT_APPLIED.value,
        LedgerEventType.EFFECT_INDETERMINATE.value,
        LedgerEventType.EFFECT_RECONCILED.value,
        LedgerEventType.GATE_OPENED_V2.value,
        LedgerEventType.GATE_RESOLVED_V2.value,
    }
)


@dataclass(frozen=True)
class PendingWorkV2QueryService:
    """Fold a bounded, identity-scoped run page into safe pending work."""

    persistence: object
    event_store: object

    async def list_pending(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = PendingWorkV2Values.DEFAULT_RUN_LIMIT,
        cursor: str | None = None,
    ) -> PendingWorkV2Response:
        """Return only caller-owned runs, ordered newest-run then newest subject.

        Pagination is over candidate *runs*, not over raw events.  This keeps
        every item's owning ``run_id`` present and makes the per-run fold a
        single, total ledger replay.  The persistence port owns the authoritative
        ``(org_id, user_id)`` predicate; no caller-supplied id becomes a filter.
        """

        bounded_limit = self._bounded_limit(limit)
        keyset = PendingWorkV2Cursor.decode(cursor)
        records = await self._list_runs(
            org_id=org_id,
            user_id=user_id,
            limit=bounded_limit + 1,
            before_created_at=keyset[0] if keyset is not None else None,
            before_run_id=keyset[1] if keyset is not None else None,
        )
        has_more = len(records) > bounded_limit
        page = tuple(records[:bounded_limit])

        items: list[PendingWorkItemV2] = []
        warnings: list[PendingWorkV2RunWarning] = []
        seen_items: set[tuple[str, str, str]] = set()
        warned_runs: set[str] = set()

        for record in page:
            run_id = self._field(record, "run_id")
            if not PendingWorkV2Cursor._is_safe_run_id(run_id):
                # Do not echo a malformed persistence identifier into a public
                # body or cursor.  It is neither a normal empty queue nor safe
                # enough to name in a warning.
                continue
            assert isinstance(run_id, str)
            projection = await self._project_run(org_id=org_id, run_id=run_id)
            if projection is None:
                if run_id not in warned_runs:
                    warnings.append(PendingWorkV2RunWarning(run_id=run_id))
                    warned_runs.add(run_id)
                continue
            for item in projection:
                key = (item.run_id, item.subject_kind.value, item.subject_id)
                if key in seen_items:
                    continue
                seen_items.add(key)
                items.append(item)

        # ``page`` is ordered ``(created_at DESC, run_id DESC)`` by the port.
        # The fold's sequence numbers are only comparable inside a run, so use
        # the run-page index first and a deterministic newest-subject ordering
        # within each run.
        run_order = {
            run_id: index
            for index, record in enumerate(page)
            if isinstance((run_id := self._field(record, "run_id")), str)
        }
        items.sort(
            key=lambda item: (
                run_order.get(item.run_id, len(page)),
                -item.latest_sequence_no,
                -item.opened_sequence_no,
                item.subject_kind.value,
                item.subject_id,
            )
        )
        warnings.sort(
            key=lambda warning: (
                run_order.get(warning.run_id, len(page)),
                warning.run_id,
            )
        )

        next_cursor = None
        if has_more and page:
            boundary = page[-1]
            boundary_run_id = self._field(boundary, "run_id")
            boundary_created_at = self._field(boundary, "created_at")
            try:
                if not isinstance(boundary_created_at, datetime):
                    raise PendingWorkV2InvalidCursor
                next_cursor = PendingWorkV2Cursor.encode(
                    boundary_created_at,
                    boundary_run_id if isinstance(boundary_run_id, str) else "",
                )
            except PendingWorkV2InvalidCursor:
                # A corrupted storage row must not leak through an encoded
                # cursor.  Stop this page rather than manufacture an unsafe
                # continuation token.
                has_more = False

        return PendingWorkV2Response(
            items=tuple(items),
            warnings=tuple(warnings),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def _list_runs(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        before_created_at: datetime | None,
        before_run_id: str | None,
    ) -> Sequence[object]:
        method = getattr(self.persistence, "list_runs_for_org", None)
        if method is None:
            raise PendingWorkV2Unavailable
        try:
            records = await method(
                org_id=org_id,
                user_id=user_id,
                limit=limit,
                before_created_at=before_created_at,
                before_run_id=before_run_id,
            )
            return tuple(records)
        except PendingWorkV2Error:
            raise
        except Exception as exc:  # noqa: BLE001 - public queue must not lie empty
            raise PendingWorkV2Unavailable from exc

    async def _project_run(
        self, *, org_id: str, run_id: str
    ) -> tuple[PendingWorkItemV2, ...] | None:
        method = getattr(self.event_store, "list_events_after", None)
        if method is None:
            return None
        try:
            events = await method(org_id=org_id, run_id=run_id, after_sequence=0)
            normalized = self._normalize_events(expected_run_id=run_id, events=events)
            if normalized is None:
                return None
            projection = PendingWorkProjectionV2.fold_raw(run_id, normalized)
            if projection.run_id != run_id:
                return None
            return projection.items
        except Exception:  # noqa: BLE001 - one bad run is a safe omission
            return None

    @classmethod
    def _normalize_events(
        cls,
        *,
        expected_run_id: str,
        events: object,
    ) -> tuple[dict[str, object], ...] | None:
        """Validate every envelope and every event that affects pending state.

        Unknown non-effect events are intentionally retained as harmless ledger
        context for forward compatibility.  Unknown ``effect.*`` or v2 gate
        events, and malformed known effect/gate payloads, omit the whole run:
        silently ignoring an approval/terminal transition could falsely show a
        stale pending action.
        """

        if isinstance(events, (str, bytes)):
            return None
        try:
            iterator = iter(events)  # type: ignore[arg-type]
        except TypeError:
            return None

        previous_sequence_no = 0
        rows: list[dict[str, object]] = []
        for event in iterator:
            run_id = cls._field(event, "run_id")
            sequence_no = cls._field(event, "sequence_no")
            event_type = cls._event_type_value(cls._field(event, "event_type"))
            payload = cls._field(event, "payload")
            if (
                run_id != expected_run_id
                or not isinstance(sequence_no, int)
                or isinstance(sequence_no, bool)
                or sequence_no <= previous_sequence_no
                or event_type is None
                or not isinstance(payload, Mapping)
            ):
                return None
            previous_sequence_no = sequence_no
            if event_type in _PENDING_LEDGER_EVENT_TYPES:
                try:
                    WorkLedgerVocabulary.validate_payload(event_type, payload)
                except Exception:  # noqa: BLE001 - invalid transition is unsafe
                    return None
            elif event_type.startswith("effect.") or event_type in {
                LedgerEventType.GATE_OPENED_V2.value,
                LedgerEventType.GATE_RESOLVED_V2.value,
            }:
                return None
            rows.append(
                {
                    "event_type": event_type,
                    "sequence_no": sequence_no,
                    "payload": dict(payload),
                }
            )
        return tuple(rows)

    @staticmethod
    def _field(record: object, key: str) -> object:
        if isinstance(record, Mapping):
            return record.get(key)
        return getattr(record, key, None)

    @staticmethod
    def _event_type_value(value: object) -> str | None:
        raw = getattr(value, "value", value)
        return raw if isinstance(raw, str) and raw else None

    @staticmethod
    def _bounded_limit(limit: object) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool):
            return PendingWorkV2Values.DEFAULT_RUN_LIMIT
        return min(max(1, limit), PendingWorkV2Values.MAX_RUN_LIMIT)


__all__ = [
    "PendingWorkV2Cursor",
    "PendingWorkV2Error",
    "PendingWorkV2InvalidCursor",
    "PendingWorkV2QueryService",
    "PendingWorkV2Response",
    "PendingWorkV2RunWarning",
    "PendingWorkV2Unavailable",
    "PendingWorkV2Values",
    "PendingWorkV2WarningStatus",
]
