"""The F5 allocator's four properties, each proved rather than described.

Determinism is proved by re-running and by tampering; the output reserve by
trying to construct a budget that spent it; omission reasons by asserting the
exact member each cause produces; and "only admitted refs hydrate" by counting
the hydrator's calls and naming which candidates it never saw.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.answer_verification import EvidenceAccessState, EvidenceTrustClass
from agent_runtime.context.context_contracts import (
    CompressionManifest,
    CompressionSummarizerIdentity,
    ContextAuthorizationScope,
    ContextBounds,
    ContextCandidate,
    ContextCandidateKind,
    ContextInclusionReason,
    ContextLossiness,
    ContextOmissionReason,
    ContextPlan,
    ContextPlanLimits,
    ContextPlanReconstruction,
    ContextPlanRevisions,
    ContextPriorityClass,
    ContextRepresentation,
    ContextRepresentationMode,
    ContextRepresentationOption,
    ContextSourceLifecycle,
    ContextSourceSpan,
)
from agent_runtime.context.planning.budgeter import (
    BASIS_POINTS,
    ContextAdmission,
    ContextAdmissionRejected,
    ContextAdmissionTables,
    ContextAllocation,
    ContextAllocationNotReproducible,
    ContextAllocationPolicy,
    ContextAllocationRejected,
    ContextBudget,
    ContextBudgetExceeded,
    ContextClassShare,
    ContextHydrationRejected,
    ContextPlanAssembler,
    aplan_model_context,
)

PLAN_AT = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
POLICY_REVISION = "policy-r1"
SUBJECT_FINGERPRINT = hashlib.sha256(b"subject").hexdigest()

KIND_FOR_CLASS: dict[ContextPriorityClass, ContextCandidateKind] = {
    ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL: ContextCandidateKind.SYSTEM_POLICY,
    ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS: (
        ContextCandidateKind.CURRENT_REQUEST
    ),
    ContextPriorityClass.APPROVAL_GATE_STATE: ContextCandidateKind.APPROVAL_STATE,
    ContextPriorityClass.ACTIVE_PLAN_OPERATIONS: ContextCandidateKind.TASK_PLAN_STATE,
    ContextPriorityClass.SELECTED_SKILLS_EVIDENCE: ContextCandidateKind.CITATION,
    ContextPriorityClass.RECENT_CONVERSATION: ContextCandidateKind.CONVERSATION_TURN,
    ContextPriorityClass.RECALLED_MEMORY: ContextCandidateKind.MEMORY,
    ContextPriorityClass.LOW_RELEVANCE_HISTORY: ContextCandidateKind.UNKNOWN,
}


def digest_of(label: str) -> str:
    """Return a stable SHA-256 hex digest standing in for real material."""

    return hashlib.sha256(label.encode()).hexdigest()


def scope() -> ContextAuthorizationScope:
    """Return the one subject scope every fixture candidate is bound to."""

    return ContextAuthorizationScope(subject_fingerprint=SUBJECT_FINGERPRINT)


def lifecycle(
    *,
    access_state: EvidenceAccessState = EvidenceAccessState.AUTHORIZED,
    retention_until: datetime | None = None,
) -> ContextSourceLifecycle:
    """Return a trusted lifecycle observed just before the plan instant."""

    return ContextSourceLifecycle(
        access_state=access_state,
        trust_label=EvidenceTrustClass.VERIFIED,
        observed_at=PLAN_AT - timedelta(seconds=1),
        retention_until=retention_until,
    )


def full_option(tokens: int) -> ContextRepresentationOption:
    """Return the whole-source form costing ``tokens``."""

    return ContextRepresentationOption(
        mode=ContextRepresentationMode.FULL,
        token_count=tokens,
        lossiness=ContextLossiness.NONE,
    )


def excerpt_option(tokens: int) -> ContextRepresentationOption:
    """Return the exact-fragment form costing ``tokens``."""

    return ContextRepresentationOption(
        mode=ContextRepresentationMode.EXCERPT,
        token_count=tokens,
        lossiness=ContextLossiness.EXTRACTIVE,
    )


def summary_option(tokens: int) -> ContextRepresentationOption:
    """Return the model-written form costing ``tokens``."""

    return ContextRepresentationOption(
        mode=ContextRepresentationMode.SUMMARY,
        token_count=tokens,
        lossiness=ContextLossiness.ABSTRACTIVE,
    )


def reference_option(tokens: int = 2) -> ContextRepresentationOption:
    """Return the bare-pointer form costing ``tokens``."""

    return ContextRepresentationOption(
        mode=ContextRepresentationMode.REFERENCE,
        token_count=tokens,
        lossiness=ContextLossiness.ELIDED,
    )


def candidate(
    candidate_id: str,
    *,
    priority_class: ContextPriorityClass = ContextPriorityClass.RECENT_CONVERSATION,
    tokens: int = 100,
    options: tuple[ContextRepresentationOption, ...] | None = None,
    relevance: int | None = None,
    source: str | None = None,
    source_lifecycle: ContextSourceLifecycle | None = None,
) -> ContextCandidate:
    """Return one valid candidate whose kind matches the class it claims."""

    return ContextCandidate(
        candidate_id=candidate_id,
        kind=KIND_FOR_CLASS[priority_class],
        source_ref=f"ref-{candidate_id}",
        source_digest=digest_of(source or candidate_id),
        scope=scope(),
        lifecycle=source_lifecycle or lifecycle(),
        priority_class=priority_class,
        original_tokens=tokens,
        relevance_score=relevance,
        representation_options=(
            options if options is not None else (full_option(tokens),)
        ),
    )


def limits(
    *,
    model_context_limit: int = 1_000,
    reserved_output_tokens: int = 200,
    fixed_tokens: int = 100,
    safety_margin_tokens: int = 0,
) -> ContextPlanLimits:
    """Return provider limits leaving a known variable budget."""

    return ContextPlanLimits(
        model_context_limit=model_context_limit,
        reserved_output_tokens=reserved_output_tokens,
        fixed_tokens=fixed_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )


def revisions(policy_revision: str = POLICY_REVISION) -> ContextPlanRevisions:
    """Return the three revisions one plan is deterministic against."""

    return ContextPlanRevisions(
        policy_revision=policy_revision,
        planner_revision="planner-r1",
        tokenizer_revision="tokenizer-r1",
    )


def policy(
    *shares: ContextClassShare,
    policy_revision: str = POLICY_REVISION,
) -> ContextAllocationPolicy:
    """Return an allocation policy carrying ``shares``."""

    return ContextAllocationPolicy(
        policy_revision=policy_revision,
        class_shares=shares,
    )


def allocate(
    candidates: tuple[ContextCandidate, ...],
    *,
    plan_limits: ContextPlanLimits | None = None,
    plan_policy: ContextAllocationPolicy | None = None,
    at: datetime = PLAN_AT,
) -> ContextAllocation:
    """Return the allocation for ``candidates`` under the default fixtures."""

    return ContextAllocation.allocate(
        candidates=candidates,
        limits=plan_limits or limits(),
        revisions=revisions(),
        policy=plan_policy
        or ContextAllocationPolicy.unconstrained(policy_revision=POLICY_REVISION),
        decided_at=at,
    )


def represent(
    source: ContextCandidate,
    option: ContextRepresentationOption,
) -> ContextRepresentation:
    """Return a valid hydrated representation of ``option`` for ``source``."""

    if option.mode is ContextRepresentationMode.FULL:
        return ContextRepresentation(
            mode=option.mode,
            token_count=option.token_count,
            lossiness=option.lossiness,
            source_digest=source.source_digest,
            content_digest=source.source_digest,
        )
    content_digest = digest_of(f"{option.mode}:{source.candidate_id}")
    if option.mode is ContextRepresentationMode.REFERENCE:
        return ContextRepresentation(
            mode=option.mode,
            token_count=option.token_count,
            lossiness=option.lossiness,
            source_digest=source.source_digest,
            content_digest=content_digest,
            content_ref=source.source_ref,
        )
    spans = (
        (
            ContextSourceSpan(
                span_id=f"span-{source.candidate_id}",
                locator="character_range",
                start=0,
                end=8,
            ),
        )
        if option.lossiness is ContextLossiness.EXTRACTIVE
        else ()
    )
    summarizer = (
        CompressionSummarizerIdentity(
            model_id="summarizer-model",
            prompt_revision="summary-prompt-r1",
            summarizer_revision="summarizer-r1",
        )
        if option.lossiness is ContextLossiness.ABSTRACTIVE
        else None
    )
    target_tokens = max(option.token_count, 1)
    manifest = CompressionManifest(
        manifest_id=f"manifest-{source.candidate_id}",
        source_ref=source.source_ref,
        source_digest=source.source_digest,
        source_tokens=source.original_tokens,
        output_digest=content_digest,
        output_tokens=option.token_count,
        target_tokens=target_tokens,
        lossiness=option.lossiness,
        source_spans=spans,
        summarizer=summarizer,
        authorization_scope=source.scope,
        policy_revision=POLICY_REVISION,
        generated_at=PLAN_AT,
        cache_key=CompressionManifest.derive_cache_key(
            source_digest=source.source_digest,
            target_tokens=target_tokens,
            policy_revision=POLICY_REVISION,
            summarizer_revision=(
                None if summarizer is None else summarizer.summarizer_revision
            ),
        ),
    )
    return ContextRepresentation(
        mode=option.mode,
        token_count=option.token_count,
        lossiness=option.lossiness,
        source_digest=source.source_digest,
        content_digest=content_digest,
        source_spans=spans,
        compression=manifest,
        generated_at=PLAN_AT,
    )


class CountingHydrator:
    """A hydrator that records exactly which candidates it was asked to fetch."""

    def __init__(self) -> None:
        self.fetched: list[str] = []

    @property
    def fetch_count(self) -> int:
        """Return how many times material was actually resolved."""

        return len(self.fetched)

    async def hydrate(
        self,
        *,
        candidate: ContextCandidate,
        option: ContextRepresentationOption,
    ) -> ContextRepresentation:
        """Record the fetch and return the admitted form."""

        self.fetched.append(candidate.candidate_id)
        return represent(candidate, option)


class FatterHydrator(CountingHydrator):
    """A hydrator that returns more than the plan budgeted for."""

    async def hydrate(
        self,
        *,
        candidate: ContextCandidate,
        option: ContextRepresentationOption,
    ) -> ContextRepresentation:
        """Return the whole source regardless of the admitted form."""

        self.fetched.append(candidate.candidate_id)
        return represent(candidate, full_option(candidate.original_tokens))


class ForeignSourceHydrator(CountingHydrator):
    """A hydrator that answers with a different source than it was asked about."""

    async def hydrate(
        self,
        *,
        candidate: ContextCandidate,
        option: ContextRepresentationOption,
    ) -> ContextRepresentation:
        """Return a representation of somebody else's material."""

        self.fetched.append(candidate.candidate_id)
        return ContextRepresentation(
            mode=ContextRepresentationMode.FULL,
            token_count=option.token_count,
            lossiness=ContextLossiness.NONE,
            source_digest=digest_of("some-other-source"),
            content_digest=digest_of("some-other-source"),
        )


