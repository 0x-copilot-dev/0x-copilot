"""Scoped, bounded, body-free candidate providers for one model call.

F5.1 states what a context *plan* may hold; this module states where the
material in it comes from.  A provider answers one question -- "what could this
model call be given from my source, that this caller is already authorized to
see?" -- and answers it as :class:`~agent_runtime.context.context_contracts.ContextCandidate`
values, which carry a digest, an opaque locator, and a size, and never a body.
Choosing among them is the sibling allocator lane's job; compressing them is
another; reading the material behind them is a third.  This lane only offers.

Five properties are structural here rather than conventional.

**A provider cannot claim authorization.**  It never constructs
:class:`~agent_runtime.context.context_contracts.ContextSourceLifecycle`.  It
enumerates body-free :class:`ContextSourceRecord` values, and
:class:`ScopedCandidateProvider` asks an injected
:class:`ContextSourceAuthorityPort` about *each one*, at collection time, before
any candidate exists.  There is no memoization anywhere in this module and no
path that reads an authorization decision from a run snapshot, which is §6.1's
"the snapshot is not an authorization cache" made unrepresentable rather than
reviewed.  A source the authority does not report as authorized -- or reports as
revoked, expired, or unreachable -- simply produces nothing, and
:class:`ContextCandidateCollection` re-checks that on every parse, so a
collection holding inadmissible material cannot be constructed at all.

**Bounds are on work, not on output.**  The consumer, never the provider,
decides how many records are pulled: :meth:`ScopedCandidateProvider.offer`
enumerates under a capacity it is handed and stops, so a source that returns an
infinite stream still costs exactly that capacity.  A provider therefore cannot
yield an unbounded number of candidates even by accident, and a source whose
records are overwhelmingly unauthorized cannot turn a bounded candidate budget
into unbounded enumeration.  The per-kind ceilings in
:class:`ContextProviderPolicies` sum to less than
:attr:`~agent_runtime.context.context_contracts.ContextBounds.MAX_CANDIDATES`,
so the aggregate holds without the collector having to police it.

**A single candidate is bounded too.**  Above
:attr:`ContextProviderBounds.MAX_INLINE_TOKENS` a record may be offered only as
a compact reference, and only when its source domain says the material is
retrievable.  Material that is both oversized and unretrievable is not offered
in any form, because there is no honest form for it.  The PRD's "inline context
from one external result defaults below 8,000 tokens" is therefore a shape the
provider can produce rather than a number a reviewer checks.

**Ordering is derived, never chosen.**  Providers run in
:class:`~agent_runtime.context.context_contracts.ContextCandidateKind`
declaration order -- which is priority order -- and the collection is emitted
through
:meth:`~agent_runtime.context.context_contracts.ContextPlanReconstruction.ordered`,
the same total order a plan re-derives on every parse.  Two candidates that tie
on class and relevance still have exactly one admissible position, so equal
ranking cannot reorder between two runs, and the collection refuses to hold any
other sequence.

**An absent source is silence, not failure.**  Memory and skills are optional;
a store can be unreachable; an enumeration can raise.  Every one of those
produces zero candidates and a reason-coded
:class:`ContextProviderReport`, never an exception that would fail a turn.  The
report distinguishes an optional source that is switched off from a required
source that went dark, because those are different operational facts even though
they contribute the same nothing.

One thing is deliberately absent.  No policy in this module grants a *protected*
priority class, and :class:`ContextSourcePolicy` refuses one, so no provider can
mint immutable safety, current-intent, or approval-gate context.  Immutable
safety and capability schemas are fixed prompt tokens with no candidate at all;
current-request and approval-gate material is named by the allocator work item,
not this one.  A future lane that needs to offer protected material must say so
in its own contract rather than inherit the ability from here, which is the
PRD's "never place untrusted retrieved content in the system-policy tier"
enforced at the only place that could violate it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, ClassVar, Protocol, Self, runtime_checkable

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from agent_runtime.context.context_contracts import (
    ContextAuthorizationScope,
    ContextBounds,
    ContextCandidate,
    ContextCandidateKind,
    ContextDigests,
    ContextLossiness,
    ContextOmissionReason,
    ContextPlanReconstruction,
    ContextPlanningError,
    ContextPriorityClass,
    ContextRepresentationMode,
    ContextRepresentationOption,
    ContextScopeDimension,
    ContextSourceLifecycle,
)
from agent_runtime.context.evidence_registry import (
    EvidenceKind,
    EvidenceLocator,
    EvidenceRefIdentity,
)
from agent_runtime.control_plane.revision_binding import Sha256Hex
from agent_runtime.execution.contracts import RuntimeContract


class ContextProviderError(ContextPlanningError):
    """Base typed, model-safe failure of an F5 candidate provider."""


class ContextProviderNotConfigured(ContextProviderError):
    """A provider was wired for a candidate kind no policy governs.

    Refusing at construction rather than falling back to a conservative policy
    is the fail-closed choice here: a silent demotion would let a kind that
    genuinely needs a protected class be wired up and then quietly lose its
    protection at run time, which is worse than never starting.
    """

    MESSAGE_TEMPLATE: ClassVar[str] = (
        "no context source policy is configured for candidate kind {kind}"
    )

    def __init__(self, *, kind: ContextCandidateKind) -> None:
        self.kind = kind
        super().__init__(self.MESSAGE_TEMPLATE.format(kind=kind.value))


class ContextProviderAlreadyRegistered(ContextProviderError):
    """Two providers claimed the same candidate kind in one collector.

    One provider per kind is what makes candidate identity unique across a
    collection without a cross-provider de-duplication pass, and what keeps the
    per-kind ceiling an actual ceiling rather than a per-instance one.
    """

    MESSAGE_TEMPLATE: ClassVar[str] = (
        "candidate kind {kind} already has a registered provider"
    )

    def __init__(self, *, kind: ContextCandidateKind) -> None:
        self.kind = kind
        super().__init__(self.MESSAGE_TEMPLATE.format(kind=kind.value))


class ContextSourcePolicyRejected(ContextProviderError):
    """A source policy granted more than a provider may ever be granted."""

    class Messages:
        """Safe public messages for policy refusals."""

        PRIORITY_PROMOTED: ClassVar[str] = (
            "a source policy cannot claim a priority class its candidate kind "
            "may not hold"
        )
        PROTECTION_CLAIMED: ClassVar[str] = (
            "no provider-sourced material may claim a protected priority class"
        )
        DEMOTION_PROMOTES: ClassVar[str] = (
            "material outside the recency window cannot become more protected"
        )
        SUBJECT_REQUIRED: ClassVar[str] = (
            "every source policy must require the subject scope dimension"
        )
        WINDOW_EXCEEDS_CEILING: ClassVar[str] = (
            "a recency window cannot exceed the candidates a source may offer"
        )
        KIND_MISMATCH: ClassVar[str] = (
            "a provider's policy must govern its own source's candidate kind"
        )


class ContextProviderReportRejected(ContextProviderError):
    """A provider report did not account for what it examined."""

    class Messages:
        """Safe public messages for report refusals."""

        UNACCOUNTED_RECORDS: ClassVar[str] = (
            "every examined record is either offered or withheld for one reason"
        )
        OVER_CAPACITY: ClassVar[str] = (
            "a provider cannot examine or offer more than its capacity"
        )
        TALLIES_NOT_CANONICAL: ClassVar[str] = (
            "withholding tallies must be unique and in reason-code order"
        )
        SILENT_OUTCOME_EXAMINED: ClassVar[str] = (
            "a disabled, out-of-scope, or unbudgeted source examines nothing"
        )
        BUDGET_OUTCOME_MISMATCH: ClassVar[str] = (
            "a source reports budget exhaustion exactly when it had no capacity"
        )
        TRUNCATION_MISMATCH: ClassVar[str] = (
            "a source is truncated exactly when it examined its whole capacity"
        )
        OFFER_COUNT_MISMATCH: ClassVar[str] = (
            "an offer must carry exactly the candidates its report counted"
        )


class ContextCollectionRejected(ContextProviderError):
    """A candidate collection was not the collection its inputs describe."""

    class Messages:
        """Safe public messages for collection refusals."""

        NAIVE_TIMESTAMP: ClassVar[str] = (
            "candidate collection timestamps must be timezone-aware"
        )
        CANDIDATES_NOT_ORDERED: ClassVar[str] = (
            "collected candidates are not the deterministic order of their class, "
            "relevance, and identity"
        )
        DUPLICATE_CANDIDATE: ClassVar[str] = (
            "a collection offers each candidate exactly once"
        )
        SCOPE_WIDENED: ClassVar[str] = (
            "a candidate cannot be scoped more widely than the collection it "
            "was gathered for"
        )
        INADMISSIBLE_OFFERED: ClassVar[str] = (
            "a source the runtime cannot currently admit must not be offered"
        )
        REPORTS_NOT_CANONICAL: ClassVar[str] = (
            "provider reports must be unique and in candidate-kind order"
        )
        REPORTED_COUNT_MISMATCH: ClassVar[str] = (
            "collected candidates are not the candidates the reports counted"
        )


class ContextProviderBounds:
    """The structural ceilings every candidate provider shares.

    ``MAX_INLINE_TOKENS`` is the PRD's inline default for one external result.
    ``REFERENCE_TOKENS`` is the flat cost of naming material instead of carrying
    it, and it is the reason an oversized source can still be offered at all:
    a reference is the one form whose cost does not grow with its source.
    """

    MAX_INLINE_TOKENS: ClassVar[int] = 8_000
    REFERENCE_TOKENS: ClassVar[int] = 48
    MAX_PROVIDERS: ClassVar[int] = len(ContextCandidateKind)


class ContextCandidateIdentity:
    """Derives the one identifier a candidate may be offered under.

    Identity is a function of the candidate kind and the opaque evidence token,
    so the same source enumerated twice -- in one collection, or in a replay of
    the same inputs a week later -- resolves to the same candidate rather than
    to two.  That is what makes the deterministic ordering's final tiebreak
    stable across runs, and what lets duplicate suppression be an equality test.

    The source domain's own locator never appears: the evidence token already
    digests it, so no host path, URL, or record id reaches a candidate, a plan,
    or a persisted decision because a provider enumerated it.
    """

    class Keys:
        """Field names in the derived candidate identity."""

        KIND: ClassVar[str] = "kind"
        SCHEMA_VERSION: ClassVar[str] = "schema_version"
        CANDIDATE_KIND: ClassVar[str] = "candidate_kind"
        EVIDENCE_TOKEN: ClassVar[str] = "evidence_token"

    LABEL: ClassVar[str] = "context.candidate"
    SCHEMA_VERSION: ClassVar[int] = 1
    PREFIX: ClassVar[str] = "ctxc-"

    @classmethod
    def evidence_token(
        cls,
        *,
        evidence_kind: EvidenceKind,
        locator: str,
    ) -> str:
        """Return the opaque token this material would be read back through.

        Minted through F5.5's own derivation rather than a second one, so the
        reference a plan carries is exactly the reference ``read_evidence``
        routes, and a candidate cannot be filed under a token no resolver
        answers for.
        """

        return EvidenceRefIdentity.token(kind=evidence_kind, locator=locator)

    @classmethod
    def of(cls, *, kind: ContextCandidateKind, evidence_token: str) -> str:
        """Return the one identity a candidate of ``kind`` may be offered under."""

        digest = ContextDigests.of(
            {
                cls.Keys.KIND: cls.LABEL,
                cls.Keys.SCHEMA_VERSION: cls.SCHEMA_VERSION,
                cls.Keys.CANDIDATE_KIND: kind.value,
                cls.Keys.EVIDENCE_TOKEN: evidence_token,
            }
        )
        return f"{cls.PREFIX}{kind.value}-{digest}"


class ContextProviderOutcome(StrEnum):
    """Closed reason one source contributed what it contributed.

    Split so that "nothing" is never one undifferentiated answer.  An optional
    source that is switched off, a scope that was never bound, a spent candidate
    budget, and a store that went dark all yield zero candidates and are four
    different operational facts.

    :meth:`conservative` returns ``UNAVAILABLE`` rather than ``DISABLED``: an
    unexplained absence means "this source could not answer", never "the user
    turned it off", because the second is a claim nobody made.
    """

    OFFERED = "offered"
    DISABLED = "disabled"
    OUT_OF_SCOPE = "out_of_scope"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNAVAILABLE = "unavailable"

    @property
    def enumerated(self) -> bool:
        """Return whether this outcome could have examined any record."""

        return self in {
            ContextProviderOutcome.OFFERED,
            ContextProviderOutcome.UNAVAILABLE,
        }

    @classmethod
    def conservative(cls) -> "ContextProviderOutcome":
        """Return the outcome an unexplained absence resolves to."""

        return cls.UNAVAILABLE


class ContextSourceRecord(RuntimeContract):
    """One thing a source domain says exists, described without saying what it is.

    This is the seam that keeps the whole lane body-free.  A record names its
    material with the source domain's own bounded locator, states the digest of
    the bytes and how many tokens they would cost, and says whether the domain
    can serve them again later.  There is no field for the material, no field
    for a preview of it, and no field for a selector expression that could
    smuggle one -- so a body cannot enter this module even before a candidate
    exists to refuse it.

    Nothing here is trusted for authority.  ``relevance_score`` is ranking
    input, ``retrievable`` only ever *removes* the reference option, and the
    locator is forwarded to the authority rather than believed.
    """

    locator: EvidenceLocator
    content_digest: Sha256Hex
    size_tokens: NonNegativeInt
    retrievable: bool = False
    relevance_score: int | None = Field(
        default=None,
        ge=0,
        le=ContextBounds.MAX_RELEVANCE_SCORE,
    )


class ContextSourcePolicy(RuntimeContract):
    """What one source is allowed to offer, and in what shape.

    Held as a value rather than as behaviour on a provider subclass so the
    entire per-kind policy surface is one legible table, and so a test can
    assert the ceiling a source is subject to instead of inferring it from
    control flow.  Every validator below narrows: a policy may state less than
    its kind allows, never more.
    """

    kind: ContextCandidateKind
    evidence_kind: EvidenceKind
    priority_class: ContextPriorityClass
    demoted_class: ContextPriorityClass
    max_candidates: Annotated[
        int,
        Field(ge=1, le=ContextBounds.MAX_CANDIDATES),
    ]
    recency_window: PositiveInt
    max_inline_tokens: Annotated[
        int,
        Field(ge=0, le=ContextProviderBounds.MAX_INLINE_TOKENS),
    ] = ContextProviderBounds.MAX_INLINE_TOKENS
    optional: bool = False
    required_dimensions: frozenset[ContextScopeDimension]

    @property
    def inline_ceiling(self) -> int:
        """Return the largest source this policy may offer whole."""

        return min(self.max_inline_tokens, ContextProviderBounds.MAX_INLINE_TOKENS)

    def class_at(self, position: int) -> ContextPriorityClass:
        """Return the class the ``position``-th offered candidate may hold.

        Position is counted over candidates this provider actually offered, so
        a source's recency window is a window over *admitted* material rather
        than over whatever the store happened to enumerate -- an unauthorized
        run of records cannot push admissible ones out of the window.
        """

        if position < self.recency_window:
            return self.priority_class
        return self.demoted_class

    @model_validator(mode="after")
    def _policy_cannot_widen_its_kind(self) -> Self:
        ceiling = self.kind.max_priority_class()
        for claimed in (self.priority_class, self.demoted_class):
            if not claimed.no_more_protected_than(ceiling):
                raise ContextSourcePolicyRejected(
                    ContextSourcePolicyRejected.Messages.PRIORITY_PROMOTED
                )
            if claimed.protected:
                raise ContextSourcePolicyRejected(
                    ContextSourcePolicyRejected.Messages.PROTECTION_CLAIMED
                )
        if not self.demoted_class.no_more_protected_than(self.priority_class):
            raise ContextSourcePolicyRejected(
                ContextSourcePolicyRejected.Messages.DEMOTION_PROMOTES
            )
        if ContextScopeDimension.SUBJECT not in self.required_dimensions:
            raise ContextSourcePolicyRejected(
                ContextSourcePolicyRejected.Messages.SUBJECT_REQUIRED
            )
        if self.recency_window > self.max_candidates:
            raise ContextSourcePolicyRejected(
                ContextSourcePolicyRejected.Messages.WINDOW_EXCEEDS_CEILING
            )
        return self


class ContextProviderPolicies:
    """The closed per-kind policy table every provider resolves through.

    Stated as data for the reason the sibling vocabulary tables are: one glance
    shows exactly what each source may offer, and adding a source is one entry
    rather than a new branch.  It is a :class:`~types.MappingProxyType` because
    it governs authority -- which class each kind may claim and which scope
    dimensions it must have -- and a mutable module attribute that decides
    authority is a mutable authority.

    Two aggregate facts matter and are asserted by the suite rather than
    assumed.  No entry claims a protected class, so no provider can produce
    non-evictable context.  The ceilings sum to less than
    :attr:`~agent_runtime.context.context_contracts.ContextBounds.MAX_CANDIDATES`,
    so every source can be exhausted within one plan's candidate budget and a
    high-priority source can never be starved by a low-priority one that ran
    first.
    """

    BY_KIND: ClassVar[Mapping[ContextCandidateKind, ContextSourcePolicy]] = (
        MappingProxyType(
            {
                ContextCandidateKind.TASK_PLAN_STATE: ContextSourcePolicy(
                    kind=ContextCandidateKind.TASK_PLAN_STATE,
                    evidence_kind=EvidenceKind.PRIOR_RESULT,
                    priority_class=ContextPriorityClass.ACTIVE_PLAN_OPERATIONS,
                    demoted_class=ContextPriorityClass.ACTIVE_PLAN_OPERATIONS,
                    max_candidates=16,
                    recency_window=16,
                    max_inline_tokens=2_000,
                    required_dimensions=frozenset(
                        {ContextScopeDimension.SUBJECT, ContextScopeDimension.RUN}
                    ),
                ),
                ContextCandidateKind.SKILL: ContextSourcePolicy(
                    kind=ContextCandidateKind.SKILL,
                    evidence_kind=EvidenceKind.SOURCE,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    demoted_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    max_candidates=24,
                    recency_window=24,
                    max_inline_tokens=4_000,
                    optional=True,
                    required_dimensions=frozenset({ContextScopeDimension.SUBJECT}),
                ),
                ContextCandidateKind.CITATION: ContextSourcePolicy(
                    kind=ContextCandidateKind.CITATION,
                    evidence_kind=EvidenceKind.SOURCE,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    demoted_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                    max_candidates=64,
                    recency_window=32,
                    required_dimensions=frozenset(
                        {ContextScopeDimension.SUBJECT, ContextScopeDimension.RUN}
                    ),
                ),
                ContextCandidateKind.ARTIFACT: ContextSourcePolicy(
                    kind=ContextCandidateKind.ARTIFACT,
                    evidence_kind=EvidenceKind.ARTIFACT,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    demoted_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                    max_candidates=48,
                    recency_window=24,
                    required_dimensions=frozenset({ContextScopeDimension.SUBJECT}),
                ),
                ContextCandidateKind.WORKSPACE_REF: ContextSourcePolicy(
                    kind=ContextCandidateKind.WORKSPACE_REF,
                    evidence_kind=EvidenceKind.ARTIFACT,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    demoted_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                    max_candidates=48,
                    recency_window=24,
                    required_dimensions=frozenset(
                        {ContextScopeDimension.SUBJECT, ContextScopeDimension.PROJECT}
                    ),
                ),
                ContextCandidateKind.TOOL_OBSERVATION: ContextSourcePolicy(
                    kind=ContextCandidateKind.TOOL_OBSERVATION,
                    evidence_kind=EvidenceKind.PRIOR_RESULT,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    demoted_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                    max_candidates=96,
                    recency_window=24,
                    required_dimensions=frozenset(
                        {ContextScopeDimension.SUBJECT, ContextScopeDimension.RUN}
                    ),
                ),
                ContextCandidateKind.CONVERSATION_TURN: ContextSourcePolicy(
                    kind=ContextCandidateKind.CONVERSATION_TURN,
                    evidence_kind=EvidenceKind.CONVERSATION,
                    priority_class=ContextPriorityClass.RECENT_CONVERSATION,
                    demoted_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                    max_candidates=120,
                    recency_window=20,
                    max_inline_tokens=4_000,
                    required_dimensions=frozenset(
                        {
                            ContextScopeDimension.SUBJECT,
                            ContextScopeDimension.CONVERSATION,
                        }
                    ),
                ),
                ContextCandidateKind.CONTINUITY_SUMMARY: ContextSourcePolicy(
                    kind=ContextCandidateKind.CONTINUITY_SUMMARY,
                    evidence_kind=EvidenceKind.CONVERSATION,
                    priority_class=ContextPriorityClass.RECENT_CONVERSATION,
                    demoted_class=ContextPriorityClass.RECENT_CONVERSATION,
                    max_candidates=8,
                    recency_window=8,
                    max_inline_tokens=4_000,
                    required_dimensions=frozenset(
                        {
                            ContextScopeDimension.SUBJECT,
                            ContextScopeDimension.CONVERSATION,
                        }
                    ),
                ),
                ContextCandidateKind.MEMORY: ContextSourcePolicy(
                    kind=ContextCandidateKind.MEMORY,
                    evidence_kind=EvidenceKind.MEMORY,
                    priority_class=ContextPriorityClass.RECALLED_MEMORY,
                    demoted_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                    max_candidates=32,
                    recency_window=16,
                    max_inline_tokens=2_000,
                    optional=True,
                    required_dimensions=frozenset({ContextScopeDimension.SUBJECT}),
                ),
            }
        )
    )
    """The one policy each provider-sourced candidate kind is governed by."""

    @classmethod
    def kinds(cls) -> frozenset[ContextCandidateKind]:
        """Return every kind a provider may be wired for."""

        return frozenset(cls.BY_KIND)

    @classmethod
    def get(cls, kind: ContextCandidateKind) -> ContextSourcePolicy | None:
        """Return the policy governing ``kind``, or ``None`` if it has none."""

        return cls.BY_KIND.get(kind)

    @classmethod
    def require(cls, kind: ContextCandidateKind) -> ContextSourcePolicy:
        """Return the policy governing ``kind``, or refuse to wire it at all."""

        policy = cls.get(kind)
        if policy is None:
            raise ContextProviderNotConfigured(kind=kind)
        return policy

    @classmethod
    def total_ceiling(cls) -> int:
        """Return the candidates every configured source could offer together."""

        return sum(policy.max_candidates for policy in cls.BY_KIND.values())


class ContextProviderTables:
    """The immutable orderings this module's canonical sequences derive from.

    Both are declaration order of a closed vocabulary rather than a hand-written
    list, so a member added to either vocabulary takes its position from the
    place it was declared and cannot silently land somewhere a reader would not
    expect.
    """

    KIND_ORDER: ClassVar[Mapping[ContextCandidateKind, int]] = MappingProxyType(
        {kind: index for index, kind in enumerate(ContextCandidateKind)}
    )
    """Where each candidate kind sorts, which is also its priority order."""

    OMISSION_ORDER: ClassVar[Mapping[ContextOmissionReason, int]] = MappingProxyType(
        {reason: index for index, reason in enumerate(ContextOmissionReason)}
    )
    """Where each withholding reason sorts inside one provider report."""


class ContextWithholdingTally(RuntimeContract):
    """How many records one source held back for one reason.

    A count and a reason code, never an identity.  Naming the material a caller
    is not authorized to see would defeat the withholding, so a provider reports
    that three things were revoked without reporting which three.
    """

    reason: ContextOmissionReason
    count: PositiveInt


class ContextProviderReport(RuntimeContract):
    """What one source did with the capacity it was handed.

    The accounting is total: every record the provider examined is either an
    offered candidate or a withheld one with exactly one reason, and the
    validator re-derives that on every parse.  A report that "lost" records
    between examination and offer is therefore unrepresentable, which is what
    makes the omitted-by-reason metric the PRD asks for trustworthy rather than
    best-effort.
    """

    kind: ContextCandidateKind
    outcome: ContextProviderOutcome
    capacity: NonNegativeInt
    examined: NonNegativeInt
    offered: NonNegativeInt
    truncated: bool = False
    withheld: tuple[ContextWithholdingTally, ...] = Field(
        default_factory=tuple,
        max_length=len(ContextOmissionReason),
    )

    @property
    def withheld_total(self) -> int:
        """Return how many examined records did not become candidates."""

        return sum(tally.count for tally in self.withheld)

    def withheld_for(self, reason: ContextOmissionReason) -> int:
        """Return how many records were held back for ``reason``."""

        return sum(tally.count for tally in self.withheld if tally.reason is reason)

    @model_validator(mode="after")
    def _report_accounts_for_every_record(self) -> Self:
        self._check_tallies_are_canonical()
        self._check_counts_fit_capacity()
        self._check_outcome_agrees_with_work()
        return self

    def _check_tallies_are_canonical(self) -> None:
        reasons = tuple(tally.reason for tally in self.withheld)
        ranks = tuple(
            ContextProviderTables.OMISSION_ORDER.get(reason, len(reasons))
            for reason in reasons
        )
        if len(set(reasons)) != len(reasons) or list(ranks) != sorted(ranks):
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.TALLIES_NOT_CANONICAL
            )

    def _check_counts_fit_capacity(self) -> None:
        if self.offered > self.examined or self.examined > self.capacity:
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.OVER_CAPACITY
            )
        if self.offered + self.withheld_total != self.examined:
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.UNACCOUNTED_RECORDS
            )

    def _check_outcome_agrees_with_work(self) -> None:
        if not self.outcome.enumerated and (self.examined or self.truncated):
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.SILENT_OUTCOME_EXAMINED
            )
        if (self.outcome is ContextProviderOutcome.BUDGET_EXHAUSTED) != (
            self.capacity == 0
        ):
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.BUDGET_OUTCOME_MISMATCH
            )
        if self.truncated and self.examined != self.capacity:
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.TRUNCATION_MISMATCH
            )


class ContextProviderOffer(RuntimeContract):
    """One source's candidates together with the account of how it got them.

    Carried as one value rather than two returns so a caller cannot keep the
    candidates and drop the reasons: the offer refuses to hold a candidate count
    its own report does not claim.
    """

    candidates: tuple[ContextCandidate, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_CANDIDATES,
    )
    report: ContextProviderReport

    @model_validator(mode="after")
    def _offer_matches_its_report(self) -> Self:
        if len(self.candidates) != self.report.offered:
            raise ContextProviderReportRejected(
                ContextProviderReportRejected.Messages.OFFER_COUNT_MISMATCH
            )
        return self


class ContextCandidateRequest(RuntimeContract):
    """The verified scope and instant one collection is gathered for.

    ``collected_at`` is the plan's recorded instant rather than a live clock, so
    retention expiry is evaluated once, the same way on a replay as on the
    original gather, and two collections over the same inputs cannot disagree
    because time passed between them.
    """

    scope: ContextAuthorizationScope
    collected_at: datetime

    @model_validator(mode="after")
    def _timestamp_is_aware(self) -> Self:
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ContextCollectionRejected(
                ContextCollectionRejected.Messages.NAIVE_TIMESTAMP
            )
        return self


class ContextCandidateCollection(RuntimeContract):
    """Everything one model call could be offered, in the one order it may take.

    The collection re-checks on every parse what the providers established once:
    the candidates are in the deterministic plan order, each appears exactly
    once, none is scoped more widely than the collection, and none is material
    the runtime could not admit at ``collected_at``.  A collection assembled by
    hand, replayed from a store, or produced by a future provider that skipped
    its authority is therefore refused at parse time rather than trusted -- the
    same reconstruction discipline F5.1 applies to a plan, applied to the plan's
    inputs.
    """

    scope: ContextAuthorizationScope
    collected_at: datetime
    candidates: tuple[ContextCandidate, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_CANDIDATES,
    )
    reports: tuple[ContextProviderReport, ...] = Field(
        default_factory=tuple,
        max_length=ContextProviderBounds.MAX_PROVIDERS,
    )

    @property
    def offered_tokens(self) -> int:
        """Return what every offered candidate would cost carried whole."""

        return sum(candidate.original_tokens for candidate in self.candidates)

    def report_for(self, kind: ContextCandidateKind) -> ContextProviderReport | None:
        """Return the report one source filed, or ``None`` if it never ran."""

        for report in self.reports:
            if report.kind is kind:
                return report
        return None

    def candidates_of(
        self,
        kind: ContextCandidateKind,
    ) -> tuple[ContextCandidate, ...]:
        """Return the offered candidates of ``kind``, in collection order."""

        return tuple(
            candidate for candidate in self.candidates if candidate.kind is kind
        )

    @model_validator(mode="after")
    def _collection_is_scoped_ordered_and_admissible(self) -> Self:
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ContextCollectionRejected(
                ContextCollectionRejected.Messages.NAIVE_TIMESTAMP
            )
        self._check_candidates_are_canonical()
        self._check_candidates_are_admissible_here()
        self._check_reports_are_canonical()
        return self

    def _check_candidates_are_canonical(self) -> None:
        identifiers = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(set(identifiers)) != len(identifiers):
            raise ContextCollectionRejected(
                ContextCollectionRejected.Messages.DUPLICATE_CANDIDATE
            )
        if self.candidates != ContextPlanReconstruction.ordered(self.candidates):
            raise ContextCollectionRejected(
                ContextCollectionRejected.Messages.CANDIDATES_NOT_ORDERED
            )

    def _check_candidates_are_admissible_here(self) -> None:
        for candidate in self.candidates:
            if not candidate.scope.narrows_to(self.scope):
                raise ContextCollectionRejected(
                    ContextCollectionRejected.Messages.SCOPE_WIDENED
                )
            if candidate.lifecycle.inadmissible_reason(self.collected_at) is not None:
                raise ContextCollectionRejected(
                    ContextCollectionRejected.Messages.INADMISSIBLE_OFFERED
                )

    def _check_reports_are_canonical(self) -> None:
        kinds = tuple(report.kind for report in self.reports)
        ranks = tuple(
            ContextProviderTables.KIND_ORDER.get(kind, len(kinds)) for kind in kinds
        )
        if len(set(kinds)) != len(kinds) or list(ranks) != sorted(ranks):
            raise ContextCollectionRejected(
                ContextCollectionRejected.Messages.REPORTS_NOT_CANONICAL
            )
        if sum(report.offered for report in self.reports) != len(self.candidates):
            raise ContextCollectionRejected(
                ContextCollectionRejected.Messages.REPORTED_COUNT_MISMATCH
            )


@runtime_checkable
class ContextSourceEnumerationPort(Protocol):
    """What one source domain says exists, without saying whether it may be used.

    A source enumerates; it does not authorize.  It is handed the collection
    scope so it can query its own store correctly, and it may narrow what it
    lists on that basis, but nothing it returns is admitted until the authority
    has been asked about that exact record.

    ``records`` may return an arbitrarily long -- even endless -- stream.  That
    is deliberate: the consumer bounds enumeration, so a source is never
    required to know a limit, and a source that gets one wrong cannot widen one.
    """

    @property
    def kind(self) -> ContextCandidateKind:
        """Return the single candidate kind this source enumerates."""

    @property
    def enabled(self) -> bool:
        """Return whether this source is configured to contribute at all."""

    def records(
        self,
        *,
        scope: ContextAuthorizationScope,
    ) -> AsyncIterator[ContextSourceRecord]:
        """Return this source's records for ``scope``, in its own order."""


