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
    # B8 — observed input-token cost for the call. Populated post-execute
    # by the tool-budget middleware so subsequent calls can enforce a
    # per-run input-token cap.
    input_tokens: int | None = None
    # B8 — set when the middleware admitted the call against a budget.
    # ``charged_calls(tool_name)`` only counts entries with
    # ``budget_charged=True`` so REJECTED calls don't burn through the
    # cap.
    budget_charged: bool = True


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

    # B8 — accessors for the per-tool budget middleware -------------------

    def charged_calls(self, tool_name: str) -> int:
        """Return the number of admitted calls to ``tool_name`` in this run.

        REJECTED calls (``budget_charged=False``) do not count: a
        rejection must not consume the budget it just blocked, otherwise
        the model could be permanently locked out by a single bad call.
        """

        return sum(
            1
            for entry in self._entries.values()
            if entry.tool_name == tool_name and entry.budget_charged
        )

    def total_input_tokens(self, tool_name: str) -> int:
        """Sum observed input tokens across admitted calls to ``tool_name``."""

        return sum(
            entry.input_tokens or 0
            for entry in self._entries.values()
            if entry.tool_name == tool_name and entry.budget_charged
        )

    def record_input_tokens(self, call_id: str, tokens: int) -> None:
        """Stamp observed input tokens on an entry. No-op for unknown call_ids."""

        entry = self._entries.get(call_id)
        if entry is None:
            return
        entry.input_tokens = tokens

    def mark_rejected(self, call_id: str) -> None:
        """Flag an entry as rejected so it does not count toward the cap."""

        entry = self._entries.get(call_id)
        if entry is None:
            return
        entry.budget_charged = False
