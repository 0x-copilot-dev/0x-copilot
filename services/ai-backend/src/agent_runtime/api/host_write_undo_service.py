"""Read + undo for the agent's writes to the user's real disk.

The capture side lives in
:mod:`agent_runtime.capabilities.desktop.write_journal` and runs inside the
graph loop. This is the other half: the owner-verified door a person reaches
through, which is what keeps the journal from being a recording nobody can play
back.

Three properties this layer owns, none of which belong in the floor:

* **Ownership.** The run is re-read through persistence and its ``org_id`` must
  match the verified identity before a single record is listed. The journal
  query is scoped by org as well, so guessing a run id gets nothing either way —
  belt and braces, because a revert writes to a disk.
* **Auditability.** A revert is a mutation of the user's files performed by the
  system, so it lands in ``runtime_audit_log`` with the run, the selection and
  the per-path outcome. An unlogged undo would be indistinguishable from the
  agent quietly writing again.
* **Granularity.** ``tool_call_id`` narrows the selection to ONE tool call.
  OpenCode's ``session/revert.ts`` reverts to a message or a part within a
  message for the same reason: a turn that did five things and one bad thing
  should cost the user the one thing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from pydantic import Field

from agent_runtime.capabilities.desktop.write_journal import (
    RETENTION_DAYS,
    HostWriteJournalPort,
    HostWriteKind,
    HostWriteRecord,
    HostWriteRevertOutcome,
    HostWriteReverter,
    RevertStatus,
)
from agent_runtime.execution.contracts import RuntimeContract

_LOGGER = logging.getLogger(__name__)

#: Audit action for an undo. Past-tense noun/verb pair matching the
#: ``<resource>.<verb>`` shape the SIEM export already indexes.
AUDIT_ACTION = "host_write.revert"


class HostWriteUndoNotFoundError(LookupError):
    """The run does not exist, or is not the caller's. Never distinguished."""


class HostWriteEntry(RuntimeContract):
    """One undoable change, in the projection a surface renders.

    Deliberately NOT the raw :class:`HostWriteRecord`: the storage digest and
    the authorizing root are internal facts. ``path`` is present because the
    user is being asked to approve putting a file back and has to recognise
    which one — the same direction of travel a folder grant allows (a path may
    travel toward consent, never toward bytes).
    """

    entry_id: str
    tool_call_id: str | None = None
    sequence: int
    path: str
    kind: HostWriteKind
    prior_size: int = 0
    revertible: bool
    captured_at: datetime


class HostWriteUndoListing(RuntimeContract):
    """Every undoable change for one run, oldest first."""

    run_id: str
    entries: tuple[HostWriteEntry, ...] = Field(default_factory=tuple)


class HostWriteRevertReport(RuntimeContract):
    """What an undo actually did, one row per affected path."""

    run_id: str
    tool_call_id: str | None = None
    outcomes: tuple[HostWriteRevertOutcome, ...] = Field(default_factory=tuple)

    @property
    def reverted(self) -> int:
        """How many paths were genuinely put back."""

        return sum(
            1
            for outcome in self.outcomes
            if outcome.status in (RevertStatus.RESTORED, RevertStatus.REMOVED)
        )


class HostWriteUndoService:
    """Owner-verified listing and reverting of one run's host writes."""

    def __init__(
        self,
        *,
        persistence: object,
        journal_store: HostWriteJournalPort,
        reverter: HostWriteReverter | None = None,
    ) -> None:
        self._persistence = persistence
        self._store = journal_store
        self._reverter = reverter or HostWriteReverter(journal_store)

    async def list_writes(
        self, *, org_id: str, run_id: str
    ) -> HostWriteUndoListing:
        """Every captured change for ``run_id``, after verifying ownership."""

        records = await self._owned_records(org_id=org_id, run_id=run_id)
        return HostWriteUndoListing(
            run_id=run_id,
            entries=tuple(self._entry(record) for record in records),
        )

    async def revert(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        tool_call_id: str | None = None,
    ) -> HostWriteRevertReport:
        """Undo the whole run, or just ``tool_call_id``, and audit the act.

        The audit row is written even when nothing was restored: "the user asked
        to undo and nothing came back" is precisely the event an operator needs
        to see, and dropping it would make a failed undo invisible.
        """

        records = await self._owned_records(org_id=org_id, run_id=run_id)
        selection = self._reverter.select(records, tool_call_id=tool_call_id)
        report = HostWriteRevertReport(
            run_id=run_id,
            tool_call_id=tool_call_id,
            outcomes=self._reverter.revert(selection),
        )
        await self._audit(org_id=org_id, user_id=user_id, report=report)
        return report

    def prune(self, *, now: datetime | None = None) -> int:
        """Drop captures past the retention window. Returns how many went."""

        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
        return self._store.prune(before=cutoff)

    async def _owned_records(
        self, *, org_id: str, run_id: str
    ) -> tuple[HostWriteRecord, ...]:
        """Re-read the run through persistence before touching the journal."""

        run = await self._persistence.get_run(  # type: ignore[attr-defined]
            org_id=org_id, run_id=run_id
        )
        if run is None or getattr(run, "org_id", None) != org_id:
            raise HostWriteUndoNotFoundError(run_id)
        return self._store.records_for_run(org_id=org_id, run_id=run_id)

    async def _audit(
        self, *, org_id: str, user_id: str, report: HostWriteRevertReport
    ) -> None:
        """Record the undo. A failure here must not un-restore the files."""

        try:
            await self._persistence.write_audit_log(  # type: ignore[attr-defined]
                event_type=AUDIT_ACTION,
                record={
                    "org_id": org_id,
                    "user_id": user_id,
                    "resource_type": "run",
                    "resource_id": report.run_id,
                    "run_id": report.run_id,
                    "outcome": "success" if report.outcomes else "no_op",
                    "metadata": {
                        "tool_call_id": report.tool_call_id,
                        "selected": len(report.outcomes),
                        "reverted": report.reverted,
                        # Per-path outcomes, not just a count: "which file came
                        # back and which refused" is the whole question after an
                        # undo, and a bare tally cannot answer it.
                        "outcomes": [
                            {
                                "path": outcome.path,
                                "kind": outcome.kind.value,
                                "status": outcome.status,
                            }
                            for outcome in report.outcomes
                        ],
                    },
                },
            )
        except Exception:  # noqa: BLE001 - the bytes are already back on disk
            _LOGGER.warning("host_write_undo.audit_failed", exc_info=True)

    @staticmethod
    def _entry(record: HostWriteRecord) -> HostWriteEntry:
        return HostWriteEntry(
            entry_id=record.entry_id,
            tool_call_id=record.tool_call_id,
            sequence=record.sequence,
            path=record.path,
            kind=record.kind,
            prior_size=record.prior_size,
            revertible=record.revertible,
            captured_at=record.captured_at,
        )


__all__ = (
    "AUDIT_ACTION",
    "HostWriteEntry",
    "HostWriteRevertReport",
    "HostWriteUndoListing",
    "HostWriteUndoNotFoundError",
    "HostWriteUndoService",
)