@runtime_checkable
class ContextSourceAuthorityPort(Protocol):
    """The one call-time question a provider asks about one piece of material.

    It is asked per record and per collection, never once per run, so the answer
    a plan is built on is the answer that was true when the plan was built.
    Implementations reauthorize the current subject and apply their own
    retention, deletion, and legal-hold state; they never widen a scope and
    never decide priority.
    """

    async def authorize(
        self,
        *,
        kind: ContextCandidateKind,
        locator: str,
        scope: ContextAuthorizationScope,
    ) -> ContextSourceLifecycle:
        """Return the trusted lifecycle of ``locator`` for ``scope``, now."""


class ScopedCandidateProvider:
    """One source's bounded, authorized, body-free contribution to a model call.

    The provider is deliberately one class parameterized by policy rather than
    nine subclasses: every source differs in its ceilings, its recency window,
    its required scope dimensions, and whether it is optional, and every one of
    those is data.  What must not differ -- ask the authority about each record,
    stop at the capacity you were handed, never carry a body, never claim a
    protected class -- is behaviour, and behaviour that must not differ belongs
    in one place.
    """

    def __init__(
        self,
        source: ContextSourceEnumerationPort,
        *,
        authority: ContextSourceAuthorityPort,
        policy: ContextSourcePolicy | None = None,
    ) -> None:
        resolved = policy or ContextProviderPolicies.require(source.kind)
        if resolved.kind is not source.kind:
            raise ContextSourcePolicyRejected(
                ContextSourcePolicyRejected.Messages.KIND_MISMATCH
            )
        self._source = source
        self._authority = authority
        self._policy = resolved

    @property
    def kind(self) -> ContextCandidateKind:
        """Return the candidate kind this provider offers."""

        return self._policy.kind

    @property
    def policy(self) -> ContextSourcePolicy:
        """Return the ceilings and scope requirements this provider obeys."""

        return self._policy

    async def offer(
        self,
        request: ContextCandidateRequest,
        *,
        capacity: int,
    ) -> ContextProviderOffer:
        """Return what this source may contribute within ``capacity`` records.

        The four silent outcomes are checked before the store is touched, so an
        unbudgeted, unscoped, or switched-off source costs nothing at all rather
        than costing one enumeration that is then thrown away.
        """

        bound = min(max(capacity, 0), self._policy.max_candidates)
        if bound == 0:
            return self._silent(ContextProviderOutcome.BUDGET_EXHAUSTED, capacity=0)
        if not self._policy.required_dimensions <= request.scope.dimensions:
            return self._silent(
                ContextProviderOutcome.OUT_OF_SCOPE,
                capacity=bound,
            )
        if not self._is_enabled():
            return self._silent(
                ContextProviderOutcome.DISABLED
                if self._policy.optional
                else ContextProviderOutcome.UNAVAILABLE,
                capacity=bound,
            )
        return await self._enumerate(request, bound=bound)

    async def _enumerate(
        self,
        request: ContextCandidateRequest,
        *,
        bound: int,
    ) -> ContextProviderOffer:
        """Pull at most ``bound`` records and admit the ones that survive."""

        candidates: list[ContextCandidate] = []
        withheld: dict[ContextOmissionReason, int] = {}
        seen: set[str] = set()
        examined = 0
        truncated = False
        outcome = ContextProviderOutcome.OFFERED
        try:
            stream = self._source.records(scope=request.scope)
        except Exception:
            return self._silent(ContextProviderOutcome.UNAVAILABLE, capacity=bound)
        try:
            async for record in stream:
                if examined >= bound:
                    truncated = True
                    break
                examined += 1
                admitted = await self._admit(
                    record,
                    request=request,
                    position=len(candidates),
                    seen=seen,
                )
                if isinstance(admitted, ContextCandidate):
                    candidates.append(admitted)
                    seen.add(admitted.candidate_id)
                    continue
                withheld[admitted] = withheld.get(admitted, 0) + 1
        except Exception:
            # A store that fails partway is unavailable, not fatal: the records
            # already admitted were each authorized on their own, and a turn
            # must not fail because one optional-looking source went dark.
            outcome = ContextProviderOutcome.UNAVAILABLE
        finally:
            await self._close(stream)
        return ContextProviderOffer(
            candidates=tuple(candidates),
            report=ContextProviderReport(
                kind=self._policy.kind,
                outcome=outcome,
                capacity=bound,
                examined=examined,
                offered=len(candidates),
                truncated=truncated,
                withheld=self._tallies(withheld),
            ),
        )

    async def _admit(
        self,
        record: ContextSourceRecord,
        *,
        request: ContextCandidateRequest,
        position: int,
        seen: set[str],
    ) -> ContextCandidate | ContextOmissionReason:
        """Return the candidate ``record`` becomes, or why it becomes none.

        The order is not incidental.  Identity comes first because a repeated
        locator is the same material and must not be authorized or counted
        twice.  Authorization comes next, before any representation exists, so
        no shape of the material is ever computed for something the caller may
        not see.  Representability comes last, because an authorized source that
        has no honest bounded form is a different fact from an unauthorized one.
        """

        token = ContextCandidateIdentity.evidence_token(
            evidence_kind=self._policy.evidence_kind,
            locator=record.locator,
        )
        candidate_id = ContextCandidateIdentity.of(
            kind=self._policy.kind,
            evidence_token=token,
        )
        if candidate_id in seen:
            return ContextOmissionReason.DUPLICATE
        lifecycle = await self._authorize(record, request=request)
        if lifecycle is None:
            return ContextOmissionReason.conservative()
        inadmissible = lifecycle.inadmissible_reason(request.collected_at)
        if inadmissible is not None:
            return inadmissible
        options = self._options_for(record, evidence_token=token)
        if not options:
            return ContextOmissionReason.SOURCE_UNAVAILABLE
        try:
            return ContextCandidate(
                candidate_id=candidate_id,
                kind=self._policy.kind,
                source_ref=token,
                source_digest=record.content_digest,
                scope=request.scope,
                lifecycle=lifecycle,
                priority_class=self._policy.class_at(position),
                original_tokens=record.size_tokens,
                relevance_score=record.relevance_score,
                representation_options=options,
            )
        except Exception:
            # A record this module cannot turn into a well-formed candidate is
            # material whose admissibility was never established, never material
            # that is quietly dropped as low-relevance.
            return ContextOmissionReason.conservative()

    async def _authorize(
        self,
        record: ContextSourceRecord,
        *,
        request: ContextCandidateRequest,
    ) -> ContextSourceLifecycle | None:
        """Ask the authority about exactly this record, converting failure to none.

        An authority that raises, returns something else, or returns a lifecycle
        this module cannot parse is indistinguishable from one that refused, and
        both must refuse: an unusable authority is never an authorization.
        """

        try:
            lifecycle = await self._authority.authorize(
                kind=self._policy.kind,
                locator=record.locator,
                scope=request.scope,
            )
        except Exception:
            return None
        if not isinstance(lifecycle, ContextSourceLifecycle):
            return None
        return lifecycle

    def _options_for(
        self,
        record: ContextSourceRecord,
        *,
        evidence_token: str,
    ) -> tuple[ContextRepresentationOption, ...]:
        """Return the forms this record may honestly be offered in.

        Whole content only within the inline ceiling; a flat-cost reference only
        when the source domain can serve the material again and the reference is
        actually cheaper than the source.  A record that qualifies for neither
        gets an empty tuple, and an empty tuple is how this module says "there
        is no bounded form of this" rather than inventing one.
        """

        options: list[ContextRepresentationOption] = []
        if record.size_tokens <= self._policy.inline_ceiling:
            options.append(
                ContextRepresentationOption(
                    mode=ContextRepresentationMode.FULL,
                    token_count=record.size_tokens,
                    lossiness=ContextLossiness.NONE,
                    content_ref=evidence_token,
                )
            )
        if record.retrievable and (
            record.size_tokens > ContextProviderBounds.REFERENCE_TOKENS
        ):
            options.append(
                ContextRepresentationOption(
                    mode=ContextRepresentationMode.REFERENCE,
                    token_count=ContextProviderBounds.REFERENCE_TOKENS,
                    lossiness=ContextLossiness.ELIDED,
                    content_ref=evidence_token,
                )
            )
        return tuple(options)

    def _is_enabled(self) -> bool:
        """Return whether the source says it is configured, failing closed."""

        try:
            return bool(self._source.enabled)
        except Exception:
            return False

    async def _close(self, stream: AsyncIterator[ContextSourceRecord]) -> None:
        """Release a partially consumed stream without letting it fail the turn.

        A bounded consumer abandons every stream longer than its capacity, so
        closing is the normal path rather than the exceptional one, and a source
        whose cleanup raises must not turn a successful gather into a failure.
        """

        closer = getattr(stream, "aclose", None)
        if closer is None:
            return
        try:
            await closer()
        except Exception:
            return

    def _silent(
        self,
        outcome: ContextProviderOutcome,
        *,
        capacity: int,
    ) -> ContextProviderOffer:
        """Return the empty offer one non-enumerating outcome produces."""

        return ContextProviderOffer(
            candidates=(),
            report=ContextProviderReport(
                kind=self._policy.kind,
                outcome=outcome,
                capacity=capacity,
                examined=0,
                offered=0,
            ),
        )

    def _tallies(
        self,
        withheld: Mapping[ContextOmissionReason, int],
    ) -> tuple[ContextWithholdingTally, ...]:
        """Return the withholding counts in the one order a report may hold."""

        return tuple(
            ContextWithholdingTally(reason=reason, count=withheld[reason])
            for reason in sorted(
                withheld,
                key=lambda reason: ContextProviderTables.OMISSION_ORDER[reason],
            )
        )


