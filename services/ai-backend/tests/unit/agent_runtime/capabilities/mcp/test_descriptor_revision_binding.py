"""RB.2 adoption proofs for the F8 descriptor-revision binding.

The shipped Step 7 behaviors are already asserted by ``test_freshness.py`` and
``test_revision_cache_composition.py``, and those files are deliberately
untouched: they are the parity proof.  This module asserts only what the
substitution newly makes true -- that the fingerprint cannot collide, that the
outcome projection is total, and that the generation barrier and the
fail-closed authority path are now the shared primitive's verdicts rather than
a private comparison.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpAuthMode,
    McpConnectionMetadata,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.descriptor_revision_binding import (
    McpDescriptorBindingIdentity,
    McpDescriptorRevisionAuthority,
    McpDescriptorRevisionBinder,
)
from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCache,
    McpDiscoveryCacheKey,
)
from agent_runtime.capabilities.mcp.freshness import (
    McpDescriptorBindingStates,
    McpDescriptorFreshnessRequest,
    McpDescriptorFreshnessState,
    McpDescriptorRevision,
    McpDescriptorSubject,
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    RevalidationDecision,
    RevalidationOutcome,
    RevalidationReason,
    RevisionAuthorityState,
    RevisionBoundScope,
    RevisionScopeDimension,
)


class _FlakyAuthority(McpDescriptorRevisionAuthority):
    """A production authority that can be made to fail like a real one."""

    def __init__(self) -> None:
        super().__init__()
        self.broken = False

    async def current_revision(self, **kwargs: Any) -> Any:
        if self.broken:
            raise RuntimeError("revision authority connection reset")
        return await super().current_revision(**kwargs)


class _RecordingBinder(McpDescriptorRevisionBinder):
    """Capture every decision the cache obtains from the shared primitive."""

    def __init__(self, authority: McpDescriptorRevisionAuthority | None = None) -> None:
        super().__init__(authority)
        self.decisions: list[RevalidationDecision] = []

    async def revalidate(self, **kwargs: Any) -> RevalidationDecision:
        decision = await super().revalidate(**kwargs)
        self.decisions.append(decision)
        return decision


class DescriptorBindingFixturesMixin:
    """Keys, requests, and descriptor records shared by the adoption cases."""

    ORG = "org-acme"
    USER = "user-alice"
    SERVER = "drive"

    def key(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        server_name: str | None = None,
    ) -> McpDiscoveryCacheKey:
        return McpDiscoveryCacheKey(
            server_name=server_name or self.SERVER,
            org_id=org_id or self.ORG,
            user_id=user_id or self.USER,
        )

    def request(self, *, revision: str = "revision-1") -> McpDescriptorFreshnessRequest:
        return McpDescriptorFreshnessRequest(
            server_name=self.SERVER,
            subject=McpDescriptorSubject(org_id=self.ORG, user_id=self.USER),
            revision=McpDescriptorRevision(value=revision),
        )

    def loaded(self, tool_name: str = "drive_search") -> LoadedMcpServer:
        return LoadedMcpServer(
            server_card=McpServerCard(
                name=self.SERVER,
                short_description="Test MCP server.",
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
                health=McpServerHealth.HEALTHY,
                load_cost=1,
            ),
            tools=(
                McpToolDescriptor(
                    name=tool_name,
                    description="A subject-visible test tool.",
                    input_schema={"type": "object"},
                    output_shape={"type": "object"},
                ),
            ),
            resources=(),
            connection_metadata=McpConnectionMetadata(
                server_name=self.SERVER,
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
            ),
        )

    def scope(self, *, generation: int = 0) -> RevisionBoundScope:
        return McpDescriptorBindingIdentity.scope(
            McpDescriptorBindingIdentity.subject_fingerprint(self.key()),
            catalog_generation=generation,
        )


class TestDescriptorSubjectFingerprint(DescriptorBindingFixturesMixin):
    """The fingerprint is stable, opaque, and structurally collision-free."""

    def test_fingerprint_is_deterministic_sha256_hex(self) -> None:
        fingerprint = McpDescriptorBindingIdentity.subject_fingerprint(self.key())

        assert fingerprint == McpDescriptorBindingIdentity.subject_fingerprint(
            self.key()
        )
        assert len(fingerprint) == 64
        assert set(fingerprint) <= set("0123456789abcdef")

    def test_every_subject_field_changes_the_fingerprint(self) -> None:
        base = McpDescriptorBindingIdentity.subject_fingerprint(self.key())
        variants = {
            McpDescriptorBindingIdentity.subject_fingerprint(self.key(org_id="org-b")),
            McpDescriptorBindingIdentity.subject_fingerprint(
                self.key(user_id="user-b")
            ),
            McpDescriptorBindingIdentity.subject_fingerprint(
                self.key(server_name="slack")
            ),
        }

        assert base not in variants
        assert len(variants) == 3

    def test_separator_injection_cannot_forge_another_subject(self) -> None:
        # The collision a naive f"{org}:{user}:{server}" fingerprint admits.
        shifted_left = McpDescriptorBindingIdentity.subject_fingerprint(
            self.key(org_id="acme:sales", user_id="u1", server_name="drive")
        )
        shifted_right = McpDescriptorBindingIdentity.subject_fingerprint(
            self.key(org_id="acme", user_id="sales:u1", server_name="drive")
        )
        trailing = McpDescriptorBindingIdentity.subject_fingerprint(
            self.key(org_id="acme", user_id="sales", server_name="u1:drive")
        )

        assert len({shifted_left, shifted_right, trailing}) == 3

    def test_fingerprint_never_carries_subject_text(self) -> None:
        fingerprint = McpDescriptorBindingIdentity.subject_fingerprint(self.key())

        assert self.ORG not in fingerprint
        assert self.USER not in fingerprint
        assert self.SERVER not in fingerprint


class TestDescriptorBoundRevision(DescriptorBindingFixturesMixin):
    """Revision binding preserves equality and only equality."""

    def test_equality_is_preserved_in_both_directions(self) -> None:
        same = McpDescriptorBindingIdentity.bound_revision("revision-1")
        again = McpDescriptorBindingIdentity.bound_revision("revision-1")
        other = McpDescriptorBindingIdentity.bound_revision("revision-2")

        assert same == again
        assert same != other

    @pytest.mark.parametrize(
        "revision",
        ["etag with spaces", 'W/"quoted-etag"', "rev\tsep", "révision-é", "x" * 512],
    )
    def test_provider_shaped_revisions_remain_representable(
        self, revision: str
    ) -> None:
        # McpDescriptorRevision admits these; a raw OpaqueRefValue would not,
        # so binding must digest rather than pass the value through.
        bound = McpDescriptorBindingIdentity.bound_revision(revision)

        assert len(bound.value) == 64
        assert bound != McpDescriptorBindingIdentity.bound_revision(f"{revision}-x")

    def test_bound_revision_never_carries_the_revision_body(self) -> None:
        bound = McpDescriptorBindingIdentity.bound_revision("secret-looking-revision")

        assert "secret-looking-revision" not in bound.value


class TestDescriptorOutcomeProjection:
    """The RB outcome projects onto F8's freshness vocabulary totally."""

    def test_every_revalidation_reason_is_mapped(self) -> None:
        assert set(McpDescriptorBindingStates.BY_REASON) == set(RevalidationReason)

    def test_only_a_confirmed_match_projects_onto_fresh(self) -> None:
        fresh = {
            reason
            for reason, state in McpDescriptorBindingStates.BY_REASON.items()
            if state is McpDescriptorFreshnessState.FRESH
        }

        assert fresh == {RevalidationReason.REVISION_MATCHES}

    def test_no_unusable_authority_projects_onto_reuse(self) -> None:
        unusable = (
            RevalidationReason.AUTHORITY_UNAVAILABLE,
            RevalidationReason.AUTHORITY_ERROR,
            RevalidationReason.AUTHORITY_CONTRACT_VIOLATION,
            RevalidationReason.AUTHORITY_REVOKED,
        )

        for reason in unusable:
            state = McpDescriptorBindingStates.BY_REASON[reason]
            assert state is McpDescriptorFreshnessState.NOT_TRACKED


