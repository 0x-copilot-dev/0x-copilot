"""SMELL-09 (b) — ``TRUSTED_PROVIDER`` must have a production source.

F6.1 built a four-source precedence chain and only ``PRODUCT_CATALOG`` ever had
anything feeding it, so the chain's strongest claim — a less authoritative
source may only narrow — had no production path to hold on. These tests drive
the real MCP annotation tier end to end through the real graph policy source,
and they pin both directions: a hint that narrows takes effect, and a hint that
would make a capability look safer cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from agent_runtime.capabilities.concurrency import (
    CapabilityConcurrencyDeclaration,
    ConcurrencyDescriptorParser,
    ConcurrencyMode,
    ConcurrencyPolicyField,
    ConcurrencyPolicyResolution,
    ConcurrencyRejectionReason,
    ConcurrencyScope,
    IdempotencyKind,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    DeclaredConcurrencyPolicySource,
    graph_capability_ref,
)
from agent_runtime.capabilities.concurrency.provider_hints import (
    McpAnnotationProviderHints,
    ProviderConcurrencyHints,
    ProviderNarrowedPolicySource,
)
from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)

SERVER = "linear"
TOOL = "create_issue"
CAPABILITY_KEY = f"{SERVER}:{TOOL}"
MCP_TOOL = "call_mcp_tool"
MCP_ARGUMENTS: Mapping[str, Any] = {"server_name": SERVER, "tool_name": TOOL}


class StaticHints:
    """A hint source that answers one capability, for the pure-fold tests."""

    def __init__(self, hints: ProviderConcurrencyHints | None) -> None:
        self._hints = hints
        self.asked: list[str] = []

    def hints_for(self, *, capability_key: str) -> ProviderConcurrencyHints | None:
        self.asked.append(capability_key)
        return self._hints


class ProviderFixtureMixin:
    """One real catalog source, wrapped by the tier under test."""

    def catalog(self, **fields: object) -> DeclaredConcurrencyPolicySource:
        """Return a real graph policy source declaring one MCP capability."""

        declaration = CapabilityConcurrencyDeclaration(
            capability_ref=graph_capability_ref(CAPABILITY_KEY),
            source=PolicySource.PRODUCT_CATALOG,
            **fields,
        )
        return DeclaredConcurrencyPolicySource({CAPABILITY_KEY: (declaration,)})

    def parallel_read_catalog(self) -> DeclaredConcurrencyPolicySource:
        """The widest thing an operator can honestly write about a read."""

        return self.catalog(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            idempotency=IdempotencyKind.NATURAL,
            rate_limit_scope=ConcurrencyScope.CONNECTOR,
        )

    def resolve(
        self,
        catalog: DeclaredConcurrencyPolicySource,
        hints: ProviderConcurrencyHints | None,
    ) -> ConcurrencyPolicyResolution | None:
        source = ProviderNarrowedPolicySource(catalog, hints=StaticHints(hints))
        return source.resolution_for(tool_name=MCP_TOOL, arguments=MCP_ARGUMENTS)

    @staticmethod
    def rejected(
        resolution: ConcurrencyPolicyResolution,
    ) -> set[ConcurrencyPolicyField]:
        return {rejection.policy_field for rejection in resolution.widening_rejections}


class TestAProviderHintNarrows(ProviderFixtureMixin):
    """THE (b) NARROWING TEST — a self-declared hint tightens a real policy."""

    def test_a_destructive_hint_makes_a_parallel_safe_read_serial(self) -> None:
        """The closing evidence for the narrowing direction.

        The operator declared this capability a parallel-safe, naturally
        idempotent read. The server itself says its dispatch is destructive.
        The narrower reading wins on every field it touches, and the effective
        trust source drops to the provider that caused it.
        """

        catalog = self.parallel_read_catalog()

        resolution = self.resolve(catalog, ProviderConcurrencyHints(destructive=True))

        assert resolution is not None
        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.side_effect is SideEffectKind.IRREVERSIBLE_WRITE
        assert resolution.policy.policy_source is PolicySource.TRUSTED_PROVIDER
        assert PolicySource.TRUSTED_PROVIDER in resolution.contributing_sources

    def test_a_non_idempotent_hint_narrows_an_idempotency_claim(self) -> None:
        catalog = self.parallel_read_catalog()

        resolution = self.resolve(catalog, ProviderConcurrencyHints(idempotent=False))

        assert resolution is not None
        assert resolution.policy.idempotency is IdempotencyKind.NONE

    def test_a_write_hint_narrows_a_read_claim(self) -> None:
        catalog = self.parallel_read_catalog()

        resolution = self.resolve(catalog, ProviderConcurrencyHints(read_only=False))

        assert resolution is not None
        assert resolution.policy.side_effect is SideEffectKind.IRREVERSIBLE_WRITE

    def test_a_narrowed_policy_carries_a_new_digest(self) -> None:
        """The digest is of the policy the resolution actually holds."""

        catalog = self.parallel_read_catalog()
        before = self.resolve(catalog, None)

        after = self.resolve(catalog, ProviderConcurrencyHints(destructive=True))

        assert before is not None
        assert after is not None
        assert after.policy_digest != before.policy_digest
        assert after.policy_digest == ConcurrencyPolicyResolution.digest_of(
            after.policy
        )


class TestAProviderHintCannotWiden(ProviderFixtureMixin):
    """THE (b) NO-WIDENING TEST — F3.3's rule, held by the fold."""

    def test_a_read_only_claim_cannot_soften_a_declared_write(self) -> None:
        """The closing evidence for the safety direction.

        A hostile or merely optimistic server declares itself read-only and
        naturally idempotent about a capability the operator declared an
        irreversible, serial, non-idempotent write. Not one field moves, and
        every over-claim is recorded as a typed widening rejection rather than
        silently dropped.
        """

        catalog = self.catalog(
            mode=ConcurrencyMode.SERIAL,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            idempotency=IdempotencyKind.NONE,
        )
        established = self.resolve(catalog, None)

        resolution = self.resolve(
            catalog,
            ProviderConcurrencyHints(read_only=True, idempotent=True),
        )

        assert established is not None
        assert resolution is not None
        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.side_effect is SideEffectKind.IRREVERSIBLE_WRITE
        assert resolution.policy.idempotency is IdempotencyKind.NONE
        assert resolution.policy_digest == established.policy_digest
        assert self.rejected(resolution) == {
            ConcurrencyPolicyField.SIDE_EFFECT,
            ConcurrencyPolicyField.IDEMPOTENCY,
        }
        assert all(
            rejection.source is PolicySource.TRUSTED_PROVIDER
            for rejection in resolution.widening_rejections
        )

    def test_a_widening_provider_never_becomes_the_effective_source(self) -> None:
        catalog = self.catalog(
            mode=ConcurrencyMode.SERIAL,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
        )

        resolution = self.resolve(catalog, ProviderConcurrencyHints(read_only=True))

        assert resolution is not None
        assert resolution.policy.policy_source is PolicySource.PRODUCT_CATALOG
        assert PolicySource.TRUSTED_PROVIDER not in resolution.contributing_sources

    def test_an_idempotency_claim_cannot_widen_the_conservative_floor(self) -> None:
        """``idempotency`` defaults to its floor, so the claim can never land."""

        catalog = self.catalog(mode=ConcurrencyMode.PARALLEL_SAFE)

        resolution = self.resolve(catalog, ProviderConcurrencyHints(idempotent=True))

        assert resolution is not None
        assert resolution.policy.idempotency is IdempotencyKind.NONE

    @pytest.mark.parametrize(
        "hints",
        [
            ProviderConcurrencyHints(read_only=True),
            ProviderConcurrencyHints(read_only=False),
            ProviderConcurrencyHints(destructive=True),
            ProviderConcurrencyHints(destructive=False),
            ProviderConcurrencyHints(idempotent=True),
            ProviderConcurrencyHints(idempotent=False),
            ProviderConcurrencyHints(read_only=True, idempotent=True),
            ProviderConcurrencyHints(
                read_only=False,
                destructive=True,
                idempotent=False,
            ),
        ],
    )
    def test_no_hint_combination_can_raise_any_field(
        self, hints: ProviderConcurrencyHints
    ) -> None:
        """Exhaustive over the declarable hint space, on rank not on values."""

        catalog = self.parallel_read_catalog()
        established = self.resolve(catalog, None)

        resolution = self.resolve(catalog, hints)

        assert established is not None
        assert resolution is not None
        for policy_field in ConcurrencyPolicyField:
            before = established.policy.value_for(policy_field)
            after = resolution.policy.value_for(policy_field)
            if isinstance(before, int) and isinstance(after, int):
                assert after <= before
            elif hasattr(before, "rank") and hasattr(after, "rank"):
                assert after.rank <= before.rank
            else:
                assert after == before


