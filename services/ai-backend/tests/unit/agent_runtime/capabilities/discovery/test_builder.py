from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityBridgeToolName,
    CapabilityCatalogGeneration,
    CapabilityCatalogScope,
    CapabilitySource,
    CapabilitySubjectFingerprint,
    CatalogDescriptorRevision,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.tools.cards import ToolCard, ToolRiskLevel
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
_REFERENCE_KEY = b"catalog-test-reference-key-32-bytes!!"
_SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"


def _context(
    *,
    run_id: str = "run_1",
    user_id: str = "user_1",
    paused_connectors: frozenset[str] = frozenset(),
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id,
        org_id="org_1",
        roles={"member"},
        permission_scopes={"docs:read", "calendar:read"},
        connector_scopes={
            "drive": frozenset({"docs:read"}),
            "calendar": frozenset({"calendar:read"}),
        },
        paused_connectors=paused_connectors,
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=32_000,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id=run_id,
    )


def _scope(context: AgentRuntimeContext) -> CapabilityCatalogScope:
    return CapabilityCatalogScope.from_context(
        context,
        profile_id="research",
        policy_revision="policy_7",
        connector_scope_revision="scope_9",
    )


def _tool(
    *,
    name: str,
    connector: str = "drive",
    scopes: frozenset[str] = frozenset({"docs:read"}),
    enabled: bool = True,
    risk: ToolRiskLevel = ToolRiskLevel.LOW,
) -> ToolCard:
    return ToolCard(
        name=name,
        display_name=name.replace("_", " ").title(),
        short_description=f"Use {name} to find relevant records.",
        connector=connector,
        tags={"search", "records"},
        required_scopes=scopes,
        risk_level=risk,
        load_cost=1,
        enabled=enabled,
    )


def _server(
    *,
    name: str,
    server_id: str | None = None,
    scopes: frozenset[str] = frozenset({"docs:read"}),
    health: McpServerHealth = McpServerHealth.HEALTHY,
) -> McpServerCard:
    return McpServerCard(
        name=name,
        server_id=server_id,
        display_name=name.replace("_", " ").title(),
        short_description=f"{name} compact server metadata.",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes=scopes,
        health=health,
        load_cost=2,
        connector_slug=name,
    )


def _build(
    *,
    context: AgentRuntimeContext,
    servers: tuple[McpServerCard, ...] = (),
    selection_ref: str = _SELECTION_REF,
    descriptor_revisions: tuple[CatalogDescriptorRevision, ...] = (),
):
    return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
        context=context,
        scope=_scope(context),
        task_policy_selection_ref=selection_ref,
        mcp_server_cards=servers,
        descriptor_revisions=descriptor_revisions,
        deferred_schema_tokens=2_000,
        expires_at=_NOW + timedelta(minutes=15),
    )


class TestAuthorizedCatalogBuilder:
    def test_projects_only_authorized_compact_cards(self) -> None:
        context = _context(paused_connectors=frozenset({"seed:paused"}))

        catalog = _build(
            context=context,
            servers=(
                _server(name="drive_server", server_id="seed:drive"),
                _server(name="paused_server", server_id="seed:paused"),
                _server(
                    name="unavailable_server",
                    health=McpServerHealth.UNAVAILABLE,
                ),
                _server(name="admin_server", scopes=frozenset({"admin:read"})),
            ),
        )

        assert [(entry.source, entry.stable_name) for entry in catalog.entries] == [
            (CapabilitySource.MCP_SERVER, "drive_server"),
        ]
        assert catalog.revision.descriptor_count == 1
        assert catalog.revision.deferred_schema_tokens == 2_000

    def test_catalog_is_stable_when_source_order_changes(self) -> None:
        context = _context()
        servers = (
            _server(name="zeta_server"),
            _server(name="calendar_server", scopes=frozenset({"calendar:read"})),
        )

        first = _build(context=context, servers=servers)
        second = _build(context=context, servers=tuple(reversed(servers)))

        assert first == second
        assert first.revision.revision == second.revision.revision

    def test_refs_are_opaque_and_change_with_run_scope(self) -> None:
        first_context = _context(run_id="run_1")
        second_context = _context(run_id="run_2")
        card = _server(name="private_server_name")

        first = _build(context=first_context, servers=(card,))
        second = _build(context=second_context, servers=(card,))

        assert first.revision.catalog_id != second.revision.catalog_id
        assert first.entries[0].capability_ref != second.entries[0].capability_ref
        assert "private_server_name" not in first.entries[0].capability_ref

    def test_scope_mismatch_fails_closed(self) -> None:
        context = _context(user_id="user_1")
        other_context = _context(user_id="user_2")

        with pytest.raises(ValueError, match="does not match"):
            AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
                context=other_context,
                scope=_scope(context),
                task_policy_selection_ref=_SELECTION_REF,
                expires_at=_NOW + timedelta(minutes=15),
            )

    def test_reference_key_must_be_strong(self) -> None:
        with pytest.raises(ValueError, match="at least 32 bytes"):
            AuthorizedCatalogBuilder(reference_key=b"short")

    def test_active_check_binds_subject_and_expiry(self) -> None:
        context = _context()
        catalog = _build(context=context, servers=(_server(name="drive_server"),))

        assert catalog.is_active_for(context, now=_NOW)
        assert not catalog.is_active_for(
            context,
            now=_NOW + timedelta(minutes=16),
        )
        assert not catalog.is_active_for(
            _context(user_id="user_2"),
            now=_NOW,
        )