class TestDescriptorGenerationBarrier(DescriptorBindingFixturesMixin):
    """The generation barrier is the primitive's, and it still holds."""

    def test_policy_requires_the_generation_dimension(self) -> None:
        required = McpDescriptorRevisionBinder.POLICY.required_dimensions

        assert RevisionScopeDimension.CATALOG_GENERATION in required
        assert RevisionScopeDimension.SUBJECT in required
        assert McpDescriptorRevisionBinder.POLICY.feature is (
            AgentQualityFeature.F8_MCP_CONTROL_PLANE
        )

    async def test_unfenced_reference_is_refused_before_the_authority(self) -> None:
        binder = McpDescriptorRevisionBinder()
        fingerprint = McpDescriptorBindingIdentity.subject_fingerprint(self.key())
        binder.authority.publish(subject_fingerprint=fingerprint, revision="revision-1")
        unfenced = McpDescriptorBindingIdentity.mint(
            scope=RevisionBoundScope(subject_fingerprint=fingerprint),
            revision="revision-1",
        )

        decision = await binder.revalidator.revalidate_at_use(
            unfenced,
            McpDescriptorBindingIdentity.use_context(fingerprint, catalog_generation=0),
            McpDescriptorRevisionBinder.POLICY,
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.SCOPE_DIMENSION_MISSING

    async def test_moved_barrier_refuses_even_when_the_revision_matches(self) -> None:
        binder = McpDescriptorRevisionBinder()

        decision = await binder.revalidate(
            key=self.key(),
            bound_revision="revision-1",
            bound_generation=0,
            trusted_revision="revision-1",
            observed_generation=1,
        )

        assert decision.outcome is RevalidationOutcome.OUT_OF_SCOPE
        assert decision.reason is RevalidationReason.CATALOG_GENERATION_MISMATCH
        assert decision.current_revision is None
        assert McpDescriptorBindingStates.BY_REASON[decision.reason] is (
            McpDescriptorFreshnessState.INVALIDATION_RACED
        )

    async def test_publication_barrier_verdict_comes_from_the_primitive(self) -> None:
        binder = _RecordingBinder()
        base = McpDiscoveryCache()
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=60,
            revision_binder=binder,
        )
        request = self.request()
        started = asyncio.Event()
        release = asyncio.Event()

        async def load() -> LoadedMcpServer:
            started.set()
            await release.wait()
            return self.loaded("stale_search")

        pending = asyncio.create_task(cache.get_or_load(request, load))
        await started.wait()
        await cache.invalidate_subject(request.subject, server_name=request.server_name)
        release.set()
        result = await pending

        assert result.record is None
        assert result.decision.state is McpDescriptorFreshnessState.INVALIDATION_RACED
        assert await base.get(request.cache_key()) is None
        assert [
            decision.reason
            for decision in binder.decisions
            if decision.reason is RevalidationReason.CATALOG_GENERATION_MISMATCH
        ] == [RevalidationReason.CATALOG_GENERATION_MISMATCH]

    async def test_read_barrier_refuses_an_invalidation_raced_in_flight_read(
        self,
    ) -> None:
        request = self.request()
        gate = asyncio.Event()

        class _SlowReadCache(McpDiscoveryCache):
            """A base read that observes the pre-invalidation record."""

            def __init__(self) -> None:
                super().__init__()
                self.slow = False

            async def get(self, key: McpDiscoveryCacheKey) -> LoadedMcpServer | None:
                record = await super().get(key)
                if self.slow:
                    self.slow = False
                    await gate.wait()
                return record

        base = _SlowReadCache()
        binder = _RecordingBinder()
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=60,
            revision_binder=binder,
        )
        await cache.put(request, self.loaded())
        base.slow = True

        pending = asyncio.create_task(cache.get(request))
        await asyncio.sleep(0)
        await cache.invalidate_subject(request.subject, server_name=request.server_name)
        gate.set()
        result = await pending

        assert result.record is None
        assert result.decision.state is McpDescriptorFreshnessState.INVALIDATION_RACED
        # The refusal is the primitive's, not a private comparison: the last
        # decision the cache obtained is the post-I/O barrier verdict.
        assert binder.decisions[-1].reason is (
            RevalidationReason.CATALOG_GENERATION_MISMATCH
        )
        assert binder.decisions[-1].outcome is RevalidationOutcome.OUT_OF_SCOPE