async def assemble(
    allocation: ContextAllocation,
    hydrator: CountingHydrator,
) -> ContextPlan:
    """Return the durable plan for ``allocation`` using ``hydrator``."""

    return await ContextPlanAssembler(hydrator=hydrator).assemble(
        allocation=allocation,
        plan_id="plan-1",
        run_id="run-1",
        model_call_id="call-1",
    )


class RejectionAssertionMixin:
    """Assert the typed domain error a validator raised, not merely that it did."""

    @staticmethod
    def assert_rejected(
        expected_type: type[Exception],
        expected_message: str,
        build,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build()
        causes = [
            error.get("ctx", {}).get("error") for error in exc_info.value.errors()
        ]
        typed = [cause for cause in causes if isinstance(cause, expected_type)]
        assert typed, f"expected {expected_type.__name__}, got {causes}"
        assert expected_message in {str(cause) for cause in typed}


class TestOutputReserveIsInviolable(RejectionAssertionMixin):
    """Over-allocation is unrepresentable, not merely rejected after the fact."""

    def test_opening_budget_withholds_reserve_fixed_and_margin(self) -> None:
        budget = ContextBudget.opening(
            limits(
                model_context_limit=1_000,
                reserved_output_tokens=200,
                fixed_tokens=150,
                safety_margin_tokens=50,
            )
        )

        assert budget.available_tokens == 600
        assert budget.remaining_tokens == 600
        assert budget.reserved_output_tokens == 200

    def test_a_budget_that_spent_the_reserve_cannot_be_constructed(self) -> None:
        plan_limits = limits()

        self.assert_rejected(
            ContextBudgetExceeded,
            ContextBudgetExceeded.Messages.RESERVE_INVADED,
            lambda: ContextBudget(
                limits=plan_limits,
                spent_tokens=plan_limits.available_tokens + 1,
            ),
        )

    def test_spending_the_last_token_is_allowed_and_one_more_is_not(self) -> None:
        budget = ContextBudget.opening(limits())

        exhausted = budget.spend(budget.available_tokens)

        assert exhausted.remaining_tokens == 0
        with pytest.raises(ContextBudgetExceeded) as exc_info:
            exhausted.spend(1)
        assert str(exc_info.value) == ContextBudgetExceeded.Messages.RESERVE_INVADED

    def test_spending_never_mutates_the_budget_it_came_from(self) -> None:
        budget = ContextBudget.opening(limits())

        spent = budget.spend(100)

        assert budget.spent_tokens == 0
        assert spent.spent_tokens == 100
        assert spent is not budget

    def test_a_plan_with_no_room_to_answer_is_not_a_plan(self) -> None:
        self.assert_rejected(
            ContextBudgetExceeded,
            ContextBudgetExceeded.Messages.NO_OUTPUT_RESERVE,
            lambda: ContextBudget.opening(limits(reserved_output_tokens=0)),
        )

    def test_fixed_content_that_crowds_out_the_reserve_is_refused(self) -> None:
        self.assert_rejected(
            ContextBudgetExceeded,
            ContextBudgetExceeded.Messages.FIXED_CONTENT_EXCEEDS_LIMIT,
            lambda: ContextBudget.opening(
                limits(
                    model_context_limit=1_000,
                    reserved_output_tokens=200,
                    fixed_tokens=900,
                )
            ),
        )

    def test_an_allocation_over_crowded_limits_produces_no_plan(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            allocate(
                (candidate("turn-1", tokens=10),),
                plan_limits=limits(
                    model_context_limit=500,
                    reserved_output_tokens=200,
                    fixed_tokens=400,
                ),
            )

        causes = [
            error.get("ctx", {}).get("error") for error in exc_info.value.errors()
        ]
        assert any(isinstance(cause, ContextBudgetExceeded) for cause in causes)

    @pytest.mark.parametrize("offered", [40, 120, 400, 900])
    def test_no_allocation_ever_leaves_the_model_without_its_reserve(
        self,
        offered: int,
    ) -> None:
        plan_limits = limits(
            model_context_limit=1_000,
            reserved_output_tokens=250,
            fixed_tokens=150,
            safety_margin_tokens=25,
        )
        candidates = tuple(
            candidate(f"turn-{index}", tokens=offered) for index in range(6)
        )

        allocation = allocate(candidates, plan_limits=plan_limits)

        spent = (
            allocation.allocated_tokens
            + plan_limits.fixed_tokens
            + plan_limits.safety_margin_tokens
        )
        assert spent + plan_limits.reserved_output_tokens <= (
            plan_limits.model_context_limit
        )
        assert allocation.closing_budget.remaining_tokens >= 0


class TestAllocationIsDeterministic(RejectionAssertionMixin):
    """The same candidates and the same budget produce the same plan, checkably."""

    def test_the_same_inputs_allocate_identically(self) -> None:
        candidates = (
            candidate("turn-1", tokens=300),
            candidate("turn-2", tokens=300),
            candidate("turn-3", tokens=300),
        )

        first = allocate(candidates)
        second = allocate(candidates)

        assert first.admissions == second.admissions
        assert first == second

    async def test_candidate_order_cannot_change_the_plan(self) -> None:
        ordered = (
            candidate("turn-1", tokens=300),
            candidate(
                "evidence-1",
                tokens=300,
                priority_class=(ContextPriorityClass.SELECTED_SKILLS_EVIDENCE),
            ),
            candidate(
                "memory-1",
                tokens=300,
                priority_class=(ContextPriorityClass.RECALLED_MEMORY),
            ),
        )
        shuffled = (ordered[2], ordered[0], ordered[1])

        first = await assemble(allocate(ordered), CountingHydrator())
        second = await assemble(allocate(shuffled), CountingHydrator())

        assert first.plan_digest == second.plan_digest
        assert first.reconstructs(second)

    def test_a_tampered_omission_reason_is_refused_on_parse(self) -> None:
        allocation = allocate(
            (
                candidate("turn-1", tokens=600),
                candidate("turn-2", tokens=600),
            )
        )
        payload = allocation.model_dump(mode="json")
        omitted = [
            index
            for index, entry in enumerate(payload["admissions"])
            if entry["omission_reason"] is not None
        ]
        assert omitted, "fixture must produce at least one omission to tamper with"
        payload["admissions"][omitted[0]]["omission_reason"] = (
            ContextOmissionReason.LOW_RELEVANCE.value
        )

        self.assert_rejected(
            ContextAllocationNotReproducible,
            ContextAllocationNotReproducible.Messages.ADMISSIONS_MISMATCH,
            lambda: ContextAllocation.model_validate(payload),
        )

    def test_a_reordered_allocation_is_refused_on_parse(self) -> None:
        allocation = allocate(
            (
                candidate("turn-1", tokens=600),
                candidate("turn-2", tokens=600),
            )
        )
        payload = allocation.model_dump(mode="json")
        payload["admissions"] = list(reversed(payload["admissions"]))

        self.assert_rejected(
            ContextAllocationNotReproducible,
            ContextAllocationNotReproducible.Messages.ADMISSIONS_MISMATCH,
            lambda: ContextAllocation.model_validate(payload),
        )

    def test_an_untouched_allocation_survives_its_own_round_trip(self) -> None:
        allocation = allocate(
            (
                candidate("turn-1", tokens=300),
                candidate("turn-2", tokens=600),
            )
        )

        replayed = ContextAllocation.model_validate(allocation.model_dump(mode="json"))

        assert replayed == allocation

    def test_an_allocation_must_record_the_policy_it_was_decided_under(self) -> None:
        self.assert_rejected(
            ContextAllocationRejected,
            ContextAllocationRejected.Messages.POLICY_REVISION_MISMATCH,
            lambda: ContextAllocation.allocate(
                candidates=(candidate("turn-1", tokens=100),),
                limits=limits(),
                revisions=revisions("policy-r1"),
                policy=ContextAllocationPolicy.unconstrained(
                    policy_revision="policy-r2"
                ),
                decided_at=PLAN_AT,
            ),
        )

    def test_a_naive_plan_instant_is_refused(self) -> None:
        self.assert_rejected(
            ContextAllocationRejected,
            ContextAllocationRejected.Messages.NAIVE_TIMESTAMP,
            lambda: allocate(
                (candidate("turn-1", tokens=100),),
                at=datetime(2026, 7, 29, 12, 0),  # noqa: DTZ001
            ),
        )

    def test_ties_on_class_and_relevance_break_on_candidate_id(self) -> None:
        candidates = (
            candidate("turn-zulu", tokens=500, relevance=10),
            candidate("turn-alpha", tokens=500, relevance=10),
        )

        allocation = allocate(candidates, plan_limits=limits(fixed_tokens=200))

        assert [entry.candidate.candidate_id for entry in allocation.admissions] == [
            "turn-alpha",
            "turn-zulu",
        ]
        assert allocation.admitted[0].candidate.candidate_id == "turn-alpha"

    def test_higher_relevance_wins_the_budget_within_one_class(self) -> None:
        candidates = (
            candidate("turn-dim", tokens=400, relevance=1),
            candidate("turn-bright", tokens=400, relevance=900),
        )

        allocation = allocate(candidates, plan_limits=limits(fixed_tokens=200))

        assert [entry.candidate.candidate_id for entry in allocation.admitted] == [
            "turn-bright"
        ]
        assert allocation.omitted[0].candidate.candidate_id == "turn-dim"

    def test_unknown_relevance_never_outranks_a_scored_candidate(self) -> None:
        candidates = (
            candidate("turn-unscored", tokens=400, relevance=None),
            candidate("turn-scored", tokens=400, relevance=1),
        )

        allocation = allocate(candidates, plan_limits=limits(fixed_tokens=200))

        assert [entry.candidate.candidate_id for entry in allocation.admitted] == [
            "turn-scored"
        ]


class TestEveryOmissionCarriesAReason(RejectionAssertionMixin):
    """A plan explains itself without carrying what it left out."""

    def test_an_admission_with_no_outcome_is_refused(self) -> None:
        self.assert_rejected(
            ContextAdmissionRejected,
            ContextAdmissionRejected.Messages.AMBIGUOUS_OUTCOME,
            lambda: ContextAdmission(candidate=candidate("turn-1")),
        )

    def test_an_admission_with_two_outcomes_is_refused(self) -> None:
        subject = candidate("turn-1", tokens=10)

        self.assert_rejected(
            ContextAdmissionRejected,
            ContextAdmissionRejected.Messages.AMBIGUOUS_OUTCOME,
            lambda: ContextAdmission(
                candidate=subject,
                admitted_option=full_option(10),
                inclusion_reason=ContextInclusionReason.RECENCY_WINDOW,
                omission_reason=ContextOmissionReason.BUDGET_EXHAUSTED,
            ),
        )

    def test_an_admission_cannot_invent_a_form(self) -> None:
        subject = candidate("turn-1", tokens=10, options=(full_option(10),))

        self.assert_rejected(
            ContextAdmissionRejected,
            ContextAdmissionRejected.Messages.UNOFFERED_OPTION,
            lambda: ContextAdmission(
                candidate=subject,
                admitted_option=summary_option(4),
                inclusion_reason=ContextInclusionReason.RECENCY_WINDOW,
            ),
        )

    def test_every_candidate_that_did_not_travel_says_why(self) -> None:
        allocation = allocate(
            tuple(candidate(f"turn-{index}", tokens=400) for index in range(6))
        )

        assert allocation.omitted
        assert all(
            entry.omission_reason is not None and entry.inclusion_reason is None
            for entry in allocation.omitted
        )
        assert all(
            entry.inclusion_reason is not None and entry.omission_reason is None
            for entry in allocation.admitted
        )

    @pytest.mark.parametrize(
        ("access_state", "expected"),
        [
            (EvidenceAccessState.UNAUTHORIZED, ContextOmissionReason.UNAUTHORIZED),
            (EvidenceAccessState.REVOKED, ContextOmissionReason.REVOKED),
            (EvidenceAccessState.EXPIRED, ContextOmissionReason.RETENTION_EXPIRED),
            (EvidenceAccessState.NOT_FOUND, ContextOmissionReason.SOURCE_UNAVAILABLE),
            (EvidenceAccessState.UNAVAILABLE, ContextOmissionReason.SOURCE_UNAVAILABLE),
        ],
    )
    def test_an_inadmissible_source_omits_for_its_own_lifecycle_reason(
        self,
        access_state: EvidenceAccessState,
        expected: ContextOmissionReason,
    ) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-1",
                    tokens=10,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    source_lifecycle=lifecycle(access_state=access_state),
                ),
            )
        )

        assert allocation.admissions[0].omission_reason is expected
        assert not allocation.admissions[0].omission_reason.budgetary

    def test_retention_that_lapsed_by_plan_time_omits_as_expired(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-1",
                    tokens=10,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    source_lifecycle=lifecycle(
                        retention_until=PLAN_AT - timedelta(seconds=1)
                    ),
                ),
            )
        )

        assert allocation.admissions[0].omission_reason is (
            ContextOmissionReason.RETENTION_EXPIRED
        )

    def test_material_dropped_for_room_says_budget_exhausted(self) -> None:
        allocation = allocate(
            (
                candidate("turn-1", tokens=600),
                candidate("turn-2", tokens=600),
            )
        )

        assert allocation.omitted[0].omission_reason is (
            ContextOmissionReason.BUDGET_EXHAUSTED
        )

    def test_a_repeated_source_says_duplicate(self) -> None:
        allocation = allocate(
            (
                candidate("turn-1", tokens=100, source="shared"),
                candidate("turn-2", tokens=100, source="shared"),
            )
        )

        assert allocation.admitted[0].candidate.candidate_id == "turn-1"
        assert allocation.omitted[0].omission_reason is ContextOmissionReason.DUPLICATE

    def test_a_candidate_offering_no_form_is_not_blamed_on_the_budget(self) -> None:
        allocation = allocate((candidate("turn-1", tokens=10, options=()),))

        reason = allocation.admissions[0].omission_reason
        assert reason is ContextOmissionReason.ADMISSIBILITY_NOT_ESTABLISHED
        assert not reason.budgetary

    def test_inclusion_reasons_name_the_authority_or_the_judgement(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "request-1",
                    tokens=10,
                    priority_class=ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS,
                ),
                candidate(
                    "approval-1",
                    tokens=10,
                    priority_class=ContextPriorityClass.APPROVAL_GATE_STATE,
                ),
                candidate(
                    "plan-1",
                    tokens=10,
                    priority_class=ContextPriorityClass.ACTIVE_PLAN_OPERATIONS,
                ),
                candidate("turn-1", tokens=10),
            )
        )

        reasons = {
            entry.candidate.candidate_id: entry.inclusion_reason
            for entry in allocation.admitted
        }
        assert reasons == {
            "request-1": ContextInclusionReason.CURRENT_INTENT,
            "approval-1": ContextInclusionReason.PENDING_APPROVAL,
            "plan-1": ContextInclusionReason.ACTIVE_PLAN,
            "turn-1": ContextInclusionReason.RECENCY_WINDOW,
        }

    def test_material_admitted_as_a_pointer_says_so(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-1",
                    tokens=900,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    options=(full_option(900), reference_option(3)),
                ),
            )
        )

        assert allocation.admitted[0].inclusion_reason is (
            ContextInclusionReason.RETRIEVABLE_REFERENCE
        )

    def test_an_unclassified_admission_claims_neither_authority_nor_relevance(
        self,
    ) -> None:
        reason = ContextAdmissionTables.inclusion_reason_for(
            priority_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
            mode=ContextRepresentationMode.FULL,
        )

        assert reason is ContextAdmissionTables.CONSERVATIVE_INCLUSION
        assert reason is not ContextInclusionReason.PROTECTED_CLASS
        assert reason is not ContextInclusionReason.HIGH_RELEVANCE


