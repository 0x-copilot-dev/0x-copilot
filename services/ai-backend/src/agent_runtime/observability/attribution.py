"""Carry-side usage attribution: purpose classification and attribution context.

:class:`Purpose` is a five-value StrEnum with a deterministic :meth:`Purpose.derive`
that classifies an LLM call from signals available at emit time. :class:`UsageAttributionContext`
is a frozen Pydantic value object carrying every dimension used for cost attribution;
validators make impossible states unrepresentable (e.g. subagent purpose without a slug).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class Purpose(StrEnum):
    """What this LLM call is for. Drives both attribution and pricing buckets.

    Determined deterministically from the call's input messages + output
    via :meth:`derive`. Exactly one Purpose per row. Reports group by
    Purpose to answer "what did context compression cost", "how much of
    the LLM bill is tool-result interpretation", etc.
    """

    MAIN = "main"
    """Orchestrator planning. No ToolMessage in input, no tool_calls in
    output. The cost of "thinking about what to do next" without a
    tool-result in context."""

    TOOL_PLANNING = "tool_planning"
    """No ToolMessage in input; output contains one or more tool_calls.
    The cost of "deciding to use tool X." Apportioned across all
    tool_calls in the output."""

    TOOL_INTERPRETATION = "tool_interpretation"
    """Input contains at least one ToolMessage. The cost of "making
    sense of tool X's output." Dominant Purpose when an LLM call both
    interprets prior results AND plans the next tool — the
    interpretation is the user-facing semantic, so it wins."""

    SUBAGENT_WORK = "subagent_work"
    """Any LLM call inside a delegated subagent task. Subagent rollups
    key on (subagent_slug, task_id); cross-subagent phase analysis
    isn't a current product need — collapsed to one bucket."""

    CONTEXT_COMPRESSION = "context_compression"
    """``summarization.py`` path. The cost of context-window squeeze
    after long conversations. Wired into the recorder in 01c."""

    @classmethod
    def derive(
        cls,
        *,
        input_has_tool_message: bool,
        output_has_tool_calls: bool,
        is_subagent: bool,
        is_compression: bool,
    ) -> "Purpose":
        """Single source of truth for Purpose classification.

        Precedence (top wins):

        1. ``is_compression`` → CONTEXT_COMPRESSION
        2. ``is_subagent``    → SUBAGENT_WORK
        3. ``input_has_tool_message`` → TOOL_INTERPRETATION
        4. ``output_has_tool_calls``  → TOOL_PLANNING
        5. otherwise          → MAIN

        Order matters. A subagent's tool-interpretation call collapses
        to SUBAGENT_WORK (subagent slug is the dominant attribution
        cut for that report). A main-loop call that both interprets
        prior results AND plans the next tool collapses to
        TOOL_INTERPRETATION — the interpretation is the user-facing
        semantic.
        """

        if is_compression:
            return cls.CONTEXT_COMPRESSION
        if is_subagent:
            return cls.SUBAGENT_WORK
        if input_has_tool_message:
            return cls.TOOL_INTERPRETATION
        if output_has_tool_calls:
            return cls.TOOL_PLANNING
        return cls.MAIN


class UsageAttributionContext(BaseModel):
    """Carried with every LLM call. Built at the emit boundary; never
    reconstructed via DB lookup.

    Required (always populated from ``RunRecord``):
      - ``org_id``, ``user_id``, ``run_id``, ``conversation_id``,
        ``trace_id``
      - ``purpose`` (derived by :meth:`Purpose.derive` at emit time)

    Optional (populated when the LLM call has the relevant signal):
      - ``task_id`` — the supervisor's ``task`` tool call_id for
        subagent work; ``None`` for orchestrator-scope calls.
      - ``parent_task_id`` — for nested subagent dispatch (future).
      - ``subagent_slug`` — the subagent's name (e.g. ``researcher``)
        resolved via ``StreamUpdateProcessor.subagent_id_for_subgraph``.
      - ``originating_tool_call_id`` / ``originating_tool_name`` —
        the most-recent settled tool whose result is in the LLM's
        input (popped from :class:`ToolCallLedger`).
      - ``connector_slug`` — connector that owned the originating tool.
        ``None`` in 01b — populating side is a follow-up.

    Invariants (enforced at construction time):
      - ``Purpose.SUBAGENT_WORK`` ⇒ ``subagent_slug is not None``
      - ``Purpose.TOOL_INTERPRETATION`` ⇒ ``originating_tool_call_id
        is not None``
      - ``subagent_slug is not None`` ⇒ ``task_id is not None``

    These invariants exist so the runtime cannot persist a
    partially-attributed row. A code path that says "this is subagent
    work" must also say which subagent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    user_id: str
    run_id: str
    conversation_id: str
    trace_id: str
    purpose: Purpose

    task_id: str | None = None
    parent_task_id: str | None = None
    subagent_slug: str | None = None

    originating_tool_call_id: str | None = None
    originating_tool_name: str | None = None
    connector_slug: str | None = None

    @model_validator(mode="after")
    def _purpose_invariants(self) -> "UsageAttributionContext":
        if self.purpose == Purpose.SUBAGENT_WORK and self.subagent_slug is None:
            raise ValueError("subagent_slug required when purpose=subagent_work")
        if (
            self.purpose == Purpose.TOOL_INTERPRETATION
            and self.originating_tool_call_id is None
        ):
            raise ValueError(
                "originating_tool_call_id required when purpose=tool_interpretation"
            )
        if self.subagent_slug is not None and self.task_id is None:
            raise ValueError("task_id required whenever subagent_slug is set")
        return self