class TestDescriptorAuthorityFailsClosed(DescriptorBindingFixturesMixin):
    """An authority that cannot answer never reads as permission to reuse."""

    async def test_unreachable_authority_refuses_a_warm_entry(self) -> None:
        authority = _FlakyAuthority()
        base = McpDiscoveryCache()
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=60,
            revision_binder=McpDescriptorRevisionBinder(authority),
        )
        request = self.request()
        await cache.put(request, self.loaded())
        assert (await cache.get(request)).record is not None

        authority.broken = True
        refused = await cache.get(request)

        assert refused.record is None
        assert refused.decision.state is McpDescriptorFreshnessState.NOT_TRACKED
        assert await base.get(request.cache_key()) is None

    async def test_unavailable_authority_refuses_a_warm_entry(self) -> None:
        authority = McpDescriptorRevisionAuthority()
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=60,
            revision_binder=McpDescriptorRevisionBinder(authority),
        )
        request = self.request()
        await cache.put(request, self.loaded())

        authority.set_unavailable(unavailable=True)
        refused = await cache.get(request)

        assert refused.record is None
        assert refused.decision.state is McpDescriptorFreshnessState.NOT_TRACKED

    async def test_authority_projection_is_released_with_its_generation_state(
        self,
    ) -> None:
        authority = McpDescriptorRevisionAuthority()
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=60,
            revision_binder=McpDescriptorRevisionBinder(authority),
        )
        request = self.request()
        await cache.put(request, self.loaded())
        await cache.invalidate_subject(request.subject, server_name=request.server_name)

        result = await authority.current_revision(
            feature=AgentQualityFeature.F8_MCP_CONTROL_PLANE,
            scope=self.scope(),
        )

        assert result.state is RevisionAuthorityState.UNKNOWN
        assert result.current_revision is None

    async def test_foreign_feature_is_never_answered_as_active(self) -> None:
        authority = McpDescriptorRevisionAuthority()
        authority.publish(
            subject_fingerprint=McpDescriptorBindingIdentity.subject_fingerprint(
                self.key()
            ),
            revision="revision-1",
        )

        result = await authority.current_revision(
            feature=AgentQualityFeature.F5_CONTEXT_BUDGETING,
            scope=self.scope(),
        )

        assert result.state is RevisionAuthorityState.UNKNOWN
