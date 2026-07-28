"""Bounded bridge registration, invocation, and the bridge-recursion guard."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityActivationDecision,
    CapabilityActivationMode,
    CapabilityActivationResolver,
    CapabilityBridgeRecursionError,
    CapabilityBridgeRegistrar,
    CapabilityBridgeToolAdapter,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogAccess,
    CapabilityCatalogMembershipError,
    CapabilityCatalogRevision,
    CapabilityCatalogRevisionAuthority,
    CapabilityCatalogScope,
    CapabilityDiscoveryErrorCode,
    CapabilityIndexEntry,
    CapabilityInvocationReceipt,
    CapabilityInvocationStatus,
    CapabilityInvocationTarget,
    CapabilityInvokeRequest,
    CapabilityInvokeTool,
    CapabilityRefRevalidation,
    CapabilityRefRevisionBinding,
    CapabilitySource,
)
from agent_runtime.capabilities.discovery.tool_bridge import CapabilityExecutionRefused
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.control_plane.revision_binding import RevisionBindingRevalidator
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from tests.unit.agent_runtime.capabilities.discovery.test_revision_authority import (
    InMemoryCatalogGenerationSource,
)

_NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
_REFERENCE_KEY = b"f3-registration-reference-key-32-bytes!!"
_SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"


def _context(*, run_id: str = "run_1") -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_1",
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
    expires_at: datetime = _NOW + timedelta(minutes=15),
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
        mcp_server_cards=(
            McpServerCard(
                name="drive_server",
                display_name="Drive Server",
                short_description="Find relevant drive records.",
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
                required_scopes=frozenset({"docs:read"}),
                health=McpServerHealth.HEALTHY,
                load_cost=2,
                connector_slug="drive",
            ),
        ),
        expires_at=expires_at,
    )


def _decision(activation: CapabilityActivationMode) -> CapabilityActivationDecision:
    """Resolve a real decision through the F3.1 resolver, never a hand-built one."""

    raw_mode = {
        CapabilityActivationMode.DIRECT: FeatureMode.OFF,
        CapabilityActivationMode.SERVER: FeatureMode.ENFORCE,
        CapabilityActivationMode.SHADOW: FeatureMode.SHADOW,
        CapabilityActivationMode.DEFERRED: FeatureMode.ENFORCE,
    }[activation]
    return CapabilityActivationResolver().resolve_configured(
        raw_mode=raw_mode.value,
        raw_activation=activation.value,
    )


class RecordingExecutor:
    """Capture what the bridge actually dispatches to the Operation Gateway."""

    def __init__(self, *, status: CapabilityInvocationStatus | None = None) -> None:
        self.status = status or CapabilityInvocationStatus.COMPLETED
        self.targets: list[CapabilityInvocationTarget] = []
        self.arguments: list[Mapping[str, Any]] = []
        self.idempotency_keys: list[str | None] = []

    async def execute(
        self,
        *,
        target: CapabilityInvocationTarget,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        runtime_context: AgentRuntimeContext,
    ) -> CapabilityInvocationReceipt:
        self.targets.append(target)
        self.arguments.append(dict(arguments))
        self.idempotency_keys.append(idempotency_key)
        return CapabilityInvocationReceipt(
            capability_ref=target.capability_ref,
            invocation_ref=f"capability-invocation://sha256/{'a' * 64}",
            status=self.status,
            safe_summary="The capability completed.",
        )


class InvokeHarness:
    """Assemble the whole invoke path over a real built catalog."""

    def __init__(
        self,
        *,
        context: AgentRuntimeContext | None = None,
        catalog: CapabilityCatalog | None = None,
        live_catalog: CapabilityCatalog | None = None,
        executor: object | None = None,
    ) -> None:
        self.context = context or _context()
        self.catalog = catalog or _catalog(self.context)
        published = live_catalog or self.catalog
        self.executor = executor if executor is not None else RecordingExecutor()
        self.source = InMemoryCatalogGenerationSource()
        generation = self.catalog.generation
        published_generation = published.generation
        assert generation is not None
        assert published_generation is not None
        self.source.publish(
            CapabilityRefRevisionBinding.scope_for(
                generation,
                run_id=self.context.run_id,
            ),
            published_generation,
        )
        self.revalidation = CapabilityRefRevalidation(
            revalidator=RevisionBindingRevalidator(
                CapabilityCatalogRevisionAuthority(self.source)
            ),
            subject_fingerprint=AuthorizedCatalogBuilder(
                reference_key=_REFERENCE_KEY
            ).subject_fingerprint(self.context),
        )

    @property
    def capability_ref(self) -> str:
        return self.catalog.entries[0].capability_ref

    def tool(self, **overrides: object) -> CapabilityInvokeTool:
        return CapabilityInvokeTool(
            access=CapabilityCatalogAccess(
                catalog=self.catalog,
                runtime_context=self.context,
                clock=lambda: _NOW,
            ),
            executor=overrides.get("executor", self.executor),  # type: ignore[arg-type]
            revalidation=overrides.get("revalidation", self.revalidation),  # type: ignore[arg-type]
        )

    def registrations(self, activation: CapabilityActivationMode, **overrides: object):
        return CapabilityBridgeRegistrar.registrations_for(
            activation=_decision(activation),
            catalog=overrides.get("catalog", self.catalog),  # type: ignore[arg-type]
            runtime_context=self.context,
            executor=overrides.get("executor", self.executor),  # type: ignore[arg-type]
            revalidation=overrides.get("revalidation", self.revalidation),  # type: ignore[arg-type]
            clock=lambda: _NOW,
        )


class TestBridgeRegistration:
    """Bridge tools exist only in deferred, and only when they can be used."""

    @pytest.mark.parametrize(
        "activation",
        [
            CapabilityActivationMode.DIRECT,
            CapabilityActivationMode.SERVER,
            CapabilityActivationMode.SHADOW,
        ],
    )
    def test_no_bridge_tool_is_registered_outside_deferred(
        self,
        activation: CapabilityActivationMode,
    ) -> None:
        harness = InvokeHarness()

        assert harness.registrations(activation) == ()

    def test_all_three_bridge_tools_are_registered_in_deferred(self) -> None:
        harness = InvokeHarness()

        registrations = harness.registrations(CapabilityActivationMode.DEFERRED)

        assert [item.name for item in registrations] == [
            CapabilityBridgeToolName.SEARCH_CAPABILITIES,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
            CapabilityBridgeToolName.INVOKE_CAPABILITY,
        ]

    def test_registered_names_come_from_the_closed_bridge_vocabulary(self) -> None:
        harness = InvokeHarness()

        registrations = harness.registrations(CapabilityActivationMode.DEFERRED)

        assert {item.name for item in registrations} == set(CapabilityBridgeToolName)
        assert all(
            getattr(item.adapter, "name") == item.name.value for item in registrations
        )

    def test_every_registered_tool_carries_a_bounded_schema(self) -> None:
        harness = InvokeHarness()

        registrations = harness.registrations(CapabilityActivationMode.DEFERRED)

        for item in registrations:
            fields = item.args_schema.model_fields
            assert fields
            assert item.args_schema.model_config["extra"] == "forbid"

    def test_every_registered_adapter_satisfies_the_factory_contract(self) -> None:
        harness = InvokeHarness()

        registrations = harness.registrations(CapabilityActivationMode.DEFERRED)

        for item in registrations:
            assert isinstance(item.adapter, CapabilityBridgeToolAdapter)
            assert str(getattr(item.adapter, "description")).strip()

    def test_registration_imports_no_model_framework(self) -> None:
        """The factory keeps sole ownership of how a model tool is composed."""

        import agent_runtime.capabilities.discovery.registration as module

        tree = ast.parse(Path(module.__file__).read_text())
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert not any(
            name.startswith(("langchain", "langgraph", "deepagents"))
            for name in imported
        )

    def test_a_feature_mode_ceiling_removes_the_bridge(self) -> None:
        harness = InvokeHarness()
        decision = CapabilityActivationResolver().resolve_configured(
            raw_mode=FeatureMode.SHADOW.value,
            raw_activation=CapabilityActivationMode.DEFERRED.value,
        )

        registrations = CapabilityBridgeRegistrar.registrations_for(
            activation=decision,
            catalog=harness.catalog,
            runtime_context=harness.context,
            executor=harness.executor,
            revalidation=harness.revalidation,
        )

        assert decision.effective_activation is CapabilityActivationMode.SHADOW
        assert registrations == ()

    def test_an_unbindable_catalog_registers_nothing(self) -> None:
        harness = InvokeHarness()
        ungenerated = CapabilityCatalog(
            scope=harness.catalog.scope,
            revision=CapabilityCatalogRevision(
                **harness.catalog.revision.model_dump(exclude={"generation"}),
            ),
            entries=harness.catalog.entries,
        )

        registrations = harness.registrations(
            CapabilityActivationMode.DEFERRED,
            catalog=ungenerated,
        )

        assert registrations == ()

    @pytest.mark.parametrize("missing", ["executor", "revalidation"])
    def test_invoke_is_absent_until_its_seam_is_wired(self, missing: str) -> None:
        harness = InvokeHarness()

        registrations = harness.registrations(
            CapabilityActivationMode.DEFERRED,
            **{missing: None},
        )

        assert [item.name for item in registrations] == [
            CapabilityBridgeToolName.SEARCH_CAPABILITIES,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
        ]


class TestBridgeRecursionGuard:
    """A bridge tool can never resolve to another bridge tool."""

    @pytest.mark.parametrize("bridge_name", sorted(CapabilityBridgeToolName))
    def test_a_catalog_entry_can_never_name_a_bridge_tool(
        self,
        bridge_name: CapabilityBridgeToolName,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityIndexEntry(
                capability_ref=f"cap_{'1' * 32}",
                source=CapabilitySource.MCP_SERVER,
                stable_name=bridge_name.value,
                display_name="Bridge",
                concise_description="A bridge tool masquerading as a capability.",
                connector_label="drive",
            )

        assert isinstance(
            exc_info.value.errors()[0].get("ctx", {}).get("error"),
            CapabilityBridgeRecursionError,
        )

    @pytest.mark.parametrize(
        "spelling",
        ["Invoke_Capability", " invoke_capability ", "INVOKE_CAPABILITY"],
    )
    def test_the_guard_is_not_defeated_by_casing_or_padding(
        self,
        spelling: str,
    ) -> None:
        with pytest.raises(ValidationError):
            CapabilityIndexEntry(
                capability_ref=f"cap_{'2' * 32}",
                source=CapabilitySource.MCP_SERVER,
                stable_name=spelling,
                display_name="Bridge",
                concise_description="A bridge tool masquerading as a capability.",
                connector_label="drive",
            )

    def test_a_dispatch_target_can_never_name_a_bridge_tool(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityInvocationTarget(
                capability_ref=f"cap_{'3' * 32}",
                stable_name=CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
                source=CapabilitySource.TOOL_CARD,
                connector_label="drive",
                effect_class="unknown",
                approval_cue="unknown",
            )

        assert isinstance(
            exc_info.value.errors()[0].get("ctx", {}).get("error"),
            CapabilityBridgeRecursionError,
        )

    def test_a_forged_entry_still_cannot_produce_a_dispatch_target(self) -> None:
        """The target constructor re-asserts what a forged entry skipped."""

        forged = CapabilityIndexEntry.model_construct(
            capability_ref=f"cap_{'4' * 32}",
            source=CapabilitySource.TOOL_CARD,
            stable_name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value,
            display_name="Bridge",
            concise_description="Forged past catalog validation.",
            intent_tags=(),
            parameter_names=(),
            parameter_types=(),
            connector_label="drive",
            descriptor_revision=None,
        )

        with pytest.raises(ValidationError):
            CapabilityInvocationTarget.from_catalog_entry(forged)

    def test_the_reserved_set_is_derived_from_the_enum(self) -> None:
        assert CapabilityBridgeToolName.reserved_names() == {
            member.value for member in CapabilityBridgeToolName
        }

    def test_a_dispatch_target_may_still_name_an_undispatchable_source(self) -> None:
        """The executor's source guard has to stay reachable to stay tested.

        A catalog member can no longer carry an undispatchable source, so the
        only way one reaches dispatch is a record forged past validation.  If
        the *target* contract also refused the source, the executor's
        fail-closed branch would become unreachable and the forgery would lose
        its last check.  Targets therefore stay permissive on purpose.
        """

        target = CapabilityInvocationTarget(
            capability_ref=f"cap_{'7' * 32}",
            stable_name="drive_search",
            source=CapabilitySource.TOOL_CARD,
            connector_label="drive",
            effect_class="unknown",
            approval_cue="unknown",
        )

        assert target.source is CapabilitySource.TOOL_CARD
        assert not target.source.has_non_model_dispatch


class TestOnlyDispatchableSourcesAreCatalogMembers:
    """M-09: catalog membership and non-model dispatchability are one set.

    A product tool card has no non-model dispatcher, so a catalog entry for one
    could be searched and described and would then be refused at invoke.  The
    refusal lives at the membership contract, which every construction path --
    builder, adapter, or test -- has to pass through.
    """

    @staticmethod
    def _entry(source: CapabilitySource) -> CapabilityIndexEntry:
        return CapabilityIndexEntry(
            capability_ref=f"cap_{'8' * 32}",
            source=source,
            stable_name="drive_search",
            display_name="Drive Search",
            concise_description="Find relevant drive records.",
            connector_label="drive",
        )

    def test_admissible_sources_are_exactly_the_dispatchable_ones(self) -> None:
        assert CapabilitySource.catalog_admissible() == {
            member for member in CapabilitySource if member.has_non_model_dispatch
        }
        assert CapabilitySource.catalog_admissible() == {CapabilitySource.MCP_SERVER}

    @pytest.mark.parametrize(
        "source",
        sorted(set(CapabilitySource) - CapabilitySource.catalog_admissible()),
    )
    def test_an_undispatchable_source_can_never_be_a_member(
        self,
        source: CapabilitySource,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            self._entry(source)

        error = exc_info.value.errors()[0].get("ctx", {}).get("error")
        assert isinstance(error, CapabilityCatalogMembershipError)
        assert str(error) == (
            CapabilityCatalogMembershipError.Messages.UNDISPATCHABLE_SOURCE
        )

    @pytest.mark.parametrize("source", sorted(CapabilitySource.catalog_admissible()))
    def test_a_dispatchable_source_is_still_admitted(
        self,
        source: CapabilitySource,
    ) -> None:
        assert self._entry(source).source is source

    def test_a_hand_built_catalog_cannot_smuggle_one_in(self) -> None:
        """The chokepoint is the entry, so assembling a catalog cannot bypass it."""

        context = _context()
        catalog = _catalog(context)

        with pytest.raises(ValidationError) as exc_info:
            CapabilityCatalog(
                scope=catalog.scope,
                revision=catalog.revision,
                entries=(
                    catalog.entries[0].model_dump()
                    | {"source": CapabilitySource.TOOL_CARD},
                ),
            )

        assert isinstance(
            exc_info.value.errors()[0].get("ctx", {}).get("error"),
            CapabilityCatalogMembershipError,
        )

    def test_the_registered_bridge_only_ever_sees_dispatchable_members(self) -> None:
        context = _context()
        catalog = _catalog(context)

        registrations = CapabilityBridgeRegistrar.registrations_for(
            activation=_decision(CapabilityActivationMode.DEFERRED),
            catalog=catalog,
            runtime_context=context,
        )

        assert registrations != ()
        assert catalog.entries != ()
        assert all(entry.source.has_non_model_dispatch for entry in catalog.entries)

    async def test_invoking_a_bridge_name_is_indistinguishable_from_unknown(
        self,
    ) -> None:
        harness = InvokeHarness()

        forged_bridge = await harness.tool().ainvoke(
            {"capability_ref": f"cap_{'5' * 32}"}
        )
        unknown = await harness.tool().ainvoke({"capability_ref": f"cap_{'6' * 32}"})

        assert forged_bridge == unknown
        assert (
            forged_bridge["error"]["code"]
            == CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND
        )

    async def test_a_dispatched_target_is_never_a_bridge_tool(self) -> None:
        harness = InvokeHarness()

        await harness.tool().ainvoke({"capability_ref": harness.capability_ref})

        executor = harness.executor
        assert isinstance(executor, RecordingExecutor)
        assert executor.targets
        assert all(
            not CapabilityBridgeToolName.is_reserved(target.stable_name)
            for target in executor.targets
        )


class TestCapabilityInvokeTool:
    """Invocation is bounded, re-authorized, and fails closed."""

    async def test_a_current_reference_dispatches_and_returns_a_receipt(self) -> None:
        harness = InvokeHarness()

        result = await harness.tool().ainvoke(
            {
                "capability_ref": harness.capability_ref,
                "arguments": {"query": "quarterly report"},
                "idempotency_key": "idem-1",
            }
        )

        executor = harness.executor
        assert isinstance(executor, RecordingExecutor)
        assert executor.arguments == [{"query": "quarterly report"}]
        assert executor.idempotency_keys == ["idem-1"]
        assert result["invocation"]["catalog_id"] == harness.catalog.revision.catalog_id
        assert result["invocation"]["receipt"]["status"] == (
            CapabilityInvocationStatus.COMPLETED
        )

    async def test_a_superseded_generation_refuses_without_dispatching(self) -> None:
        context = _context()
        harness = InvokeHarness(
            context=context,
            catalog=_catalog(context),
            live_catalog=_catalog(
                context,
                selection_ref=(
                    f"task-policy-selection://run_1/research/sha256/{'d' * 64}"
                ),
            ),
        )

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        executor = harness.executor
        assert isinstance(executor, RecordingExecutor)
        assert executor.targets == []
        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.CAPABILITY_STALE

    async def test_an_unknown_authority_scope_refuses_without_dispatching(self) -> None:
        harness = InvokeHarness()
        harness.source = InMemoryCatalogGenerationSource()
        harness.revalidation = CapabilityRefRevalidation(
            revalidator=RevisionBindingRevalidator(
                CapabilityCatalogRevisionAuthority(harness.source)
            ),
            subject_fingerprint=AuthorizedCatalogBuilder(
                reference_key=_REFERENCE_KEY
            ).subject_fingerprint(harness.context),
        )

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        executor = harness.executor
        assert isinstance(executor, RecordingExecutor)
        assert executor.targets == []
        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.CAPABILITY_STALE

    @pytest.mark.parametrize("missing", ["executor", "revalidation"])
    async def test_a_missing_invocation_seam_refuses(self, missing: str) -> None:
        harness = InvokeHarness()

        result = await harness.tool(**{missing: None}).ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )

    async def test_an_expired_catalog_refuses_without_catalog_metadata(self) -> None:
        context = _context()
        harness = InvokeHarness(
            context=context,
            catalog=_catalog(context, expires_at=_NOW - timedelta(microseconds=1)),
        )

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.CATALOG_INACTIVE
        assert "catalog_id" not in json.dumps(result)

    async def test_a_cross_run_catalog_refuses(self) -> None:
        owner = _context(run_id="run_owner")
        harness = InvokeHarness(context=owner)
        tool = CapabilityInvokeTool(
            access=CapabilityCatalogAccess(
                catalog=harness.catalog,
                runtime_context=_context(run_id="run_other"),
                clock=lambda: _NOW,
            ),
            executor=harness.executor,  # type: ignore[arg-type]
            revalidation=harness.revalidation,
        )

        result = await tool.ainvoke({"capability_ref": harness.capability_ref})

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.CATALOG_INACTIVE

    async def test_an_executor_failure_never_leaks_internal_detail(self) -> None:
        class ExplodingExecutor:
            async def execute(self, **kwargs: object) -> CapabilityInvocationReceipt:
                raise RuntimeError("postgres://secret-host/connector_tokens")

        harness = InvokeHarness(executor=ExplodingExecutor())

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.EXECUTION_FAILED
        assert "secret-host" not in json.dumps(result)

    @pytest.mark.parametrize(
        ("code", "safe_message"),
        [
            (
                CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                "Those arguments do not match the capability's current schema. "
                "Describe it again before retrying.",
            ),
            (
                CapabilityDiscoveryErrorCode.CAPABILITY_STALE,
                "That capability reference is no longer current. "
                "Search again before invoking.",
            ),
            (
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE,
                "Capability invocation is unavailable for this run. "
                "Use the direct tools instead.",
            ),
            (
                CapabilityDiscoveryErrorCode.EXECUTION_FAILED,
                "That capability could not be invoked. Try a different approach.",
            ),
        ],
    )
    async def test_a_typed_refusal_maps_to_its_code_and_this_modules_message(
        self,
        code: CapabilityDiscoveryErrorCode,
        safe_message: str,
    ) -> None:
        """The executor names a code; the bridge owns every model-visible word."""

        class RefusingExecutor:
            async def execute(self, **kwargs: object) -> CapabilityInvocationReceipt:
                raise CapabilityExecutionRefused(code)

        harness = InvokeHarness(executor=RefusingExecutor())

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == code
        assert result["error"]["safe_message"] == safe_message

    async def test_a_refusal_cannot_answer_with_the_membership_code(self) -> None:
        """An executor must never become the catalog-existence oracle."""

        class ProbingExecutor:
            async def execute(self, **kwargs: object) -> CapabilityInvocationReceipt:
                raise CapabilityExecutionRefused(
                    CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND
                )

        harness = InvokeHarness(executor=ProbingExecutor())

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.EXECUTION_FAILED

    async def test_an_executor_cannot_substitute_another_capability(self) -> None:
        class SubstitutingExecutor:
            async def execute(
                self,
                *,
                target: CapabilityInvocationTarget,
                **kwargs: object,
            ) -> CapabilityInvocationReceipt:
                return CapabilityInvocationReceipt(
                    capability_ref=f"cap_{'9' * 32}",
                    invocation_ref="capability-invocation://sha256/" + "b" * 64,
                    status=CapabilityInvocationStatus.COMPLETED,
                    safe_summary="A different capability entirely.",
                )

        harness = InvokeHarness(executor=SubstitutingExecutor())

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.EXECUTION_FAILED

    async def test_an_off_contract_executor_result_is_refused(self) -> None:
        class UntypedExecutor:
            async def execute(self, **kwargs: object) -> CapabilityInvocationReceipt:
                return {"capability_ref": "anything"}  # type: ignore[return-value]

        harness = InvokeHarness(executor=UntypedExecutor())

        result = await harness.tool().ainvoke(
            {"capability_ref": harness.capability_ref}
        )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.EXECUTION_FAILED

    @pytest.mark.parametrize(
        "payload",
        [
            {"capability_ref": "drive_search"},
            {"capability_ref": f"cap_{'1' * 32}", "arguments": "not-an-object"},
            {
                "capability_ref": f"cap_{'1' * 32}",
                "arguments": {"bad key": 1},
            },
            {
                "capability_ref": f"cap_{'1' * 32}",
                "arguments": {f"k{index}": index for index in range(65)},
            },
            {
                "capability_ref": f"cap_{'1' * 32}",
                "arguments": {"blob": "x" * 20_000},
            },
            {
                "capability_ref": f"cap_{'1' * 32}",
                "idempotency_key": "has spaces",
            },
        ],
    )
    async def test_an_unbounded_request_is_refused_safely(
        self,
        payload: dict[str, object],
    ) -> None:
        harness = InvokeHarness()

        result = await harness.tool().ainvoke(payload)

        assert result == {
            "error": {
                "code": CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                "safe_message": "The capability discovery request is invalid.",
            }
        }

    def test_deeply_nested_arguments_are_refused(self) -> None:
        nested: dict[str, Any] = {"level": 1}
        for _ in range(12):
            nested = {"level": nested}

        with pytest.raises(ValidationError):
            CapabilityInvokeRequest(
                capability_ref=f"cap_{'1' * 32}",
                arguments=nested,
            )

    async def test_the_bridge_result_carries_no_raw_payload(self) -> None:
        harness = InvokeHarness()

        result = await harness.tool().ainvoke(
            {
                "capability_ref": harness.capability_ref,
                "arguments": {"secret": "do-not-echo"},
            }
        )

        encoded = json.dumps(result)
        assert "do-not-echo" not in encoded
        assert "drive_search" not in encoded
        assert len(encoded.encode()) < 2_000