class TestOnlyAdmittedRefsHydrate:
    """A candidate that did not make the plan was never fetched."""

    async def test_the_fetch_count_equals_the_admitted_count(self) -> None:
        candidates = tuple(candidate(f"turn-{index}", tokens=300) for index in range(5))
        allocation = allocate(candidates)
        hydrator = CountingHydrator()

        plan = await assemble(allocation, hydrator)

        assert len(allocation.admitted) == 2
        assert len(allocation.omitted) == 3
        assert hydrator.fetch_count == 2
        assert hydrator.fetched == [
            entry.candidate.candidate_id for entry in allocation.admitted
        ]
        assert len(plan.omitted_decisions) == 3

    async def test_an_omitted_candidate_is_never_fetched(self) -> None:
        allocation = allocate(
            (
                candidate("turn-keep", tokens=600, relevance=900),
                candidate("turn-drop", tokens=600, relevance=1),
            )
        )
        hydrator = CountingHydrator()

        await assemble(allocation, hydrator)

        assert hydrator.fetched == ["turn-keep"]
        assert "turn-drop" not in hydrator.fetched

    async def test_an_inadmissible_source_is_never_fetched(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-revoked",
                    tokens=10,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    source_lifecycle=lifecycle(
                        access_state=EvidenceAccessState.REVOKED
                    ),
                ),
            )
        )
        hydrator = CountingHydrator()

        await assemble(allocation, hydrator)

        assert hydrator.fetch_count == 0

    async def test_a_plan_with_no_admissions_fetches_nothing(self) -> None:
        allocation = allocate(
            tuple(candidate(f"turn-{index}", tokens=800) for index in range(3))
        )
        hydrator = CountingHydrator()

        plan = await assemble(allocation, hydrator)

        assert hydrator.fetch_count == 0
        assert plan.allocated_tokens == 0

    async def test_hydration_happens_once_per_admitted_candidate(self) -> None:
        candidates = tuple(candidate(f"turn-{index}", tokens=100) for index in range(6))
        allocation = allocate(candidates)
        hydrator = CountingHydrator()

        await assemble(allocation, hydrator)

        assert len(set(hydrator.fetched)) == hydrator.fetch_count
        assert hydrator.fetch_count == len(allocation.admitted)


