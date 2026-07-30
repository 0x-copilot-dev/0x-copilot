"""The F5 evidence resolver registry and its bounded ``read_evidence`` path.

Every case here is deterministic: outcomes are asserted from observed call
counts and orderings, never from elapsed time.  The counters on the fake source
are load-bearing rather than decorative -- several properties this module must
hold ("deletion refuses before a byte is read", "an out-of-scope reference
never touches a store", "no decision survives its own call") are only provable
by counting what the registry did, not by inspecting what it returned.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from agent_runtime.context.evidence_registry import (
    EvidenceBooleanCoercion,
    EvidenceGrant,
    EvidenceGrantIndex,
    EvidenceGrantIndexFull,
    EvidenceKind,
    EvidenceLifecycle,
    EvidenceLifecycleTables,
    EvidenceMaterial,
    EvidenceMaterialState,
    EvidenceNotResolved,
    EvidenceReadBatch,
    EvidenceReadFact,
    EvidenceReadLimits,
    EvidenceReadOutcome,
    EvidenceReadRequest,
    EvidenceReadResult,
    EvidenceRefIdentity,
    EvidenceRefusal,
    EvidenceRefusalReason,
    EvidenceResolverAlreadyRegistered,
    EvidenceResolverDirectory,
    EvidenceResolverRegistry,
    EvidenceSelector,
    EvidenceSelectorUnsupported,
    EvidenceSpan,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    BoundRevision,
    RevalidationDecision,
    RevalidationOutcome,
    RevalidationPolicy,
    RevalidationReason,
    RevisionAuthorityState,
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionUseContext,
)


class FakeEvidenceSource:
    """A controllable stand-in for one source domain, with call counters.

    It records how often each question was asked so a test can assert that the
    registry consulted the source exactly as often as the property under test
    requires -- and, more importantly, that it did not consult it at all on the
    paths that must refuse first.
    """

    def __init__(
        self,
        *,
        kind: EvidenceKind = EvidenceKind.SOURCE,
    ) -> None:
        self._kind = kind
        self.lifecycles: dict[str, EvidenceLifecycle] = {}
        self.materials: dict[str, EvidenceMaterial] = {}
        self.lifecycle_calls = 0
        self.read_calls = 0
        self.read_budgets: list[int] = []
        self.read_selectors: list[EvidenceSelector | None] = []
        self.lifecycle_error: Exception | None = None
        self.read_error: Exception | None = None
        self.lifecycle_override: object | None = None
        self.material_override: object | None = None

    @property
    def kind(self) -> EvidenceKind:
        return self._kind

    def publish(
        self,
        locator: str,
        *,
        revision: str,
        content: str,
        is_complete: bool = True,
        span: EvidenceSpan | None = None,
    ) -> None:
        """Make ``locator`` readable at ``revision`` with exactly ``content``."""

        self.lifecycles[locator] = EvidenceLifecycle.available(revision=revision)
        self.materials[locator] = EvidenceMaterial.available(
            revision=revision,
            content=content,
            span=span,
            is_complete=is_complete,
        )

    def set_lifecycle_state(self, locator: str, state: EvidenceMaterialState) -> None:
        """Report ``state`` for ``locator`` from the probe onwards."""

        self.lifecycles[locator] = EvidenceLifecycle.for_state(state)
        self.materials[locator] = EvidenceMaterial.for_state(state)

    def set_material_state(self, locator: str, state: EvidenceMaterialState) -> None:
        """Leave the probe readable but make the read itself fail."""

        self.materials[locator] = EvidenceMaterial.for_state(state)

    async def current_lifecycle(
        self,
        *,
        scope: RevisionBoundScope,
        locator: str,
    ) -> EvidenceLifecycle:
        self.lifecycle_calls += 1
        if self.lifecycle_error is not None:
            raise self.lifecycle_error
        if self.lifecycle_override is not None:
            return self.lifecycle_override  # type: ignore[return-value]
        return self.lifecycles.get(
            locator,
            EvidenceLifecycle.for_state(EvidenceMaterialState.UNKNOWN),
        )

    async def read_material(
        self,
        *,
        scope: RevisionBoundScope,
        locator: str,
        selector: EvidenceSelector | None,
        max_chars: int,
    ) -> EvidenceMaterial:
        self.read_calls += 1
        self.read_budgets.append(max_chars)
        self.read_selectors.append(selector)
        if self.read_error is not None:
            raise self.read_error
        if self.material_override is not None:
            return self.material_override  # type: ignore[return-value]
        return self.materials.get(
            locator,
            EvidenceMaterial.for_state(EvidenceMaterialState.UNKNOWN),
        )


class NonConformingRevalidator:
    """A revalidator that claims currency without naming a revision.

    The shipped decision contract makes this unrepresentable, but the
    revalidator is an injected port, so a substituted implementation is not
    bound by those validators.  ``model_construct`` is the only way to build
    the shape a bad adapter could return, and it is what keeps the registry's
    second currency check from being a guard nothing can ever reach.
    """

    async def revalidate_at_use(
        self,
        ref: RevisionBoundRef,
        runtime_context: RevisionUseContext,
        policy: RevalidationPolicy,
        *,
        resolution_handle: object | None = None,
    ) -> RevalidationDecision:
        return RevalidationDecision.model_construct(
            schema_version=1,
            feature=policy.feature,
            outcome=RevalidationOutcome.CURRENT,
            reason=RevalidationReason.REVISION_MATCHES,
            ref_binding_digest=ref.computed_binding_digest,
            current_revision=None,
        )


class EvidenceRegistryFixtures:
    """Scopes, contexts, grants, and requests shared by every case."""

    SUBJECT: ClassVar[str] = "5f" * 32
    OTHER_SUBJECT: ClassVar[str] = "a7" * 32
    RUN: ClassVar[str] = "run-evidence-a"
    OTHER_RUN: ClassVar[str] = "run-evidence-b"
    LOCATOR: ClassVar[str] = "library://acme/handbook#p12"
    REVISION: ClassVar[str] = "rev-1"
    NEXT_REVISION: ClassVar[str] = "rev-2"
    CONTENT: ClassVar[str] = "the exact evidence span"

    def scope(
        self,
        *,
        subject_fingerprint: str | None = None,
        run_id: str | None = RUN,
    ) -> RevisionBoundScope:
        """Build the bound scope an evidence reference is minted for."""

        return RevisionBoundScope(
            subject_fingerprint=subject_fingerprint or self.SUBJECT,
            run_id=run_id,
        )

    def use_context(
        self,
        *,
        subject_fingerprint: str | None = None,
        run_id: str | None = RUN,
    ) -> RevisionUseContext:
        """Build the verified at-use facts the registry compares against."""

        return RevisionUseContext(
            subject_fingerprint=subject_fingerprint or self.SUBJECT,
            run_id=run_id,
        )

    def grant(
        self,
        *,
        kind: EvidenceKind = EvidenceKind.SOURCE,
        locator: str | None = None,
        revision: str | None = None,
        scope: RevisionBoundScope | None = None,
    ) -> EvidenceGrant:
        """Mint one evidence grant through the production issuing path."""

        return EvidenceGrant.issue(
            scope=scope or self.scope(),
            kind=kind,
            locator=locator or self.LOCATOR,
            revision=revision or self.REVISION,
        )

    def request(
        self,
        token: str,
        *,
        max_chars: int = 4_096,
        selector: EvidenceSelector | None = None,
    ) -> EvidenceReadRequest:
        """Build one model-facing read request."""

        return EvidenceReadRequest(
            token=token,
            max_chars=max_chars,
            selector=selector,
        )


class EvidenceRegistryBuilderMixin(EvidenceRegistryFixtures):
    """Assemble a registry, a published source, and a run's grant index."""

    def build(
        self,
        *,
        limits: EvidenceReadLimits | None = None,
        kind: EvidenceKind = EvidenceKind.SOURCE,
        register: bool = True,
        content: str | None = None,
        locator: str | None = None,
        revision: str | None = None,
        scope: RevisionBoundScope | None = None,
    ) -> tuple[
        EvidenceResolverRegistry,
        FakeEvidenceSource,
        EvidenceGrantIndex,
        EvidenceGrant,
    ]:
        """Return a registry wired to one published, granted evidence item."""

        source = FakeEvidenceSource(kind=kind)
        source.publish(
            locator or self.LOCATOR,
            revision=revision or self.REVISION,
            content=content if content is not None else self.CONTENT,
        )
        registry = EvidenceResolverRegistry(
            [source] if register else [],
            limits=limits,
        )
        grants = EvidenceGrantIndex()
        grant = grants.issue(
            self.grant(
                kind=kind,
                locator=locator,
                revision=revision,
                scope=scope,
            )
        )
        return registry, source, grants, grant


