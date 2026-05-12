"""In-flight tool call tracking for run-level reconciliation.

Records every `tool_call_started` event the worker emits and clears the
entry when a matching `tool_result` event fires naturally. When the run
hits a terminal failure path (asyncio.timeout, unhandled exception), the
handler iterates `unsettled()` and emits a synthetic terminal `tool_result`
for each entry — preventing orphaned "Running" cards from sticking on the
client when the run failed before LangGraph could close the loop.

The ledger is per-run, in-memory, lifecycle-scoped to a single run handler
invocation. Crash recovery after a worker death relies on the persisted
``runtime_tool_invocations`` projection rather than this in-memory ledger.
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
    # Settled-at timestamp drives the "latest settled" ordering for
    # ``pop_pending_attribution``.
    settled_at: datetime | None = None
    # Observed input-token cost for the call. Populated post-execute
    # by the tool-budget middleware so subsequent calls can enforce a
    # per-run input-token cap.
    input_tokens: int | None = None
    # Set when the middleware admitted the call against a budget.
    # ``charged_calls(tool_name)`` only counts entries with
    # ``budget_charged=True`` so REJECTED calls don't burn through the cap.
    budget_charged: bool = True
    # Connector that owned this tool; currently always ``None`` until a
    # tool-name → connector lookup is plumbed in.
    connector_slug: str | None = None
    # ``True`` once this entry has been popped as the "originating tool"
    # for an LLM call's attribution. Prevents double-attribution when
    # multiple LLM emits land in the same scope after a tool settles.
    consumed_for_attribution: bool = False
    # Monotonic settle order used as the primary sort key in
    # ``pop_pending_attribution`` so ties at the same wall-clock
    # microsecond still resolve deterministically.
    settle_order: int = 0


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
        # Monotonic settle counter for stable ordering even when two
        # ``observed_settled`` calls land in the same microsecond
        # (Python's ``datetime.now`` resolution is platform-dependent).
        self._settle_counter: int = 0

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
        replays into a cold ledger). Also stamps ``settled_at`` and primes
        the entry for :meth:`pop_pending_attribution`; the entry stays in
        the ledger with ``consumed_for_attribution=False`` until the next
        LLM emit in matching scope reads it.
        """

        entry = self._entries.get(call_id)
        if entry is not None:
            entry.settled = True
            if entry.settled_at is None:
                entry.settled_at = datetime.now(timezone.utc)
                self._settle_counter += 1
                entry.settle_order = self._settle_counter

    def pop_pending_attribution(
        self,
        scope_key: str | None,
    ) -> ToolCallEntry | None:
        """Return the most recently settled tool entry for ``scope_key``.

        At LLM emit time, the streaming executor asks the ledger "what
        tool's result did this call interpret?" The answer is the most-recent
        settled entry whose ``subagent_id`` matches the LLM call's scope
        (the LLM's own subagent slug, or ``None`` for orchestrator-scope calls).

        Pop semantics:

        - Filter by ``subagent_id == scope_key`` so a parallel
          subagent's tool result doesn't stamp an orchestrator-scope
          LLM call (and vice versa).
        - Filter by ``settled and not consumed_for_attribution`` —
          don't return entries that haven't fired their ``tool_result``
          yet, and don't double-attribute.
        - Return the entry with the latest ``settled_at``. Multiple
          parallel tools settling before a single LLM emit (e.g. a
          ReAct step that fans out): we pick one representative for
          attribution rather than splitting cost proportionally. The
          rest get marked consumed too so they don't leak to a later
          emit.

        Returns ``None`` if no eligible entry exists.
        """

        eligible = [
            entry
            for entry in self._entries.values()
            if entry.settled
            and not entry.consumed_for_attribution
            and entry.subagent_id == scope_key
        ]
        if not eligible:
            return None
        # Latest by ``settle_order`` (monotonic per ledger, set when
        # ``observed_settled`` fires). Wall-clock ``settled_at`` is
        # retained for reporting but is platform-resolution-sensitive
        # — two settles in the same microsecond would compare equal
        # under wall-clock alone and the choice would depend on
        # iteration order. ``settle_order`` is deterministic.
        latest = max(eligible, key=lambda e: e.settle_order)
        for entry in eligible:
            entry.consumed_for_attribution = True
        return latest

    def unsettled(self) -> list[ToolCallEntry]:
        """Return entries that have not yet been settled, oldest first.

        The handler iterates this on terminal failure paths to emit
        synthetic `tool_result` events for each in-flight call, so the
        client never sees a "Running" card outlive the run.
        """

        return [entry for entry in self._entries.values() if not entry.settled]

    def has_entries(self) -> bool:
        return bool(self._entries)

    # Accessors for the per-tool budget middleware -------------------------

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
