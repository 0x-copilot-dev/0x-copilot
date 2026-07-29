"""Deterministic allocation of one model call's context, reserve first.

:mod:`~agent_runtime.context.context_contracts` states what a context plan *is*
and refuses to hold one it cannot reproduce; it deliberately leaves the decision
of which candidates to admit to this module.  This is that decision, and nothing
else: candidate supply, compression, evidence resolution, and emergency replan
are sibling lanes.

The allocation runs in two phases, and the split is the point.

**Phase one decides; it never fetches.**  :meth:`ContextAllocation.allocate` is a
pure function of the candidates, the limits, the policy, and the plan instant.
It orders candidates once -- ``O(C log C)`` -- then makes one bounded pass over
them, choosing per candidate the highest-fidelity offered form that fits both the
remaining budget and its class share.  Nothing in that pass reads a body, so a
candidate that loses can never have been fetched.

**Phase two hydrates only what phase one admitted.**
:class:`ContextPlanAssembler` walks the admissions in plan order and calls the
hydrator exactly once per admitted candidate; an omitted candidate takes
:meth:`ContextRepresentation.omitted`, which needs no material at all.  The
hydrated form is then checked against the option that was budgeted for, so a
resolver cannot return something larger than the plan allowed.

Four properties are structural here rather than conventional.

**Determinism is re-derived, not asserted.**  Lane F6.2 set the precedent and
:class:`~agent_runtime.context.context_contracts.ContextPlanReconstruction`
followed it: a record stores its inputs beside its decision and re-derives the
decision on every parse.  :class:`ContextAllocation` stores the candidates, the
limits, the revisions, and the policy, and its validator re-runs the whole
allocation and refuses the value unless every admission matches.  A hand-edited,
reordered, or replayed-under-a-different-build allocation therefore fails at
parse time rather than at whatever later point somebody compares digests.

**The output reserve is inviolable because over-allocation is unrepresentable.**
:class:`ContextBudget` carries the limits and the tokens spent, and its validator
refuses any value whose spend exceeds
:attr:`~agent_runtime.context.context_contracts.ContextPlanLimits.available_tokens`
-- which already subtracts the output reserve, the fixed prompt, and the safety
margin.  The contract is frozen, so :meth:`ContextBudget.spend` returns a *new*
budget through that same validator.  There is no code path, including a buggy one
in a later lane, that can hold a budget which has eaten into the reserve.

**Every omission carries a reason code.**  :class:`ContextAdmission` is either an
admitted option with an inclusion reason or an omission reason, never both and
never neither, so "dropped, unexplained" cannot be represented.  Lifecycle
omissions carry the exact member
:meth:`~agent_runtime.context.context_contracts.ContextSourceLifecycle.inadmissible_reason`
derives, budget omissions carry ``budget_exhausted``, share omissions carry
``class_share_exhausted``, and a repeated source carries ``duplicate``.

**A plan that would not fit is refused, never truncated.**  Lane F3.4 found
silent truncation shipped in a capability description and it was the worse
failure: the model authors against something that is not the real thing.  So a
fixed prompt that does not leave room for the reserve, an immutable safety
candidate that cannot be carried whole, and protected material that does not fit
all raise :class:`ContextBudgetExceeded`.  No plan is produced, and the caller --
the emergency-replan lane -- decides what to do about it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, ClassVar, Protocol, Self, runtime_checkable

from pydantic import Field, NonNegativeInt, model_validator

from agent_runtime.context.context_contracts import (
    ContextBounds,
    ContextCandidate,
    ContextCandidateDecision,
    ContextInclusionReason,
    ContextOmissionReason,
    ContextPlan,
    ContextPlanLimits,
    ContextPlanningError,
    ContextPlanReconstruction,
    ContextPlanRevisions,
    ContextPriorityClass,
    ContextRepresentation,
    ContextRepresentationMode,
    ContextRepresentationOption,
)
from agent_runtime.control_plane.revision_binding import ControlToken
from agent_runtime.execution.contracts import RuntimeContract

BASIS_POINTS: int = 10_000
"""The whole of a class share, in integer basis points.