class TestRefusedNeverTruncated:
    """A plan that would exceed the budget is refused, not quietly shrunk."""

    def test_protected_context_that_does_not_fit_refuses_the_plan(self) -> None:
        with pytest.raises(ContextBudgetExceeded) as exc_info:
            allocate(
                (
                    candidate(
                        "request-1",
                        tokens=900,
                        priority_class=(
                            ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS
                        ),
                    ),
                )
            )

        assert str(exc_info.value) == (
            ContextBudgetExceeded.Messages.PROTECTED_DOES_NOT_FIT
        )

    def test_immutable_context_is_carried_whole_or_refused(self) -> None:
        with pytest.raises(ContextBudgetExceeded) as exc_info:
            allocate(
                (
                    candidate(
                        "policy-1",
                        tokens=900,
                        priority_class=(ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL),
                        options=(full_option(900), summary_option(10)),
                    ),
                )
            )

        assert str(exc_info.value) == (
            ContextBudgetExceeded.Messages.IMMUTABLE_DOES_NOT_FIT
        )

    def test_immutable_context_must_be_offered_whole(self) -> None:
        with pytest.raises(ContextBudgetExceeded) as exc_info:
            allocate(
                (
                    candidate(
                        "policy-1",
                        tokens=100,
                        priority_class=(ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL),
                        options=(summary_option(10),),
                    ),
                )
            )

        assert str(exc_info.value) == (
            ContextBudgetExceeded.Messages.IMMUTABLE_NOT_OFFERED_WHOLE
        )

    def test_protected_context_may_still_be_reduced_to_fit(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "approval-1",
                    tokens=900,
                    priority_class=ContextPriorityClass.APPROVAL_GATE_STATE,
                    options=(full_option(900), summary_option(50)),
                ),
            )
        )

        admitted = allocation.admitted[0]
        assert admitted.admitted_option is not None
        assert admitted.admitted_option.mode is ContextRepresentationMode.SUMMARY
        assert admitted.inclusion_reason is ContextInclusionReason.PENDING_APPROVAL

    async def test_an_admitted_form_is_exactly_the_form_that_was_budgeted(
        self,
    ) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-1",
                    tokens=600,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    options=(full_option(600), excerpt_option(120)),
                ),
                candidate("turn-1", tokens=500),
            )
        )

        plan = await assemble(allocation, CountingHydrator())

        budgeted = {
            entry.candidate.candidate_id: entry.token_count
            for entry in allocation.admissions
        }
        recorded = {
            decision.candidate.candidate_id: decision.token_count
            for decision in plan.candidate_decisions
        }
        assert recorded == budgeted
        assert plan.allocated_tokens == allocation.allocated_tokens

    async def test_a_hydrator_returning_more_than_it_was_allowed_is_refused(
        self,
    ) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-1",
                    tokens=900,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    options=(full_option(900), excerpt_option(100)),
                ),
            )
        )
        hydrator = FatterHydrator()

        with pytest.raises(ContextHydrationRejected) as exc_info:
            await assemble(allocation, hydrator)

        assert str(exc_info.value) == (
            ContextHydrationRejected.Messages.NOT_THE_ADMITTED_OPTION
        )

    async def test_a_hydrator_answering_about_another_source_is_refused(self) -> None:
        allocation = allocate((candidate("turn-1", tokens=100),))

        with pytest.raises(ContextHydrationRejected) as exc_info:
            await assemble(allocation, ForeignSourceHydrator())

        assert str(exc_info.value) == (
            ContextHydrationRejected.Messages.HYDRATED_ANOTHER_SOURCE
        )

    def test_more_candidates_than_one_plan_may_decide_are_refused(self) -> None:
        too_many = tuple(
            candidate(f"turn-{index}", tokens=1)
            for index in range(ContextBounds.MAX_CANDIDATES + 1)
        )

        with pytest.raises(ContextAllocationRejected) as exc_info:
            allocate(too_many)

        assert str(exc_info.value) == (
            ContextAllocationRejected.Messages.TOO_MANY_CANDIDATES
        )

    def test_a_candidate_decided_twice_is_refused(self) -> None:
        repeated = candidate("turn-1", tokens=10)

        with pytest.raises(ContextAllocationRejected) as exc_info:
            allocate((repeated, repeated))

        assert str(exc_info.value) == (
            ContextAllocationRejected.Messages.DUPLICATE_CANDIDATE_ID
        )