class TestAProviderCannotMakeACapabilityPlannable(ProviderFixtureMixin):
    """Silence from the operator stays silence, whatever the server says."""

    def test_an_undeclared_capability_stays_undeclared(self) -> None:
        empty = DeclaredConcurrencyPolicySource({})
        source = ProviderNarrowedPolicySource(
            empty,
            hints=StaticHints(ProviderConcurrencyHints(read_only=True)),
        )

        resolution = source.resolution_for(
            tool_name=MCP_TOOL,
            arguments=MCP_ARGUMENTS,
        )

        assert resolution is None

    def test_the_hint_source_is_not_even_consulted(self) -> None:
        """Cheap, and it pins the ordering the safety argument depends on."""

        hints = StaticHints(ProviderConcurrencyHints(read_only=True))
        source = ProviderNarrowedPolicySource(
            DeclaredConcurrencyPolicySource({}),
            hints=hints,
        )

        source.resolution_for(tool_name=MCP_TOOL, arguments=MCP_ARGUMENTS)

        assert hints.asked == []


class TestSilenceIsNotAClaim(ProviderFixtureMixin):
    """An absent, empty, or unreadable hint leaves the catalog untouched."""

    def test_no_hints_leaves_the_resolution_identical(self) -> None:
        catalog = self.parallel_read_catalog()
        established = self.resolve(catalog, None)

        resolution = self.resolve(catalog, ProviderConcurrencyHints())

        assert established is not None
        assert resolution is not None
        assert resolution.model_dump() == established.model_dump()

    def test_a_silent_hint_set_declares_nothing(self) -> None:
        assert ProviderConcurrencyHints().is_silent
        assert ProviderConcurrencyHints().declaration_payload() == {}
        assert (
            ProviderConcurrencyHints().declaration(
                capability_ref=graph_capability_ref(CAPABILITY_KEY)
            )
            is None
        )

    def test_a_hint_source_that_raises_leaves_the_catalog_intact(self) -> None:
        class Exploding:
            def hints_for(
                self, *, capability_key: str
            ) -> ProviderConcurrencyHints | None:
                raise RuntimeError("descriptor registry is unreachable")

        catalog = self.parallel_read_catalog()
        established = self.resolve(catalog, None)
        source = ProviderNarrowedPolicySource(catalog, hints=Exploding())

        resolution = source.resolution_for(
            tool_name=MCP_TOOL,
            arguments=MCP_ARGUMENTS,
        )

        assert established is not None
        assert resolution is not None
        assert resolution.model_dump() == established.model_dump()

    def test_a_hint_source_returning_junk_leaves_the_catalog_intact(self) -> None:
        class Junk:
            def hints_for(
                self, *, capability_key: str
            ) -> ProviderConcurrencyHints | None:
                return "read_only"  # type: ignore[return-value]

        catalog = self.parallel_read_catalog()
        established = self.resolve(catalog, None)
        source = ProviderNarrowedPolicySource(catalog, hints=Junk())

        resolution = source.resolution_for(
            tool_name=MCP_TOOL,
            arguments=MCP_ARGUMENTS,
        )

        assert established is not None
        assert resolution is not None
        assert resolution.model_dump() == established.model_dump()


