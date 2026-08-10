"""Total result admission: every tool, every shape, every configuration.

:mod:`agent_runtime.context.tool_result_admission` states *how* one tool return
is bounded.  This module states *that every one of them is*, and it closes the
three ways a result used to get past the boundary.

**Not every tool was wrapped.**  A ``BaseTool`` decorator cannot be the model/
tool boundary, because Deep Agents adds todo, filesystem, execute, and task
tools after the caller's list is assembled, and a locally compiled subagent
gets its own copies.  Step 2 already solved this shape for admission control:
:class:`~agent_runtime.capabilities.middleware.runtime_tool_control.RuntimeControlMiddleware`
runs around *every* tool the completed graph exposes, framework-injected and
delegated ones included, and its result sweep already routes each emitted
``ToolMessage`` through the guard.  This gate is what that seam calls, so
coverage is a property of the graph's topology rather than of who remembered to
wrap a tool.

**The gate itself was optional.**  Admission bounded a result only where an
object store had been wired up -- the desktop file-native store -- and returned
the raw result everywhere else.  An optional boundary is not a boundary: the
same oversized result was bounded or unbounded depending on deployment.  So
this gate is constructible with *no* arguments: absent a durable writer it
offloads to a bounded :class:`InProcessOffloadWriter` rather than declining to
bound.  A durable store is strictly better and remains the production wiring;
its absence is no longer permission to hand the model an unbounded result.

**The decision was made twice.**  The value handed to the model and the value
projected into a persisted tool event were budgeted independently at two
layers.  Here the decision is made once and *recorded* once, as the raw-free
:class:`~agent_runtime.context.tool_result_admission.ToolResultAdmissionFact`,
and both paths read that record.  :meth:`ToolResultAdmissionGate.verify_model_visible`
then makes the ordering enforceable rather than conventional: content that was
never admitted for a call is refused at the point it would become model-visible,
so "admission happens before the result becomes a ``ToolMessage``" fails closed
instead of relying on every future call site remembering it.
"""

from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
from threading import Lock
from typing import ClassVar

from agent_runtime.context.memory.contracts import TokenBudgetPolicy
from agent_runtime.context.memory.summarization import OffloadWriter
from agent_runtime.context.tool_result_admission import (
    ToolResultAdmission,
    ToolResultAdmissionAdapter,
    ToolResultAdmissionFact,
)


class ToolResultAdmissionBypassed(RuntimeError):
    """Model-visible content was never admitted for the call that carries it.

    Raised at the point content would become a ``ToolMessage``.  It is a
    ``RuntimeError`` rather than a typed model-safe refusal on purpose: this is
    never a condition a tool, a connector, or a model can cause, so it is a
    runtime defect to surface, not an outcome to report to the model.
    """

    class Messages:
        """Safe public messages for bypass refusals."""

        NOT_ADMITTED: ClassVar[str] = (
            "this tool call has no admitted result; admission must happen "
            "before the result becomes model-visible"
        )
        CONTENT_NOT_ADMITTED: ClassVar[str] = (
            "model-visible content does not match anything admitted for this tool call"
        )


class InProcessOffloadWriter:
    """Bounded in-process fallback store for oversized tool results.

    Exists so that "no object store is configured" cannot mean "no bounding".
    It is deliberately modest: a fixed number of entries and a fixed total byte
    ceiling, oldest evicted first, so an unbounded stream of oversized results
    cannot turn the fallback into an unbounded cache.  Eviction makes a
    reference unresolvable, never wrong -- :meth:`read` returns ``None`` for
    bytes it no longer holds, and the model was told at admission time that the
    preview is non-authoritative and the reference is what to read.

    A durable writer is strictly better and is what production wires up.  This
    is the floor, not the target.
    """

    DEFAULT_MAX_ENTRIES: ClassVar[int] = 64
    DEFAULT_MAX_TOTAL_CHARS: ClassVar[int] = 8_000_000
    # An offload reference is carried in ``ContextCompressionEvent.files_written``
    # and ``ManagedContextPayload.reference``, both of which validate against the
    # absolute memory-path grammar (``^/[A-Za-z0-9._:/-]+$``).  A ``scheme://``
    # reference does not match it, so emitting one made the no-store gate raise on
    # every oversized result instead of bounding it -- the exact opposite of this
    # class's reason to exist.  The prefix stays distinct from the durable file
    # store's ``/large_tool_results/`` so an in-process ref is never mistaken for
    # an object a durable reader could resolve.
    REFERENCE_PREFIX: ClassVar[str] = "/in_process_tool_results/"

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    ) -> None:
        if max_entries < 1:
            raise ValueError("in-process offload store needs at least one entry")
        if max_total_chars < 1:
            raise ValueError("in-process offload store needs a positive byte ceiling")
        self._max_entries = max_entries
        self._max_total_chars = max_total_chars
        self._entries: dict[str, str] = {}
        self._order: deque[str] = deque()
        self._total_chars = 0
        self._lock = Lock()

    def __call__(self, content: str) -> str:
        """Store ``content`` and return the reference that reads it back."""

        reference = self.REFERENCE_PREFIX + sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if reference in self._entries:
                return reference
            self._entries[reference] = content
            self._order.append(reference)
            self._total_chars += len(content)
            self._evict_to_ceiling()
        return reference

    def read(self, reference: str) -> str | None:
        """Return the exact stored bytes, or ``None`` once they were evicted."""

        with self._lock:
            return self._entries.get(reference)

    @property
    def stored_chars(self) -> int:
        """Return the characters currently retained, for ceiling assertions."""

        with self._lock:
            return self._total_chars

    def _evict_to_ceiling(self) -> None:
        while self._order and (
            len(self._order) > self._max_entries
            or self._total_chars > self._max_total_chars
        ):
            oldest = self._order.popleft()
            evicted = self._entries.pop(oldest, "")
            self._total_chars -= len(evicted)


