"""In-flight tool call tracking for run-level reconciliation.

Records every `tool_call_started` event the worker emits and clears the
entry when a matching `tool_result` event fires naturally. When the run
hits a terminal failure path (asyncio.timeout, unhandled exception), the
handler iterates `unsettled()` and emits a synthetic terminal `tool_result`
for each entry — preventing orphaned "Running" cards from sticking on the
client when the run failed before LangGraph could close the loop.

The ledger is per-run, in-memory, lifecycle-scoped to a single run handler
invocation. Crash recovery (worker death) is the reaper's job (Phase 3) and
relies on the persisted `runtime_tool_invocations` projection rather than
this in-memory ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ToolCallEntry:
    """Bookkeeping for a single in-flight tool call."""

    call_id: str
    tool_name: str
    parent_task_id: str | None = None
    subagent_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    settled: bool = False


class ToolCallLedger:
    """Per-run tracker of in-flight tool calls.

    Thread-safety: not threadsafe. Each `RuntimeRunHandler` invocation
    serially streams its own LangGraph events, so a single ledger only ever
    sees one writer. Concurrent tool calls within a run are fine because
    each writes to a distinct `call_id` slot.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._entries: dict[str, ToolCallEntry] = {}

    def started(
        self,
        call_id: str,
        *,
        tool_name: str,
        parent_task_id: str | None = None,
        subagent_id: str | None = None,
    ) -> None:
        """Record that a tool call has begun. Idempotent on repeat call_ids."""

        if call_id in self._entries:
            return
        self._entries[call_id] = ToolCallEntry(
            call_id=call_id,
            tool_name=tool_name,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
        )

    def observed_settled(self, call_id: str) -> None:
        """Mark the call as naturally settled (a `tool_result` fired).

        No-op if the call_id is unknown — this can happen for tool_results
        emitted before a corresponding tool_call_started (e.g. event-store
        replays into a cold ledger).
        """

        entry = self._entries.get(call_id)
        if entry is not None:
            entry.settled = True

    def unsettled(self) -> list[ToolCallEntry]:
        """Return entries that have not yet been settled, oldest first.

        The handler iterates this on terminal failure paths to emit
        synthetic `tool_result` events for each in-flight call, so the
        client never sees a "Running" card outlive the run.
        """

        return [entry for entry in self._entries.values() if not entry.settled]

    def has_entries(self) -> bool:
        return bool(self._entries)
