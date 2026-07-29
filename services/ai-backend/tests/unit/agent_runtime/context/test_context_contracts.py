"""F5.1 context-planning contracts: bodies, lossiness, authority, and replay.

Every test answers one of the five lane questions: can a body reach a plan, is
lossiness ever implied rather than declared, does a compression stay linked to
its exact source, can authority widen anywhere between a candidate and a
decision, and does a persisted plan reproduce itself on every parse.

The body question is deliberately answered by *injection* rather than by
inspection.  Reading field names would only prove that nobody named a field
``content``; seeding a secret into every field of every contract proves that no
field will hold one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from typing import Any

from pydantic import ValidationError
import pytest

from agent_runtime.answer_verification import EvidenceAccessState, EvidenceTrustClass
from agent_runtime.context import context_contracts
from agent_runtime.context.context_contracts import (
    CompressionManifest,
    CompressionManifestRejected,
    CompressionSummarizerIdentity,
    ContextAuthorityWidened,
    ContextAuthorizationScope,
    ContextBounds,
    ContextCandidate,
    ContextCandidateDecision,
    ContextCandidateKind,
    ContextCandidateRejected,
    ContextDecisionRejected,
    ContextInclusionReason,
    ContextLossiness,
    ContextOmissionReason,
    ContextPlan,
    ContextPlanLimits,
    ContextPlanReconstruction,
    ContextPlanReconstructionFailed,
    ContextPlanRevisions,
    ContextPriorityClass,
    ContextRepresentation,
    ContextRepresentationMode,
    ContextRepresentationOption,
    ContextRepresentationRejected,
    ContextSourceLifecycle,
    ContextSourceSpan,
    ContextSpanLocator,
    ContextSpanRejected,
    ContextVocabularyTables,
)
from agent_runtime.execution.contracts import RuntimeContract

_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


class ContextContractFactoryMixin:
    """Valid, minimally-interesting instances of every contract in the module.

    The factories compute digests and plan identities from the values they are
    given rather than taking them as arguments, so a test can never accidentally
    pass because it hand-wrote a digest that agreed with a bug.
    """

    SOURCE_TEXT = "quarterly revenue was 42 crore across three regions"
    SUMMARY_TEXT = "revenue: 42 crore, three regions"
    REFERENCE_TEXT = "artifact art_9f2 (1 page, 1000 tokens)"
    POLICY_REVISION = "pol-r7"
    SUMMARIZER_REVISION = "sum-r3"

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def scope(
        self,
        *,
        subject: str = "subject-a",
        run_id: str | None = "run-1",
        conversation_id: str | None = "conv-1",
        project_id: str | None = None,
    ) -> ContextAuthorizationScope:
        return ContextAuthorizationScope(
            subject_fingerprint=self.digest(subject),
            run_id=run_id,
            conversation_id=conversation_id,
            project_id=project_id,
        )

    def lifecycle(
        self,
        *,
        access_state: EvidenceAccessState = EvidenceAccessState.AUTHORIZED,
        trust_label: EvidenceTrustClass = EvidenceTrustClass.PRIMARY,
        observed_at: datetime = _NOW,
        retention_until: datetime | None = None,
        legal_hold: bool = False,
    ) -> ContextSourceLifecycle:
        return ContextSourceLifecycle(
            access_state=access_state,
            trust_label=trust_label,
            observed_at=observed_at,
            retention_until=retention_until,
            legal_hold=legal_hold,
        )

    def span(
        self,
        *,
        span_id: str = "span-1",
        locator: ContextSpanLocator = ContextSpanLocator.CHARACTER_RANGE,
        start: int | None = 0,
        end: int | None = 24,
        locator_ref: str | None = None,
    ) -> ContextSourceSpan:
        return ContextSourceSpan(
            span_id=span_id,
            locator=locator,
            start=start,
            end=end,
            locator_ref=locator_ref,
        )

    def summarizer(self) -> CompressionSummarizerIdentity:
        return CompressionSummarizerIdentity(
            model_id="nano-1",
            prompt_revision="prompt-r2",
            summarizer_revision=self.SUMMARIZER_REVISION,
        )

    def manifest(
        self,
        *,
        lossiness: ContextLossiness = ContextLossiness.ABSTRACTIVE,
        source_text: str | None = None,
        output_text: str | None = None,
        spans: tuple[ContextSourceSpan, ...] = (),
        summarizer: CompressionSummarizerIdentity | None | str = "default",
        scope: ContextAuthorizationScope | None = None,
        source_tokens: int = 1_000,
        output_tokens: int = 200,
        target_tokens: int = 200,
    ) -> CompressionManifest:
        resolved_summarizer = (
            self.summarizer() if summarizer == "default" else summarizer
        )
        source_digest = self.digest(source_text or self.SOURCE_TEXT)
        return CompressionManifest(
            manifest_id="man-1",
            source_ref="evi_art_9f2",
            source_digest=source_digest,
            source_tokens=source_tokens,
            output_digest=self.digest(output_text or self.SUMMARY_TEXT),
            output_tokens=output_tokens,
            target_tokens=target_tokens,
            lossiness=lossiness,
            source_spans=spans,
            summarizer=resolved_summarizer,
            authorization_scope=scope or self.scope(),
            policy_revision=self.POLICY_REVISION,
            generated_at=_NOW,
            cache_key=CompressionManifest.derive_cache_key(
                source_digest=source_digest,
                target_tokens=target_tokens,
                policy_revision=self.POLICY_REVISION,
                summarizer_revision=(
                    None
                    if resolved_summarizer is None
                    else resolved_summarizer.summarizer_revision
                ),
            ),
        )

    def summary_representation(
        self,
        *,
        manifest: CompressionManifest | None = None,
        token_count: int = 200,
    ) -> ContextRepresentation:
        resolved = manifest or self.manifest()
        return ContextRepresentation(
            mode=ContextRepresentationMode.SUMMARY,
            token_count=token_count,
            lossiness=resolved.lossiness,
            source_digest=resolved.source_digest,
            content_digest=resolved.output_digest,
            source_spans=resolved.source_spans,
            compression=resolved,
            generated_at=_NOW,
        )

    def full_representation(
        self,
        *,
        source_text: str | None = None,
        token_count: int = 1_000,
    ) -> ContextRepresentation:
        source_digest = self.digest(source_text or self.SOURCE_TEXT)
        return ContextRepresentation(
            mode=ContextRepresentationMode.FULL,
            token_count=token_count,
            lossiness=ContextLossiness.NONE,
            source_digest=source_digest,
            content_digest=source_digest,
        )

    def reference_representation(
        self,
        *,
        source_text: str | None = None,
        token_count: int = 20,
    ) -> ContextRepresentation:
        return ContextRepresentation(
            mode=ContextRepresentationMode.REFERENCE,
            token_count=token_count,
            lossiness=ContextLossiness.ELIDED,
            source_digest=self.digest(source_text or self.SOURCE_TEXT),
            content_digest=self.digest(self.REFERENCE_TEXT),
            content_ref="evi_art_9f2",
        )

    def option(
        self,
        mode: ContextRepresentationMode,
        token_count: int,
        lossiness: ContextLossiness,
    ) -> ContextRepresentationOption:
        return ContextRepresentationOption(
            mode=mode,
            token_count=token_count,
            lossiness=lossiness,
        )

    def default_options(self) -> tuple[ContextRepresentationOption, ...]:
        return (
            self.option(ContextRepresentationMode.FULL, 1_000, ContextLossiness.NONE),
            self.option(
                ContextRepresentationMode.SUMMARY, 200, ContextLossiness.ABSTRACTIVE
            ),
            self.option(
                ContextRepresentationMode.REFERENCE, 20, ContextLossiness.ELIDED
            ),
        )

    def candidate(
        self,
        *,
        candidate_id: str = "cand-1",
        kind: ContextCandidateKind = ContextCandidateKind.ARTIFACT,
        priority_class: ContextPriorityClass = (
            ContextPriorityClass.SELECTED_SKILLS_EVIDENCE
        ),
        source_text: str | None = None,
        lifecycle: ContextSourceLifecycle | None = None,
        scope: ContextAuthorizationScope | None = None,
        relevance_score: int | None = 700,
        original_tokens: int = 1_000,
        options: tuple[ContextRepresentationOption, ...] | None = None,
    ) -> ContextCandidate:
        return ContextCandidate(
            candidate_id=candidate_id,
            kind=kind,
            source_ref="evi_art_9f2",
            source_digest=self.digest(source_text or self.SOURCE_TEXT),
            scope=scope or self.scope(),
            lifecycle=lifecycle or self.lifecycle(),
            priority_class=priority_class,
            original_tokens=original_tokens,
            relevance_score=relevance_score,
            representation_options=(
                self.default_options() if options is None else options
            ),
        )

    def included_decision(
        self,
        *,
        candidate: ContextCandidate | None = None,
        representation: ContextRepresentation | None = None,
        reason: ContextInclusionReason = ContextInclusionReason.HIGH_RELEVANCE,
    ) -> ContextCandidateDecision:
        return ContextCandidateDecision(
            candidate=candidate or self.candidate(),
            representation=representation or self.summary_representation(),
            inclusion_reason=reason,
        )

    def omitted_decision(
        self,
        *,
        candidate: ContextCandidate | None = None,
        reason: ContextOmissionReason = ContextOmissionReason.BUDGET_EXHAUSTED,
    ) -> ContextCandidateDecision:
        resolved = candidate or self.candidate(candidate_id="cand-2")
        return ContextCandidateDecision(
            candidate=resolved,
            representation=ContextRepresentation.omitted(
                source_digest=resolved.source_digest
            ),
            omission_reason=reason,
        )

    def limits(
        self,
        *,
        model_context_limit: int = 8_000,
        reserved_output_tokens: int = 1_000,
        fixed_tokens: int = 500,
        safety_margin_tokens: int = 100,
    ) -> ContextPlanLimits:
        return ContextPlanLimits(
            model_context_limit=model_context_limit,
            reserved_output_tokens=reserved_output_tokens,
            fixed_tokens=fixed_tokens,
            safety_margin_tokens=safety_margin_tokens,
        )

    def revisions(self) -> ContextPlanRevisions:
        return ContextPlanRevisions(
            policy_revision=self.POLICY_REVISION,
            planner_revision="plan-r4",
            tokenizer_revision="tok-r1",
        )

    def plan(
        self,
        *,
        decisions: tuple[ContextCandidateDecision, ...] | None = None,
        limits: ContextPlanLimits | None = None,
        revisions: ContextPlanRevisions | None = None,
        created_at: datetime = _NOW,
        plan_id: str = "cplan-1",
    ) -> ContextPlan:
        resolved_decisions = (
            (self.included_decision(), self.omitted_decision())
            if decisions is None
            else decisions
        )
        resolved_limits = limits or self.limits()
        resolved_revisions = revisions or self.revisions()
        candidates = tuple(decision.candidate for decision in resolved_decisions)
        ordered_decisions = tuple(
            sorted(
                resolved_decisions,
                key=lambda decision: ContextPlanReconstruction.ordering_key(
                    decision.candidate
                ),
            )
        )
        input_digest = ContextPlanReconstruction.input_digest(
            candidates=candidates,
            limits=resolved_limits,
            revisions=resolved_revisions,
        )
        return ContextPlan(
            plan_id=plan_id,
            run_id="run-1",
            model_call_id="mcall-7",
            limits=resolved_limits,
            revisions=resolved_revisions,
            candidates=candidates,
            candidate_decisions=ordered_decisions,
            allocated_tokens=ContextPlanReconstruction.allocated_tokens(
                ordered_decisions
            ),
            input_digest=input_digest,
            plan_digest=ContextPlanReconstruction.plan_digest(
                input_digest=input_digest,
                decisions=ordered_decisions,
            ),
            created_at=created_at,
        )

    def every_contract_instance(self) -> dict[type[RuntimeContract], RuntimeContract]:
        """Return one valid instance of every contract this module defines."""

        return {
            ContextSourceSpan: self.span(),
            ContextAuthorizationScope: self.scope(),
            ContextSourceLifecycle: self.lifecycle(),
            CompressionSummarizerIdentity: self.summarizer(),
            CompressionManifest: self.manifest(),
            ContextRepresentationOption: self.option(
                ContextRepresentationMode.SUMMARY, 200, ContextLossiness.ABSTRACTIVE
            ),
            ContextRepresentation: self.summary_representation(),
            ContextCandidate: self.candidate(),
            ContextCandidateDecision: self.included_decision(),
            ContextPlanLimits: self.limits(),
            ContextPlanRevisions: self.revisions(),
            ContextPlan: self.plan(),
        }


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


class SeededSecretMixin:
    """One secret, seeded three shapes, to prove no field can hold a body."""

    PROSE_SECRET = "the wire instructions are IBAN GB33 9999 and must stay private"
    LONG_SECRET = "x" * 4_096
    STRUCTURED_SECRET: dict[str, str] = {"leaked": PROSE_SECRET}

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


class TestSeededSecretCannotEnterAPlan(
    ContextContractFactoryMixin,
    SeededSecretMixin,
):
    def test_the_probe_reports_a_field_that_would_hold_a_body(self) -> None:
        leaky = self.LeakyContract(note="a bounded note")

        assert self.fields_accepting(leaky, self.PROSE_SECRET) == ["note"]
        assert self.fields_accepting(leaky, self.LONG_SECRET) == ["note"]

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

    def test_a_plan_over_secret_material_serialises_without_the_secret(self) -> None:
        source_body = f"payment memo: {self.PROSE_SECRET}"
        summary_body = f"a payment memo was discussed containing {self.PROSE_SECRET}"
        manifest = self.manifest(source_text=source_body, output_text=summary_body)
        candidate = self.candidate(source_text=source_body)
        plan = self.plan(
            decisions=(
                self.included_decision(
                    candidate=candidate,
                    representation=self.summary_representation(manifest=manifest),
                ),
            )
        )

        serialised = plan.model_dump_json()

        assert self.PROSE_SECRET not in serialised
        assert "IBAN" not in serialised
        assert "payment memo" not in serialised
        assert self.digest(source_body) in serialised

    def test_every_contract_in_the_module_is_probed(self) -> None:
        defined = {
            member
            for _name, member in inspect.getmembers(context_contracts, inspect.isclass)
            if issubclass(member, RuntimeContract)
            and member is not RuntimeContract
            and member.__module__ == context_contracts.__name__
        }

        assert defined == set(self.every_contract_instance())


class TestClosedVocabulariesResolveConservatively(ContextContractFactoryMixin):
    def test_every_candidate_kind_declares_a_priority_ceiling(self) -> None:
        assert set(ContextCandidateKind) == set(
            ContextVocabularyTables.PRIORITY_CEILING_BY_KIND
        )

    def test_every_representation_mode_declares_its_admissible_loss(self) -> None:
        assert set(ContextRepresentationMode) == set(
            ContextVocabularyTables.LOSSINESS_BY_MODE
        )

    def test_unknown_kind_resolves_to_the_least_protected_class(self) -> None:
        assert ContextCandidateKind.conservative() is ContextCandidateKind.UNKNOWN
        assert (
            ContextCandidateKind.UNKNOWN.max_priority_class()
            is ContextPriorityClass.conservative()
        )
        assert (
            ContextPriorityClass.conservative()
            is ContextPriorityClass.LOW_RELEVANCE_HISTORY
        )
        assert not ContextPriorityClass.conservative().protected

    def test_undeclared_lossiness_resolves_to_abstractive(self) -> None:
        assert ContextLossiness.conservative() is ContextLossiness.ABSTRACTIVE
        assert not ContextLossiness.conservative().verbatim

    def test_an_access_state_with_no_mapping_omits_conservatively(self) -> None:
        assert (
            ContextOmissionReason.for_access_state(EvidenceAccessState.AUTHORIZED)
            is ContextOmissionReason.ADMISSIBILITY_NOT_ESTABLISHED
        )
        assert not ContextOmissionReason.conservative().budgetary

    def test_inadmissible_states_map_to_their_own_reason(self) -> None:
        assert (
            ContextOmissionReason.for_access_state(EvidenceAccessState.REVOKED)
            is ContextOmissionReason.REVOKED
        )
        assert (
            ContextOmissionReason.for_access_state(EvidenceAccessState.EXPIRED)
            is ContextOmissionReason.RETENTION_EXPIRED
        )
        assert (
            ContextOmissionReason.for_access_state(EvidenceAccessState.NOT_FOUND)
            is ContextOmissionReason.SOURCE_UNAVAILABLE
        )

    def test_authority_tables_cannot_be_mutated(self) -> None:
        with pytest.raises(TypeError):
            ContextVocabularyTables.PRIORITY_CEILING_BY_KIND[  # type: ignore[index]
                ContextCandidateKind.MEMORY
            ] = ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL
        with pytest.raises(TypeError):
            ContextVocabularyTables.OMISSION_BY_ACCESS_STATE[  # type: ignore[index]
                EvidenceAccessState.REVOKED
            ] = ContextOmissionReason.LOW_RELEVANCE

    def test_priority_declaration_order_is_the_eviction_order(self) -> None:
        ranks = [member.priority_rank for member in ContextPriorityClass]

        assert ranks == sorted(ranks)
        assert ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL.priority_rank == 0
        assert ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL.immutable
        assert ContextPriorityClass.APPROVAL_GATE_STATE.protected
        assert not ContextPriorityClass.ACTIVE_PLAN_OPERATIONS.protected

    def test_fidelity_declaration_order_is_the_preference_order(self) -> None:
        assert [mode.fidelity_rank for mode in ContextRepresentationMode] == list(
            range(len(ContextRepresentationMode))
        )
        assert ContextRepresentationMode.FULL.fidelity_rank == 0
        assert not ContextRepresentationMode.OMITTED.admitted


class TestLossinessIsDeclaredNeverImplied(
    ContextContractFactoryMixin,
    RejectionAssertionMixin,
):
    def test_full_content_must_declare_no_loss(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.LOSSINESS_NOT_ADMITTED,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.FULL,
                token_count=1_000,
                lossiness=ContextLossiness.EXTRACTIVE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.SOURCE_TEXT),
            ),
        )

    def test_an_excerpt_cannot_claim_a_model_wrote_it(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.LOSSINESS_NOT_ADMITTED,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.EXCERPT,
                token_count=100,
                lossiness=ContextLossiness.ABSTRACTIVE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.SUMMARY_TEXT),
            ),
        )

    def test_a_reference_retains_nothing_and_must_say_so(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.LOSSINESS_NOT_ADMITTED,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.REFERENCE,
                token_count=20,
                lossiness=ContextLossiness.NONE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.REFERENCE_TEXT),
                content_ref="evi_art_9f2",
            ),
        )

    def test_a_full_representation_must_digest_to_its_own_source(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.FULL_MUST_BE_THE_SOURCE,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.FULL,
                token_count=1_000,
                lossiness=ContextLossiness.NONE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.SUMMARY_TEXT),
            ),
        )

    def test_a_summary_cannot_secretly_be_the_whole_source(self) -> None:
        manifest = self.manifest(output_text=self.SOURCE_TEXT)
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.COMPRESSED_MUST_BE_DIFFERENT,
            lambda: self.summary_representation(manifest=manifest),
        )

    def test_an_extractive_representation_must_name_its_spans(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.EXTRACT_REQUIRES_SPANS,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.EXCERPT,
                token_count=100,
                lossiness=ContextLossiness.EXTRACTIVE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.SUMMARY_TEXT),
            ),
        )

    def test_an_omitted_representation_carries_nothing(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.OMITTED_MUST_BE_EMPTY,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.OMITTED,
                token_count=12,
                lossiness=ContextLossiness.ELIDED,
                source_digest=self.digest(self.SOURCE_TEXT),
            ),
        )

    def test_a_reference_must_carry_the_ref_it_defers_to(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.REFERENCE_REQUIRES_REF,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.REFERENCE,
                token_count=20,
                lossiness=ContextLossiness.ELIDED,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.REFERENCE_TEXT),
            ),
        )

    def test_only_a_retained_span_lets_a_representation_be_cited(self) -> None:
        spans = (self.span(),)
        cited = self.summary_representation(
            manifest=self.manifest(
                lossiness=ContextLossiness.EXTRACTIVE,
                spans=spans,
                summarizer=None,
            )
        )
        uncited = self.summary_representation()

        assert cited.may_originate_citation
        assert not uncited.may_originate_citation
        assert self.full_representation().may_originate_citation
        assert not self.reference_representation().may_originate_citation


class TestCompressionStaysSourceLinked(
    ContextContractFactoryMixin,
    RejectionAssertionMixin,
):
    def test_a_manifest_describes_loss_only(self) -> None:
        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.NOT_A_COMPRESSION,
            lambda: self.manifest(lossiness=ContextLossiness.NONE),
        )
        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.NOT_A_COMPRESSION,
            lambda: self.manifest(lossiness=ContextLossiness.ELIDED),
        )

    def test_model_written_text_must_name_its_generator(self) -> None:
        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.SUMMARIZER_REQUIRED,
            lambda: self.manifest(summarizer=None),
        )

    def test_extracted_text_must_name_its_spans(self) -> None:
        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.SPANS_REQUIRED,
            lambda: self.manifest(
                lossiness=ContextLossiness.EXTRACTIVE,
                summarizer=None,
            ),
        )

    def test_compression_cannot_grow_its_source(self) -> None:
        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.NOT_SMALLER,
            lambda: self.manifest(source_tokens=100, output_tokens=200),
        )

    def test_the_cache_key_is_derived_not_asserted(self) -> None:
        manifest = self.manifest()
        payload = manifest.model_dump(mode="json")
        payload["cache_key"] = self.digest("some other cache entry")

        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.CACHE_KEY_MISMATCH,
            lambda: CompressionManifest.model_validate(payload),
        )

    def test_a_new_summarizer_revision_is_a_new_cache_entry(self) -> None:
        first = CompressionManifest.derive_cache_key(
            source_digest=self.digest(self.SOURCE_TEXT),
            target_tokens=200,
            policy_revision=self.POLICY_REVISION,
            summarizer_revision="sum-r3",
        )
        second = CompressionManifest.derive_cache_key(
            source_digest=self.digest(self.SOURCE_TEXT),
            target_tokens=200,
            policy_revision=self.POLICY_REVISION,
            summarizer_revision="sum-r4",
        )
        changed_source = CompressionManifest.derive_cache_key(
            source_digest=self.digest("a different source"),
            target_tokens=200,
            policy_revision=self.POLICY_REVISION,
            summarizer_revision="sum-r3",
        )

        assert len({first, second, changed_source}) == 3

    def test_a_manifest_must_belong_to_the_representation_it_is_attached_to(
        self,
    ) -> None:
        foreign = self.manifest(source_text="an entirely different document")

        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.MANIFEST_NOT_SOURCE_LINKED,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.SUMMARY,
                token_count=200,
                lossiness=ContextLossiness.ABSTRACTIVE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=foreign.output_digest,
                compression=foreign,
                generated_at=_NOW,
            ),
        )

    def test_a_compressed_representation_must_carry_its_manifest(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.MISSING_MANIFEST,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.SUMMARY,
                token_count=200,
                lossiness=ContextLossiness.ABSTRACTIVE,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.SUMMARY_TEXT),
            ),
        )

    def test_an_uncompressed_representation_cannot_carry_one(self) -> None:
        self.assert_rejected(
            ContextRepresentationRejected,
            ContextRepresentationRejected.Messages.UNEXPECTED_MANIFEST,
            lambda: ContextRepresentation(
                mode=ContextRepresentationMode.REFERENCE,
                token_count=20,
                lossiness=ContextLossiness.ELIDED,
                source_digest=self.digest(self.SOURCE_TEXT),
                content_digest=self.digest(self.REFERENCE_TEXT),
                content_ref="evi_art_9f2",
                compression=self.manifest(),
            ),
        )

    def test_compression_timestamps_must_be_absolute(self) -> None:
        payload = self.manifest().model_dump(mode="json")
        payload["generated_at"] = "2026-07-29T12:00:00"

        self.assert_rejected(
            CompressionManifestRejected,
            CompressionManifestRejected.Messages.NAIVE_TIMESTAMP,
            lambda: CompressionManifest.model_validate(payload),
        )


class TestSpansLocateWithoutQuoting(
    ContextContractFactoryMixin,
    RejectionAssertionMixin,
):
    def test_a_ranged_locator_needs_both_offsets(self) -> None:
        self.assert_rejected(
            ContextSpanRejected,
            ContextSpanRejected.Messages.RANGE_REQUIRED,
            lambda: self.span(end=None),
        )

    def test_a_range_must_be_ordered(self) -> None:
        self.assert_rejected(
            ContextSpanRejected,
            ContextSpanRejected.Messages.RANGE_NOT_ORDERED,
            lambda: self.span(start=40, end=24),
        )

    def test_an_opaque_locator_needs_its_source_issued_ref(self) -> None:
        self.assert_rejected(
            ContextSpanRejected,
            ContextSpanRejected.Messages.LOCATOR_REF_REQUIRED,
            lambda: self.span(
                locator=ContextSpanLocator.SELECTOR,
                start=None,
                end=None,
            ),
        )

    def test_a_whole_source_span_locates_nothing_further(self) -> None:
        self.assert_rejected(
            ContextSpanRejected,
            ContextSpanRejected.Messages.WHOLE_SOURCE_IS_UNLOCATED,
            lambda: self.span(locator=ContextSpanLocator.WHOLE_SOURCE),
        )
        assert (
            self.span(
                locator=ContextSpanLocator.WHOLE_SOURCE,
                start=None,
                end=None,
            ).locator
            is ContextSpanLocator.WHOLE_SOURCE
        )


class TestAuthorityCanOnlyNarrow(
    ContextContractFactoryMixin,
    RejectionAssertionMixin,
):
    @pytest.mark.parametrize(
        ("state", "reason"),
        [
            (EvidenceAccessState.UNAUTHORIZED, ContextOmissionReason.UNAUTHORIZED),
            (EvidenceAccessState.REVOKED, ContextOmissionReason.REVOKED),
            (EvidenceAccessState.EXPIRED, ContextOmissionReason.RETENTION_EXPIRED),
            (
                EvidenceAccessState.NOT_FOUND,
                ContextOmissionReason.SOURCE_UNAVAILABLE,
            ),
            (
                EvidenceAccessState.UNAVAILABLE,
                ContextOmissionReason.SOURCE_UNAVAILABLE,
            ),
        ],
    )
    def test_an_inadmissible_source_cannot_be_included(
        self,
        state: EvidenceAccessState,
        reason: ContextOmissionReason,
    ) -> None:
        candidate = self.candidate(lifecycle=self.lifecycle(access_state=state))

        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.INADMISSIBLE_SOURCE,
            lambda: self.included_decision(candidate=candidate),
        )
        assert self.omitted_decision(candidate=candidate, reason=reason)

    def test_an_inadmissible_source_cannot_be_relabelled_as_low_relevance(
        self,
    ) -> None:
        candidate = self.candidate(
            lifecycle=self.lifecycle(access_state=EvidenceAccessState.REVOKED)
        )

        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.WRONG_OMISSION_REASON,
            lambda: self.omitted_decision(
                candidate=candidate,
                reason=ContextOmissionReason.LOW_RELEVANCE,
            ),
        )

    def test_retention_that_lapses_before_planning_is_caught_by_the_plan(self) -> None:
        observed = _NOW - timedelta(hours=2)
        candidate = self.candidate(
            lifecycle=self.lifecycle(
                observed_at=observed,
                retention_until=_NOW - timedelta(hours=1),
            )
        )
        decision = self.included_decision(candidate=candidate)

        assert decision.authority_violation(observed) is None
        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.INADMISSIBLE_SOURCE,
            lambda: self.plan(decisions=(decision,)),
        )

    def test_a_legal_hold_never_makes_a_revoked_source_readable(self) -> None:
        held = self.lifecycle(
            access_state=EvidenceAccessState.REVOKED,
            legal_hold=True,
        )

        assert held.inadmissible_reason(_NOW) is ContextOmissionReason.REVOKED

    def test_protected_context_cannot_be_evicted_for_budget(self) -> None:
        candidate = self.candidate(
            kind=ContextCandidateKind.CURRENT_REQUEST,
            priority_class=ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS,
        )

        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.PROTECTED_EVICTED,
            lambda: self.omitted_decision(
                candidate=candidate,
                reason=ContextOmissionReason.BUDGET_EXHAUSTED,
            ),
        )

    def test_protected_context_may_leave_when_it_cannot_be_admitted(self) -> None:
        candidate = self.candidate(
            kind=ContextCandidateKind.APPROVAL_STATE,
            priority_class=ContextPriorityClass.APPROVAL_GATE_STATE,
            lifecycle=self.lifecycle(access_state=EvidenceAccessState.NOT_FOUND),
        )

        decision = self.omitted_decision(
            candidate=candidate,
            reason=ContextOmissionReason.SOURCE_UNAVAILABLE,
        )

        assert not decision.included

    def test_immutable_context_cannot_be_carried_in_a_reduced_form(self) -> None:
        candidate = self.candidate(
            kind=ContextCandidateKind.SYSTEM_POLICY,
            priority_class=ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL,
        )

        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.IMMUTABLE_TRUNCATED,
            lambda: self.included_decision(candidate=candidate),
        )
        assert self.included_decision(
            candidate=candidate,
            representation=self.full_representation(),
            reason=ContextInclusionReason.PROTECTED_CLASS,
        )

    def test_retrieved_material_cannot_claim_the_safety_tier(self) -> None:
        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.PRIORITY_PROMOTED,
            lambda: self.candidate(
                kind=ContextCandidateKind.MEMORY,
                priority_class=ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL,
            ),
        )
        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.PRIORITY_PROMOTED,
            lambda: self.candidate(
                kind=ContextCandidateKind.TOOL_OBSERVATION,
                priority_class=ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS,
            ),
        )

    def test_material_of_unknown_kind_takes_the_lowest_class(self) -> None:
        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.UNKNOWN_KIND_PROMOTED,
            lambda: self.candidate(
                kind=ContextCandidateKind.UNKNOWN,
                priority_class=ContextPriorityClass.RECALLED_MEMORY,
            ),
        )
        assert self.candidate(
            kind=ContextCandidateKind.UNKNOWN,
            priority_class=ContextPriorityClass.LOW_RELEVANCE_HISTORY,
        )

    def test_protection_cannot_be_claimed_by_evictable_material(self) -> None:
        self.assert_rejected(
            ContextAuthorityWidened,
            ContextAuthorityWidened.Messages.PROTECTION_CLAIMED,
            lambda: self.included_decision(
                reason=ContextInclusionReason.PROTECTED_CLASS
            ),
        )

    def test_a_compression_cannot_be_authorised_more_widely_than_its_candidate(
        self,
    ) -> None:
        wider = self.manifest(scope=self.scope(conversation_id=None))
        cross_subject = self.manifest(scope=self.scope(subject="subject-b"))

        for manifest in (wider, cross_subject):
            self.assert_rejected(
                ContextAuthorityWidened,
                ContextAuthorityWidened.Messages.COMPRESSION_SCOPE_WIDENED,
                lambda manifest=manifest: self.included_decision(
                    representation=self.summary_representation(manifest=manifest)
                ),
            )

    def test_a_narrower_compression_scope_is_admitted(self) -> None:
        narrower = self.manifest(scope=self.scope(project_id="proj-1"))

        decision = self.included_decision(
            representation=self.summary_representation(manifest=narrower)
        )

        assert decision.included

    def test_scope_narrowing_is_directional(self) -> None:
        broad = self.scope(conversation_id=None)
        narrow = self.scope(project_id="proj-1")

        assert narrow.narrows_to(broad)
        assert not broad.narrows_to(narrow)
        assert not self.scope(subject="subject-b").narrows_to(broad)


class TestCandidatesOfferOnlyWhatTheyCanDeliver(
    ContextContractFactoryMixin,
    RejectionAssertionMixin,
):
    def test_omission_is_never_an_offered_option(self) -> None:
        self.assert_rejected(
            ContextCandidateRejected,
            ContextCandidateRejected.Messages.OMISSION_IS_NOT_AN_OPTION,
            lambda: self.option(
                ContextRepresentationMode.OMITTED, 0, ContextLossiness.ELIDED
            ),
        )

    def test_options_are_unique_and_ordered_by_fidelity(self) -> None:
        unordered = (
            self.option(
                ContextRepresentationMode.SUMMARY, 200, ContextLossiness.ABSTRACTIVE
            ),
            self.option(ContextRepresentationMode.FULL, 1_000, ContextLossiness.NONE),
        )
        duplicated = (
            self.option(
                ContextRepresentationMode.SUMMARY, 200, ContextLossiness.ABSTRACTIVE
            ),
            self.option(
                ContextRepresentationMode.SUMMARY, 150, ContextLossiness.ABSTRACTIVE
            ),
        )

        for options in (unordered, duplicated):
            self.assert_rejected(
                ContextCandidateRejected,
                ContextCandidateRejected.Messages.OPTIONS_NOT_CANONICAL,
                lambda options=options: self.candidate(options=options),
            )

    def test_an_option_cannot_cost_more_than_the_whole_source(self) -> None:
        self.assert_rejected(
            ContextCandidateRejected,
            ContextCandidateRejected.Messages.OPTION_EXCEEDS_SOURCE,
            lambda: self.candidate(
                original_tokens=100,
                options=(
                    self.option(
                        ContextRepresentationMode.SUMMARY,
                        200,
                        ContextLossiness.ABSTRACTIVE,
                    ),
                ),
            ),
        )

    def test_a_full_option_costs_exactly_the_whole_source(self) -> None:
        self.assert_rejected(
            ContextCandidateRejected,
            ContextCandidateRejected.Messages.FULL_OPTION_IS_THE_SOURCE,
            lambda: self.candidate(
                original_tokens=1_000,
                options=(
                    self.option(
                        ContextRepresentationMode.FULL, 900, ContextLossiness.NONE
                    ),
                ),
            ),
        )

    def test_a_decision_cannot_admit_an_unoffered_form(self) -> None:
        self.assert_rejected(
            ContextCandidateRejected,
            ContextCandidateRejected.Messages.UNOFFERED_REPRESENTATION,
            lambda: self.included_decision(
                representation=self.summary_representation(token_count=150)
            ),
        )

    def test_a_decision_must_represent_its_own_candidate(self) -> None:
        self.assert_rejected(
            ContextCandidateRejected,
            ContextCandidateRejected.Messages.SOURCE_MISMATCH,
            lambda: self.included_decision(
                representation=self.full_representation(
                    source_text="an entirely different document"
                )
            ),
        )

    def test_a_decision_states_exactly_one_outcome(self) -> None:
        candidate = self.candidate()
        self.assert_rejected(
            ContextDecisionRejected,
            ContextDecisionRejected.Messages.AMBIGUOUS_OUTCOME,
            lambda: ContextCandidateDecision(
                candidate=candidate,
                representation=self.summary_representation(),
            ),
        )
        self.assert_rejected(
            ContextDecisionRejected,
            ContextDecisionRejected.Messages.AMBIGUOUS_OUTCOME,
            lambda: ContextCandidateDecision(
                candidate=candidate,
                representation=self.summary_representation(),
                inclusion_reason=ContextInclusionReason.HIGH_RELEVANCE,
                omission_reason=ContextOmissionReason.LOW_RELEVANCE,
            ),
        )

    def test_an_omission_reason_requires_the_omitted_form(self) -> None:
        candidate = self.candidate()
        self.assert_rejected(
            ContextDecisionRejected,
            ContextDecisionRejected.Messages.REASON_CONTRADICTS_MODE,
            lambda: ContextCandidateDecision(
                candidate=candidate,
                representation=self.summary_representation(),
                omission_reason=ContextOmissionReason.LOW_RELEVANCE,
            ),
        )

    def test_unknown_relevance_sorts_last_within_its_class(self) -> None:
        scored = self.candidate(candidate_id="cand-a", relevance_score=900)
        unscored = self.candidate(candidate_id="cand-b", relevance_score=None)

        assert unscored.effective_relevance == ContextBounds.UNKNOWN_RELEVANCE_SCORE
        assert ContextPlanReconstruction.ordered((unscored, scored)) == (
            scored,
            unscored,
        )


class TestPlanReconstructsItself(
    ContextContractFactoryMixin,
    RejectionAssertionMixin,
):
    def test_a_persisted_plan_replays_identically(self) -> None:
        plan = self.plan()

        replayed = ContextPlan.model_validate_json(plan.model_dump_json())

        assert replayed == plan
        assert plan.reconstructs(replayed)

    def test_the_same_inputs_produce_the_same_plan(self) -> None:
        first = self.plan()
        second = self.plan(plan_id="cplan-2")

        assert first.plan_digest == second.plan_digest
        assert first.input_digest == second.input_digest
        assert first.reconstructs(second)
        assert first.plan_id != second.plan_id

    def test_candidate_enumeration_order_does_not_change_the_plan(self) -> None:
        included = self.included_decision()
        omitted = self.omitted_decision()

        forward = self.plan(decisions=(included, omitted))
        reversed_input = self.plan(decisions=(omitted, included))

        assert forward.input_digest == reversed_input.input_digest
        assert forward.plan_digest == reversed_input.plan_digest

    def test_a_different_policy_revision_is_a_different_plan(self) -> None:
        baseline = self.plan()
        retokenised = self.plan(
            revisions=ContextPlanRevisions(
                policy_revision=self.POLICY_REVISION,
                planner_revision="plan-r4",
                tokenizer_revision="tok-r2",
            )
        )

        assert baseline.input_digest != retokenised.input_digest
        assert not baseline.reconstructs(retokenised)

    def test_reordered_decisions_fail_on_parse(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["candidate_decisions"] = list(reversed(payload["candidate_decisions"]))

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.DECISIONS_NOT_ORDERED,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_decision_for_a_candidate_the_plan_does_not_hold_fails(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["candidate_decisions"][0]["candidate"]["relevance_score"] = 701

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.CANDIDATE_MISMATCH,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_candidate_decided_twice_fails(self) -> None:
        decision = self.included_decision()
        plan = self.plan(decisions=(decision,))
        payload = plan.model_dump(mode="json")
        payload["candidate_decisions"].append(payload["candidate_decisions"][0])

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.DUPLICATE_CANDIDATE,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_dropped_decision_fails(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["candidate_decisions"] = payload["candidate_decisions"][:1]

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.DECISIONS_NOT_ORDERED,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_retotalled_allocation_fails(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["allocated_tokens"] = plan.allocated_tokens - 1

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.ALLOCATION_MISMATCH,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_plan_cannot_allocate_past_its_context_limit(self) -> None:
        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.BUDGET_EXCEEDED,
            lambda: self.plan(
                limits=self.limits(
                    model_context_limit=1_200,
                    reserved_output_tokens=900,
                    fixed_tokens=100,
                    safety_margin_tokens=100,
                )
            ),
        )

    def test_fixed_content_that_does_not_fit_is_refused_before_the_model(self) -> None:
        limits = self.limits(
            model_context_limit=1_000,
            reserved_output_tokens=400,
            fixed_tokens=700,
            safety_margin_tokens=50,
        )

        assert limits.available_tokens < 0
        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.BUDGET_EXCEEDED,
            lambda: self.plan(decisions=(self.omitted_decision(),), limits=limits),
        )

    def test_a_tampered_input_digest_fails(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["input_digest"] = self.digest("some other inputs")

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.INPUT_DIGEST_MISMATCH,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_tampered_plan_digest_fails(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["plan_digest"] = self.digest("some other decision")

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.PLAN_DIGEST_MISMATCH,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_swapped_limit_fails_even_with_a_matching_plan_digest(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["limits"]["model_context_limit"] = 16_000

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.INPUT_DIGEST_MISMATCH,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_plan_timestamps_must_be_absolute(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        payload["created_at"] = "2026-07-29T12:00:00"

        self.assert_rejected(
            ContextPlanReconstructionFailed,
            ContextPlanReconstructionFailed.Messages.NAIVE_TIMESTAMP,
            lambda: ContextPlan.model_validate(payload),
        )

    def test_a_plan_exposes_its_omissions_with_reasons(self) -> None:
        plan = self.plan()

        omitted = plan.omitted_decisions

        assert len(omitted) == 1
        assert omitted[0].omission_reason is ContextOmissionReason.BUDGET_EXHAUSTED
        assert plan.allocated_tokens == 200
        assert plan.model_context_limit == 8_000
        assert plan.reserved_output_tokens == 1_000
        assert plan.fixed_tokens == 500
        assert plan.policy_revision == self.POLICY_REVISION

    def test_planning_is_capped_at_the_declared_candidate_ceiling(self) -> None:
        plan = self.plan()
        payload = plan.model_dump(mode="json")
        over_cap = [payload["candidates"][0]] * (ContextBounds.MAX_CANDIDATES + 1)
        payload["candidates"] = over_cap

        assert ContextBounds.MAX_CANDIDATES == 500
        with pytest.raises(ValidationError):
            ContextPlan.model_validate(payload)

    def test_an_empty_plan_is_representable(self) -> None:
        empty = self.plan(decisions=())

        assert empty.allocated_tokens == 0
        assert empty.candidate_decisions == ()
        assert ContextPlan.model_validate_json(empty.model_dump_json()) == empty