class TestEvidenceLifecycleTables:
    """The lifecycle-to-authority mapping is total and conservative."""

    def test_every_lifecycle_state_maps_to_an_authority_state(self) -> None:
        assert set(EvidenceLifecycleTables.AUTHORITY_STATE) == set(
            EvidenceMaterialState
        )
        assert set(EvidenceLifecycleTables.REFUSAL_REASON) == set(EvidenceMaterialState)

    def test_only_available_material_maps_to_an_active_authority(self) -> None:
        active = [
            state
            for state in EvidenceMaterialState
            if state.authority_state is RevisionAuthorityState.ACTIVE
        ]

        assert active == [EvidenceMaterialState.AVAILABLE]

    def test_only_available_material_carries_no_refusal(self) -> None:
        readable = [
            state for state in EvidenceMaterialState if state.refusal_reason is None
        ]

        assert readable == [EvidenceMaterialState.AVAILABLE]
        assert EvidenceMaterialState.AVAILABLE.is_readable is True

    def test_deletion_and_expiry_are_not_reported_as_revocation(self) -> None:
        # They refuse identically through the primitive, which is correct, but
        # they are different answers to a retention question and F5 keeps them
        # apart rather than collapsing them into "revoked".
        assert (
            EvidenceMaterialState.DELETED.authority_state
            is RevisionAuthorityState.UNKNOWN
        )
        assert (
            EvidenceMaterialState.RETENTION_EXPIRED.authority_state
            is RevisionAuthorityState.UNKNOWN
        )
        assert (
            EvidenceMaterialState.ACCESS_REVOKED.authority_state
            is RevisionAuthorityState.REVOKED
        )
        assert (
            EvidenceMaterialState.DELETED.refusal_reason
            is not EvidenceMaterialState.RETENTION_EXPIRED.refusal_reason
        )


