"""The published Step RB conformance suite every RB adopter must pass.

This module is deliberately not named ``test_*``: pytest does not collect it on
its own.  Each domain that binds a revision-bound reference (F3 capability refs,
F5 evidence refs, F8 descriptor revisions, F9 child grants, F11 target
manifests) adds one test module that supplies a harness and subclasses
:class:`RevisionBindingConformanceSuite`::

    class FooHarnessMixin:
        async def build_harness(self) -> RevisionBindingConformanceHarness:
            return FooRevisionBindingHarness(...)

    class TestFooRevisionBindingConformance(
        FooHarnessMixin,
        RevisionBindingConformanceSuite,
    ):
        '''F8 descriptor revisions bind through the shared RB primitive.'''

Adopters add an instantiation; they never add a second staleness
implementation, and they never edit this suite to accommodate a domain.  A new
required behavior belongs here, once, so every adopter inherits it.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

import pytest

from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    BoundRevision,
    RevalidationBooleanCoercion,
    RevalidationOutcome,
    RevalidationPolicy,
    RevalidationReason,
    RevisionBoundRef,
    RevisionBoundRefNotCurrent,
    RevisionBoundScope,
    RevisionRevalidatorPort,
    RevisionScopeDimension,
    RevisionUseContext,
)


@runtime_checkable
class RevisionBindingConformanceHarness(Protocol):
    """The small surface each domain supplies so the suite can drive it.

    The harness owns minting and the domain authority.  It never implements
    staleness semantics: those belong to the shared revalidator under test.
    """

    @property
    def feature(self) -> AgentQualityFeature:
        """Return the single feature this harness mints references for."""

    @property
    def revalidator(self) -> RevisionRevalidatorPort:
        """Return the revalidator under conformance test."""

    async def mint(self, scope: RevisionBoundScope) -> RevisionBoundRef:
        """Register ``scope`` with the authority and bind a reference to it."""

    async def supersede(self, scope: RevisionBoundScope) -> None:
        """Advance the authority's revision for ``scope``."""

    async def revoke(self, scope: RevisionBoundScope) -> None:
        """Revoke the authority for ``scope``."""

    async def set_authority_unavailable(self, *, unavailable: bool) -> None:
        """Make every authority answer unavailable, or restore it."""


class RevisionBindingConformanceFixtures:
    """Scope, context, and policy builders shared by every conformance case."""

    SUBJECT_A: ClassVar[str] = "a1" * 32
    SUBJECT_B: ClassVar[str] = "b2" * 32
    RUN_A: ClassVar[str] = "run-conformance-a"
    RUN_B: ClassVar[str] = "run-conformance-b"
    GENERATION_A: ClassVar[str] = "catalog-generation-a"
    GENERATION_B: ClassVar[str] = "catalog-generation-b"
    FOREIGN_REVISION: ClassVar[str] = "revision-never-issued"

    def scope(
        self,
        *,
        subject_fingerprint: str | None = None,
        run_id: str | None = None,
        catalog_generation: str | None = None,
    ) -> RevisionBoundScope:
        """Build a bound scope, defaulting to subject A."""

        return RevisionBoundScope(
            subject_fingerprint=subject_fingerprint or self.SUBJECT_A,
            run_id=run_id,
            catalog_generation=catalog_generation,
        )

    def use_context(
        self,
        *,
        subject_fingerprint: str | None = None,
        run_id: str | None = None,
        catalog_generation: str | None = None,
    ) -> RevisionUseContext:
        """Build verified at-use runtime facts, defaulting to subject A/run A."""

        return RevisionUseContext(
            subject_fingerprint=subject_fingerprint or self.SUBJECT_A,
            run_id=run_id or self.RUN_A,
            catalog_generation=catalog_generation,
        )

    def policy(
        self,
        harness: RevisionBindingConformanceHarness,
        *,
        feature: AgentQualityFeature | None = None,
        required_dimensions: frozenset[RevisionScopeDimension] = frozenset(),
    ) -> RevalidationPolicy:
        """Build the call-site policy for ``harness``'s feature."""

        return RevalidationPolicy(
            feature=feature or harness.feature,
            required_dimensions=required_dimensions,
        )

    def foreign_feature(
        self,
        harness: RevisionBindingConformanceHarness,
    ) -> AgentQualityFeature:
        """Return any closed feature other than the harness's own."""

        return next(
            feature for feature in AgentQualityFeature if feature is not harness.feature
        )