class TestAllocationAmongCategories(RejectionAssertionMixin):
    """Shares make this an allocation among categories rather than a queue."""

    def test_a_share_stops_one_class_from_starving_the_next(self) -> None:
        candidates = (
            candidate("turn-1", tokens=200),
            candidate("turn-2", tokens=200),
            candidate("turn-3", tokens=200),
            candidate(
                "memory-1",
                tokens=200,
                priority_class=ContextPriorityClass.RECALLED_MEMORY,
            ),
        )
        shared = policy(
            ContextClassShare(
                priority_class=ContextPriorityClass.RECENT_CONVERSATION,
                max_share_basis_points=BASIS_POINTS // 2,
            )
        )

        starved = allocate(candidates)
        fair = allocate(candidates, plan_policy=shared)

        assert [entry.candidate.candidate_id for entry in starved.admitted] == [
            "turn-1",
            "turn-2",
            "turn-3",
        ]
        assert starved.omitted[0].candidate.candidate_id == "memory-1"
        assert starved.omitted[0].omission_reason is (
            ContextOmissionReason.BUDGET_EXHAUSTED
        )

        assert [entry.candidate.candidate_id for entry in fair.admitted] == [
            "turn-1",
            "memory-1",
        ]
        assert [entry.candidate.candidate_id for entry in fair.omitted] == [
            "turn-2",
            "turn-3",
        ]
        assert all(
            entry.omission_reason is ContextOmissionReason.CLASS_SHARE_EXHAUSTED
            for entry in fair.omitted
        )

    def test_a_share_of_nothing_omits_the_whole_class_by_share(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "memory-1",
                    tokens=10,
                    priority_class=(ContextPriorityClass.RECALLED_MEMORY),
                ),
            ),
            plan_policy=policy(
                ContextClassShare(
                    priority_class=ContextPriorityClass.RECALLED_MEMORY,
                    max_share_basis_points=0,
                )
            ),
        )

        assert allocation.admissions[0].omission_reason is (
            ContextOmissionReason.CLASS_SHARE_EXHAUSTED
        )

    def test_protected_context_can_never_carry_a_share(self) -> None:
        self.assert_rejected(
            ContextAllocationRejected,
            ContextAllocationRejected.Messages.PROTECTED_CLASS_SHARED,
            lambda: ContextClassShare(
                priority_class=ContextPriorityClass.APPROVAL_GATE_STATE,
                max_share_basis_points=1_000,
            ),
        )

    def test_shares_are_canonically_ordered_and_unique(self) -> None:
        self.assert_rejected(
            ContextAllocationRejected,
            ContextAllocationRejected.Messages.SHARE_NOT_CANONICAL,
            lambda: policy(
                ContextClassShare(
                    priority_class=ContextPriorityClass.RECALLED_MEMORY,
                    max_share_basis_points=1_000,
                ),
                ContextClassShare(
                    priority_class=ContextPriorityClass.RECENT_CONVERSATION,
                    max_share_basis_points=1_000,
                ),
            ),
        )

    def test_a_share_is_integer_arithmetic_over_the_variable_budget(self) -> None:
        share = ContextClassShare(
            priority_class=ContextPriorityClass.RECENT_CONVERSATION,
            max_share_basis_points=3_333,
        )

        assert share.cap_tokens(700) == 233
        assert share.cap_tokens(0) == 0