class TestBuiltCatalogGeneration:
    """The shipped builder always stamps a bindable generation."""

    def test_a_built_catalog_carries_its_generation(self) -> None:
        context = _context()

        catalog = _build(context=context, servers=(_server(name="drive_server"),))

        generation = catalog.generation
        assert generation is not None
        assert generation.connector_scope_revision == "scope_9"
        assert generation.task_policy_selection_ref == _SELECTION_REF

    def test_a_built_catalog_can_bind_a_member_ref(self) -> None:
        context = _context()
        catalog = _build(context=context, servers=(_server(name="drive_server"),))

        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        assert binding.catalog_id == catalog.revision.catalog_id
        assert binding.issued_generation == catalog.generation

    def test_the_generation_is_reproducible_for_identical_inputs(self) -> None:
        context = _context()
        servers = (_server(name="drive_server"),)

        first = _build(context=context, servers=servers)
        second = _build(context=context, servers=servers)

        assert first.generation == second.generation

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("selection_ref", f"task-policy-selection://run_1/other/sha256/{'d' * 64}"),
            (
                "descriptor_revisions",
                (
                    CatalogDescriptorRevision(
                        source_id="drive",
                        descriptor_revision="rev-b",
                    ),
                ),
            ),
        ],
    )
    def test_the_generation_changes_when_a_keyed_input_changes(
        self,
        field: str,
        value: object,
    ) -> None:
        context = _context()
        baseline = _build(context=context, servers=(_server(name="drive_server"),))

        changed = _build(
            context=context,
            servers=(_server(name="drive_server"),),
            **{field: value},  # type: ignore[arg-type]
        )

        assert baseline.generation is not None
        assert changed.generation is not None
        assert (
            baseline.generation.generation_digest
            != changed.generation.generation_digest
        )

    def test_the_generation_changes_when_the_subject_changes(self) -> None:
        baseline = _build(context=_context(user_id="user_1"))

        changed = _build(context=_context(user_id="user_2"))

        assert baseline.generation is not None
        assert changed.generation is not None
        assert (
            baseline.generation.subject_fingerprint
            != changed.generation.subject_fingerprint
        )

    def test_the_subject_fingerprint_is_derived_not_disclosed(self) -> None:
        context = _context()
        builder = AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY)

        fingerprint = builder.subject_fingerprint(context)

        assert len(fingerprint) == 64
        assert set(fingerprint) <= set("0123456789abcdef")
        assert context.user_id not in fingerprint
        assert context.org_id not in fingerprint

    def test_the_same_derivation_is_reused_for_the_generation(self) -> None:
        context = _context()
        builder = AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY)

        catalog = builder.build(
            context=context,
            scope=_scope(context),
            task_policy_selection_ref=_SELECTION_REF,
            expires_at=_NOW + timedelta(minutes=15),
        )

        assert catalog.generation is not None
        assert catalog.generation.subject_fingerprint == builder.subject_fingerprint(
            context
        )

    def test_a_different_reference_key_yields_a_different_fingerprint(self) -> None:
        context = _context()

        first = CapabilitySubjectFingerprint(reference_key=_REFERENCE_KEY)
        second = CapabilitySubjectFingerprint(reference_key=b"y" * 32)

        assert first.derive(context) != second.derive(context)

    def test_a_weak_reference_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 32 bytes"):
            CapabilitySubjectFingerprint(reference_key=b"short")

    def test_descriptor_revisions_fold_into_the_generation(self) -> None:
        revisions = (
            CatalogDescriptorRevision(source_id="drive", descriptor_revision="rev-a"),
            CatalogDescriptorRevision(source_id="mail", descriptor_revision="rev-b"),
        )

        catalog = _build(context=_context(), descriptor_revisions=revisions)

        assert catalog.generation is not None
        assert catalog.generation.descriptor_revision_count == 2
        assert (
            catalog.generation.descriptor_revision_digest
            == (CapabilityCatalogGeneration.fold_descriptor_revisions(revisions)[0])
        )


