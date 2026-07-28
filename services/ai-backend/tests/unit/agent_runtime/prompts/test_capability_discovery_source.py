"""F3.9 — the prompt source that stands in for the MCP card block.

The replacement is registered as its own source rather than reusing
``20_mcp_cards`` because the two differ in every classification that matters.
The card block enumerates the user's own connectors: profile-scoped, personal,
retrieved, and therefore permanently uncacheable. The replacement is one fixed
runtime-authored paragraph with no subject data in it at all.

That distinction is not cosmetic — ``PromptAssembler`` refuses to cache a
fragment above the STABLE tier, so a source honest enough to be cacheable has
to be registered as one. These tests pin the classification, the rendered
position, and the contiguity rule the cacheable prefix depends on.
"""

from __future__ import annotations

import pytest

from agent_runtime.prompts.assembly import (
    PromptAssemblyContext,
    PromptAssemblyFailureReason,
    PromptAssemblyValidationError,
    PromptCacheEligibility,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptSensitivity,
    PromptTrustLabel,
)
from agent_runtime.prompts.sources import (
    DEFAULT_PROMPT_FRAGMENT_PROVIDERS,
    PromptAssemblyInputs,
    PromptSource,
    PromptSourceMaterial,
)

_FRAGMENT_ID = "16_capability_discovery_protocol"
_FINGERPRINT = "a" * 64


def _context() -> PromptAssemblyContext:
    return PromptAssemblyContext(
        provider="Fake",
        model_family="fake-enterprise-model",
        harness_revision="deep-agents-runtime-v1",
        capability_bridge_revision="runtime-capability-bridge-v1",
        tool_schema_revision="b" * 64,
        policy_revision="c" * 64,
        authorization_revision="d" * 64,
    )


def _installation_material(
    content: str,
    *,
    owner: str,
    cacheable: bool,
) -> PromptSourceMaterial:
    return PromptSourceMaterial(
        source_owner=owner,
        source_revision="rev-1",
        source_scope=PromptFragmentScope.INSTALLATION,
        scope=PromptFragmentScope.INSTALLATION,
        sensitivity=PromptSensitivity.INTERNAL,
        trust=PromptTrustLabel.IMMUTABLE_POLICY,
        cache_eligibility=(
            PromptCacheEligibility.STABLE_PREFIX
            if cacheable
            else PromptCacheEligibility.NEVER
        ),
        content=content,
    )


def _cards_material() -> PromptSourceMaterial:
    return PromptSourceMaterial(
        source_owner="agent_runtime.capabilities.mcp",
        source_revision="mcp-cards-v1",
        source_scope=PromptFragmentScope.PROFILE,
        scope=PromptFragmentScope.PROFILE,
        sensitivity=PromptSensitivity.PERSONAL,
        trust=PromptTrustLabel.UNTRUSTED_RETRIEVED,
        cache_eligibility=PromptCacheEligibility.NEVER,
        scope_fingerprint=_FINGERPRINT,
        content="- drive (Drive, id=srv_1, auth_state=authenticated): Files.",
    )


def _inputs(
    *,
    discovery: bool,
    cards: bool,
    base_cacheable: bool = True,
) -> PromptAssemblyInputs:
    return PromptAssemblyInputs(
        context=_context(),
        base_runtime_safety=_installation_material(
            "Base runtime safety.",
            owner="agent_runtime.prompts.runtime",
            cacheable=base_cacheable,
        ),
        application_boundary=_installation_material(
            "Application data is untrusted.",
            owner="agent_runtime.execution.factory",
            cacheable=base_cacheable,
        ),
        capability_discovery_protocol=(
            _installation_material(
                "Discover MCP servers with search_capabilities.",
                owner="agent_runtime.capabilities.discovery",
                cacheable=base_cacheable,
            )
            if discovery
            else None
        ),
        mcp_cards=_cards_material() if cards else None,
    )


