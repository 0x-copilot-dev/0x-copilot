"""Decision scopes (``once`` / ``always``) and retroactive resolution.

Approval used to be binary — approve *this* call, and be asked again for the
next identical one. That is why a task with thirty writes produces thirty cards,
and thirty cards is how consent prompts get clicked through unread. The fix is
the second axis OpenCode already has (``permission/index.ts`` ``reply`` :109-167):
a reply carries a SCOPE, and an ``always`` writes a rule that answers the rest.

Where an ``always`` is persisted, and why it is not durable
----------------------------------------------------------
**Run-scoped, in memory.** A durable store was available and is deliberately not
used, for the reason ``runtime_worker/stream_events.py:227-234`` already writes
down on the filesystem lane: the card the user clicked named ONE call. Turning
that click into standing authority is "a grant appearing from a card that never
mentioned one" — the exact escalation the card exists to prevent. Its own
conclusion there ("what the user actually wants after the third card is *stop
pausing for this run*") is precisely a run-scoped always.

So the two lanes stay separate and each says what it is:

* **run-scoped** — this module. Expires with the run. Raised from a card.
* **durable** — ``user_policies_json['tool_use']['permission_rules']`` and
  ``['never']`` (:meth:`PermissionRuleset.authored`). Authored deliberately in
  settings, never as a side effect of approving something.

Retroactive resolution
----------------------
A run parks several tool calls on separate interrupts. When one is answered with
``always``, the rule it writes may already answer the others, and re-asking a
question the user has just answered is the friction this whole module exists to
remove. :meth:`RunDecisionLedger.reply` therefore re-evaluates every other
pending ask against the new ruleset and reports which ones the rule now covers —
the same sweep as OpenCode's ``reply`` :153-166.

The resolution is not "the ledger unblocks a coroutine": each parked call
re-enters :meth:`~agent_runtime.capabilities.policy.service.PdpPolicyService.decide`
on LangGraph's node replay and the new rule makes that call ALLOW. The ledger's
job is to hold the rule and to name what it covered.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from agent_runtime.capabilities.policy.contracts import PolicyContract
from agent_runtime.capabilities.policy.rules import PermissionRuleset, RuleAction


class DecisionScope(StrEnum):
    """How far one approval reaches.

    Only two values, and the omission is deliberate: OpenCode's third reply
    (``reject``, which cancels the session's other pending asks) is NOT modelled
    here because nothing in this runtime would call it — a rejected write already
    fails closed at its own call site, and a scope value with no caller is the
    "landed but not wired" shape this codebase is trying to stop producing.
    """

    ONCE = "once"
    ALWAYS = "always"

    @classmethod
    def from_wire(cls, value: object) -> "DecisionScope":
        """Coerce a client-supplied scope; anything unrecognised is ``ONCE``.

        Fail-closed by construction: the safe answer is the narrow one, so a
        malformed or absent scope can only ever under-grant.
        """

        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError:
                return cls.ONCE
        return cls.ONCE


class PendingAsk(PolicyContract):
    """One parked approval, remembered so a later ``always`` can cover it."""

    #: The approval id the card and the resume path join on.
    request_id: str
    #: The permission key the rule is written against (the capability URN).
    permission: str
    #: The strings a rule pattern matches (:class:`PolicySubjects`).
    subjects: tuple[str, ...]


class ReplyOutcome(PolicyContract):
    """What a reply did: its scope, and the asks the new rule now answers."""

    scope: DecisionScope
    #: Request ids of OTHER pending asks the new rule covers. Empty for ``ONCE``.
    resolved: tuple[str, ...] = ()


class RunDecisionLedger:
    """The pending asks and session ruleset for ONE run.

    Not a Pydantic contract: this is mutable per-run state, not a boundary
    payload, and the ruleset it hands to the PDP is the frozen contract.
    """

    __slots__ = ("_pending", "_rules")

    #: A run cannot park an unbounded number of approvals in practice, but the
    #: registry is process-local, so the list is capped rather than trusted.
    _MAX_PENDING: ClassVar[int] = 256

    def __init__(self) -> None:
        self._pending: dict[str, PendingAsk] = {}
        self._rules = PermissionRuleset()

    @property
    def rules(self) -> PermissionRuleset:
        """The session ruleset every ``always`` in this run has written."""

        return self._rules

    def pending(self) -> tuple[PendingAsk, ...]:
        """The asks parked and not yet replied to, in registration order."""

        return tuple(self._pending.values())

    def register(self, ask: PendingAsk) -> None:
        """Remember an ask the PDP just gated. Idempotent on ``request_id``.

        Idempotence matters: LangGraph re-executes a tool node from the top on
        resume, so the same ask is registered again on the replay pass. The id is
        deterministic across that replay for exactly this reason (see
        ``PolicyGatedMcpTool._approval_id``), so a re-register is a no-op rather
        than a duplicate.
        """

        if len(self._pending) >= self._MAX_PENDING:
            return
        self._pending[ask.request_id] = ask

    def reply(self, *, request_id: str, scope: DecisionScope) -> ReplyOutcome:
        """Resolve one ask; on ``ALWAYS`` write its rule and sweep the rest.

        ``ONCE`` persists nothing — it is what a plain approve has always done.
        ``ALWAYS`` appends an ALLOW rule per subject of the replied ask, then
        reports every other pending ask whose subjects the merged ruleset now
        answers with ALLOW. An ask is only reported when EVERY one of its
        subjects allows: a rule that covers the tool but not the path it was
        handed has not answered that question.
        """

        ask = self._pending.pop(request_id, None)
        if scope is not DecisionScope.ONCE and ask is None:
            # Nothing to derive a rule from — an unknown id must not widen
            # anything, so it degrades to the narrow scope.
            return ReplyOutcome(scope=DecisionScope.ONCE)
        if ask is None or scope is DecisionScope.ONCE:
            return ReplyOutcome(scope=DecisionScope.ONCE)

        self._rules = self._rules.with_allow(
            permission=ask.permission, patterns=ask.subjects
        )
        resolved: list[str] = []
        for other in tuple(self._pending.values()):
            if not self._is_allowed(other):
                continue
            self._pending.pop(other.request_id, None)
            resolved.append(other.request_id)
        return ReplyOutcome(scope=DecisionScope.ALWAYS, resolved=tuple(resolved))

    def _is_allowed(self, ask: PendingAsk) -> bool:
        """True when the session ruleset now allows EVERY subject of ``ask``."""

        return all(
            (rule := self._rules.evaluate(ask.permission, subject)) is not None
            and rule.action is RuleAction.ALLOW
            for subject in ask.subjects
        )


class RunDecisionLedgers:
    """Process-local, run-keyed registry of :class:`RunDecisionLedger`.

    Mirrors the registry pattern this package already uses for per-run state
    (``McpToolAnnotationsRegistry``), so there is one recognisable shape for "a
    fact the whole run shares but no contract carries". Bounded, and evicted
    oldest-first: the worker runs many runs per process and a leak here would be
    unbounded memory for the life of the worker.
    """

    _BY_RUN: ClassVar[dict[str, RunDecisionLedger]] = {}
    _MAX_RUNS: ClassVar[int] = 64

    @classmethod
    def for_run(cls, run_id: str) -> RunDecisionLedger:
        """The ledger for ``run_id``, created on first use."""

        ledger = cls._BY_RUN.get(run_id)
        if ledger is not None:
            return ledger
        while len(cls._BY_RUN) >= cls._MAX_RUNS:
            cls._BY_RUN.pop(next(iter(cls._BY_RUN)))
        ledger = RunDecisionLedger()
        cls._BY_RUN[run_id] = ledger
        return ledger

    @classmethod
    def discard(cls, run_id: str) -> None:
        """Drop a run's ledger. Safe to call for a run that never had one."""

        cls._BY_RUN.pop(run_id, None)

    @classmethod
    def reset(cls) -> None:
        """Clear every ledger — test isolation only."""

        cls._BY_RUN.clear()


__all__ = [
    "DecisionScope",
    "PendingAsk",
    "ReplyOutcome",
    "RunDecisionLedger",
    "RunDecisionLedgers",
]