class TestEvidenceRefIdentity(EvidenceRegistryFixtures):
    """Token derivation is injective, reproducible, and locator-free."""

    def test_token_round_trips_its_routing_kind(self) -> None:
        for kind in EvidenceKind:
            token = EvidenceRefIdentity.token(kind=kind, locator=self.LOCATOR)

            assert EvidenceRefIdentity.kind_of(token) is kind

    def test_malformed_tokens_route_nowhere(self) -> None:
        assert EvidenceRefIdentity.kind_of("not-a-token") is None
        assert EvidenceRefIdentity.kind_of("evidence-source-short") is None
        assert EvidenceRefIdentity.kind_of(f"evidence-invented-{'0' * 64}") is None

    def test_distinct_locators_never_share_a_token(self) -> None:
        first = EvidenceRefIdentity.token(kind=EvidenceKind.SOURCE, locator="a:b")
        second = EvidenceRefIdentity.token(
            kind=EvidenceKind.SOURCE,
            locator="a",
        )

        assert first != second

    def test_separator_injection_cannot_forge_a_subject(self) -> None:
        # The classic collision a naive f"{kind}:{locator}" admits.
        collided = EvidenceRefIdentity.subject_fingerprint(
            kind=EvidenceKind.SOURCE,
            locator="acme:sales",
            principal_fingerprint=self.SUBJECT,
        )
        distinct = EvidenceRefIdentity.subject_fingerprint(
            kind=EvidenceKind.SOURCE,
            locator="acme",
            principal_fingerprint=self.SUBJECT,
        )

        assert collided != distinct

    def test_the_same_locator_under_two_kinds_is_two_subjects(self) -> None:
        as_source = EvidenceRefIdentity.subject_fingerprint(
            kind=EvidenceKind.SOURCE,
            locator=self.LOCATOR,
            principal_fingerprint=self.SUBJECT,
        )
        as_artifact = EvidenceRefIdentity.subject_fingerprint(
            kind=EvidenceKind.ARTIFACT,
            locator=self.LOCATOR,
            principal_fingerprint=self.SUBJECT,
        )

        assert as_source != as_artifact

    def test_two_principals_never_share_an_evidence_subject(self) -> None:
        mine = EvidenceRefIdentity.subject_fingerprint(
            kind=EvidenceKind.SOURCE,
            locator=self.LOCATOR,
            principal_fingerprint=self.SUBJECT,
        )
        theirs = EvidenceRefIdentity.subject_fingerprint(
            kind=EvidenceKind.SOURCE,
            locator=self.LOCATOR,
            principal_fingerprint=self.OTHER_SUBJECT,
        )

        assert mine != theirs

    def test_minting_is_reproducible_from_the_bound_body(self) -> None:
        first = EvidenceRefIdentity.mint(
            scope=self.scope(),
            kind=EvidenceKind.SOURCE,
            locator=self.LOCATOR,
            revision=self.REVISION,
        )
        second = EvidenceRefIdentity.mint(
            scope=self.scope(),
            kind=EvidenceKind.SOURCE,
            locator=self.LOCATOR,
            revision=self.REVISION,
        )

        assert first == second
        assert first.binding_is_intact is True
        assert first.feature is AgentQualityFeature.F5_CONTEXT_BUDGETING

    def test_a_reference_never_carries_the_locator_it_stands_for(self) -> None:
        ref = EvidenceRefIdentity.mint(
            scope=self.scope(),
            kind=EvidenceKind.SOURCE,
            locator=self.LOCATOR,
            revision=self.REVISION,
        )

        assert self.LOCATOR not in ref.model_dump_json()

    def test_a_revision_is_bound_as_a_digest_not_as_its_provider_shape(self) -> None:
        bound = EvidenceRefIdentity.bound_revision('W/"etag with spaces"')

        assert bound == EvidenceRefIdentity.bound_revision('W/"etag with spaces"')
        assert "etag" not in bound.value
        assert len(bound.value) == 64