class ToolResultAdmissionLedger:
    """The one place an admission decision is recorded and looked up.

    Keyed by the call the decision was made for rather than by the bytes it
    decided about.  Content-keyed lookup was the divergence risk: two layers
    re-serializing the same result had to agree byte for byte to find each
    other, and when they did not the event path silently re-derived its own
    decision.  Identity cannot diverge.

    Each key owns a FIFO because a retried call legitimately produces several
    admissions under one identity, and the entries carry the *bounded*
    admission -- never the raw result.
    """

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str], deque[ToolResultAdmissionFact]] = (
            defaultdict(deque)
        )
        self._admissions: dict[tuple[str, str], deque[ToolResultAdmission]] = (
            defaultdict(deque)
        )
        self._admitted_digests: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._lock = Lock()

    def record(
        self,
        admission: ToolResultAdmission,
        *,
        scope: str,
        tool_call_id: str,
    ) -> None:
        """File one admission and its raw-free fact under one call identity."""

        if admission.fact is None:
            return
        key = (scope, ToolResultAdmissionFact.bounded_identifier(tool_call_id))
        with self._lock:
            self._facts[key].append(admission.fact)
            self._admissions[key].append(admission)
            self._admitted_digests[key].add(admission.fact.model_content_digest)

    def fact_for(
        self,
        *,
        scope: str,
        tool_call_id: str,
    ) -> ToolResultAdmissionFact | None:
        """Return the earliest unconsumed fact for one call, without consuming it."""

        key = (scope, ToolResultAdmissionFact.bounded_identifier(tool_call_id))
        with self._lock:
            pending = self._facts.get(key)
            return pending[0] if pending else None

    def consume(
        self,
        *,
        scope: str,
        tool_call_id: str,
    ) -> ToolResultAdmission | None:
        """Consume the admission the event path should project for one call."""

        key = (scope, ToolResultAdmissionFact.bounded_identifier(tool_call_id))
        with self._lock:
            pending = self._admissions.get(key)
            if not pending:
                return None
            self._facts[key].popleft()
            return pending.popleft()

    def admitted_digests(
        self,
        *,
        scope: str,
        tool_call_id: str,
    ) -> frozenset[str]:
        """Return every model-content digest admitted for one call identity.

        Read-only and non-consuming: verification runs on the way to the model
        and must not disturb what the event path will later project.
        """

        key = (scope, ToolResultAdmissionFact.bounded_identifier(tool_call_id))
        with self._lock:
            return frozenset(self._admitted_digests.get(key, frozenset()))

    def discard(self, *, scope: str) -> None:
        """Drop every record for one run when it exits early."""

        with self._lock:
            for registry in (self._facts, self._admissions, self._admitted_digests):
                for key in [key for key in registry if key[0] == scope]:
                    registry.pop(key, None)