class TestTheHintMapping:
    """The wire payload one hint set claims, folded by ``narrowest``."""

    def test_a_read_only_hint_claims_a_read(self) -> None:
        payload = ProviderConcurrencyHints(read_only=True).declaration_payload()

        assert payload == {
            ConcurrencyPolicyField.SIDE_EFFECT.value: SideEffectKind.READ.value
        }

    def test_a_contradiction_folds_to_the_narrower_reading(self) -> None:
        """A server declaring itself read-only *and* destructive is not trusted."""

        payload = ProviderConcurrencyHints(
            read_only=True,
            destructive=True,
        ).declaration_payload()

        assert payload[ConcurrencyPolicyField.SIDE_EFFECT.value] == (
            SideEffectKind.IRREVERSIBLE_WRITE.value
        )
        assert (
            payload[ConcurrencyPolicyField.MODE.value] == ConcurrencyMode.SERIAL.value
        )

    def test_a_non_destructive_claim_implies_nothing(self) -> None:
        assert ProviderConcurrencyHints(destructive=False).declaration_payload() == {}

    def test_the_declaration_is_attributed_to_the_provider(self) -> None:
        declaration = ProviderConcurrencyHints(destructive=True).declaration(
            capability_ref=graph_capability_ref(CAPABILITY_KEY)
        )

        assert declaration is not None
        assert declaration.source is PolicySource.TRUSTED_PROVIDER
        assert declaration.capability_ref == graph_capability_ref(CAPABILITY_KEY)

    def test_the_declaration_goes_through_the_domain_parser(self) -> None:
        """One entry point for provider metadata, so one floor for junk."""

        class CountingParser(ConcurrencyDescriptorParser):
            calls = 0

            def parse(self, **kwargs: object) -> CapabilityConcurrencyDeclaration:
                type(self).calls += 1
                return super().parse(**kwargs)  # type: ignore[arg-type]

        parser = CountingParser()
        ProviderConcurrencyHints(destructive=True).declaration(
            capability_ref=graph_capability_ref(CAPABILITY_KEY),
            parser=parser,
        )

        assert CountingParser.calls == 1

    def test_every_declared_field_is_a_widening_or_a_narrowing_never_junk(
        self,
    ) -> None:
        """No mapping may emit a value outside the closed vocabulary."""

        parser = ConcurrencyDescriptorParser()
        for hints in (
            ProviderConcurrencyHints(read_only=True, idempotent=True),
            ProviderConcurrencyHints(read_only=False, destructive=True),
            ProviderConcurrencyHints(idempotent=False),
        ):
            declaration = hints.declaration(
                capability_ref=graph_capability_ref(CAPABILITY_KEY),
                parser=parser,
            )
            assert declaration is not None
            assert not any(
                rejection.reason
                is ConcurrencyRejectionReason.UNPARSEABLE_DEFAULTED_SAFE
                for rejection in declaration.rejections
            )