class RevisionBindingConformanceSuite(RevisionBindingConformanceFixtures):
    """Behaviors every revision-bound-reference adopter must satisfy.

    Subclass it and implement :meth:`build_harness`.  A fresh harness is built
    per test, so no case can leak authority state into another.
    """

    class Messages:
        """Messages owned by this suite."""

        HARNESS_REQUIRED: ClassVar[str] = (
            "RevisionBindingConformanceSuite subclasses must implement "
            "build_harness() and return a RevisionBindingConformanceHarness"
        )

    async def build_harness(self) -> RevisionBindingConformanceHarness:
        """Return a fresh harness for one conformance case."""

        raise NotImplementedError(self.Messages.HARNESS_REQUIRED)

    async def test_harness_satisfies_the_published_conformance_surface(self) -> None:
        harness = await self.build_harness()
        assert isinstance(harness, RevisionBindingConformanceHarness)
        assert isinstance(harness.feature, AgentQualityFeature)

    async def test_matching_scope_and_revision_revalidates_as_current(self) -> None:
        harness = await self.build_harness()
        scope = self.scope(run_id=self.RUN_A)
        ref = await harness.mint(scope)

        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(),
            self.policy(harness),
        )

        assert decision.outcome is RevalidationOutcome.CURRENT
        assert decision.reason is RevalidationReason.REVISION_MATCHES
        assert decision.is_current is True
        assert decision.require_current() == ref.revision

    async def test_repeated_revalidation_is_idempotent(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))
        context = self.use_context()
        policy = self.policy(harness)

        first = await harness.revalidator.revalidate_at_use(ref, context, policy)
        second = await harness.revalidator.revalidate_at_use(ref, context, policy)
        third = await harness.revalidator.revalidate_at_use(ref, context, policy)

        assert first == second == third
        assert first.outcome is RevalidationOutcome.CURRENT

    async def test_cross_subject_use_is_rejected(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))

        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(subject_fingerprint=self.SUBJECT_B),
            self.policy(harness),
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.SUBJECT_MISMATCH
        assert decision.current_revision is None

    async def test_cross_run_use_is_rejected(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))

        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(run_id=self.RUN_B),
            self.policy(harness),
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.RUN_MISMATCH

    async def test_cross_catalog_generation_use_is_rejected(self) -> None:
        harness = await self.build_harness()
        scope = self.scope(run_id=self.RUN_A, catalog_generation=self.GENERATION_A)
        ref = await harness.mint(scope)

        superseded_generation = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(catalog_generation=self.GENERATION_B),
            self.policy(harness),
        )
        missing_generation = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(),
            self.policy(harness),
        )

        assert superseded_generation.reason is (
            RevalidationReason.CATALOG_GENERATION_MISMATCH
        )
        assert missing_generation.reason is (
            RevalidationReason.CATALOG_GENERATION_MISMATCH
        )
        assert superseded_generation.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert missing_generation.outcome is RevalidationOutcome.OUT_OF_SCOPE

    async def test_required_scope_dimension_is_enforced(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope())

        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(),
            self.policy(
                harness,
                required_dimensions=frozenset({RevisionScopeDimension.RUN}),
            ),
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.SCOPE_DIMENSION_MISSING

    async def test_reference_cannot_be_replayed_on_another_feature(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))

        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(),
            self.policy(harness, feature=self.foreign_feature(harness)),
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.FEATURE_MISMATCH

    async def test_superseded_reference_replay_never_returns_current(self) -> None:
        harness = await self.build_harness()
        scope = self.scope(run_id=self.RUN_A)
        ref = await harness.mint(scope)
        context = self.use_context()
        policy = self.policy(harness)

        await harness.supersede(scope)
        first = await harness.revalidator.revalidate_at_use(ref, context, policy)
        replayed = await harness.revalidator.revalidate_at_use(ref, context, policy)

        assert first.outcome is RevalidationOutcome.SUPERSEDED
        assert first.reason is RevalidationReason.REVISION_CHANGED
        assert first.is_current is False
        assert replayed == first
        with pytest.raises(RevisionBoundRefNotCurrent):
            first.require_current()

    async def test_revocation_between_mint_and_use_is_revoked(self) -> None:
        harness = await self.build_harness()
        scope = self.scope(run_id=self.RUN_A)
        ref = await harness.mint(scope)

        await harness.revoke(scope)
        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(),
            self.policy(harness),
        )

        assert decision.outcome is RevalidationOutcome.REVOKED
        assert decision.reason is RevalidationReason.AUTHORITY_REVOKED
        assert decision.current_revision is None
        with pytest.raises(RevisionBoundRefNotCurrent):
            decision.require_current()

    async def test_unavailable_authority_fails_closed(self) -> None:
        harness = await self.build_harness()
        scope = self.scope(run_id=self.RUN_A)
        ref = await harness.mint(scope)
        context = self.use_context()
        policy = self.policy(harness)

        await harness.set_authority_unavailable(unavailable=True)
        decision = await harness.revalidator.revalidate_at_use(ref, context, policy)

        assert decision.outcome is RevalidationOutcome.UNAVAILABLE
        assert decision.outcome.admits_use is False
        assert decision.is_current is False
        assert decision.current_revision is None
        with pytest.raises(RevisionBoundRefNotCurrent):
            decision.require_current()

        await harness.set_authority_unavailable(unavailable=False)
        restored = await harness.revalidator.revalidate_at_use(ref, context, policy)
        assert restored.outcome is RevalidationOutcome.CURRENT

    async def test_revalidation_results_cannot_be_coerced_to_a_boolean(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))

        await harness.set_authority_unavailable(unavailable=True)
        decision = await harness.revalidator.revalidate_at_use(
            ref,
            self.use_context(),
            self.policy(harness),
        )

        with pytest.raises(RevalidationBooleanCoercion):
            bool(decision)
        with pytest.raises(RevalidationBooleanCoercion):
            bool(decision.outcome)

    async def test_tampered_binding_digest_is_rejected(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))
        forged = ref.model_copy(update={"binding_digest": "f" * 64})

        decision = await harness.revalidator.revalidate_at_use(
            forged,
            self.use_context(),
            self.policy(harness),
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.BINDING_DIGEST_MISMATCH

    async def test_tampered_bound_body_is_rejected(self) -> None:
        harness = await self.build_harness()
        scope = self.scope(run_id=self.RUN_A)
        ref = await harness.mint(scope)
        widened = ref.model_copy(
            update={
                "binding": ref.binding.model_copy(
                    update={"revision": BoundRevision(value=self.FOREIGN_REVISION)}
                )
            }
        )

        decision = await harness.revalidator.revalidate_at_use(
            widened,
            self.use_context(),
            self.policy(harness),
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.BINDING_DIGEST_MISMATCH

    async def test_minting_is_reproducible_from_the_bound_body(self) -> None:
        harness = await self.build_harness()
        ref = await harness.mint(self.scope(run_id=self.RUN_A))

        reminted = RevisionBoundRef.mint(
            feature=ref.feature,
            opaque_ref=ref.opaque_ref,
            scope=ref.scope,
            revision=ref.revision,
        )

        assert reminted.binding_digest == ref.binding_digest
        assert reminted == ref
        assert ref.binding_is_intact is True


__all__ = [
    "RevisionBindingConformanceFixtures",
    "RevisionBindingConformanceHarness",
    "RevisionBindingConformanceSuite",
]