class ToolResultAdmissionGate:
    """The runtime-owned boundary every tool result crosses exactly once.

    Constructible with no arguments, which is the whole point: a deployment
    without an object store gets a bounded in-process writer rather than an
    absent boundary.  Nothing about the decision depends on which tool produced
    the result -- there is no allowlist, no denylist, and no per-tool policy --
    so a framework-injected tool, a delegated subagent's tool, and a connector
    tool are bounded by the same arithmetic.
    """

    DEFAULT_SCOPE: ClassVar[str] = "unscoped"

    def __init__(
        self,
        *,
        offload_writer: OffloadWriter | None = None,
        policy: TokenBudgetPolicy | None = None,
        offloaded_model_content_limit_chars: int = (
            ToolResultAdmissionAdapter.DEFAULT_OFFLOADED_MODEL_CONTENT_LIMIT_CHARS
        ),
        ledger: ToolResultAdmissionLedger | None = None,
    ) -> None:
        self._fallback_writer = (
            InProcessOffloadWriter() if offload_writer is None else None
        )
        self._writer: OffloadWriter = offload_writer or self._fallback_writer
        self._adapter = ToolResultAdmissionAdapter(
            self._writer,
            policy=policy,
            offloaded_model_content_limit_chars=offloaded_model_content_limit_chars,
        )
        self._ledger = ledger or ToolResultAdmissionLedger()

    @property
    def adapter(self) -> ToolResultAdmissionAdapter:
        """Expose the underlying adapter for diagnostics and legacy call sites."""

        return self._adapter

    @property
    def ledger(self) -> ToolResultAdmissionLedger:
        """Expose the record both consuming paths read."""

        return self._ledger

    @property
    def fallback_store(self) -> InProcessOffloadWriter | None:
        """Return the in-process store, or ``None`` when a durable one is wired."""

        return self._fallback_writer

    def admit_tool_return(
        self,
        output: object,
        *,
        tool_name: str,
        tool_call_id: str,
        scope: str | None = None,
    ) -> ToolResultAdmission:
        """Bound one raw tool return before anything can make it model-visible.

        This is the seam the graph-wide tool middleware calls with the value a
        tool just produced -- before LangChain builds a ``ToolMessage`` from it,
        and long before the worker's stream projector observes one.  The writer
        runs synchronously here, so a store failure propagates rather than
        degrading into "return the raw result", and the decision is recorded
        before this method returns.
        """

        run_scope = self._scope(scope)
        admission = self._adapter.admit(
            output,
            trace_id=tool_call_id,
            projection_key=run_scope,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
        self._ledger.record(admission, scope=run_scope, tool_call_id=tool_call_id)
        return admission

    def verify_model_visible(
        self,
        content: object,
        *,
        tool_call_id: str,
        scope: str | None = None,
    ) -> ToolResultAdmissionFact:
        """Refuse content that reaches the model without having been admitted.

        The check is a digest comparison against what was admitted for this
        exact call, so it cannot be satisfied by a value that merely looks
        bounded, and an unknown call identity is a refusal rather than a pass --
        without that, a path that skipped admission entirely would verify
        vacuously, which is precisely the failure this guard exists to catch.
        """

        run_scope = self._scope(scope)
        fact = self._ledger.fact_for(scope=run_scope, tool_call_id=tool_call_id)
        if fact is None:
            raise ToolResultAdmissionBypassed(
                ToolResultAdmissionBypassed.Messages.NOT_ADMITTED
            )
        serialized = ToolResultAdmissionAdapter.serialize(content)
        digest = sha256(serialized.encode("utf-8")).hexdigest()
        if digest not in self._ledger.admitted_digests(
            scope=run_scope,
            tool_call_id=tool_call_id,
        ):
            raise ToolResultAdmissionBypassed(
                ToolResultAdmissionBypassed.Messages.CONTENT_NOT_ADMITTED
            )
        return fact

    def fact_for(
        self,
        *,
        tool_call_id: str,
        scope: str | None = None,
    ) -> ToolResultAdmissionFact | None:
        """Return the recorded decision for one call without re-deriving it."""

        return self._ledger.fact_for(
            scope=self._scope(scope),
            tool_call_id=tool_call_id,
        )

    def consume_event_projection(
        self,
        *,
        tool_call_id: str,
        scope: str | None = None,
    ) -> ToolResultAdmission | None:
        """Return the exact admission the event path should project.

        The same object the model path was handed, looked up by call identity.
        The event path therefore never serializes, budgets, or offloads the
        result a second time.
        """

        return self._ledger.consume(
            scope=self._scope(scope),
            tool_call_id=tool_call_id,
        )

    def discard(self, *, scope: str | None = None) -> None:
        """Drop one run's bounded records when it ends or is cancelled."""

        run_scope = self._scope(scope)
        self._ledger.discard(scope=run_scope)
        self._adapter.discard_projections(projection_key=run_scope)

    @classmethod
    def _scope(cls, scope: str | None) -> str:
        candidate = (scope or "").strip()
        return candidate or cls.DEFAULT_SCOPE


__all__ = (
    "InProcessOffloadWriter",
    "ToolResultAdmissionBypassed",
    "ToolResultAdmissionGate",
    "ToolResultAdmissionLedger",
)