class TestPlanAssembly:
    """The allocator's output is a plan the contract module accepts as its own."""

    async def test_the_assembled_plan_reconstructs_itself(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "policy-1",
                    tokens=50,
                    priority_class=ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL,
                ),
                candidate(
                    "evidence-1",
                    tokens=400,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    options=(full_option(400), excerpt_option(120)),
                ),
                candidate("turn-1", tokens=200),
                candidate("turn-2", tokens=900),
            )
        )

        plan = await assemble(allocation, CountingHydrator())

        assert ContextPlan.model_validate(plan.model_dump(mode="json")) == plan
        assert plan.input_digest == ContextPlanReconstruction.input_digest(
            candidates=allocation.candidates,
            limits=allocation.limits,
            revisions=allocation.revisions,
        )
        assert plan.created_at == allocation.decided_at
        assert plan.allocated_tokens <= plan.limits.available_tokens

    async def test_the_one_call_entry_point_decides_then_hydrates(self) -> None:
        hydrator = CountingHydrator()

        plan = await aplan_model_context(
            candidates=(
                candidate("turn-1", tokens=400),
                candidate("turn-2", tokens=400),
                candidate("turn-3", tokens=400),
            ),
            limits=limits(),
            revisions=revisions(),
            policy=ContextAllocationPolicy.unconstrained(
                policy_revision=POLICY_REVISION
            ),
            hydrator=hydrator,
            plan_id="plan-1",
            run_id="run-1",
            model_call_id="call-1",
            at=PLAN_AT,
        )

        assert hydrator.fetch_count == 1
        assert plan.allocated_tokens == 400
        assert len(plan.omitted_decisions) == 2

    async def test_an_omitted_decision_carries_no_content_at_all(self) -> None:
        allocation = allocate(
            (
                candidate("turn-1", tokens=600),
                candidate("turn-2", tokens=600),
            )
        )

        plan = await assemble(allocation, CountingHydrator())

        omitted = plan.omitted_decisions[0]
        assert omitted.representation.mode is ContextRepresentationMode.OMITTED
        assert omitted.representation.content_digest is None
        assert omitted.representation.content_ref is None
        assert omitted.token_count == 0

    async def test_a_compressed_admission_keeps_its_source_link(self) -> None:
        allocation = allocate(
            (
                candidate(
                    "evidence-1",
                    tokens=900,
                    priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                    options=(full_option(900), excerpt_option(100)),
                ),
            )
        )

        plan = await assemble(allocation, CountingHydrator())

        representation = plan.candidate_decisions[0].representation
        assert representation.mode is ContextRepresentationMode.EXCERPT
        assert representation.compression is not None
        assert representation.compression.source_digest == (
            representation.source_digest
        )
        assert representation.may_originate_citation
