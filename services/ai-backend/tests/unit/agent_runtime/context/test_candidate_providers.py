"""F5.2 scoped candidate providers: authority, bounds, order, and silence.

Every test answers one of the six lane questions.  Does a provider ever offer
material the caller is not authorized to see; does a revoked or unreachable
source refuse or explode; is the bound on enumeration structural or merely
respected; do two runs over the same inputs produce the same sequence; does a
switched-off optional source stay silent; and can a body reach a candidate.

The body question is answered by *injection*, twice.  Probing every contract
with a seeded secret proves no field will hold one, and running a whole
collection over a source whose locator carries the secret proves the one field
that is allowed to name material never lets that name escape.  Reading field
names would prove only that nobody called a field ``content``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from typing import Any

from pydantic import ValidationError
import pytest

from agent_runtime.answer_verification import EvidenceAccessState, EvidenceTrustClass
from agent_runtime.context.context_contracts import (
    ContextAuthorizationScope,
    ContextBounds,
    ContextCandidate,
    ContextCandidateKind,
    ContextLossiness,
    ContextOmissionReason,
    ContextPriorityClass,
    ContextRepresentationMode,
    ContextScopeDimension,
    ContextSourceLifecycle,
)
from agent_runtime.context.evidence_registry import EvidenceKind, EvidenceRefIdentity
from agent_runtime.context.planning import providers as provider_module
from agent_runtime.context.planning.providers import (
    ContextCandidateCollection,
    ContextCandidateCollector,
    ContextCandidateIdentity,
    ContextCandidateRequest,
    ContextCollectionRejected,
    ContextProviderAlreadyRegistered,
    ContextProviderBounds,
    ContextProviderNotConfigured,
    ContextProviderOffer,
    ContextProviderOutcome,
    ContextProviderPolicies,
    ContextProviderReport,
    ContextProviderReportRejected,
    ContextSourceAuthorityPort,
    ContextSourceEnumerationPort,
    ContextSourcePolicy,
    ContextSourcePolicyRejected,
    ContextSourceRecord,
    ContextWithholdingTally,
    ScopedCandidateProvider,
)
from agent_runtime.execution.contracts import RuntimeContract

_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


class FakeSource:
    """A source domain that lists exactly what a test tells it to list.

    It records whether it was enumerated at all, which is how the suite proves
    that a disabled, unscoped, or unbudgeted source costs nothing rather than
    costing one enumeration whose result is discarded.
    """

    def __init__(
        self,
        kind: ContextCandidateKind,
        records: tuple[ContextSourceRecord, ...] = (),
        *,
        enabled: bool = True,
        raise_before: bool = False,
        raise_after: int | None = None,
        endless: bool = False,
        enabled_raises: bool = False,
    ) -> None:
        self._kind = kind
        self._records = records
        self._enabled = enabled
        self._raise_before = raise_before
        self._raise_after = raise_after
        self._endless = endless
        self._enabled_raises = enabled_raises
        self.enumerations = 0
        self.scopes: list[ContextAuthorizationScope] = []
        self.closed = 0

    @property
    def kind(self) -> ContextCandidateKind:
        return self._kind

    @property
    def enabled(self) -> bool:
        if self._enabled_raises:
            raise RuntimeError("store handshake failed")
        return self._enabled

    def records(
        self,
        *,
        scope: ContextAuthorizationScope,
    ) -> AsyncIterator[ContextSourceRecord]:
        # Counted here rather than inside the generator body: an async generator
        # runs nothing until it is first awaited, so a counter inside it would
        # read zero for a source that *was* asked and never iterated, and the
        # "a silent source costs nothing" assertions would pass vacuously.
        self.enumerations += 1
        self.scopes.append(scope)
        if self._raise_before:
            raise RuntimeError("store unreachable")
        return self._stream()

    async def _stream(self) -> AsyncIterator[ContextSourceRecord]:
        try:
            for position, record in enumerate(self._records):
                if self._raise_after is not None and position >= self._raise_after:
                    raise RuntimeError("store went dark mid-stream")
                yield record
            index = 0
            while self._endless:
                index += 1
                yield ContextSourceRecordFactory.record(
                    locator=f"endless/{index}",
                    size_tokens=10,
                )
        finally:
            self.closed += 1


class FakeAuthority:
    """The call-time authority, with a per-locator answer and a call log.

    The log is the point: it is what lets a test assert that authorization was
    asked once per record *per collection* rather than resolved once and reused,
    which is the difference between checking authority and caching it.
    """

    def __init__(
        self,
        *,
        answers: Mapping[str, ContextSourceLifecycle] | None = None,
        default: ContextSourceLifecycle | None = None,
        raises: bool = False,
        returns: Any = None,
        sequence: tuple[ContextSourceLifecycle, ...] = (),
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self._raises = raises
        self._returns = returns
        self._sequence = sequence
        self.calls: list[tuple[ContextCandidateKind, str]] = []

    async def authorize(
        self,
        *,
        kind: ContextCandidateKind,
        locator: str,
        scope: ContextAuthorizationScope,
    ) -> ContextSourceLifecycle:
        self.calls.append((kind, locator))
        if self._raises:
            raise RuntimeError("identity service unreachable")
        if self._returns is not None:
            return self._returns
        if self._sequence:
            index = min(len(self.calls) - 1, len(self._sequence) - 1)
            return self._sequence[index]
        if locator in self._answers:
            return self._answers[locator]
        if self._default is None:
            return LifecycleFactory.authorized()
        return self._default


class LifecycleFactory:
    """Trusted lifecycles a source domain could report, built one way."""

    @staticmethod
    def authorized(
        *,
        observed_at: datetime = _NOW,
        retention_until: datetime | None = None,
        legal_hold: bool = False,
        trust_label: EvidenceTrustClass = EvidenceTrustClass.PRIMARY,
    ) -> ContextSourceLifecycle:
        return ContextSourceLifecycle(
            access_state=EvidenceAccessState.AUTHORIZED,
            trust_label=trust_label,
            observed_at=observed_at,
            retention_until=retention_until,
            legal_hold=legal_hold,
        )

    @staticmethod
    def refused(state: EvidenceAccessState) -> ContextSourceLifecycle:
        return ContextSourceLifecycle(
            access_state=state,
            trust_label=EvidenceTrustClass.UNVERIFIED,
            observed_at=_NOW,
        )


class ContextSourceRecordFactory:
    """Body-free source records, with digests derived rather than supplied."""

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def record(
        cls,
        *,
        locator: str = "conversation/turn/1",
        size_tokens: int = 120,
        retrievable: bool = True,
        relevance_score: int | None = None,
    ) -> ContextSourceRecord:
        return ContextSourceRecord(
            locator=locator,
            content_digest=cls.digest(locator),
            size_tokens=size_tokens,
            retrievable=retrievable,
            relevance_score=relevance_score,
        )


class ProviderFactoryMixin:
    """One wiring helper per lane concept; concrete tests only assert."""

    NOW = _NOW

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def scope(
        self,
        *,
        subject: str = "subject-a",
        run_id: str | None = "run-1",
        conversation_id: str | None = "conv-1",
        project_id: str | None = "proj-1",
    ) -> ContextAuthorizationScope:
        return ContextAuthorizationScope(
            subject_fingerprint=self.digest(subject),
            run_id=run_id,
            conversation_id=conversation_id,
            project_id=project_id,
        )

    def request(
        self,
        *,
        scope: ContextAuthorizationScope | None = None,
        collected_at: datetime = _NOW,
    ) -> ContextCandidateRequest:
        return ContextCandidateRequest(
            scope=scope if scope is not None else self.scope(),
            collected_at=collected_at,
        )

    def record(self, **kwargs: Any) -> ContextSourceRecord:
        return ContextSourceRecordFactory.record(**kwargs)

    def records(self, *locators: str, **kwargs: Any) -> tuple[ContextSourceRecord, ...]:
        return tuple(
            ContextSourceRecordFactory.record(locator=locator, **kwargs)
            for locator in locators
        )

    def source(
        self,
        kind: ContextCandidateKind = ContextCandidateKind.CONVERSATION_TURN,
        records: tuple[ContextSourceRecord, ...] = (),
        **kwargs: Any,
    ) -> FakeSource:
        return FakeSource(kind, records, **kwargs)

    def provider(
        self,
        source: FakeSource | None = None,
        *,
        authority: FakeAuthority | None = None,
        policy: ContextSourcePolicy | None = None,
    ) -> ScopedCandidateProvider:
        return ScopedCandidateProvider(
            source if source is not None else self.source(),
            authority=authority if authority is not None else FakeAuthority(),
            policy=policy,
        )

    def collector(
        self,
        *providers: ScopedCandidateProvider,
        max_candidates: int | None = None,
    ) -> ContextCandidateCollector:
        return ContextCandidateCollector(providers, max_candidates=max_candidates)

    def evidence_token(
        self,
        locator: str,
        *,
        kind: ContextCandidateKind = ContextCandidateKind.CONVERSATION_TURN,
    ) -> str:
        policy = ContextProviderPolicies.require(kind)
        return EvidenceRefIdentity.token(kind=policy.evidence_kind, locator=locator)

    def candidate_id(
        self,
        locator: str,
        *,
        kind: ContextCandidateKind = ContextCandidateKind.CONVERSATION_TURN,
    ) -> str:
        return ContextCandidateIdentity.of(
            kind=kind,
            evidence_token=self.evidence_token(locator, kind=kind),
        )

    def candidate(
        self,
        *,
        locator: str = "conversation/turn/1",
        kind: ContextCandidateKind = ContextCandidateKind.CONVERSATION_TURN,
        priority_class: ContextPriorityClass = ContextPriorityClass.RECENT_CONVERSATION,
        scope: ContextAuthorizationScope | None = None,
        lifecycle: ContextSourceLifecycle | None = None,
        original_tokens: int = 120,
        relevance_score: int | None = None,
    ) -> ContextCandidate:
        token = self.evidence_token(locator, kind=kind)
        return ContextCandidate(
            candidate_id=ContextCandidateIdentity.of(kind=kind, evidence_token=token),
            kind=kind,
            source_ref=token,
            source_digest=self.digest(locator),
            scope=scope if scope is not None else self.scope(),
            lifecycle=lifecycle
            if lifecycle is not None
            else LifecycleFactory.authorized(),
            priority_class=priority_class,
            original_tokens=original_tokens,
            relevance_score=relevance_score,
        )

    def report(
        self,
        *,
        kind: ContextCandidateKind = ContextCandidateKind.CONVERSATION_TURN,
        outcome: ContextProviderOutcome = ContextProviderOutcome.OFFERED,
        capacity: int = 4,
        examined: int = 1,
        offered: int = 1,
        truncated: bool = False,
        withheld: tuple[ContextWithholdingTally, ...] = (),
    ) -> ContextProviderReport:
        return ContextProviderReport(
            kind=kind,
            outcome=outcome,
            capacity=capacity,
            examined=examined,
            offered=offered,
            truncated=truncated,
            withheld=withheld,
        )


class RejectionAssertionMixin:
    """Assert the typed domain error a validator raised, not merely that it did."""

    @staticmethod
    def assert_rejected(
        expected_type: type[Exception],
        expected_message: str,
        build: Any,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build()
        causes = [
            error.get("ctx", {}).get("error") for error in exc_info.value.errors()
        ]
        typed = [cause for cause in causes if isinstance(cause, expected_type)]
        assert typed, f"expected {expected_type.__name__}, got {causes}"
        assert expected_message in {str(cause) for cause in typed}


class TestProvidersOfferOnlyAuthorizedMaterial(ProviderFactoryMixin):
    async def test_only_the_authorized_record_becomes_a_candidate(self) -> None:
        authority = FakeAuthority(
            answers={
                "turn/ok": LifecycleFactory.authorized(),
                "turn/denied": LifecycleFactory.refused(
                    EvidenceAccessState.UNAUTHORIZED
                ),
                "turn/gone": LifecycleFactory.refused(EvidenceAccessState.REVOKED),
            }
        )
        provider = self.provider(
            self.source(
                records=self.records("turn/ok", "turn/denied", "turn/gone"),
            ),
            authority=authority,
        )

        offer = await provider.offer(self.request(), capacity=8)

        assert [candidate.source_ref for candidate in offer.candidates] == [
            self.evidence_token("turn/ok")
        ]
        assert offer.report.examined == 3
        assert offer.report.offered == 1

    async def test_an_unauthorized_source_yields_nothing_rather_than_raising(
        self,
    ) -> None:
        provider = self.provider(
            self.source(records=self.records("a", "b", "c")),
            authority=FakeAuthority(
                default=LifecycleFactory.refused(EvidenceAccessState.UNAUTHORIZED)
            ),
        )

        offer = await provider.offer(self.request(), capacity=8)

        assert offer.candidates == ()
        assert offer.report.outcome is ContextProviderOutcome.OFFERED
        assert offer.report.withheld_for(ContextOmissionReason.UNAUTHORIZED) == 3

    async def test_a_revoked_source_is_reason_coded_not_reclassified(self) -> None:
        provider = self.provider(
            self.source(records=self.records("a")),
            authority=FakeAuthority(
                default=LifecycleFactory.refused(EvidenceAccessState.REVOKED)
            ),
        )

        report = (await provider.offer(self.request(), capacity=8)).report

        assert report.withheld_for(ContextOmissionReason.REVOKED) == 1
        assert report.withheld_for(ContextOmissionReason.LOW_RELEVANCE) == 0
        assert report.withheld_for(ContextOmissionReason.BUDGET_EXHAUSTED) == 0

    async def test_the_authority_is_asked_about_every_examined_record(self) -> None:
        authority = FakeAuthority()
        provider = self.provider(
            self.source(records=self.records("a", "b", "c")),
            authority=authority,
        )

        await provider.offer(self.request(), capacity=8)

        assert [locator for _kind, locator in authority.calls] == ["a", "b", "c"]
        assert {kind for kind, _locator in authority.calls} == {
            ContextCandidateKind.CONVERSATION_TURN
        }

    async def test_an_authorization_is_never_reused_across_collections(self) -> None:
        authority = FakeAuthority(
            sequence=(
                LifecycleFactory.authorized(),
                LifecycleFactory.refused(EvidenceAccessState.REVOKED),
            )
        )
        provider = self.provider(
            self.source(records=self.records("a")),
            authority=authority,
        )
        request = self.request()

        first = await provider.offer(request, capacity=8)
        second = await provider.offer(request, capacity=8)

        assert len(first.candidates) == 1
        assert second.candidates == ()
        assert len(authority.calls) == 2

    async def test_retention_is_evaluated_at_the_collection_instant(self) -> None:
        expiring = LifecycleFactory.authorized(
            retention_until=_NOW + timedelta(minutes=5)
        )
        provider = self.provider(
            self.source(records=self.records("a")),
            authority=FakeAuthority(default=expiring),
        )

        before = await provider.offer(self.request(), capacity=8)
        after = await provider.offer(
            self.request(collected_at=_NOW + timedelta(minutes=30)),
            capacity=8,
        )

        assert len(before.candidates) == 1
        assert after.candidates == ()
        assert after.report.withheld_for(ContextOmissionReason.RETENTION_EXPIRED) == 1

    async def test_an_unreachable_authority_withholds_rather_than_admits(self) -> None:
        provider = self.provider(
            self.source(records=self.records("a")),
            authority=FakeAuthority(raises=True),
        )

        report = (await provider.offer(self.request(), capacity=8)).report

        assert report.offered == 0
        assert (
            report.withheld_for(ContextOmissionReason.ADMISSIBILITY_NOT_ESTABLISHED)
            == 1
        )

    async def test_an_authority_answering_off_contract_withholds(self) -> None:
        provider = self.provider(
            self.source(records=self.records("a")),
            authority=FakeAuthority(returns=True),
        )

        report = (await provider.offer(self.request(), capacity=8)).report

        assert report.offered == 0
        assert (
            report.withheld_for(ContextOmissionReason.ADMISSIBILITY_NOT_ESTABLISHED)
            == 1
        )

    async def test_a_candidate_carries_the_collection_scope_not_its_own(self) -> None:
        scope = self.scope(subject="subject-b", run_id="run-9")
        provider = self.provider(self.source(records=self.records("a")))

        offer = await provider.offer(self.request(scope=scope), capacity=8)

        assert offer.candidates[0].scope == scope
        assert "scope" not in ContextSourceRecord.model_fields

    async def test_the_source_is_handed_the_collection_scope(self) -> None:
        source = self.source(records=self.records("a"))
        scope = self.scope(subject="subject-c")

        await self.provider(source).offer(self.request(scope=scope), capacity=8)

        assert source.scopes == [scope]


class TestBoundsAreStructural(ProviderFactoryMixin):
    async def test_an_endless_source_is_bounded_by_the_capacity_it_is_handed(
        self,
    ) -> None:
        authority = FakeAuthority()
        provider = self.provider(self.source(endless=True), authority=authority)

        offer = await provider.offer(self.request(), capacity=5)

        assert offer.report.examined == 5
        assert offer.report.offered == 5
        assert offer.report.truncated is True
        assert len(authority.calls) == 5

    async def test_an_endless_source_cannot_exceed_its_own_policy_ceiling(
        self,
    ) -> None:
        policy = ContextProviderPolicies.require(ContextCandidateKind.MEMORY)
        provider = self.provider(
            self.source(ContextCandidateKind.MEMORY, endless=True),
        )

        offer = await provider.offer(
            self.request(),
            capacity=ContextBounds.MAX_CANDIDATES,
        )

        assert offer.report.capacity == policy.max_candidates
        assert offer.report.offered == policy.max_candidates

    async def test_an_abandoned_stream_is_closed(self) -> None:
        source = self.source(endless=True)

        await self.provider(source).offer(self.request(), capacity=3)

        assert source.closed == 1

    async def test_withheld_records_cost_examination_not_candidate_budget(
        self,
    ) -> None:
        denied = FakeAuthority(
            default=LifecycleFactory.refused(EvidenceAccessState.UNAUTHORIZED)
        )
        conversation = self.provider(
            self.source(records=self.records("a", "b", "c")),
            authority=denied,
        )
        memory = self.provider(
            self.source(ContextCandidateKind.MEMORY, self.records("m1")),
        )

        collection = await self.collector(conversation, memory).collect(self.request())

        memory_report = collection.report_for(ContextCandidateKind.MEMORY)
        assert memory_report is not None
        assert (
            memory_report.capacity
            == ContextProviderPolicies.require(
                ContextCandidateKind.MEMORY
            ).max_candidates
        )
        assert len(collection.candidates) == 1

    async def test_a_spent_budget_leaves_later_sources_unenumerated(self) -> None:
        conversation_source = self.source(endless=True)
        memory_source = self.source(ContextCandidateKind.MEMORY, self.records("m1"))
        collector = self.collector(
            self.provider(conversation_source),
            self.provider(memory_source),
            max_candidates=4,
        )

        collection = await collector.collect(self.request())

        memory_report = collection.report_for(ContextCandidateKind.MEMORY)
        assert memory_report is not None
        assert memory_report.outcome is ContextProviderOutcome.BUDGET_EXHAUSTED
        assert memory_source.enumerations == 0
        assert len(collection.candidates) == 4

    def test_every_configured_source_fits_one_plan_budget(self) -> None:
        assert ContextProviderPolicies.total_ceiling() <= ContextBounds.MAX_CANDIDATES

    def test_a_collector_cannot_widen_the_plan_candidate_cap(self) -> None:
        assert (
            self.collector(max_candidates=10_000).max_candidates
            == ContextBounds.MAX_CANDIDATES
        )

    async def test_oversized_material_can_only_be_offered_as_a_reference(self) -> None:
        provider = self.provider(
            self.source(records=(self.record(locator="big", size_tokens=200_000),)),
        )

        candidate = (await provider.offer(self.request(), capacity=4)).candidates[0]

        assert [option.mode for option in candidate.representation_options] == [
            ContextRepresentationMode.REFERENCE
        ]
        assert (
            candidate.representation_options[0].token_count
            == ContextProviderBounds.REFERENCE_TOKENS
        )
        assert candidate.original_tokens == 200_000

    async def test_oversized_unretrievable_material_is_not_offered_at_all(
        self,
    ) -> None:
        provider = self.provider(
            self.source(
                records=(
                    self.record(
                        locator="big",
                        size_tokens=200_000,
                        retrievable=False,
                    ),
                )
            ),
        )

        offer = await provider.offer(self.request(), capacity=4)

        assert offer.candidates == ()
        assert offer.report.withheld_for(ContextOmissionReason.SOURCE_UNAVAILABLE) == 1

    async def test_the_inline_ceiling_is_the_policy_not_the_record(self) -> None:
        policy = ContextProviderPolicies.require(ContextCandidateKind.MEMORY)
        provider = self.provider(
            self.source(
                ContextCandidateKind.MEMORY,
                (self.record(locator="m1", size_tokens=policy.inline_ceiling + 1),),
            ),
        )

        candidate = (await provider.offer(self.request(), capacity=4)).candidates[0]

        assert policy.inline_ceiling < ContextProviderBounds.MAX_INLINE_TOKENS
        assert [option.mode for option in candidate.representation_options] == [
            ContextRepresentationMode.REFERENCE
        ]

    async def test_small_material_is_offered_whole_and_never_as_a_costlier_reference(
        self,
    ) -> None:
        provider = self.provider(
            self.source(records=(self.record(locator="tiny", size_tokens=12),)),
        )

        candidate = (await provider.offer(self.request(), capacity=4)).candidates[0]

        assert [option.mode for option in candidate.representation_options] == [
            ContextRepresentationMode.FULL
        ]
        assert candidate.representation_options[0].token_count == 12
        assert candidate.representation_options[0].lossiness is ContextLossiness.NONE


class TestOrderingIsDeterministic(ProviderFactoryMixin):
    async def test_equal_ranking_does_not_reorder_between_runs(self) -> None:
        forward = self.collector(
            self.provider(self.source(records=self.records("a", "b", "c")))
        )
        backward = self.collector(
            self.provider(self.source(records=self.records("c", "b", "a")))
        )

        first = await forward.collect(self.request())
        second = await backward.collect(self.request())

        assert [c.candidate_id for c in first.candidates] == [
            c.candidate_id for c in second.candidates
        ]
        assert len({c.effective_relevance for c in first.candidates}) == 1

    async def test_provider_registration_order_does_not_change_a_collection(
        self,
    ) -> None:
        def wiring() -> tuple[ScopedCandidateProvider, ScopedCandidateProvider]:
            return (
                self.provider(self.source(records=self.records("a", "b"))),
                self.provider(
                    self.source(ContextCandidateKind.MEMORY, self.records("m1"))
                ),
            )

        conversation, memory = wiring()
        forward = await self.collector(conversation, memory).collect(self.request())
        conversation, memory = wiring()
        backward = await self.collector(memory, conversation).collect(self.request())

        assert forward == backward

    async def test_repeating_a_collection_over_the_same_inputs_is_identical(
        self,
    ) -> None:
        provider = self.provider(
            self.source(
                records=(
                    self.record(locator="a", relevance_score=10),
                    self.record(locator="b", relevance_score=900),
                    self.record(locator="c"),
                )
            )
        )
        collector = self.collector(provider)
        request = self.request()

        assert await collector.collect(request) == await collector.collect(request)

    async def test_relevance_outranks_identity_and_unknown_sorts_last(self) -> None:
        provider = self.provider(
            self.source(
                records=(
                    self.record(locator="zzz", relevance_score=900),
                    self.record(locator="aaa"),
                    self.record(locator="mmm", relevance_score=500),
                )
            )
        )

        collection = await self.collector(provider).collect(self.request())

        assert [c.effective_relevance for c in collection.candidates] == [900, 500, 0]

    def test_candidate_identity_is_a_function_of_kind_and_source(self) -> None:
        first = self.candidate_id("workspace/report.md")
        second = self.candidate_id("workspace/report.md")
        other_kind = self.candidate_id(
            "workspace/report.md", kind=ContextCandidateKind.MEMORY
        )

        assert first == second
        assert first != other_kind

    async def test_the_same_locator_is_offered_once(self) -> None:
        provider = self.provider(
            self.source(records=self.records("a", "a", "b")),
        )

        offer = await provider.offer(self.request(), capacity=8)

        assert len(offer.candidates) == 2
        assert offer.report.withheld_for(ContextOmissionReason.DUPLICATE) == 1

    async def test_the_recency_window_demotes_the_tail(self) -> None:
        policy = ContextProviderPolicies.require(ContextCandidateKind.CONVERSATION_TURN)
        provider = self.provider(self.source(endless=True))

        offer = await provider.offer(
            self.request(),
            capacity=policy.recency_window + 3,
        )

        classes = [candidate.priority_class for candidate in offer.candidates]
        assert set(classes[: policy.recency_window]) == {policy.priority_class}
        assert set(classes[policy.recency_window :]) == {policy.demoted_class}
        assert policy.demoted_class.priority_rank > policy.priority_class.priority_rank


class TestOptionalAndFailingSourcesStaySilent(ProviderFactoryMixin):
    async def test_a_disabled_optional_source_is_silent(self) -> None:
        source = self.source(ContextCandidateKind.MEMORY, enabled=False)

        offer = await self.provider(source).offer(self.request(), capacity=8)

        assert offer.candidates == ()
        assert offer.report.outcome is ContextProviderOutcome.DISABLED
        assert source.enumerations == 0

    async def test_a_disabled_required_source_reads_as_unavailable(self) -> None:
        source = self.source(enabled=False)

        offer = await self.provider(source).offer(self.request(), capacity=8)

        assert offer.candidates == ()
        assert offer.report.outcome is ContextProviderOutcome.UNAVAILABLE

    async def test_a_source_that_cannot_be_reached_does_not_fail_the_turn(
        self,
    ) -> None:
        collector = self.collector(
            self.provider(self.source(raise_before=True)),
            self.provider(self.source(ContextCandidateKind.MEMORY, self.records("m1"))),
        )

        collection = await collector.collect(self.request())

        conversation = collection.report_for(ContextCandidateKind.CONVERSATION_TURN)
        assert conversation is not None
        assert conversation.outcome is ContextProviderOutcome.UNAVAILABLE
        assert len(collection.candidates) == 1

    async def test_a_source_that_fails_on_its_first_record_stays_silent(self) -> None:
        provider = self.provider(
            self.source(records=self.records("a", "b"), raise_after=0),
        )

        offer = await provider.offer(self.request(), capacity=8)

        assert offer.candidates == ()
        assert offer.report.outcome is ContextProviderOutcome.UNAVAILABLE
        assert offer.report.examined == 0

    async def test_a_source_failing_midway_keeps_what_it_already_authorized(
        self,
    ) -> None:
        provider = self.provider(
            self.source(records=self.records("a", "b", "c"), raise_after=2),
        )

        offer = await provider.offer(self.request(), capacity=8)

        assert len(offer.candidates) == 2
        assert offer.report.outcome is ContextProviderOutcome.UNAVAILABLE

    async def test_a_source_whose_enablement_check_fails_stays_silent(self) -> None:
        source = self.source(ContextCandidateKind.MEMORY, enabled_raises=True)

        offer = await self.provider(source).offer(self.request(), capacity=8)

        assert offer.candidates == ()
        assert source.enumerations == 0

    async def test_an_unbound_scope_dimension_yields_nothing(self) -> None:
        source = self.source(
            ContextCandidateKind.WORKSPACE_REF,
            self.records("workspace/a"),
        )

        offer = await self.provider(source).offer(
            self.request(scope=self.scope(project_id=None)),
            capacity=8,
        )

        assert offer.candidates == ()
        assert offer.report.outcome is ContextProviderOutcome.OUT_OF_SCOPE
        assert source.enumerations == 0

    async def test_every_configured_source_can_be_wired_and_run(self) -> None:
        providers = [
            self.provider(self.source(kind, self.records(f"{kind.value}/1")))
            for kind in ContextProviderPolicies.kinds()
        ]

        collection = await self.collector(*providers).collect(self.request())

        assert {report.kind for report in collection.reports} == (
            ContextProviderPolicies.kinds()
        )
        assert {candidate.kind for candidate in collection.candidates} == (
            ContextProviderPolicies.kinds()
        )


class TestNoProviderCanClaimProtectedContext(
    ProviderFactoryMixin,
    RejectionAssertionMixin,
):
    def test_no_configured_policy_grants_a_protected_class(self) -> None:
        assert not [
            policy.kind
            for policy in ContextProviderPolicies.BY_KIND.values()
            if policy.priority_class.protected or policy.demoted_class.protected
        ]

    def test_a_policy_claiming_protection_is_refused(self) -> None:
        self.assert_rejected(
            ContextSourcePolicyRejected,
            ContextSourcePolicyRejected.Messages.PROTECTION_CLAIMED,
            lambda: ContextSourcePolicy(
                kind=ContextCandidateKind.APPROVAL_STATE,
                evidence_kind=EvidenceKind.PRIOR_RESULT,
                priority_class=ContextPriorityClass.APPROVAL_GATE_STATE,
                demoted_class=ContextPriorityClass.APPROVAL_GATE_STATE,
                max_candidates=4,
                recency_window=4,
                required_dimensions=frozenset({ContextScopeDimension.SUBJECT}),
            ),
        )

    def test_a_policy_promoting_above_its_kind_is_refused(self) -> None:
        self.assert_rejected(
            ContextSourcePolicyRejected,
            ContextSourcePolicyRejected.Messages.PRIORITY_PROMOTED,
            lambda: ContextSourcePolicy(
                kind=ContextCandidateKind.MEMORY,
                evidence_kind=EvidenceKind.MEMORY,
                priority_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                demoted_class=ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
                max_candidates=4,
                recency_window=4,
                required_dimensions=frozenset({ContextScopeDimension.SUBJECT}),
            ),
        )

    def test_a_policy_promoting_its_own_tail_is_refused(self) -> None:
        self.assert_rejected(
            ContextSourcePolicyRejected,
            ContextSourcePolicyRejected.Messages.DEMOTION_PROMOTES,
            lambda: ContextSourcePolicy(
                kind=ContextCandidateKind.CONVERSATION_TURN,
                evidence_kind=EvidenceKind.CONVERSATION,
                priority_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
                demoted_class=ContextPriorityClass.RECENT_CONVERSATION,
                max_candidates=4,
                recency_window=4,
                required_dimensions=frozenset({ContextScopeDimension.SUBJECT}),
            ),
        )

    def test_a_policy_that_never_binds_a_subject_is_refused(self) -> None:
        self.assert_rejected(
            ContextSourcePolicyRejected,
            ContextSourcePolicyRejected.Messages.SUBJECT_REQUIRED,
            lambda: ContextSourcePolicy(
                kind=ContextCandidateKind.MEMORY,
                evidence_kind=EvidenceKind.MEMORY,
                priority_class=ContextPriorityClass.RECALLED_MEMORY,
                demoted_class=ContextPriorityClass.RECALLED_MEMORY,
                max_candidates=4,
                recency_window=4,
                required_dimensions=frozenset({ContextScopeDimension.RUN}),
            ),
        )

    def test_an_unconfigured_kind_cannot_be_wired_at_all(self) -> None:
        with pytest.raises(ContextProviderNotConfigured) as exc_info:
            self.provider(self.source(ContextCandidateKind.SYSTEM_POLICY))

        assert exc_info.value.kind is ContextCandidateKind.SYSTEM_POLICY

    def test_a_provider_cannot_be_given_another_kinds_policy(self) -> None:
        with pytest.raises(ContextSourcePolicyRejected) as exc_info:
            self.provider(
                self.source(ContextCandidateKind.MEMORY),
                policy=ContextProviderPolicies.require(
                    ContextCandidateKind.CONVERSATION_TURN
                ),
            )

        assert str(exc_info.value) == ContextSourcePolicyRejected.Messages.KIND_MISMATCH

    def test_two_providers_cannot_claim_one_kind(self) -> None:
        with pytest.raises(ContextProviderAlreadyRegistered) as exc_info:
            self.collector(self.provider(), self.provider())

        assert exc_info.value.kind is ContextCandidateKind.CONVERSATION_TURN

    def test_every_configured_policy_routes_to_a_registered_evidence_kind(
        self,
    ) -> None:
        assert {
            policy.evidence_kind for policy in ContextProviderPolicies.BY_KIND.values()
        } <= set(EvidenceKind)


class TestCollectionsRefuseWhatProvidersRefuseToProduce(
    ProviderFactoryMixin,
    RejectionAssertionMixin,
):
    def test_an_out_of_order_collection_is_refused(self) -> None:
        low = self.candidate(
            locator="a",
            priority_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
        )
        recent = self.candidate(locator="b")

        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.CANDIDATES_NOT_ORDERED,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                candidates=(low, recent),
                reports=(self.report(examined=2, offered=2),),
            ),
        )

    def test_a_repeated_candidate_is_refused(self) -> None:
        candidate = self.candidate(locator="a")

        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.DUPLICATE_CANDIDATE,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                candidates=(candidate, candidate),
                reports=(self.report(examined=2, offered=2),),
            ),
        )

    def test_a_candidate_scoped_wider_than_its_collection_is_refused(self) -> None:
        widened = self.candidate(locator="a", scope=self.scope(project_id=None))

        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.SCOPE_WIDENED,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                candidates=(widened,),
                reports=(self.report(),),
            ),
        )

    def test_an_inadmissible_candidate_cannot_be_collected(self) -> None:
        revoked = self.candidate(
            locator="a",
            lifecycle=LifecycleFactory.refused(EvidenceAccessState.REVOKED),
        )

        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.INADMISSIBLE_OFFERED,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                candidates=(revoked,),
                reports=(self.report(),),
            ),
        )

    def test_a_candidate_whose_retention_lapsed_cannot_be_collected(self) -> None:
        expiring = self.candidate(
            locator="a",
            lifecycle=LifecycleFactory.authorized(
                retention_until=_NOW + timedelta(minutes=1)
            ),
        )

        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.INADMISSIBLE_OFFERED,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW + timedelta(hours=1),
                candidates=(expiring,),
                reports=(self.report(),),
            ),
        )

    def test_a_naive_collection_timestamp_is_refused(self) -> None:
        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.NAIVE_TIMESTAMP,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=datetime(2026, 7, 29, 12, 0, 0),
            ),
        )

    def test_a_naive_request_timestamp_is_refused(self) -> None:
        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.NAIVE_TIMESTAMP,
            lambda: ContextCandidateRequest(
                scope=self.scope(),
                collected_at=datetime(2026, 7, 29, 12, 0, 0),
            ),
        )

    def test_out_of_order_reports_are_refused(self) -> None:
        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.REPORTS_NOT_CANONICAL,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                reports=(
                    self.report(
                        kind=ContextCandidateKind.MEMORY, examined=0, offered=0
                    ),
                    self.report(
                        kind=ContextCandidateKind.CITATION,
                        examined=0,
                        offered=0,
                    ),
                ),
            ),
        )

    def test_a_collection_disagreeing_with_its_reports_is_refused(self) -> None:
        self.assert_rejected(
            ContextCollectionRejected,
            ContextCollectionRejected.Messages.REPORTED_COUNT_MISMATCH,
            lambda: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                candidates=(self.candidate(locator="a"),),
                reports=(self.report(examined=2, offered=2),),
            ),
        )

    def test_a_report_that_loses_records_is_refused(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.UNACCOUNTED_RECORDS,
            lambda: self.report(capacity=4, examined=3, offered=1),
        )

    def test_a_report_beyond_its_capacity_is_refused(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.OVER_CAPACITY,
            lambda: self.report(capacity=2, examined=3, offered=3),
        )

    def test_uncanonical_withholding_tallies_are_refused(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.TALLIES_NOT_CANONICAL,
            lambda: self.report(
                capacity=4,
                examined=3,
                offered=1,
                withheld=(
                    ContextWithholdingTally(
                        reason=ContextOmissionReason.REVOKED, count=1
                    ),
                    ContextWithholdingTally(
                        reason=ContextOmissionReason.UNAUTHORIZED, count=1
                    ),
                ),
            ),
        )

    def test_a_silent_outcome_that_examined_records_is_refused(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.SILENT_OUTCOME_EXAMINED,
            lambda: self.report(
                outcome=ContextProviderOutcome.DISABLED,
                capacity=4,
                examined=1,
                offered=1,
            ),
        )

    def test_budget_exhaustion_must_mean_no_capacity(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.BUDGET_OUTCOME_MISMATCH,
            lambda: self.report(
                outcome=ContextProviderOutcome.BUDGET_EXHAUSTED,
                capacity=4,
                examined=0,
                offered=0,
            ),
        )

    def test_truncation_must_mean_the_capacity_was_spent(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.TRUNCATION_MISMATCH,
            lambda: self.report(capacity=4, examined=1, offered=1, truncated=True),
        )

    def test_an_offer_cannot_carry_candidates_its_report_denies(self) -> None:
        self.assert_rejected(
            ContextProviderReportRejected,
            ContextProviderReportRejected.Messages.OFFER_COUNT_MISMATCH,
            lambda: ContextProviderOffer(
                candidates=(self.candidate(locator="a"),),
                report=self.report(capacity=4, examined=0, offered=0),
            ),
        )


class SeededSecretMixin:
    """One secret, seeded four shapes, to prove no field can hold a body."""

    PROSE_SECRET = "the wire instructions are IBAN GB33 9999 and must stay private"
    LONG_SECRET = "x" * 4_096
    STRUCTURED_SECRET: dict[str, str] = {"leaked": PROSE_SECRET}
    TOKEN_SECRET = "IBAN-GB33-9999-must-stay-private"

    class LeakyContract(RuntimeContract):
        """A contract that does accept a body, so the probe can be shown to work.

        Without this, a probe that silently stopped injecting anything would
        make every body-freedom test below pass for the wrong reason.
        """

        note: str

    @classmethod
    def fields_accepting(cls, instance: RuntimeContract, seed: Any) -> list[str]:
        """Return every field of ``instance`` that round-trips ``seed`` intact."""

        model = type(instance)
        payload = instance.model_dump(mode="json")
        accepted: list[str] = []
        for field_name in model.model_fields:
            probe = dict(payload)
            probe[field_name] = seed
            try:
                rebuilt = model.model_validate(probe)
            except ValidationError:
                continue
            if cls._retains(rebuilt.model_dump(mode="json"), seed):
                accepted.append(field_name)
        return accepted

    @staticmethod
    def _retains(payload: Any, seed: Any) -> bool:
        return str(seed) in str(payload)


class TestNoCandidateCanCarryABody(ProviderFactoryMixin, SeededSecretMixin):
    def every_contract_instance(self) -> dict[type[RuntimeContract], RuntimeContract]:
        record = self.record(locator="conversation/turn/1")
        report = self.report()
        candidate = self.candidate(locator="conversation/turn/1")
        return {
            ContextSourceRecord: record,
            ContextSourcePolicy: ContextProviderPolicies.require(
                ContextCandidateKind.CONVERSATION_TURN
            ),
            ContextWithholdingTally: ContextWithholdingTally(
                reason=ContextOmissionReason.UNAUTHORIZED,
                count=1,
            ),
            ContextProviderReport: report,
            ContextProviderOffer: ContextProviderOffer(
                candidates=(candidate,),
                report=report,
            ),
            ContextCandidateRequest: self.request(),
            ContextCandidateCollection: ContextCandidateCollection(
                scope=self.scope(),
                collected_at=_NOW,
                candidates=(candidate,),
                reports=(report,),
            ),
        }

    def test_the_probe_reports_a_field_that_would_hold_a_body(self) -> None:
        leaky = self.LeakyContract(note="a bounded note")

        assert self.fields_accepting(leaky, self.PROSE_SECRET) == ["note"]
        assert self.fields_accepting(leaky, self.TOKEN_SECRET) == ["note"]

    def test_no_field_of_any_contract_accepts_a_prose_secret(self) -> None:
        offenders = {
            model.__name__: self.fields_accepting(instance, self.PROSE_SECRET)
            for model, instance in self.every_contract_instance().items()
        }

        assert not {name: fields for name, fields in offenders.items() if fields}

    def test_no_field_of_any_contract_accepts_a_body_sized_value(self) -> None:
        offenders = {
            model.__name__: self.fields_accepting(instance, self.LONG_SECRET)
            for model, instance in self.every_contract_instance().items()
        }

        assert not {name: fields for name, fields in offenders.items() if fields}

    def test_no_field_of_any_contract_accepts_an_arbitrary_structure(self) -> None:
        offenders = {
            model.__name__: self.fields_accepting(instance, self.STRUCTURED_SECRET)
            for model, instance in self.every_contract_instance().items()
        }

        assert not {name: fields for name, fields in offenders.items() if fields}

    def test_the_only_field_that_names_material_is_the_source_locator(self) -> None:
        offenders = {
            model.__name__: self.fields_accepting(instance, self.TOKEN_SECRET)
            for model, instance in self.every_contract_instance().items()
        }

        assert {name: fields for name, fields in offenders.items() if fields} == {
            ContextSourceRecord.__name__: ["locator"]
        }

    async def test_a_collection_over_secret_material_serialises_without_the_secret(
        self,
    ) -> None:
        locator = f"s3://ledger/{self.TOKEN_SECRET}/page-3"
        provider = self.provider(
            self.source(records=(self.record(locator=locator, size_tokens=90_000),)),
        )

        collection = await self.collector(provider).collect(self.request())
        serialised = collection.model_dump_json()

        assert len(collection.candidates) == 1
        assert self.TOKEN_SECRET not in serialised
        assert "IBAN" not in serialised
        assert "s3://" not in serialised
        assert self.digest(locator) in serialised

    async def test_a_candidate_names_its_source_only_through_an_evidence_token(
        self,
    ) -> None:
        locator = "s3://ledger/private/page-3"
        provider = self.provider(
            self.source(records=(self.record(locator=locator, size_tokens=90_000),))
        )

        candidate = (await provider.offer(self.request(), capacity=4)).candidates[0]

        assert candidate.source_ref == EvidenceRefIdentity.token(
            kind=EvidenceKind.CONVERSATION,
            locator=locator,
        )
        assert EvidenceRefIdentity.kind_of(candidate.source_ref) is (
            EvidenceKind.CONVERSATION
        )
        assert candidate.representation_options[0].content_ref == candidate.source_ref

    def test_every_contract_in_the_module_is_probed(self) -> None:
        defined = {
            member
            for _name, member in inspect.getmembers(provider_module, inspect.isclass)
            if issubclass(member, RuntimeContract)
            and member is not RuntimeContract
            and member.__module__ == provider_module.__name__
        }

        assert defined == set(self.every_contract_instance())


class TestPortsAreStructural(ProviderFactoryMixin):
    def test_a_fake_source_satisfies_the_enumeration_port(self) -> None:
        assert isinstance(self.source(), ContextSourceEnumerationPort)

    def test_a_fake_authority_satisfies_the_authority_port(self) -> None:
        assert isinstance(FakeAuthority(), ContextSourceAuthorityPort)

    def test_a_collector_gathers_its_sources_in_priority_order(self) -> None:
        collector = self.collector(
            self.provider(self.source(ContextCandidateKind.MEMORY)),
            self.provider(self.source(ContextCandidateKind.CONVERSATION_TURN)),
            self.provider(self.source(ContextCandidateKind.CITATION)),
        )

        assert collector.kinds == (
            ContextCandidateKind.CITATION,
            ContextCandidateKind.CONVERSATION_TURN,
            ContextCandidateKind.MEMORY,
        )

    def test_an_unexplained_absence_resolves_to_unavailable(self) -> None:
        assert (
            ContextProviderOutcome.conservative() is ContextProviderOutcome.UNAVAILABLE
        )
        assert [
            outcome for outcome in ContextProviderOutcome if outcome.enumerated
        ] == [ContextProviderOutcome.OFFERED, ContextProviderOutcome.UNAVAILABLE]

    async def test_a_collection_reports_what_every_offered_candidate_would_cost(
        self,
    ) -> None:
        provider = self.provider(
            self.source(
                records=(
                    self.record(locator="a", size_tokens=100),
                    self.record(locator="b", size_tokens=250),
                )
            )
        )

        collection = await self.collector(provider).collect(self.request())

        assert collection.offered_tokens == 350
        assert (
            len(collection.candidates_of(ContextCandidateKind.CONVERSATION_TURN)) == 2
        )
        assert collection.candidates_of(ContextCandidateKind.MEMORY) == ()
