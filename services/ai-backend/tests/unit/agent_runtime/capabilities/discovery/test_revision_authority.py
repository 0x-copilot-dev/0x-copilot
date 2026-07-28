"""F3's instantiation of the published Step RB conformance suite.

F3 adds a harness and an authority adapter here; it deliberately adds no second
staleness implementation.  Every scope, ordering, tamper, revocation, and
unavailability behavior asserted below is inherited from the one shared suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityCatalog,
    CapabilityCatalogGeneration,
    CapabilityCatalogGenerationPort,
    CapabilityCatalogIdentityError,
    CapabilityCatalogRevisionAuthority,
    CapabilityCatalogScope,
    CapabilityRefBinding,
    CapabilityRefBindingError,
    CapabilityRefRevalidation,
    CapabilityRefRevisionBinding,
    LiveCapabilityCatalogGeneration,
)
from agent_runtime.capabilities.tools.cards import ToolCard, ToolRiskLevel
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    RevalidationOutcome,
    RevalidationReason,
    RevisionAuthorityResult,
    RevisionAuthorityState,
    RevisionBindingRevalidator,
    RevisionBoundScope,
    RevisionRevalidatorPort,
    RevisionScopeDimension,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from tests.unit.agent_runtime.control_plane.revision_binding_conformance import (
    RevisionBindingConformanceFixtures,
    RevisionBindingConformanceHarness,
    RevisionBindingConformanceSuite,
)

_NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
_REFERENCE_KEY = b"f3-revision-authority-reference-key-32!!"
_SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"


def _context(*, run_id: str = "run_1", user_id: str = "user_1") -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id,
        org_id="org_1",
        roles={"member"},
        permission_scopes={"docs:read"},
        connector_scopes={"drive": frozenset({"docs:read"})},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=32_000,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id=run_id,
    )


def _catalog(
    context: AgentRuntimeContext,
    *,
    selection_ref: str = _SELECTION_REF,
) -> CapabilityCatalog:
    return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
        context=context,
        scope=CapabilityCatalogScope.from_context(
            context,
            profile_id="research",
            policy_revision="policy_7",
            connector_scope_revision="scope_9",
        ),
        task_policy_selection_ref=selection_ref,
        tool_cards=(
            ToolCard(
                name="drive_search",
                display_name="Drive Search",
                short_description="Find relevant drive records.",
                connector="drive",
                tags={"search"},
                required_scopes=frozenset({"docs:read"}),
                risk_level=ToolRiskLevel.LOW,
                load_cost=1,
            ),
        ),
        expires_at=_NOW + timedelta(minutes=15),
    )


def _generation(
    *, selection_ref: str, subject_fingerprint: str
) -> CapabilityCatalogGeneration:
    return CapabilityCatalogGeneration.create(
        subject_fingerprint=subject_fingerprint,
        connector_scope_revision="scope_9",
        task_policy_selection_ref=selection_ref,
    )


class InMemoryCatalogGenerationSource:
    """Test double for whatever would rebuild the catalog right now."""

    def __init__(self) -> None:
        self._live: dict[
            tuple[str, str | None, str | None], CapabilityCatalogGeneration
        ] = {}
        self._revoked: set[tuple[str, str | None, str | None]] = set()
        self._unavailable = False
        self._issued = 0
        self.calls = 0

    @staticmethod
    def _key(scope: RevisionBoundScope) -> tuple[str, str | None, str | None]:
        return (scope.subject_fingerprint, scope.run_id, scope.catalog_generation)

    def issue(self, scope: RevisionBoundScope) -> CapabilityCatalogGeneration:
        """Register or advance the live generation for one bound scope."""

        key = self._key(scope)
        self._issued += 1
        generation = _generation(
            selection_ref=f"task-policy-selection://live/{self._issued}",
            subject_fingerprint=scope.subject_fingerprint,
        )
        self._live[key] = generation
        self._revoked.discard(key)
        return generation

    def publish(
        self,
        scope: RevisionBoundScope,
        generation: CapabilityCatalogGeneration,
    ) -> None:
        """Make an exact generation the live answer for one bound scope."""

        key = self._key(scope)
        self._live[key] = generation
        self._revoked.discard(key)

    def revoke(self, scope: RevisionBoundScope) -> None:
        """Withdraw authorization for one bound scope."""

        self._revoked.add(self._key(scope))

    def set_unavailable(self, *, unavailable: bool) -> None:
        """Make the source unable to answer at all."""

        self._unavailable = unavailable

    async def live_generation(
        self,
        *,
        scope: RevisionBoundScope,
    ) -> LiveCapabilityCatalogGeneration:
        self.calls += 1
        if self._unavailable:
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNAVAILABLE
            )
        key = self._key(scope)
        if key in self._revoked:
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.REVOKED
            )
        generation = self._live.get(key)
        if generation is None:
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNKNOWN
            )
        return LiveCapabilityCatalogGeneration.active(generation)


class CapabilityCatalogConformanceHarness:
    """Drive the shared suite against the real F3 authority adapter."""

    OPAQUE_REF: ClassVar[str] = f"cap_{'a' * 32}"

    def __init__(self) -> None:
        self.source = InMemoryCatalogGenerationSource()
        self.authority = CapabilityCatalogRevisionAuthority(self.source)
        self._revalidator = RevisionBindingRevalidator(self.authority)

    @property
    def feature(self) -> AgentQualityFeature:
        return AgentQualityFeature.F3_CAPABILITY_DISCOVERY

    @property
    def revalidator(self) -> RevisionRevalidatorPort:
        return self._revalidator

    async def mint(self, scope: RevisionBoundScope):
        generation = self.source.issue(scope)
        from agent_runtime.control_plane.revision_binding import RevisionBoundRef

        return RevisionBoundRef.mint(
            feature=self.feature,
            opaque_ref=self.OPAQUE_REF,
            scope=scope,
            revision=CapabilityRefRevisionBinding.revision_for(generation),
        )

    async def supersede(self, scope: RevisionBoundScope) -> None:
        self.source.issue(scope)

    async def revoke(self, scope: RevisionBoundScope) -> None:
        self.source.revoke(scope)

    async def set_authority_unavailable(self, *, unavailable: bool) -> None:
        self.source.set_unavailable(unavailable=unavailable)


class CapabilityCatalogHarnessMixin:
    """Build a fresh F3 harness for every inherited conformance case."""

    async def build_harness(self) -> RevisionBindingConformanceHarness:
        return CapabilityCatalogConformanceHarness()


class TestCapabilityRefRevisionBindingConformance(
    CapabilityCatalogHarnessMixin,
    RevisionBindingConformanceSuite,
):
    """F3 capability refs bind through the shared Step RB primitive."""


class TestCapabilityCatalogRevisionAuthority(RevisionBindingConformanceFixtures):
    """The authority adapter narrows and never invents freshness."""

    def authority(
        self,
        source: CapabilityCatalogGenerationPort,
    ) -> CapabilityCatalogRevisionAuthority:
        return CapabilityCatalogRevisionAuthority(source)

    async def test_a_foreign_feature_is_never_resolved(self) -> None:
        source = InMemoryCatalogGenerationSource()
        scope = self.scope(run_id=self.RUN_A)
        source.issue(scope)

        result = await self.authority(source).current_revision(
            feature=AgentQualityFeature.F5_CONTEXT_BUDGETING,
            scope=scope,
        )

        assert result.state is RevisionAuthorityState.UNKNOWN
        assert result.current_revision is None
        assert source.calls == 0

    async def test_an_active_answer_reports_the_generation_digest(self) -> None:
        source = InMemoryCatalogGenerationSource()
        scope = self.scope(run_id=self.RUN_A)
        generation = source.issue(scope)

        result = await self.authority(source).current_revision(
            feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
            scope=scope,
        )

        assert result.state is RevisionAuthorityState.ACTIVE
        assert result.current_revision is not None
        assert result.current_revision.value == generation.generation_digest

    async def test_a_tampered_live_generation_is_never_read_as_fresh(self) -> None:
        class TamperedSource:
            async def live_generation(
                self,
                *,
                scope: RevisionBoundScope,
            ) -> LiveCapabilityCatalogGeneration:
                honest = _generation(
                    selection_ref="task-policy-selection://live/1",
                    subject_fingerprint=scope.subject_fingerprint,
                )
                forged = CapabilityCatalogGeneration.model_construct(
                    **honest.model_dump(
                        exclude={"connector_scope_revision", "generation_digest"}
                    ),
                    connector_scope_revision="scope_10",
                    generation_digest=honest.generation_digest,
                )
                return LiveCapabilityCatalogGeneration.model_construct(
                    state=RevisionAuthorityState.ACTIVE,
                    generation=forged,
                )

        result = await self.authority(TamperedSource()).current_revision(
            feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
            scope=self.scope(run_id=self.RUN_A),
        )

        assert result.state is RevisionAuthorityState.UNAVAILABLE
        assert result.current_revision is None

    async def test_an_off_contract_source_answer_is_unavailable(self) -> None:
        class UntypedSource:
            async def live_generation(
                self,
                *,
                scope: RevisionBoundScope,
            ) -> LiveCapabilityCatalogGeneration:
                return {"state": "active"}  # type: ignore[return-value]

        result = await self.authority(UntypedSource()).current_revision(
            feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
            scope=self.scope(run_id=self.RUN_A),
        )

        assert result.state is RevisionAuthorityState.UNAVAILABLE

    async def test_a_source_failure_never_widens_the_outcome(self) -> None:
        class ExplodingSource:
            async def live_generation(
                self,
                *,
                scope: RevisionBoundScope,
            ) -> LiveCapabilityCatalogGeneration:
                raise RuntimeError("postgres://secret-host/catalog_generations")

        scope = self.scope(run_id=self.RUN_A, catalog_generation=self.GENERATION_A)
        ref = await CapabilityCatalogConformanceHarness().mint(scope)

        decision = await RevisionBindingRevalidator(
            self.authority(ExplodingSource())
        ).revalidate_at_use(
            ref,
            self.use_context(catalog_generation=self.GENERATION_A),
            CapabilityRefRevisionBinding.policy(),
        )

        assert decision.outcome is RevalidationOutcome.UNAVAILABLE
        assert decision.reason is RevalidationReason.AUTHORITY_ERROR
        assert "secret-host" not in decision.model_dump_json()

    @pytest.mark.parametrize(
        "state",
        [
            RevisionAuthorityState.REVOKED,
            RevisionAuthorityState.UNKNOWN,
            RevisionAuthorityState.UNAVAILABLE,
        ],
    )
    def test_a_non_active_answer_cannot_carry_a_generation(
        self,
        state: RevisionAuthorityState,
    ) -> None:
        with pytest.raises(ValueError, match="only an active"):
            LiveCapabilityCatalogGeneration(
                state=state,
                generation=_generation(
                    selection_ref=_SELECTION_REF,
                    subject_fingerprint="a" * 64,
                ),
            )

    def test_an_active_answer_must_carry_a_generation(self) -> None:
        with pytest.raises(ValueError, match="must carry the generation"):
            LiveCapabilityCatalogGeneration(state=RevisionAuthorityState.ACTIVE)


class TestCapabilityRefRevisionBinding:
    """Projection onto the primitive is exact, reproducible, and narrowing."""

    def test_a_built_catalog_ref_projects_onto_the_primitive(self) -> None:
        context = _context()
        catalog = _catalog(context)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        generation = catalog.generation
        assert generation is not None

        bound = CapabilityRefRevisionBinding.bound_ref(binding, run_id=context.run_id)

        assert bound.feature is AgentQualityFeature.F3_CAPABILITY_DISCOVERY
        assert bound.opaque_ref == binding.capability_ref
        assert bound.scope.subject_fingerprint == generation.subject_fingerprint
        assert bound.scope.run_id == context.run_id
        assert bound.scope.catalog_generation == generation.generation_ref
        assert bound.revision.value == generation.generation_digest
        assert bound.binding_is_intact is True

    def test_projection_is_reproducible(self) -> None:
        context = _context()
        catalog = _catalog(context)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        first = CapabilityRefRevisionBinding.bound_ref(binding, run_id=context.run_id)
        second = CapabilityRefRevisionBinding.bound_ref(binding, run_id=context.run_id)

        assert first == second

    def test_the_f3_policy_requires_run_and_generation_scope(self) -> None:
        policy = CapabilityRefRevisionBinding.policy()

        assert policy.feature is AgentQualityFeature.F3_CAPABILITY_DISCOVERY
        assert policy.required_dimensions == frozenset(
            {
                RevisionScopeDimension.SUBJECT,
                RevisionScopeDimension.RUN,
                RevisionScopeDimension.CATALOG_GENERATION,
            }
        )

    def test_an_unprojectable_run_id_fails_closed_without_echoing_it(self) -> None:
        context = _context()
        catalog = _catalog(context)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        with pytest.raises(CapabilityRefBindingError) as exc_info:
            CapabilityRefRevisionBinding.bound_ref(
                binding,
                run_id="run with spaces",
            )

        assert "run with spaces" not in str(exc_info.value)

    def test_a_projection_of_a_tampered_binding_is_refused(self) -> None:
        context = _context()
        catalog = _catalog(context)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        forged = CapabilityRefBinding.model_construct(
            schema_version=binding.schema_version,
            capability_ref=f"cap_{'0' * 32}",
            catalog_id=binding.catalog_id,
            catalog_revision=binding.catalog_revision,
            issued_generation=binding.issued_generation,
            binding_digest=binding.binding_digest,
        )

        with pytest.raises(CapabilityCatalogIdentityError, match="does not match"):
            CapabilityRefRevisionBinding.bound_ref(forged, run_id=context.run_id)


class TestBuiltCatalogRefsRevalidate:
    """End-to-end: a real built catalog mints refs the primitive can judge."""

    def revalidation(
        self,
        source: InMemoryCatalogGenerationSource,
        context: AgentRuntimeContext,
    ) -> CapabilityRefRevalidation:
        return CapabilityRefRevalidation(
            revalidator=RevisionBindingRevalidator(
                CapabilityCatalogRevisionAuthority(source)
            ),
            subject_fingerprint=AuthorizedCatalogBuilder(
                reference_key=_REFERENCE_KEY
            ).subject_fingerprint(context),
        )

    async def test_a_ref_from_the_live_generation_revalidates_current(self) -> None:
        context = _context()
        catalog = _catalog(context)
        generation = catalog.generation
        assert generation is not None
        source = InMemoryCatalogGenerationSource()
        source.publish(
            CapabilityRefRevisionBinding.scope_for(generation, run_id=context.run_id),
            generation,
        )

        decision = await self.revalidation(source, context).decide(
            binding=catalog.bind_ref(catalog.entries[0].capability_ref),
            run_id=context.run_id,
            live_generation=generation,
        )

        assert decision.outcome is RevalidationOutcome.CURRENT
        assert decision.reason is RevalidationReason.REVISION_MATCHES
        assert decision.require_current().value == generation.generation_digest

    async def test_a_ref_from_a_superseded_generation_is_refused(self) -> None:
        context = _context()
        catalog = _catalog(context)
        generation = catalog.generation
        assert generation is not None
        rebuilt = _catalog(
            context,
            selection_ref=f"task-policy-selection://run_1/research/sha256/{'d' * 64}",
        )
        assert rebuilt.generation is not None
        source = InMemoryCatalogGenerationSource()
        source.publish(
            CapabilityRefRevisionBinding.scope_for(generation, run_id=context.run_id),
            rebuilt.generation,
        )

        decision = await self.revalidation(source, context).decide(
            binding=catalog.bind_ref(catalog.entries[0].capability_ref),
            run_id=context.run_id,
            live_generation=generation,
        )

        assert decision.outcome is RevalidationOutcome.SUPERSEDED
        assert decision.reason is RevalidationReason.REVISION_CHANGED
        assert decision.is_current is False

    async def test_another_subjects_ref_is_out_of_scope(self) -> None:
        owner = _context(user_id="user_1")
        catalog = _catalog(owner)
        generation = catalog.generation
        assert generation is not None
        source = InMemoryCatalogGenerationSource()
        source.publish(
            CapabilityRefRevisionBinding.scope_for(generation, run_id=owner.run_id),
            generation,
        )

        decision = await self.revalidation(source, _context(user_id="user_2")).decide(
            binding=catalog.bind_ref(catalog.entries[0].capability_ref),
            run_id=owner.run_id,
            live_generation=generation,
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.SUBJECT_MISMATCH

    async def test_an_unknown_scope_never_returns_current(self) -> None:
        context = _context()
        catalog = _catalog(context)
        generation = catalog.generation
        assert generation is not None

        decision = await self.revalidation(
            InMemoryCatalogGenerationSource(), context
        ).decide(
            binding=catalog.bind_ref(catalog.entries[0].capability_ref),
            run_id=context.run_id,
            live_generation=generation,
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.UNKNOWN_REFERENCE

    async def test_the_authority_result_is_the_primitives_own_contract(self) -> None:
        source = InMemoryCatalogGenerationSource()
        scope = RevisionBoundScope(subject_fingerprint="a" * 64, run_id="run_1")
        source.issue(scope)

        result = await CapabilityCatalogRevisionAuthority(source).current_revision(
            feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
            scope=scope,
        )

        assert isinstance(result, RevisionAuthorityResult)