Shares are integers rather than fractions on purpose: a float share would make
the same policy allocate differently on two builds, and the allocation feeds a
digest that is supposed to be an equality check.
"""


class ContextAllocationError(ContextPlanningError):
    """Base typed, model-safe failure of the F5 context allocator."""


class ContextBudgetExceeded(ContextAllocationError):
    """The requested context cannot be planned inside the provider limit.

    Raised rather than resolved.  Every member below names material the
    allocator is not allowed to shrink, drop, or borrow the output reserve from,
    so the honest outcome is a refusal the caller can retry differently.
    """

    class Messages:
        """Safe public messages for budget refusals."""

        NO_OUTPUT_RESERVE: ClassVar[str] = (
            "a plan must reserve output tokens for the model to answer"
        )
        FIXED_CONTENT_EXCEEDS_LIMIT: ClassVar[str] = (
            "the fixed prompt, tool schemas, output reserve, and safety margin "
            "do not fit the model context limit"
        )
        RESERVE_INVADED: ClassVar[str] = (
            "allocated context cannot spend the output reserve"
        )
        PROTECTED_DOES_NOT_FIT: ClassVar[str] = (
            "protected context does not fit the available budget and cannot be "
            "dropped to make room"
        )
        IMMUTABLE_DOES_NOT_FIT: ClassVar[str] = (
            "immutable safety, authority, and protocol context does not fit the "
            "available budget and cannot be carried in a reduced form"
        )
        IMMUTABLE_NOT_OFFERED_WHOLE: ClassVar[str] = (
            "immutable safety, authority, and protocol context must be offered "
            "as a whole representation"
        )


class ContextAllocationRejected(ContextAllocationError):
    """The allocator was handed a candidate set it cannot plan over."""

    class Messages:
        """Safe public messages for input refusals."""

        TOO_MANY_CANDIDATES: ClassVar[str] = (
            "more candidates were offered than one plan may decide"
        )
        DUPLICATE_CANDIDATE_ID: ClassVar[str] = (
            "each candidate is decided exactly once, so candidate ids are unique"
        )
        SHARE_NOT_CANONICAL: ClassVar[str] = (
            "class shares must be unique and ordered by priority class"
        )
        PROTECTED_CLASS_SHARED: ClassVar[str] = (
            "protected context is never limited by a class share"
        )
        POLICY_REVISION_MISMATCH: ClassVar[str] = (
            "an allocation must record the policy revision it was decided under"
        )
        NAIVE_TIMESTAMP: ClassVar[str] = "allocation timestamps must be timezone-aware"


class ContextAllocationNotReproducible(ContextAllocationError):
    """A stored allocation is not the allocation its own inputs produce."""

    class Messages:
        """Safe public messages for reproduction refusals."""

        ADMISSIONS_MISMATCH: ClassVar[str] = (
            "stored admissions are not what these candidates, limits, and "
            "policy allocate to"
        )


class ContextAdmissionRejected(ContextAllocationError):
    """An admission was neither a reasoned inclusion nor a reasoned omission."""

    class Messages:
        """Safe public messages for admission refusals."""

        AMBIGUOUS_OUTCOME: ClassVar[str] = (
            "an admission states exactly one admitted option or omission reason"
        )
        UNOFFERED_OPTION: ClassVar[str] = (
            "an admission cannot admit a form the candidate never offered"
        )


class ContextHydrationRejected(ContextAllocationError):
    """A hydrator returned something other than the form that was budgeted."""

    class Messages:
        """Safe public messages for hydration refusals."""

        NOT_THE_ADMITTED_OPTION: ClassVar[str] = (
            "a hydrated representation must be exactly the admitted form"
        )
        HYDRATED_ANOTHER_SOURCE: ClassVar[str] = (
            "a hydrated representation must carry its own candidate's source"
        )


class ContextBudget(RuntimeContract):
    """The variable-context budget, with the output reserve already withheld.

    Every value of this type is a budget whose reserve is intact.  The limits
    subtract the reserve, the fixed prompt, and the safety margin before this
    contract sees a token, ``spent_tokens`` is non-negative, and the validator
    refuses any spend beyond what is left -- so "the model still has room to
    answer" is a property of the type rather than a rule the allocator remembers
    to apply.  The contract is frozen, so :meth:`spend` produces a new value
    through the same validator instead of mutating one that already passed it.
    """

    limits: ContextPlanLimits
    spent_tokens: NonNegativeInt = 0

    @classmethod
    def opening(cls, limits: ContextPlanLimits) -> Self:
        """Return the unspent budget one plan may allocate inside ``limits``."""

        return cls(limits=limits)

    @property
    def available_tokens(self) -> int:
        """Return the whole variable budget, reserve and fixed content removed."""

        return self.limits.available_tokens

    @property
    def remaining_tokens(self) -> int:
        """Return what is still allocatable without touching the reserve."""

        return self.available_tokens - self.spent_tokens

    @property
    def reserved_output_tokens(self) -> int:
        """Return the output budget this allocation may never spend."""

        return self.limits.reserved_output_tokens

    def admits(self, tokens: int) -> bool:
        """Return whether ``tokens`` still fit without invading the reserve."""

        return 0 <= tokens <= self.remaining_tokens

    def spend(self, tokens: int) -> Self:
        """Return the budget after spending ``tokens``, or refuse to.

        Refusing is the whole contract: there is no result of this call that
        represents a budget which spent more than it had.
        """

        if not self.admits(tokens):
            raise ContextBudgetExceeded(ContextBudgetExceeded.Messages.RESERVE_INVADED)
        return type(self)(limits=self.limits, spent_tokens=self.spent_tokens + tokens)

    @model_validator(mode="after")
    def _reserve_is_intact(self) -> Self:
        if self.limits.reserved_output_tokens <= 0:
            raise ContextBudgetExceeded(
                ContextBudgetExceeded.Messages.NO_OUTPUT_RESERVE
            )
        if self.limits.available_tokens < 0:
            raise ContextBudgetExceeded(
                ContextBudgetExceeded.Messages.FIXED_CONTENT_EXCEEDS_LIMIT
            )
        if self.spent_tokens > self.limits.available_tokens:
            raise ContextBudgetExceeded(ContextBudgetExceeded.Messages.RESERVE_INVADED)
        return self


class ContextClassShare(RuntimeContract):
    """The most of the variable budget one evictable priority class may take.

    Without shares, allocation is pure priority order and a flood of recent
    turns starves plan state and evidence entirely.  The share is what makes
    this an allocation *among* the PRD's categories rather than a queue.

    A protected class can never carry one: protected material may only leave a
    plan when the runtime cannot admit its source, so a share that could evict
    it would be a budget reason wearing another name.
    """

    priority_class: ContextPriorityClass
    max_share_basis_points: Annotated[int, Field(ge=0, le=BASIS_POINTS)]

    def cap_tokens(self, available_tokens: int) -> int:
        """Return this share's ceiling over ``available_tokens``.

        Floor division on integers, so the same share over the same budget is
        the same ceiling on every build and in every replay.
        """

        return max(available_tokens, 0) * self.max_share_basis_points // BASIS_POINTS

    @model_validator(mode="after")
    def _share_cannot_evict_protected_context(self) -> Self:
        if self.priority_class.protected:
            raise ContextAllocationRejected(
                ContextAllocationRejected.Messages.PROTECTED_CLASS_SHARED
            )
        return self


class ContextAllocationPolicy(RuntimeContract):
    """The revision-bound allocation policy one plan was decided under.

    ``policy_revision`` is not decoration.  The plan's input digest covers the
    candidates, the limits, and the revisions -- not this policy -- so a changed
    share table would otherwise produce a different plan under an unchanged input
    digest.  :class:`ContextAllocation` refuses any policy whose revision is not
    the one the plan records, which turns "planning is reproducible for a
    candidate set *and* these revisions" into an equality check.
    """

    policy_revision: ControlToken
    class_shares: tuple[ContextClassShare, ...] = Field(
        default_factory=tuple,
        max_length=len(ContextPriorityClass),
    )

    @classmethod
    def unconstrained(cls, *, policy_revision: str) -> Self:
        """Return the policy that allocates by priority order alone."""

        return cls(policy_revision=policy_revision)

    def share_caps(
        self, *, available_tokens: int
    ) -> Mapping[ContextPriorityClass, int]:
        """Return the token ceiling each shared class may take, if any."""

        return MappingProxyType(
            {
                share.priority_class: share.cap_tokens(available_tokens)
                for share in self.class_shares
            }
        )

    @model_validator(mode="after")
    def _shares_are_canonical(self) -> Self:
        ranks = [share.priority_class.priority_rank for share in self.class_shares]
        if len(set(ranks)) != len(ranks) or ranks != sorted(ranks):
            raise ContextAllocationRejected(
                ContextAllocationRejected.Messages.SHARE_NOT_CANONICAL
            )
        return self


class ContextAdmissionTables:
    """The immutable table admitted context reads its inclusion reason from.

    Stated as data for the same reason the contract module states its
    vocabularies as data: the reason a candidate was kept should be legible at
    one glance, and extending the priority vocabulary should be one entry rather
    than one more branch.  Read through :meth:`inclusion_reason_for`, never
    subscripted, so a class added without an entry falls back to the reason that
    asserts the least rather than raising somewhere arbitrary.
    """

    INCLUSION_BY_PRIORITY_CLASS: ClassVar[
        Mapping[ContextPriorityClass, ContextInclusionReason]
    ] = MappingProxyType(
        {
            ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL: (
                ContextInclusionReason.PROTECTED_CLASS
            ),
            ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS: (
                ContextInclusionReason.CURRENT_INTENT
            ),
            ContextPriorityClass.APPROVAL_GATE_STATE: (
                ContextInclusionReason.PENDING_APPROVAL
            ),
            ContextPriorityClass.ACTIVE_PLAN_OPERATIONS: (
                ContextInclusionReason.ACTIVE_PLAN
            ),
            ContextPriorityClass.SELECTED_SKILLS_EVIDENCE: (
                ContextInclusionReason.HIGH_RELEVANCE
            ),
            ContextPriorityClass.RECENT_CONVERSATION: (
                ContextInclusionReason.RECENCY_WINDOW
            ),
            ContextPriorityClass.RECALLED_MEMORY: ContextInclusionReason.CONTINUITY,
            ContextPriorityClass.LOW_RELEVANCE_HISTORY: (
                ContextInclusionReason.CONTINUITY
            ),
        }
    )
    """Why material of each priority class earns its place in a model call."""

    CONSERVATIVE_INCLUSION: ClassVar[ContextInclusionReason] = (
        ContextInclusionReason.CONTINUITY
    )
    """The reason unclassified admitted material records.

    ``continuity`` claims neither authority nor a relevance judgement nobody
    made, which is what makes it the safe fallback: ``protected_class`` would be
    a widened authority and ``high_relevance`` would be an invented score.
    """

    @classmethod
    def inclusion_reason_for(
        cls,
        *,
        priority_class: ContextPriorityClass,
        mode: ContextRepresentationMode,
    ) -> ContextInclusionReason:
        """Return the one reason this admission records for being kept.

        Protected material always records the authority that kept it, because
        that is why it is there whatever form it took.  Everything else admitted
        as a bare pointer records ``retrievable_reference``, which is the honest
        description of a candidate whose body did not travel.
        """

        if not priority_class.protected and mode is ContextRepresentationMode.REFERENCE:
            return ContextInclusionReason.RETRIEVABLE_REFERENCE
        return cls.INCLUSION_BY_PRIORITY_CLASS.get(
            priority_class,
            cls.CONSERVATIVE_INCLUSION,
        )


class ContextAdmission(RuntimeContract):
    """One candidate's outcome: the form it takes, or why it did not travel.

    An admission is body-free like everything else in F5 -- it names a candidate,
    an offered option, and closed-vocabulary reasons.  It is deliberately *not*
    a :class:`~agent_runtime.context.context_contracts.ContextCandidateDecision`:
    a decision carries the hydrated representation with its content digest, and
    producing one for a candidate that lost would mean fetching material the plan
    already rejected.  This is the phase-one record, and it is exactly what can
    be decided without reading anything.
    """

    candidate: ContextCandidate
    admitted_option: ContextRepresentationOption | None = None
    inclusion_reason: ContextInclusionReason | None = None
    omission_reason: ContextOmissionReason | None = None

    @property
    def admitted(self) -> bool:
        """Return whether this candidate reaches the model call."""

        return self.omission_reason is None

    @property
    def token_count(self) -> int:
        """Return the tokens this admission spends from the variable budget."""

        return 0 if self.admitted_option is None else self.admitted_option.token_count

    @model_validator(mode="after")
    def _admission_is_one_reasoned_outcome(self) -> Self:
        admitted = (
            self.admitted_option is not None and self.inclusion_reason is not None
        )
        omitted = (
            self.admitted_option is None
            and self.inclusion_reason is None
            and self.omission_reason is not None
        )
        if admitted == omitted or (admitted and self.omission_reason is not None):
            raise ContextAdmissionRejected(
                ContextAdmissionRejected.Messages.AMBIGUOUS_OUTCOME
            )
        if (
            self.admitted_option is not None
            and self.admitted_option not in self.candidate.representation_options
        ):
            raise ContextAdmissionRejected(
                ContextAdmissionRejected.Messages.UNOFFERED_OPTION
            )
        return self


def _refuse_unplannable(candidates: Sequence[ContextCandidate]) -> None:
    """Refuse a candidate set no plan could hold before spending work on it."""

    if len(candidates) > ContextBounds.MAX_CANDIDATES:
        raise ContextAllocationRejected(
            ContextAllocationRejected.Messages.TOO_MANY_CANDIDATES
        )
    identifiers = {candidate.candidate_id for candidate in candidates}
    if len(identifiers) != len(candidates):
        raise ContextAllocationRejected(
            ContextAllocationRejected.Messages.DUPLICATE_CANDIDATE_ID
        )


def _choose_option(
    *,
    candidate: ContextCandidate,
    budget: ContextBudget,
    class_remaining: int | None,
) -> ContextRepresentationOption | ContextOmissionReason:
    """Return the form this candidate takes, or the reason it takes none.

    A union rather than an optional pair: there is no outcome here that is both
    a form and a reason, so the type says so.  Options are already canonically
    ordered by fidelity, which makes the first one that fits the most faithful
    one that fits.  Immutable material is the one exception -- it is carried
    whole or not at all, so a reduced option is never considered for it.
    """

    options = candidate.representation_options
    if not options:
        return ContextOmissionReason.conservative()
    if candidate.priority_class.immutable:
        whole = next(
            (
                option
                for option in options
                if option.mode is ContextRepresentationMode.FULL
            ),
            None,
        )
        if whole is None:
            return ContextOmissionReason.conservative()
        if not budget.admits(whole.token_count):
            return ContextOmissionReason.BUDGET_EXHAUSTED
        return whole
    share_blocked = False
    for option in options:
        if not budget.admits(option.token_count):
            continue
        if class_remaining is not None and option.token_count > class_remaining:
            share_blocked = True
            continue
        return option
    if share_blocked:
        return ContextOmissionReason.CLASS_SHARE_EXHAUSTED
    return ContextOmissionReason.BUDGET_EXHAUSTED


def _refuse_if_unevictable(
    candidate: ContextCandidate,
    blocked: ContextOmissionReason,
) -> None:
    """Refuse the whole allocation rather than drop context that cannot be dropped.

    An admissible immutable candidate has no representable omission at all --
    :meth:`ContextCandidateDecision.authority_violation` rejects both a budgetary
    omission and a non-full form -- so there is nothing to record and the
    allocation ends here.  Protected material one step down may be reduced, but
    never evicted for budget, so it ends here too.
    """

    if candidate.priority_class.immutable:
        raise ContextBudgetExceeded(
            ContextBudgetExceeded.Messages.IMMUTABLE_DOES_NOT_FIT
            if blocked.budgetary
            else ContextBudgetExceeded.Messages.IMMUTABLE_NOT_OFFERED_WHOLE
        )
    if candidate.priority_class.protected and blocked.budgetary:
        raise ContextBudgetExceeded(
            ContextBudgetExceeded.Messages.PROTECTED_DOES_NOT_FIT
        )


def _allocate_admissions(
    *,
    candidates: Sequence[ContextCandidate],
    limits: ContextPlanLimits,
    policy: ContextAllocationPolicy,
    at: datetime,
) -> tuple[ContextAdmission, ...]:
    """Return the one admission sequence these inputs produce.

    Pure and fetch-free: it reads candidate metadata, priority order, declared
    costs, and the plan instant, and nothing else.  ``at`` is the plan's recorded
    instant rather than a live clock, so a replay reaches the same answer the
    original run did.

    One sort dominates the cost -- ``O(C log C)`` -- and the pass that follows is
    linear in candidates times the bounded number of forms each may offer.
    """

    _refuse_unplannable(candidates)
    budget = ContextBudget.opening(limits)
    caps = policy.share_caps(available_tokens=budget.available_tokens)
    spent_by_class: dict[ContextPriorityClass, int] = {}
    seen_digests: set[str] = set()
    admissions: list[ContextAdmission] = []
    for candidate in ContextPlanReconstruction.ordered(tuple(candidates)):
        forced = candidate.lifecycle.inadmissible_reason(at)
        if forced is not None:
            admissions.append(
                ContextAdmission(candidate=candidate, omission_reason=forced)
            )
            continue
        protected = candidate.priority_class.protected
        if not protected and candidate.source_digest in seen_digests:
            admissions.append(
                ContextAdmission(
                    candidate=candidate,
                    omission_reason=ContextOmissionReason.DUPLICATE,
                )
            )
            continue
        cap = caps.get(candidate.priority_class)
        class_remaining = (
            None
            if cap is None
            else cap - spent_by_class.get(candidate.priority_class, 0)
        )
        chosen = _choose_option(
            candidate=candidate,
            budget=budget,
            class_remaining=class_remaining,
        )
        if isinstance(chosen, ContextOmissionReason):
            _refuse_if_unevictable(candidate, chosen)
            admissions.append(
                ContextAdmission(candidate=candidate, omission_reason=chosen)
            )
            continue
        option = chosen
        budget = budget.spend(option.token_count)
        spent_by_class[candidate.priority_class] = (
            spent_by_class.get(candidate.priority_class, 0) + option.token_count
        )
        seen_digests.add(candidate.source_digest)
        admissions.append(
            ContextAdmission(
                candidate=candidate,
                admitted_option=option,
                inclusion_reason=ContextAdmissionTables.inclusion_reason_for(
                    priority_class=candidate.priority_class,
                    mode=option.mode,
                ),
            )
        )
    return tuple(admissions)


class ContextAllocation(RuntimeContract):
    """One deterministic decision about which candidates a model call gets.

    The allocation stores every input it was decided from and re-derives its own
    admissions on construction *and* on every parse, so determinism is checked
    rather than promised: a stored allocation that a re-run does not reproduce is
    refused, whether it was hand-edited, replayed under a different policy
    revision, or produced by a build that decided differently.

    It carries no representations and no content digests.  Hydration happens in
    :class:`ContextPlanAssembler`, after this decision is final, which is what
    lets the fetch count equal the admitted count exactly.
    """

    limits: ContextPlanLimits
    revisions: ContextPlanRevisions
    policy: ContextAllocationPolicy
    candidates: tuple[ContextCandidate, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_CANDIDATES,
    )
    admissions: tuple[ContextAdmission, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_CANDIDATES,
    )
    decided_at: datetime

    @classmethod
    def allocate(
        cls,
        *,
        candidates: Iterable[ContextCandidate],
        limits: ContextPlanLimits,
        revisions: ContextPlanRevisions,
        policy: ContextAllocationPolicy,
        decided_at: datetime,
    ) -> Self:
        """Return the allocation these inputs produce, or refuse to produce one."""

        offered = tuple(candidates)
        return cls(
            limits=limits,
            revisions=revisions,
            policy=policy,
            candidates=offered,
            admissions=_allocate_admissions(
                candidates=offered,
                limits=limits,
                policy=policy,
                at=decided_at,
            ),
            decided_at=decided_at,
        )

    @property
    def admitted(self) -> tuple[ContextAdmission, ...]:
        """Return every admission that reaches the model, in plan order."""

        return tuple(admission for admission in self.admissions if admission.admitted)

    @property
    def omitted(self) -> tuple[ContextAdmission, ...]:
        """Return every admission that does not reach the model, in plan order."""

        return tuple(
            admission for admission in self.admissions if not admission.admitted
        )

    @property
    def allocated_tokens(self) -> int:
        """Return the variable tokens this allocation spends."""

        return sum(admission.token_count for admission in self.admitted)

    @property
    def closing_budget(self) -> ContextBudget:
        """Return the budget as this allocation leaves it.

        Constructing it is itself the proof that the reserve survived: the type
        cannot represent a budget that overspent.
        """

        return ContextBudget(limits=self.limits, spent_tokens=self.allocated_tokens)

    @model_validator(mode="after")
    def _allocation_reproduces_itself(self) -> Self:
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ContextAllocationRejected(
                ContextAllocationRejected.Messages.NAIVE_TIMESTAMP
            )
        if self.policy.policy_revision != self.revisions.policy_revision:
            raise ContextAllocationRejected(
                ContextAllocationRejected.Messages.POLICY_REVISION_MISMATCH
            )
        expected = _allocate_admissions(
            candidates=self.candidates,
            limits=self.limits,
            policy=self.policy,
            at=self.decided_at,
        )
        if expected != self.admissions:
            raise ContextAllocationNotReproducible(
                ContextAllocationNotReproducible.Messages.ADMISSIONS_MISMATCH
            )
        return self


@runtime_checkable
class ContextRepresentationHydrator(Protocol):
    """The one question the allocator asks about material it already admitted.

    Implementations resolve, authorize, excerpt, or summarize -- the allocator
    does not care which -- and return the form that was budgeted for.  They are
    called once per admitted candidate and never for an omitted one, so an
    implementation may treat a call as authorization to spend I/O.
    """

    async def hydrate(
        self,
        *,
        candidate: ContextCandidate,
        option: ContextRepresentationOption,
    ) -> ContextRepresentation: ...


class ContextPlanAssembler:
    """Turns one final allocation into one durable plan, fetching only the winners.

    The assembler is the only place a body is touched, and it touches exactly the
    admitted ones.  An omitted candidate takes
    :meth:`ContextRepresentation.omitted`, which is constructed from the digest
    the candidate already carried, so the hydrator never learns it existed.

    Each hydrated form is then checked against the option the allocation budgeted
    for.  Without that check a resolver could return a fatter representation than
    the plan allowed and the overflow would surface as a provider error rather
    than as the refusal it is.
    """

    def __init__(self, *, hydrator: ContextRepresentationHydrator) -> None:
        self._hydrator = hydrator

    async def assemble(
        self,
        *,
        allocation: ContextAllocation,
        plan_id: str,
        run_id: str,
        model_call_id: str,
    ) -> ContextPlan:
        """Return the durable plan for ``allocation``.

        ``created_at`` is the allocation's own instant rather than a fresh
        reading, because :class:`ContextPlan` re-checks every lifecycle against
        it: planning at one instant and stamping another would let a retention
        window lapse between the decision and the record.
        """

        decisions = tuple(
            [
                ContextCandidateDecision(
                    candidate=admission.candidate,
                    representation=await self._represent(admission),
                    inclusion_reason=admission.inclusion_reason,
                    omission_reason=admission.omission_reason,
                )
                for admission in allocation.admissions
            ]
        )
        input_digest = ContextPlanReconstruction.input_digest(
            candidates=allocation.candidates,
            limits=allocation.limits,
            revisions=allocation.revisions,
        )
        return ContextPlan(
            plan_id=plan_id,
            run_id=run_id,
            model_call_id=model_call_id,
            limits=allocation.limits,
            revisions=allocation.revisions,
            candidates=allocation.candidates,
            candidate_decisions=decisions,
            allocated_tokens=ContextPlanReconstruction.allocated_tokens(decisions),
            input_digest=input_digest,
            plan_digest=ContextPlanReconstruction.plan_digest(
                input_digest=input_digest,
                decisions=decisions,
            ),
            created_at=allocation.decided_at,
        )

    async def _represent(self, admission: ContextAdmission) -> ContextRepresentation:
        """Return the form one admission takes, hydrating only if it was admitted."""

        if admission.admitted_option is None:
            return ContextRepresentation.omitted(
                source_digest=admission.candidate.source_digest
            )
        representation = await self._hydrator.hydrate(
            candidate=admission.candidate,
            option=admission.admitted_option,
        )
        if representation.source_digest != admission.candidate.source_digest:
            raise ContextHydrationRejected(
                ContextHydrationRejected.Messages.HYDRATED_ANOTHER_SOURCE
            )
        if not admission.admitted_option.matches(representation):
            raise ContextHydrationRejected(
                ContextHydrationRejected.Messages.NOT_THE_ADMITTED_OPTION
            )
        return representation


async def aplan_model_context(
    *,
    candidates: Iterable[ContextCandidate],
    limits: ContextPlanLimits,
    revisions: ContextPlanRevisions,
    policy: ContextAllocationPolicy,
    hydrator: ContextRepresentationHydrator,
    plan_id: str,
    run_id: str,
    model_call_id: str,
    at: datetime,
) -> ContextPlan:
    """Allocate and assemble one model call's context in the order that matters.

    The single entry point ``abefore_model`` calls: decide first over metadata
    alone, then hydrate only the winners.  Any refusal -- fixed content that does
    not fit, protected material that cannot be carried -- surfaces here as
    :class:`ContextBudgetExceeded` with no plan produced.
    """

    allocation = ContextAllocation.allocate(
        candidates=candidates,
        limits=limits,
        revisions=revisions,
        policy=policy,
        decided_at=at,
    )
    return await ContextPlanAssembler(hydrator=hydrator).assemble(
        allocation=allocation,
        plan_id=plan_id,
        run_id=run_id,
        model_call_id=model_call_id,
    )


__all__ = (
    "BASIS_POINTS",
    "ContextAdmission",
    "ContextAdmissionRejected",
    "ContextAdmissionTables",
    "ContextAllocation",
    "ContextAllocationError",
    "ContextAllocationNotReproducible",
    "ContextAllocationPolicy",
    "ContextAllocationRejected",
    "ContextBudget",
    "ContextBudgetExceeded",
    "ContextClassShare",
    "ContextHydrationRejected",
    "ContextPlanAssembler",
    "ContextRepresentationHydrator",
    "aplan_model_context",
)