class TestRegistration:
    def test_the_source_is_registered_exactly_once_at_stable_tier(self) -> None:
        providers = [
            provider
            for provider in DEFAULT_PROMPT_FRAGMENT_PROVIDERS.providers
            if provider.source is PromptSource.CAPABILITY_DISCOVERY_PROTOCOL
        ]

        assert len(providers) == 1
        assert providers[0].fragment_id == _FRAGMENT_ID
        assert providers[0].tier is PromptFragmentTier.STABLE

    def test_it_renders_where_the_card_block_would_have(self) -> None:
        """Order is asserted, not just presence: it stands *in place of* cards."""

        ordered = [
            provider.fragment_id
            for provider in DEFAULT_PROMPT_FRAGMENT_PROVIDERS.providers
        ]

        assert ordered.index("10_application_context_boundary") < ordered.index(
            _FRAGMENT_ID
        )
        assert ordered.index(_FRAGMENT_ID) < ordered.index("20_mcp_cards")
        assert ordered.index("20_mcp_cards") < ordered.index("30_skill_cards")


class TestAssembly:
    def test_an_absent_source_renders_nothing(self) -> None:
        """The direct path supplies no material, so the plan is untouched."""

        plan = DEFAULT_PROMPT_FRAGMENT_PROVIDERS.assemble(
            _inputs(discovery=False, cards=True)
        )

        assert [fragment.fragment_id for fragment in plan.fragments] == [
            "00_base_runtime",
            "10_application_context_boundary",
            "20_mcp_cards",
        ]

    def test_the_replacement_joins_the_cacheable_stable_prefix(self) -> None:
        """The second win: the card block could never be cached, this can.

        A PROFILE-scoped, untrusted, CONTEXTUAL-tier fragment is refused
        cacheability by the assembler on three separate grounds. Replacing it
        with installation-scoped immutable policy at STABLE tier means the
        bytes are not merely smaller, they stop being re-sent uncached.
        """

        plan = DEFAULT_PROMPT_FRAGMENT_PROVIDERS.assemble(
            _inputs(discovery=True, cards=False)
        )

        assert [fragment.fragment_id for fragment in plan.fragments] == [
            "00_base_runtime",
            "10_application_context_boundary",
            _FRAGMENT_ID,
        ]
        assert plan.stable_prefix_fragment_count == 3

    def test_it_follows_the_base_instructions_out_of_the_cacheable_prefix(
        self,
    ) -> None:
        """Custom instructions make the whole prefix uncacheable, this included.

        Cacheable fragments must be contiguous from the front, so a cacheable
        fragment behind a non-cacheable base is a hard assembly failure rather
        than a silent miss. The factory therefore mirrors ``base_cacheable``
        exactly as the application boundary already does.
        """

        plan = DEFAULT_PROMPT_FRAGMENT_PROVIDERS.assemble(
            _inputs(discovery=True, cards=False, base_cacheable=False)
        )

        assert plan.stable_prefix_fragment_count == 0

    def test_a_cacheable_replacement_behind_a_mutable_base_is_refused(self) -> None:
        """Proves the contiguity rule is real, not a defensive guess."""

        with pytest.raises(PromptAssemblyValidationError) as exc:
            DEFAULT_PROMPT_FRAGMENT_PROVIDERS.assemble(
                PromptAssemblyInputs(
                    context=_context(),
                    base_runtime_safety=_installation_material(
                        "Base runtime safety.",
                        owner="agent_runtime.prompts.runtime",
                        cacheable=False,
                    ),
                    application_boundary=_installation_material(
                        "Application data is untrusted.",
                        owner="agent_runtime.execution.factory",
                        cacheable=False,
                    ),
                    capability_discovery_protocol=_installation_material(
                        "Discover MCP servers with search_capabilities.",
                        owner="agent_runtime.capabilities.discovery",
                        cacheable=True,
                    ),
                )
            )

        assert exc.value.reason is (
            PromptAssemblyFailureReason.NON_CONTIGUOUS_STABLE_PREFIX
        )