class ContextCandidateCollector:
    """Runs every wired source once, in priority order, under one shared budget.

    Two things make a collection reproducible rather than merely repeatable.
    Providers are sorted at construction by candidate-kind declaration order, so
    the sequence they run in -- and therefore which source gets the budget when
    it runs short -- does not depend on the order a caller happened to pass
    them.  And the budget is spent on candidates *offered*, not records
    examined, so an unauthorized run of records in one source cannot starve
    another.

    The collector holds no state between collections.  It is the same object
    across a run, and it re-asks every source and every authority every time,
    because a plan built for the second model call must not inherit an
    authorization that was true before the first.
    """

    def __init__(
        self,
        providers: Sequence[ScopedCandidateProvider] = (),
        *,
        max_candidates: int | None = None,
    ) -> None:
        registered: dict[ContextCandidateKind, ScopedCandidateProvider] = {}
        for provider in providers:
            if provider.kind in registered:
                raise ContextProviderAlreadyRegistered(kind=provider.kind)
            registered[provider.kind] = provider
        self._providers = tuple(
            sorted(
                registered.values(),
                key=lambda provider: ContextProviderTables.KIND_ORDER[provider.kind],
            )
        )
        self._max_candidates = min(
            max_candidates
            if max_candidates is not None
            else ContextBounds.MAX_CANDIDATES,
            ContextBounds.MAX_CANDIDATES,
        )

    @property
    def kinds(self) -> tuple[ContextCandidateKind, ...]:
        """Return the kinds this collector gathers, in the order it gathers them."""

        return tuple(provider.kind for provider in self._providers)

    @property
    def max_candidates(self) -> int:
        """Return the candidate budget one collection may spend."""

        return self._max_candidates

    async def collect(
        self,
        request: ContextCandidateRequest,
    ) -> ContextCandidateCollection:
        """Return everything the wired sources may offer for ``request``."""

        remaining = self._max_candidates
        candidates: list[ContextCandidate] = []
        reports: list[ContextProviderReport] = []
        for provider in self._providers:
            offer = await provider.offer(request, capacity=remaining)
            candidates.extend(offer.candidates)
            reports.append(offer.report)
            remaining -= offer.report.offered
        return ContextCandidateCollection(
            scope=request.scope,
            collected_at=request.collected_at,
            candidates=ContextPlanReconstruction.ordered(tuple(candidates)),
            reports=tuple(reports),
        )


__all__ = (
    "ContextCandidateCollection",
    "ContextCandidateCollector",
    "ContextCandidateIdentity",
    "ContextCandidateRequest",
    "ContextCollectionRejected",
    "ContextProviderAlreadyRegistered",
    "ContextProviderBounds",
    "ContextProviderError",
    "ContextProviderNotConfigured",
    "ContextProviderOffer",
    "ContextProviderOutcome",
    "ContextProviderPolicies",
    "ContextProviderReport",
    "ContextProviderReportRejected",
    "ContextProviderTables",
    "ContextSourceAuthorityPort",
    "ContextSourceEnumerationPort",
    "ContextSourcePolicy",
    "ContextSourcePolicyRejected",
    "ContextSourceRecord",
    "ContextWithholdingTally",
    "ScopedCandidateProvider",
)