class TestTheLiveMcpRegistryIsTheSource(ProviderFixtureMixin):
    """The tier reads the per-run registry the MCP loader already fills."""

    @pytest.fixture
    def registry(self) -> Any:
        bound: dict[tuple[str, str], McpToolAnnotations] = {}
        token = McpToolAnnotationsRegistry.bind_for_run(bound)
        try:
            yield bound
        finally:
            McpToolAnnotationsRegistry.unbind(token)

    def test_a_registered_annotation_reaches_the_resolved_policy(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        """End to end: wire annotations -> live registry -> resolved policy."""

        McpToolAnnotationsRegistry.register(
            SERVER,
            TOOL,
            McpToolAnnotations.from_wire({"destructiveHint": True}),
        )
        source = ProviderNarrowedPolicySource(
            self.parallel_read_catalog(),
            hints=McpAnnotationProviderHints(),
        )

        resolution = source.resolution_for(
            tool_name=MCP_TOOL,
            arguments=MCP_ARGUMENTS,
        )

        assert registry
        assert resolution is not None
        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.policy_source is PolicySource.TRUSTED_PROVIDER

    def test_a_registered_safety_claim_still_cannot_widen(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        McpToolAnnotationsRegistry.register(
            SERVER,
            TOOL,
            McpToolAnnotations.from_wire(
                {"readOnlyHint": True, "idempotentHint": True}
            ),
        )
        catalog = self.catalog(
            mode=ConcurrencyMode.SERIAL,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
        )
        source = ProviderNarrowedPolicySource(
            catalog,
            hints=McpAnnotationProviderHints(),
        )

        resolution = source.resolution_for(
            tool_name=MCP_TOOL,
            arguments=MCP_ARGUMENTS,
        )

        assert resolution is not None
        assert resolution.policy.side_effect is SideEffectKind.IRREVERSIBLE_WRITE
        assert resolution.policy.mode is ConcurrencyMode.SERIAL

    def test_a_seed_prefixed_server_resolves_to_the_same_entry(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        """``server_slug`` strips the catalog prefix on both write and read."""

        McpToolAnnotationsRegistry.register(
            f"seed:{SERVER}",
            TOOL,
            McpToolAnnotations.from_wire({"destructiveHint": True}),
        )

        hints = McpAnnotationProviderHints().hints_for(capability_key=CAPABILITY_KEY)

        assert hints == ProviderConcurrencyHints(destructive=True)

    def test_a_colon_in_the_server_name_still_splits_correctly(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        McpToolAnnotationsRegistry.register(
            f"seed:{SERVER}",
            TOOL,
            McpToolAnnotations.from_wire({"idempotentHint": False}),
        )

        hints = McpAnnotationProviderHints().hints_for(
            capability_key=f"seed:{SERVER}:{TOOL}"
        )

        assert hints == ProviderConcurrencyHints(idempotent=False)

    def test_a_non_mcp_capability_key_has_no_provider(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        assert McpAnnotationProviderHints().hints_for(capability_key="write_todos") is (
            None
        )

    def test_a_tool_the_run_never_loaded_declares_nothing(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        assert (
            McpAnnotationProviderHints().hints_for(capability_key="notion:search")
            is None
        )

    def test_a_non_boolean_wire_hint_is_no_hint(
        self, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> None:
        McpToolAnnotationsRegistry.register(
            SERVER,
            TOOL,
            McpToolAnnotations.from_wire({"destructiveHint": "yes"}),
        )

        hints = McpAnnotationProviderHints().hints_for(capability_key=CAPABILITY_KEY)

        assert hints is not None
        assert hints.is_silent


class TestAnUnboundRegistryDeclaresNothing:
    """No bind means no run — replay, eval, and unit paths must not fault."""

    def test_an_unbound_registry_answers_none(self) -> None:
        assert McpToolAnnotationsRegistry.active() is None

        assert (
            McpAnnotationProviderHints().hints_for(capability_key=CAPABILITY_KEY)
            is None
        )