class TestBridgeNamesAreNeverCatalogMembers:
    """A bridge tool can never become resolvable through the catalog."""

    @pytest.mark.parametrize("bridge_name", sorted(CapabilityBridgeToolName))
    def test_a_card_claiming_a_bridge_name_is_excluded(
        self,
        bridge_name: CapabilityBridgeToolName,
    ) -> None:
        context = _context()

        catalog = _build(
            context=context,
            servers=(_server(name=bridge_name.value), _server(name="drive_server")),
        )

        assert [entry.stable_name for entry in catalog.entries] == ["drive_server"]

    def test_an_mcp_server_claiming_a_bridge_name_is_excluded(self) -> None:
        context = _context()

        catalog = _build(
            context=context,
            servers=(
                _server(name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value),
                _server(name="drive_server"),
            ),
        )

        assert [entry.stable_name for entry in catalog.entries] == ["drive_server"]

    def test_excluding_a_bridge_name_does_not_fail_the_whole_catalog(self) -> None:
        context = _context()

        catalog = _build(
            context=context,
            servers=(_server(name=CapabilityBridgeToolName.SEARCH_CAPABILITIES.value),),
        )

        assert catalog.entries == ()
        assert catalog.revision.descriptor_count == 0


class TestToolCardsAreNotCatalogMembers:
    """M-09: the bridge index holds only what the bridge can dispatch.

    A product tool card has no non-model dispatcher, so an entry for one could
    be searched and described and would then be refused at invoke.  The
    exclusion is asserted at both structural layers -- the builder offers no
    seam to pass one, and the membership contract refuses the source outright --
    because either alone would leave a construction path open.
    """

    def test_the_builder_offers_no_seam_for_tool_cards(self) -> None:
        """A parameter that always raised would still read as an option."""

        parameters = inspect.signature(AuthorizedCatalogBuilder.build).parameters

        assert "tool_cards" not in parameters
        assert "mcp_server_cards" in parameters

    def test_passing_tool_cards_is_refused_rather_than_dropped(self) -> None:
        context = _context()

        with pytest.raises(TypeError, match="tool_cards"):
            AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
                context=context,
                scope=_scope(context),
                task_policy_selection_ref=_SELECTION_REF,
                tool_cards=(_tool(name="drive_search"),),  # type: ignore[call-arg]
                expires_at=_NOW + timedelta(minutes=15),
            )

    def test_a_built_catalog_holds_only_dispatchable_sources(self) -> None:
        catalog = _build(
            context=_context(),
            servers=(
                _server(name="drive_server"),
                _server(name="calendar_server", scopes=frozenset({"calendar:read"})),
            ),
        )

        assert catalog.entries != ()
        assert {entry.source for entry in catalog.entries} == {
            CapabilitySource.MCP_SERVER
        }
        assert all(
            entry.source in CapabilitySource.catalog_admissible()
            for entry in catalog.entries
        )

    def test_the_unused_tool_card_helpers_still_describe_a_real_card(self) -> None:
        """Guard the negative tests above against a vacuous fixture."""

        card = _tool(name="drive_search", risk=ToolRiskLevel.HIGH)

        assert card.name == "drive_search"
        assert card.risk_level is ToolRiskLevel.HIGH