class TestEvidenceGrantIdentity(EvidenceRegistryFixtures):
    """A grant cannot record a locator its own token does not stand for."""

    def test_issue_binds_the_reference_and_the_record_together(self) -> None:
        grant = self.grant()

        assert grant.token == grant.ref.opaque_ref
        assert grant.ref.revision == EvidenceRefIdentity.bound_revision(self.REVISION)
        assert grant.ref.scope.run_id == self.RUN

    def test_a_grant_whose_locator_is_not_its_token_is_unrepresentable(self) -> None:
        grant = self.grant()

        with pytest.raises(ValidationError) as excinfo:
            EvidenceGrant(
                ref=grant.ref,
                kind=grant.kind,
                locator="library://acme/other",
            )

        assert EvidenceGrant.Messages.TOKEN_MISMATCH in str(excinfo.value)

    def test_a_grant_whose_kind_is_not_its_token_is_unrepresentable(self) -> None:
        grant = self.grant()

        with pytest.raises(ValidationError) as excinfo:
            EvidenceGrant(
                ref=grant.ref,
                kind=EvidenceKind.ARTIFACT,
                locator=grant.locator,
            )

        assert EvidenceGrant.Messages.TOKEN_MISMATCH in str(excinfo.value)

    def test_a_reference_minted_for_another_feature_is_refused(self) -> None:
        foreign = RevisionBoundRef.mint(
            feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
            opaque_ref=EvidenceRefIdentity.token(
                kind=EvidenceKind.SOURCE,
                locator=self.LOCATOR,
            ),
            scope=self.scope(),
            revision=BoundRevision(value="whatever"),
        )

        with pytest.raises(ValidationError) as excinfo:
            EvidenceGrant(
                ref=foreign,
                kind=EvidenceKind.SOURCE,
                locator=self.LOCATOR,
            )

        assert EvidenceGrant.Messages.WRONG_FEATURE in str(excinfo.value)

    def test_the_grant_index_is_bounded(self) -> None:
        index = EvidenceGrantIndex(max_grants=2)
        index.issue(self.grant(locator="a"))
        index.issue(self.grant(locator="b"))

        with pytest.raises(EvidenceGrantIndexFull) as excinfo:
            index.issue(self.grant(locator="c"))

        assert index.size == 2
        assert "at most 2" in str(excinfo.value)

    def test_reissuing_one_item_replaces_rather_than_accumulates(self) -> None:
        index = EvidenceGrantIndex(max_grants=1)
        index.issue(self.grant(revision=self.REVISION))
        reminted = index.issue(self.grant(revision=self.NEXT_REVISION))

        assert index.size == 1
        assert index.lookup(reminted.token) == reminted

    def test_revoking_visibility_removes_the_token(self) -> None:
        index = EvidenceGrantIndex()
        grant = index.issue(self.grant())

        index.revoke(grant.token)

        assert index.lookup(grant.token) is None
        assert index.size == 0


class TestEvidenceContracts(EvidenceRegistryFixtures):
    """The read contracts refuse every representable inconsistency."""

    def test_available_lifecycle_requires_a_revision(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceLifecycle(state=EvidenceMaterialState.AVAILABLE)

        assert EvidenceLifecycle.Messages.AVAILABLE_REQUIRES_REVISION in str(
            excinfo.value
        )

    def test_unreadable_lifecycle_cannot_claim_a_revision(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceLifecycle(
                state=EvidenceMaterialState.DELETED,
                revision=self.REVISION,
            )

        assert EvidenceLifecycle.Messages.REVISION_NOT_PERMITTED in str(excinfo.value)

    def test_unreadable_material_cannot_carry_content(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceMaterial(
                state=EvidenceMaterialState.DELETED,
                content=self.CONTENT,
            )

        assert EvidenceMaterial.Messages.CONTENT_NOT_PERMITTED in str(excinfo.value)

    def test_material_cannot_exceed_the_hard_character_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceMaterial.available(
                revision=self.REVISION,
                content="x" * (EvidenceReadLimits.MAX_CHARS_PER_READ + 1),
            )

    def test_a_read_request_cannot_ask_beyond_the_hard_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceReadRequest(
                token="evidence-source-" + "0" * 64,
                max_chars=EvidenceReadLimits.MAX_CHARS_PER_READ + 1,
            )

    def test_limits_cannot_make_a_single_read_unreachable(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceReadLimits(max_chars_per_read=1_000, max_total_chars=100)

        assert EvidenceReadLimits.Messages.BATCH_BELOW_READ in str(excinfo.value)

    def test_a_span_must_cover_at_least_one_character(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceSpan(start_char=10, end_char=10)

        assert EvidenceSpan.Messages.EMPTY_SPAN in str(excinfo.value)
        assert EvidenceSpan(start_char=10, end_char=14).length == 4

    def test_only_a_revalidation_refusal_carries_a_revalidation_reason(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceRefusal(
                reason=EvidenceRefusalReason.UNKNOWN_REFERENCE,
                revalidation_reason=RevalidationReason.REVISION_CHANGED,
            )

        assert EvidenceRefusal.Messages.REVALIDATION_REASON_NOT_PERMITTED in str(
            excinfo.value
        )

    def test_a_revalidation_refusal_must_say_which_reason(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceRefusal(reason=EvidenceRefusalReason.NOT_CURRENT)

        assert EvidenceRefusal.Messages.NOT_CURRENT_REQUIRES_REASON in str(
            excinfo.value
        )

    def test_a_result_cannot_be_both_resolved_and_refused(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            EvidenceReadResult(
                outcome=EvidenceReadOutcome.RESOLVED,
                refusal=EvidenceRefusal(reason=EvidenceRefusalReason.UNKNOWN_REFERENCE),
            )

        assert EvidenceReadResult.Messages.RESOLVED_REQUIRES_EVIDENCE in str(
            excinfo.value
        )

    def test_results_cannot_be_coerced_to_a_boolean(self) -> None:
        refused = EvidenceReadResult.refused_as(
            EvidenceRefusal(reason=EvidenceRefusalReason.UNKNOWN_REFERENCE)
        )

        with pytest.raises(EvidenceBooleanCoercion):
            bool(refused)
        with pytest.raises(EvidenceBooleanCoercion):
            bool(refused.outcome)

    def test_requiring_evidence_from_a_refusal_raises_the_typed_error(self) -> None:
        refused = EvidenceReadResult.refused_as(
            EvidenceRefusal(reason=EvidenceRefusalReason.MATERIAL_DELETED)
        )

        with pytest.raises(EvidenceNotResolved) as excinfo:
            refused.require_resolved()

        assert EvidenceRefusalReason.MATERIAL_DELETED.value in str(excinfo.value)
        assert refused.is_resolved is False
        assert refused.content_chars == 0


class TestEvidenceResolverDirectory:
    """Routing is closed and fixed at construction."""

    def test_two_resolvers_cannot_claim_one_kind(self) -> None:
        with pytest.raises(EvidenceResolverAlreadyRegistered) as excinfo:
            EvidenceResolverDirectory(
                [
                    FakeEvidenceSource(kind=EvidenceKind.SOURCE),
                    FakeEvidenceSource(kind=EvidenceKind.SOURCE),
                ]
            )

        assert excinfo.value.kind is EvidenceKind.SOURCE

    def test_the_directory_routes_only_what_was_registered(self) -> None:
        directory = EvidenceResolverDirectory(
            [FakeEvidenceSource(kind=EvidenceKind.ARTIFACT)]
        )

        assert directory.kinds == frozenset({EvidenceKind.ARTIFACT})
        assert directory.lookup(EvidenceKind.MEMORY) is None


class TestEvidenceRevisionAuthority(EvidenceRegistryBuilderMixin):
    """The authority projects a source answer and never invents one."""

    async def test_a_missing_handle_resolves_to_unknown(self) -> None:
        registry, _source, _grants, _grant = self.build()

        result = await registry.authority.current_revision(
            feature=AgentQualityFeature.F5_CONTEXT_BUDGETING,
            scope=self.scope(),
        )

        assert result.state is RevisionAuthorityState.UNKNOWN
        assert result.current_revision is None

    async def test_another_domains_handle_resolves_to_unknown(self) -> None:
        registry, _source, _grants, _grant = self.build()

        result = await registry.authority.current_revision(
            feature=AgentQualityFeature.F5_CONTEXT_BUDGETING,
            scope=self.scope(),
            resolution_handle=object(),
        )

        assert result.state is RevisionAuthorityState.UNKNOWN

    async def test_a_foreign_feature_resolves_to_unknown(self) -> None:
        registry, source, _grants, grant = self.build()

        result = await registry.authority.current_revision(
            feature=AgentQualityFeature.F8_MCP_CONTROL_PLANE,
            scope=self.scope(),
            resolution_handle=grant.resolution_handle(source),
        )

        assert result.state is RevisionAuthorityState.UNKNOWN
        assert source.lifecycle_calls == 0

    async def test_the_probe_asks_its_source_at_most_once(self) -> None:
        _registry, source, _grants, grant = self.build()
        probe = grant.resolution_handle(source).probe

        assert probe.observed is None
        first = await probe.resolve()
        second = await probe.resolve()

        assert first == second
        assert source.lifecycle_calls == 1
        assert probe.observed == first

    async def test_a_fresh_probe_is_built_for_every_read(self) -> None:
        _registry, source, _grants, grant = self.build()

        first = grant.resolution_handle(source).probe
        second = grant.resolution_handle(source).probe

        assert first is not second
        assert second.observed is None


class TestEvidenceReadResolution(EvidenceRegistryBuilderMixin):
    """The resolving path admits exactly the bytes the source produced."""

    async def test_a_current_reference_resolves_to_bounded_material(self) -> None:
        registry, source, grants, grant = self.build()

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        evidence = result.require_resolved()
        assert result.is_resolved is True
        assert evidence.content == self.CONTENT
        assert evidence.content_chars == len(self.CONTENT)
        assert evidence.kind is EvidenceKind.SOURCE
        assert evidence.ref_binding_digest == grant.ref.binding_digest
        assert evidence.confirmed_revision == EvidenceRefIdentity.bound_revision(
            self.REVISION
        )
        assert source.lifecycle_calls == 1
        assert source.read_calls == 1

    async def test_the_resolver_is_told_the_budget_and_the_selector(self) -> None:
        registry, source, grants, grant = self.build()
        selector = EvidenceSelector(span=EvidenceSpan(start_char=2, end_char=9))

        await registry.read_one(
            self.request(grant.token, max_chars=64, selector=selector),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert source.read_budgets == [64]
        assert source.read_selectors == [selector]

    async def test_truncated_material_is_reported_as_incomplete(self) -> None:
        registry, source, grants, grant = self.build()
        source.publish(
            self.LOCATOR,
            revision=self.REVISION,
            content=self.CONTENT,
            is_complete=False,
            span=EvidenceSpan(start_char=0, end_char=len(self.CONTENT)),
        )

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        evidence = result.require_resolved()
        assert evidence.is_complete is False
        assert evidence.span == EvidenceSpan(start_char=0, end_char=len(self.CONTENT))

    async def test_a_resolved_read_projects_a_body_free_fact(self) -> None:
        registry, _source, grants, grant = self.build()

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )
        fact = EvidenceReadFact.from_result(result)

        serialized = fact.model_dump_json()
        assert self.CONTENT not in serialized
        assert self.LOCATOR not in serialized
        assert fact.outcome is EvidenceReadOutcome.RESOLVED
        assert fact.content_digest == result.require_resolved().content_digest
        assert fact.content_chars == len(self.CONTENT)
        assert fact.feature is AgentQualityFeature.F5_CONTEXT_BUDGETING

    async def test_a_refused_read_projects_a_reason_only_fact(self) -> None:
        registry, source, grants, grant = self.build()
        source.set_lifecycle_state(self.LOCATOR, EvidenceMaterialState.DELETED)

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )
        fact = EvidenceReadFact.from_result(result)

        assert fact.outcome is EvidenceReadOutcome.REFUSED
        assert fact.reason is EvidenceRefusalReason.NOT_CURRENT
        assert fact.material_state is EvidenceMaterialState.DELETED
        assert fact.content_digest is None
        assert fact.content_chars == 0


class TestEvidenceReadRefusals(EvidenceRegistryBuilderMixin):
    """Every non-resolving path refuses with a typed, body-free reason."""

    async def test_a_token_this_run_never_held_refuses_without_any_source_call(
        self,
    ) -> None:
        registry, source, grants, _grant = self.build()
        guessed = EvidenceRefIdentity.token(
            kind=EvidenceKind.SOURCE,
            locator="library://acme/somebody-elses-doc",
        )

        result = await registry.read_one(
            self.request(guessed),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.UNKNOWN_REFERENCE
        # Guessing a locator must reveal nothing at all, not even that the
        # guess parsed as a routable kind.
        assert result.refusal.kind is None
        assert result.refusal.ref_binding_digest is None
        assert source.lifecycle_calls == 0
        assert source.read_calls == 0

    async def test_an_unregistered_kind_refuses_before_any_authority_call(self) -> None:
        registry, source, grants, grant = self.build(register=False)

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.UNREGISTERED_KIND
        assert result.refusal.kind is EvidenceKind.SOURCE
        assert source.lifecycle_calls == 0

    async def test_a_superseded_source_refuses_and_reads_nothing(self) -> None:
        registry, source, grants, grant = self.build()
        source.publish(
            self.LOCATOR,
            revision=self.NEXT_REVISION,
            content="the edited document",
        )

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.NOT_CURRENT
        assert result.refusal.revalidation_reason is RevalidationReason.REVISION_CHANGED
        assert source.read_calls == 0

    @pytest.mark.parametrize(
        ("state", "expected_reason"),
        [
            (EvidenceMaterialState.DELETED, RevalidationReason.UNKNOWN_REFERENCE),
            (
                EvidenceMaterialState.RETENTION_EXPIRED,
                RevalidationReason.UNKNOWN_REFERENCE,
            ),
            (
                EvidenceMaterialState.ACCESS_REVOKED,
                RevalidationReason.AUTHORITY_REVOKED,
            ),
            (
                EvidenceMaterialState.UNAVAILABLE,
                RevalidationReason.AUTHORITY_UNAVAILABLE,
            ),
        ],
    )
    async def test_unreadable_material_refuses_before_a_byte_is_read(
        self,
        state: EvidenceMaterialState,
        expected_reason: RevalidationReason,
    ) -> None:
        registry, source, grants, grant = self.build()
        source.set_lifecycle_state(self.LOCATOR, state)

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.NOT_CURRENT
        assert result.refusal.revalidation_reason is expected_reason
        # The primitive's four-state vocabulary cannot tell deletion from
        # retention expiry; the lifecycle carried alongside it can.
        assert result.refusal.material_state is state
        assert source.read_calls == 0

    async def test_deletion_is_distinguishable_from_a_reference_never_issued(
        self,
    ) -> None:
        registry, source, grants, grant = self.build()
        source.set_lifecycle_state(self.LOCATOR, EvidenceMaterialState.DELETED)

        deleted = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )
        never_issued = await registry.read_one(
            self.request(
                EvidenceRefIdentity.token(
                    kind=EvidenceKind.SOURCE,
                    locator="library://acme/never-granted",
                )
            ),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert deleted.refusal is not None
        assert never_issued.refusal is not None
        assert deleted.refusal.reason is not never_issued.refusal.reason
        assert deleted.refusal.material_state is EvidenceMaterialState.DELETED
        assert never_issued.refusal.material_state is None

    async def test_material_deleted_during_the_read_never_returns_stale_bytes(
        self,
    ) -> None:
        registry, source, grants, grant = self.build()
        # The probe still reports the reference current; the read finds it gone.
        source.set_material_state(self.LOCATOR, EvidenceMaterialState.DELETED)

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.MATERIAL_DELETED
        assert result.refusal.material_state is EvidenceMaterialState.DELETED
        assert source.read_calls == 1

    async def test_material_edited_during_the_read_is_refused_not_admitted(
        self,
    ) -> None:
        registry, source, grants, grant = self.build()
        edited = "content written after the decision"
        source.materials[self.LOCATOR] = EvidenceMaterial.available(
            revision=self.NEXT_REVISION,
            content=edited,
        )

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.MATERIAL_SUPERSEDED
        assert edited not in result.model_dump_json()

    async def test_a_cross_run_reference_never_touches_the_source(self) -> None:
        registry, source, grants, grant = self.build()

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(run_id=self.OTHER_RUN),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.revalidation_reason is RevalidationReason.RUN_MISMATCH
        assert source.lifecycle_calls == 0
        assert source.read_calls == 0

    async def test_a_cross_subject_reference_never_touches_the_source(self) -> None:
        registry, source, grants, grant = self.build()

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(subject_fingerprint=self.OTHER_SUBJECT),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.revalidation_reason is RevalidationReason.SUBJECT_MISMATCH
        assert source.lifecycle_calls == 0

    async def test_a_run_less_reference_is_refused_structurally(self) -> None:
        registry, source, grants, grant = self.build(scope=self.scope(run_id=None))

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert (
            result.refusal.revalidation_reason
            is RevalidationReason.SCOPE_DIMENSION_MISSING
        )
        assert source.lifecycle_calls == 0

    async def test_a_tampered_reference_never_touches_the_source(self) -> None:
        registry, source, grants, grant = self.build()
        # ``model_copy`` rebuilds without validation, which is exactly how a
        # reference can leave the minting path with a body its digest no longer
        # covers.  The index accepts it; the primitive does not.
        forged = grant.model_copy(
            update={"ref": grant.ref.model_copy(update={"binding_digest": "f" * 64})}
        )
        grants.issue(forged)

        result = await registry.read_one(
            self.request(forged.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert (
            result.refusal.revalidation_reason
            is RevalidationReason.BINDING_DIGEST_MISMATCH
        )
        assert source.lifecycle_calls == 0

    async def test_a_lifecycle_probe_failure_fails_closed(self) -> None:
        registry, source, grants, grant = self.build()
        source.lifecycle_error = RuntimeError("postgres: connection refused at /var/x")

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert (
            result.refusal.revalidation_reason
            is RevalidationReason.AUTHORITY_UNAVAILABLE
        )
        assert result.refusal.material_state is EvidenceMaterialState.UNAVAILABLE
        assert "postgres" not in result.model_dump_json()

    async def test_a_lifecycle_answer_of_the_wrong_type_fails_closed(self) -> None:
        registry, source, grants, grant = self.build()
        source.lifecycle_override = {"state": "available", "revision": "rev-1"}

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.material_state is EvidenceMaterialState.UNAVAILABLE

    async def test_a_read_failure_never_leaks_internal_detail(self) -> None:
        registry, source, grants, grant = self.build()
        source.read_error = RuntimeError("s3 GetObject denied for bucket acme-private")

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.RESOLVER_ERROR
        assert "acme-private" not in result.model_dump_json()

    async def test_an_unsupported_selector_refuses_rather_than_widening(self) -> None:
        registry, source, grants, grant = self.build()
        source.read_error = EvidenceSelectorUnsupported()

        result = await registry.read_one(
            self.request(
                grant.token,
                selector=EvidenceSelector(locator_hint="section-4"),
            ),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.SELECTOR_UNSUPPORTED

    async def test_a_resolver_returning_the_wrong_type_is_refused(self) -> None:
        registry, source, grants, grant = self.build()
        source.material_override = {"content": "not a contract"}

        result = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert (
            result.refusal.reason is EvidenceRefusalReason.RESOLVER_CONTRACT_VIOLATION
        )


class TestEvidenceReadBounding(EvidenceRegistryBuilderMixin):
    """No resolver can return unbounded bytes into the model path."""

    async def test_a_resolver_that_ignores_its_budget_is_refused(self) -> None:
        oversized = "x" * 50
        registry, _source, grants, grant = self.build(content=oversized)

        result = await registry.read_one(
            self.request(grant.token, max_chars=10),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.READ_LIMIT_EXCEEDED
        assert oversized not in result.model_dump_json()

    async def test_the_registry_limit_narrows_a_larger_request(self) -> None:
        registry, source, grants, grant = self.build(
            limits=EvidenceReadLimits(max_chars_per_read=16, max_total_chars=64),
            content="0123456789",
        )

        await registry.read_one(
            self.request(grant.token, max_chars=4_096),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert source.read_budgets == [16]

    async def test_a_batch_returns_one_ordered_result_per_request(self) -> None:
        registry, _source, grants, grant = self.build()
        batch = EvidenceReadBatch(
            requests=(
                self.request(grant.token),
                self.request(
                    EvidenceRefIdentity.token(
                        kind=EvidenceKind.SOURCE,
                        locator="library://acme/absent",
                    )
                ),
                self.request(grant.token),
            )
        )

        batch_result = await registry.read_evidence(
            batch,
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert len(batch_result.results) == 3
        assert batch_result.resolved_count == 2
        assert batch_result.refused_count == 1
        assert batch_result.results[1].refusal is not None
        assert batch_result.total_chars == 2 * len(self.CONTENT)

    async def test_an_exhausted_aggregate_budget_refuses_the_remainder(self) -> None:
        content = "0123456789"
        registry, source, grants, grant = self.build(
            limits=EvidenceReadLimits(max_chars_per_read=10, max_total_chars=15),
            content=content,
        )
        batch = EvidenceReadBatch(
            requests=(self.request(grant.token), self.request(grant.token))
        )

        batch_result = await registry.read_evidence(
            batch,
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert batch_result.results[0].is_resolved is True
        assert batch_result.results[1].refusal is not None
        assert (
            batch_result.results[1].refusal.reason
            is EvidenceRefusalReason.BATCH_LIMIT_EXHAUSTED
        )
        assert batch_result.total_chars == len(content)
        # The second request never reached the source at all.
        assert source.read_calls == 1

    async def test_the_batch_reference_count_is_bounded(self) -> None:
        registry, _source, grants, grant = self.build(
            limits=EvidenceReadLimits(max_refs_per_batch=1),
        )
        batch = EvidenceReadBatch(
            requests=(self.request(grant.token), self.request(grant.token))
        )

        batch_result = await registry.read_evidence(
            batch,
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert batch_result.resolved_count == 1
        assert batch_result.results[1].refusal is not None
        assert (
            batch_result.results[1].refusal.reason
            is EvidenceRefusalReason.BATCH_LIMIT_EXHAUSTED
        )

    async def test_a_batch_cannot_exceed_the_hard_reference_ceiling(self) -> None:
        _registry, _source, _grants, grant = self.build()

        with pytest.raises(ValidationError):
            EvidenceReadBatch(
                requests=tuple(
                    self.request(grant.token)
                    for _ in range(EvidenceReadLimits.MAX_REFS_PER_BATCH + 1)
                )
            )


class TestEvidenceReauthorization(EvidenceRegistryBuilderMixin):
    """No authorization decision survives the call that produced it."""

    async def test_every_read_re_asks_the_source(self) -> None:
        registry, source, grants, grant = self.build()
        request = self.request(grant.token)
        context = self.use_context()

        for _ in range(3):
            result = await registry.read_one(
                request,
                runtime_context=context,
                grants=grants,
            )
            assert result.is_resolved is True

        assert source.lifecycle_calls == 3
        assert source.read_calls == 3

    async def test_a_source_that_moves_between_reads_stops_resolving(self) -> None:
        registry, source, grants, grant = self.build()
        request = self.request(grant.token)
        context = self.use_context()

        first = await registry.read_one(request, runtime_context=context, grants=grants)
        source.publish(
            self.LOCATOR,
            revision=self.NEXT_REVISION,
            content="edited",
        )
        second = await registry.read_one(
            request, runtime_context=context, grants=grants
        )

        assert first.is_resolved is True
        assert second.refusal is not None
        assert second.refusal.revalidation_reason is RevalidationReason.REVISION_CHANGED

    async def test_revocation_between_reads_takes_effect_immediately(self) -> None:
        registry, source, grants, grant = self.build()
        request = self.request(grant.token)
        context = self.use_context()

        first = await registry.read_one(request, runtime_context=context, grants=grants)
        source.set_lifecycle_state(self.LOCATOR, EvidenceMaterialState.ACCESS_REVOKED)
        second = await registry.read_one(
            request, runtime_context=context, grants=grants
        )
        source.publish(self.LOCATOR, revision=self.REVISION, content=self.CONTENT)
        restored = await registry.read_one(
            request, runtime_context=context, grants=grants
        )

        assert first.is_resolved is True
        assert second.refusal is not None
        assert second.refusal.material_state is EvidenceMaterialState.ACCESS_REVOKED
        assert restored.is_resolved is True

    async def test_currency_without_a_revision_admits_nothing(self) -> None:
        _registry, source, grants, grant = self.build()
        substituted = EvidenceResolverRegistry(
            [source],
            revalidator=NonConformingRevalidator(),
        )

        result = await substituted.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert result.refusal is not None
        assert result.refusal.reason is EvidenceRefusalReason.NOT_CURRENT
        assert source.read_calls == 0

    async def test_a_second_registry_over_the_same_source_agrees(self) -> None:
        # Two registries sharing one source must reach the same verdict: the
        # decision lives in the source, not in whichever registry asked first.
        registry, source, grants, grant = self.build()
        other = EvidenceResolverRegistry([source])
        source.set_lifecycle_state(self.LOCATOR, EvidenceMaterialState.DELETED)

        first = await registry.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )
        second = await other.read_one(
            self.request(grant.token),
            runtime_context=self.use_context(),
            grants=grants,
        )

        assert first.refusal is not None
        assert second.refusal is not None
        assert first.refusal.material_state is second.refusal.material_state
