"""The worker-side F3 activation wiring: dark by default, switchable on demand."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
import subprocess
import sys

import pytest

from agent_runtime.capabilities.discovery import (
    CapabilityActivationMode,
    CapabilityActivationReason,
    CapabilityBridgeRegistrar,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogGeneration,
    CapabilityCatalogGenerationPort,
    CapabilityCatalogRevisionAuthority,
    CapabilityExpansionLimits,
    CapabilityRefRevalidation,
    CapabilitySource,
    CatalogDescriptorRevision,
)
from agent_runtime.capabilities.discovery.contracts import CapabilityExpansionBounds
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.control_plane.revision_binding import (
    RevalidationOutcome,
    RevisionAuthorityState,
    RevisionBindingRevalidator,
    RevisionBoundScope,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import _capability_bridge_tools
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.settings import RuntimeSettings
from runtime_worker.capability_discovery_composition import (
    CapabilityDiscoveryComposer,
    CapabilityDiscoveryEnvironment,
    RunCapabilityDiscovery,
    RunScopedCapabilityCatalogGeneration,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.run_control import RunControlAssignment


_NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
_SECRET = "f3-composition-deployment-secret-value"
_DEFERRED_ENV: Mapping[str, str] = {
    CapabilityDiscoveryEnvironment.ACTIVATION: "deferred",
    CapabilityDiscoveryEnvironment.REFERENCE_SECRET: _SECRET,
}


@pytest.fixture(autouse=True)
def _unconfigured_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the process environment to "operator configured nothing".

    Several cases assert what a deployment with no F3 configuration composes,
    and the dependency factory reads the presence gate from the process
    environment exactly as it does in the worker. Clearing the keys here keeps
    a developer's own shell from deciding the result.
    """

    for key in (
        CapabilityDiscoveryEnvironment.ACTIVATION,
        CapabilityDiscoveryEnvironment.CATALOG_TTL_SECONDS,
    ):
        monkeypatch.delenv(key, raising=False)


class CapabilityCompositionMixin:
    """Every fake, builder, and constant the F3 composition cases share."""

    RUN_A = "run_a"
    RUN_B = "run_b"

    # ---------------------------------------------------------------- context

    def context(
        self,
        *,
        run_id: str = RUN_A,
        connector_scopes: Mapping[str, frozenset[str]] | None = None,
        paused: frozenset[str] = frozenset(),
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_1",
            org_id="org_1",
            roles={"member"},
            permission_scopes={"docs:read"},
            connector_scopes=dict(
                connector_scopes
                if connector_scopes is not None
                else {"drive": frozenset({"docs:read"})}
            ),
            paused_connectors=paused,
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-test",
                max_input_tokens=32_000,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=run_id,
        )

    # --------------------------------------------------------- run control

    def binding(
        self,
        *,
        run_id: str = RUN_A,
        f3: FeatureMode = FeatureMode.ENFORCE,
    ) -> RunControlBinding:
        assignment = RunControlAssignment.safe_active_v1()
        snapshot = RunControlSnapshot.create(
            run_id=run_id,
            conversation_id="conv_1",
            subject_fingerprint="a" * 64,
            deployment_profile="single_user_desktop",
            harness_variant_ref=assignment.harness_variant_ref,
            task_policy_selection_ref=assignment.task_policy_selection_ref,
            policy_revisions=assignment.policy_revisions,
            feature_modes=FeatureModeSet(f3=f3),
            budget_envelope_ref=assignment.budget_envelope_ref,
            assignment_revision=assignment.assignment_revision,
        )
        return RunControlBinding(
            snapshot=snapshot,
            effective_modes=FeatureModeSet(f3=f3),
            decisions=(),
        )

    # ------------------------------------------------------------- sources

    @staticmethod
    def card(
        name: str = "drive_server",
        *,
        slug: str = "drive",
        server_id: str | None = None,
    ) -> McpServerCard:
        return McpServerCard(
            name=name,
            display_name="Drive Server",
            short_description="Find relevant drive records.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            required_scopes=frozenset({"docs:read"}),
            health=McpServerHealth.HEALTHY,
            load_cost=2,
            connector_slug=slug,
            server_id=server_id,
        )

    def card_source(self, *cards: McpServerCard) -> "CapabilityCompositionMixin._Cards":
        return self._Cards(cards or (self.card(),))

    class _Cards:
        def __init__(self, cards: Sequence[McpServerCard]) -> None:
            self.cards = tuple(cards)
            self.calls = 0

        def __call__(self, _context: AgentRuntimeContext) -> Sequence[McpServerCard]:
            self.calls += 1
            return self.cards

    class _Revisions:
        """A descriptor-revision source whose answer can move mid-run."""

        def __init__(self, revision: str = "rev-1") -> None:
            self.revision = revision
            self.calls = 0

        def __call__(
            self, _context: AgentRuntimeContext
        ) -> Sequence[CatalogDescriptorRevision]:
            self.calls += 1
            return (
                CatalogDescriptorRevision(
                    source_id="drive_server",
                    descriptor_revision=self.revision,
                ),
            )

    # ------------------------------------------------------------ composer

    def composer(
        self,
        *,
        cards: object | None = None,
        revisions: object | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> CapabilityDiscoveryComposer:
        return CapabilityDiscoveryComposer(
            card_source=cards if cards is not None else self.card_source(),
            descriptor_revision_source=revisions,
            environ=dict(environ if environ is not None else _DEFERRED_ENV),
            clock=lambda: _NOW,
        )

    def compose(
        self,
        composer: CapabilityDiscoveryComposer,
        context: AgentRuntimeContext,
        *,
        binding: RunControlBinding | None = None,
    ) -> RunCapabilityDiscovery | None:
        """Compose inside a bound run, mirroring the worker's own execution."""

        if binding is None:
            binding = self.binding(run_id=context.run_id)
        token = RunControlContext.bind_for_run(binding)
        try:
            return composer.compose(context)
        finally:
            RunControlContext.unbind(token)


class TestDarkByDefault(CapabilityCompositionMixin):
    """A deployment that has configured nothing keeps the pre-F3 surface."""

    def test_unconfigured_dependencies_carry_neither_f3_field(self) -> None:
        factory = DefaultRuntimeDependenciesFactory(RuntimeSettings.load(environ={}))
        context = self.context()

        token = RunControlContext.bind_for_run(self.binding())
        try:
            dependencies = factory(context)
        finally:
            RunControlContext.unbind(token)

        assert dependencies.capability_activation is None
        assert dependencies.capability_catalog is None

    def test_unconfigured_composes_the_byte_identical_pre_f3_tool_surface(
        self,
    ) -> None:
        """The composed bridge surface equals the hard-``None`` surface exactly."""

        factory = DefaultRuntimeDependenciesFactory(RuntimeSettings.load(environ={}))
        context = self.context()

        token = RunControlContext.bind_for_run(self.binding())
        try:
            dependencies = factory(context)
        finally:
            RunControlContext.unbind(token)

        composed = _capability_bridge_tools(
            activation=dependencies.capability_activation,
            catalog=dependencies.capability_catalog,
            runtime_context=context,
        )
        pre_f3 = _capability_bridge_tools(
            activation=None,
            catalog=None,
            runtime_context=context,
        )

        assert composed == pre_f3 == ()

    def test_unconfigured_never_imports_the_discovery_package(self) -> None:
        """The dark path's import graph is unchanged, not merely its output."""

        script = (
            "import sys\n"
            "from agent_runtime.execution.contracts import "
            "AgentRuntimeContext, ModelConfig\n"
            "from agent_runtime.settings import RuntimeSettings\n"
            "from runtime_worker.dependencies import "
            "DefaultRuntimeDependenciesFactory\n"
            "context = AgentRuntimeContext(\n"
            "    user_id='user_1',\n"
            "    org_id='org_1',\n"
            "    roles={'member'},\n"
            "    model_profile=ModelConfig(\n"
            "        provider='openai',\n"
            "        model_name='gpt-test',\n"
            "        max_input_tokens=32000,\n"
            "        timeout_seconds=30,\n"
            "        temperature=0,\n"
            "    ),\n"
            ")\n"
            "factory = DefaultRuntimeDependenciesFactory("
            "RuntimeSettings.load(environ={}))\n"
            "dependencies = factory(context)\n"
            "assert dependencies.capability_activation is None\n"
            "assert dependencies.capability_catalog is None\n"
            "leaked = [\n"
            "    name for name in sys.modules\n"
            "    if name.startswith('agent_runtime.capabilities.discovery')\n"
            "]\n"
            "print(','.join(sorted(leaked)))\n"
        )
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={
                **{
                    key: value
                    for key, value in _os_environ().items()
                    if not key.startswith("F3_")
                },
                "PYTHONPATH": _import_path(),
            },
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == ""

    def test_a_supplied_composer_still_stays_dark_without_configuration(self) -> None:
        composer = self.composer(environ={})
        result = self.compose(composer, self.context())

        assert result is None


def _os_environ() -> Mapping[str, str]:
    import os

    return dict(os.environ)


def _import_path() -> str:
    """Return the interpreter path the in-process test run already resolves with."""

    import os

    return os.pathsep.join(path for path in sys.path if path)


class TestConfiguredDeferredComposesTheBridge(CapabilityCompositionMixin):
    """``deferred`` plus an enforce-mode run registers the bounded bridge."""

    def test_deferred_composes_activation_and_catalog(self) -> None:
        result = self.compose(self.composer(), self.context())

        assert result is not None
        assert result.activation.effective_activation is (
            CapabilityActivationMode.DEFERRED
        )
        assert result.activation.reason is CapabilityActivationReason.CONFIGURED
        assert result.catalog.generation is not None
        assert [entry.source for entry in result.catalog.entries] == [
            CapabilitySource.MCP_SERVER
        ]

    def test_the_registrar_mounts_the_bounded_bridge_tools(self) -> None:
        result = self.compose(self.composer(), self.context())
        assert result is not None

        registrations = CapabilityBridgeRegistrar.registrations_for(
            activation=result.activation,
            catalog=result.catalog,
            runtime_context=self.context(),
        )

        assert tuple(registration.name for registration in registrations) == (
            CapabilityBridgeToolName.SEARCH_CAPABILITIES,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
        )

    def test_the_dependency_factory_threads_both_fields(self) -> None:
        factory = DefaultRuntimeDependenciesFactory(
            RuntimeSettings.load(environ={}),
            capability_discovery=self.composer(),
        )
        context = self.context()

        token = RunControlContext.bind_for_run(self.binding())
        try:
            dependencies = factory(context)
        finally:
            RunControlContext.unbind(token)

        assert isinstance(dependencies, RuntimeDependencies)
        assert dependencies.capability_activation is not None
        assert dependencies.capability_catalog is not None
        composed = _capability_bridge_tools(
            activation=dependencies.capability_activation,
            catalog=dependencies.capability_catalog,
            runtime_context=context,
        )
        assert tuple(str(getattr(tool, "name", "")) for tool in composed) == (
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        )

    def test_the_card_snapshot_argument_overrides_the_source(self) -> None:
        cards = self.card_source()
        factory = DefaultRuntimeDependenciesFactory(
            RuntimeSettings.load(environ={}),
            capability_discovery=self.composer(cards=cards),
        )
        context = self.context()

        token = RunControlContext.bind_for_run(self.binding())
        try:
            dependencies = factory.for_run(
                context,
                rollout_admission=E2RolloutAdmission(
                    resolution=factory.settings.execution.rollout,
                    cohorts=factory.settings.execution.rollout_cohorts,
                    kill_switches=factory.settings.execution.rollout_kill_switches,
                ),
                rollout_facts=PersistedRunCohortFactsProvider(
                    org_id=context.org_id,
                    user_id=context.user_id,
                ),
                mcp_server_cards=(self.card(name="mail_server", slug="mail"),),
            )
        finally:
            RunControlContext.unbind(token)

        catalog = dependencies.capability_catalog
        assert catalog is not None
        assert [entry.stable_name for entry in catalog.entries] == ["mail_server"]
        assert cards.calls == 0

    def test_the_generation_is_keyed_to_the_run_control_selection(self) -> None:
        binding = self.binding()
        result = self.compose(self.composer(), self.context(), binding=binding)

        assert result is not None
        generation = result.catalog.generation
        assert generation is not None
        assert generation.task_policy_selection_ref == (
            binding.snapshot.task_policy_selection_ref
        )
        assert result.catalog.scope.policy_revision == (
            binding.snapshot.policy_revisions.capability
        )
        assert result.catalog.scope.profile_id == binding.snapshot.deployment_profile


class TestConservativeDefaults(CapabilityCompositionMixin):
    """Unknown, narrower, and ceiling-clamped configuration all stay dark."""

    @pytest.mark.parametrize(
        "raw_activation",
        ["deferrred", "DEFERRED ON", "enabled", "true", "3", "   -   "],
    )
    def test_unparseable_activation_falls_to_the_conservative_default(
        self,
        raw_activation: str,
    ) -> None:
        composer = self.composer(
            environ={
                CapabilityDiscoveryEnvironment.ACTIVATION: raw_activation,
                CapabilityDiscoveryEnvironment.REFERENCE_SECRET: _SECRET,
            }
        )

        assert self.compose(composer, self.context()) is None
        decision = composer._activation()
        assert decision.effective_activation is CapabilityActivationMode.DIRECT
        assert decision.reason is CapabilityActivationReason.UNKNOWN_DEFAULTED_SAFE

    @pytest.mark.parametrize("raw_activation", ["direct", "server", "shadow"])
    def test_every_narrower_posture_keeps_the_pre_f3_path(
        self,
        raw_activation: str,
    ) -> None:
        composer = self.composer(
            environ={
                CapabilityDiscoveryEnvironment.ACTIVATION: raw_activation,
                CapabilityDiscoveryEnvironment.REFERENCE_SECRET: _SECRET,
            }
        )

        assert self.compose(composer, self.context()) is None

    @pytest.mark.parametrize("f3", [FeatureMode.OFF, FeatureMode.SHADOW])
    def test_the_run_feature_mode_is_a_hard_ceiling(self, f3: FeatureMode) -> None:
        composer = self.composer()
        binding = self.binding(f3=f3)

        result = self.compose(composer, self.context(), binding=binding)

        assert result is None
        token = RunControlContext.bind_for_run(binding)
        try:
            decision = composer._activation()
        finally:
            RunControlContext.unbind(token)
        assert decision.requested_activation is CapabilityActivationMode.DEFERRED
        assert decision.effective_activation is not CapabilityActivationMode.DEFERRED
        assert decision.reason is CapabilityActivationReason.FEATURE_MODE_CEILING

    def test_an_unbound_run_has_no_posture_and_stays_dark(self) -> None:
        assert RunControlContext.current() is None

        assert self.composer().compose(self.context()) is None

    @pytest.mark.parametrize(
        ("raw_ttl", "expected"),
        [
            ("", CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS),
            ("   ", CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS),
            ("abc", CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS),
            ("0", CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS),
            ("-5", CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS),
            ("99999", CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS),
            ("60", 60.0),
        ],
    )
    def test_an_invalid_ttl_defaults_rather_than_reaching_the_ceiling(
        self,
        raw_ttl: str,
        expected: float,
    ) -> None:
        resolved = CapabilityDiscoveryEnvironment.catalog_ttl_seconds(
            {CapabilityDiscoveryEnvironment.CATALOG_TTL_SECONDS: raw_ttl}
        )

        assert resolved == expected


class TestAnUnbuildableCatalogStaysOnTheFallbackPath(CapabilityCompositionMixin):
    """Anything that blocks a bindable catalog narrows to the pre-F3 path."""

    def test_no_card_source_registers_nothing(self) -> None:
        composer = CapabilityDiscoveryComposer(
            environ=dict(_DEFERRED_ENV),
            clock=lambda: _NOW,
        )

        assert self.compose(composer, self.context()) is None

    def test_configuring_deferred_without_a_card_snapshot_stays_dark(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The env knob is live, and on its own it still cannot widen anything."""

        monkeypatch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, "deferred")
        monkeypatch.setenv(
            CapabilityDiscoveryEnvironment.REFERENCE_SECRET,
            _SECRET,
        )
        factory = DefaultRuntimeDependenciesFactory(RuntimeSettings.load(environ={}))
        context = self.context()

        token = RunControlContext.bind_for_run(self.binding())
        try:
            dependencies = factory(context)
        finally:
            RunControlContext.unbind(token)

        assert dependencies.capability_activation is None
        assert dependencies.capability_catalog is None

    def test_an_empty_card_snapshot_registers_nothing(self) -> None:
        composer = self.composer(cards=lambda _context: ())

        assert self.compose(composer, self.context()) is None

    def test_a_card_no_longer_visible_to_the_run_registers_nothing(self) -> None:
        """The builder's defensive recheck can empty the projection."""

        composer = self.composer(
            cards=self.card_source(self.card(server_id="srv_drive"))
        )

        assert self.compose(composer, self.context()) is not None
        assert (
            self.compose(
                composer,
                self.context(paused=frozenset({"srv_drive"})),
            )
            is None
        )

    def test_a_raising_card_source_never_fails_the_run(self) -> None:
        def explode(_context: AgentRuntimeContext) -> Sequence[McpServerCard]:
            raise RuntimeError("card snapshot unavailable")

        assert self.compose(self.composer(cards=explode), self.context()) is None

    def test_a_missing_production_secret_registers_nothing(self) -> None:
        composer = self.composer(
            environ={
                CapabilityDiscoveryEnvironment.ACTIVATION: "deferred",
                CapabilityDiscoveryEnvironment.ENVIRONMENT: "production",
            }
        )

        assert self.compose(composer, self.context()) is None
        assert (
            CapabilityDiscoveryEnvironment.reference_key(
                run_id=self.RUN_A,
                environ={CapabilityDiscoveryEnvironment.ENVIRONMENT: "production"},
            )
            is None
        )

    def test_a_weak_secret_registers_nothing(self) -> None:
        assert (
            CapabilityDiscoveryEnvironment.reference_key(
                run_id=self.RUN_A,
                environ={CapabilityDiscoveryEnvironment.REFERENCE_SECRET: "short"},
            )
            is None
        )

    def test_development_derives_a_stable_key_without_a_secret(self) -> None:
        first = CapabilityDiscoveryEnvironment.reference_key(
            run_id=self.RUN_A, environ={}
        )
        second = CapabilityDiscoveryEnvironment.reference_key(
            run_id=self.RUN_A, environ={}
        )

        assert first is not None
        assert first == second
        assert len(first) == 32


class TestTheGenerationSourceAnswersFromCurrentAuthority(CapabilityCompositionMixin):
    """M-01: the live source recomputes; it never replays the held snapshot."""

    def _scope(self, result: RunCapabilityDiscovery) -> RevisionBoundScope:
        generation = result.catalog.generation
        assert generation is not None
        return RevisionBoundScope(
            subject_fingerprint=generation.subject_fingerprint,
            run_id=self.RUN_A,
            catalog_generation=generation.generation_ref,
        )

    async def test_a_moved_descriptor_revision_changes_the_live_answer(self) -> None:
        revisions = self._Revisions("rev-1")
        composer = self.composer(revisions=revisions)
        result = self.compose(composer, self.context())
        assert result is not None
        held = result.catalog.generation
        assert held is not None

        revisions.revision = "rev-2"
        token = RunControlContext.bind_for_run(self.binding())
        try:
            live = await result.generation_source.live_generation(
                scope=self._scope(result)
            )
        finally:
            RunControlContext.unbind(token)

        assert live.state is RevisionAuthorityState.ACTIVE
        assert live.generation is not None
        # The held catalog is still stamped with what it was projected from...
        assert result.catalog.generation is held
        # ...and the authority answers with what the inputs say *now*.
        assert not live.generation.is_same_generation(held)

    async def test_an_unchanged_run_answers_the_same_generation(self) -> None:
        revisions = self._Revisions("rev-1")
        composer = self.composer(revisions=revisions)
        result = self.compose(composer, self.context())
        assert result is not None
        held = result.catalog.generation
        assert held is not None

        token = RunControlContext.bind_for_run(self.binding())
        try:
            live = await result.generation_source.live_generation(
                scope=self._scope(result)
            )
        finally:
            RunControlContext.unbind(token)

        assert live.generation is not None
        assert live.generation.is_same_generation(held)
        assert live.generation is not held

    def test_the_source_satisfies_the_generation_port(self) -> None:
        """M-01: this is the production implementation of the F3-owned port."""

        result = self.compose(self.composer(), self.context())
        assert result is not None

        assert isinstance(
            result.generation_source,
            CapabilityCatalogGenerationPort,
        )

    async def test_the_source_holds_no_catalog_to_replay(self) -> None:
        """A source that answered from its own snapshot would validate itself."""

        revisions = self._Revisions("rev-1")
        result = self.compose(self.composer(revisions=revisions), self.context())
        assert result is not None
        held = result.catalog.generation
        assert held is not None

        source = result.generation_source
        retained = list(vars(source).values())
        # The re-reading callable is the one thing the source keeps, so a lazy
        # replay could only hide in its closure.
        resolver = getattr(source, "_resolve_inputs", None)
        retained.extend(
            cell.cell_contents
            for cell in (getattr(resolver, "__closure__", None) or ())
        )

        assert held not in retained
        assert result.catalog not in retained
        assert not any(
            isinstance(value, (CapabilityCatalogGeneration, CapabilityCatalog))
            for value in retained
        )

    async def test_a_moved_descriptor_revision_fails_a_bound_ref_closed(self) -> None:
        """End to end through the shared revalidator, not just the adapter."""

        revisions = self._Revisions("rev-1")
        result = self.compose(self.composer(revisions=revisions), self.context())
        assert result is not None
        binding_ref = result.catalog.bind_ref(result.catalog.entries[0].capability_ref)
        held = result.catalog.generation
        assert held is not None

        revalidation = CapabilityRefRevalidation(
            revalidator=RevisionBindingRevalidator(
                CapabilityCatalogRevisionAuthority(result.generation_source)
            ),
            subject_fingerprint=result.subject_fingerprint,
        )

        token = RunControlContext.bind_for_run(self.binding())
        try:
            current = await revalidation.decide(
                binding=binding_ref,
                run_id=self.RUN_A,
                live_generation=held,
            )
            revisions.revision = "rev-2"
            live_now = await result.generation_source.live_generation(
                scope=self._scope(result)
            )
            assert live_now.generation is not None
            superseded = await revalidation.decide(
                binding=binding_ref,
                run_id=self.RUN_A,
                live_generation=live_now.generation,
            )
        finally:
            RunControlContext.unbind(token)

        assert current.outcome is RevalidationOutcome.CURRENT
        assert superseded.outcome is not RevalidationOutcome.CURRENT
        assert not superseded.outcome.admits_use

    async def test_a_foreign_subject_scope_is_never_resolved(self) -> None:
        result = self.compose(self.composer(), self.context())
        assert result is not None

        live = await result.generation_source.live_generation(
            scope=RevisionBoundScope(
                subject_fingerprint="f" * 64,
                run_id=self.RUN_A,
            )
        )

        assert live.state is RevisionAuthorityState.UNKNOWN
        assert live.generation is None

    async def test_a_foreign_run_scope_is_never_resolved(self) -> None:
        result = self.compose(self.composer(), self.context())
        assert result is not None
        generation = result.catalog.generation
        assert generation is not None

        live = await result.generation_source.live_generation(
            scope=RevisionBoundScope(
                subject_fingerprint=generation.subject_fingerprint,
                run_id=self.RUN_B,
            )
        )

        assert live.state is RevisionAuthorityState.UNKNOWN

    async def test_unresolvable_inputs_report_unavailable(self) -> None:
        source = RunScopedCapabilityCatalogGeneration(
            subject_fingerprint="b" * 64,
            run_id=self.RUN_A,
            resolve_inputs=lambda: None,
        )

        live = await source.live_generation(
            scope=RevisionBoundScope(subject_fingerprint="b" * 64, run_id=self.RUN_A)
        )

        assert live.state is RevisionAuthorityState.UNAVAILABLE
        assert live.generation is None

    async def test_a_raising_input_resolver_reports_unavailable(self) -> None:
        def explode() -> None:
            raise RuntimeError("run control vanished")

        source = RunScopedCapabilityCatalogGeneration(
            subject_fingerprint="b" * 64,
            run_id=self.RUN_A,
            resolve_inputs=explode,  # type: ignore[arg-type]
        )

        live = await source.live_generation(
            scope=RevisionBoundScope(subject_fingerprint="b" * 64, run_id=self.RUN_A)
        )

        assert live.state is RevisionAuthorityState.UNAVAILABLE

    async def test_an_unbound_run_makes_the_source_unavailable(self) -> None:
        result = self.compose(self.composer(), self.context())
        assert result is not None
        assert RunControlContext.current() is None

        live = await result.generation_source.live_generation(scope=self._scope(result))

        assert live.state is RevisionAuthorityState.UNAVAILABLE


class TestNothingLeaksAcrossRuns(CapabilityCompositionMixin):
    """The key, the catalog, and the authority are all bounded by one run."""

    def test_two_runs_mint_different_references_for_the_same_capability(self) -> None:
        composer = self.composer()
        first = self.compose(composer, self.context(run_id=self.RUN_A))
        second = self.compose(
            composer,
            self.context(run_id=self.RUN_B),
            binding=self.binding(run_id=self.RUN_B),
        )
        assert first is not None
        assert second is not None

        assert first.catalog.entries[0].stable_name == (
            second.catalog.entries[0].stable_name
        )
        assert first.catalog.entries[0].capability_ref != (
            second.catalog.entries[0].capability_ref
        )
        assert first.catalog.revision.catalog_id != second.catalog.revision.catalog_id
        assert first.subject_fingerprint != second.subject_fingerprint

    def test_recomposing_one_run_reproduces_identical_references(self) -> None:
        """The generation source depends on this: rebuild must be reproducible."""

        composer = self.composer()
        context = self.context()
        first = self.compose(composer, context)
        second = self.compose(composer, context)
        assert first is not None
        assert second is not None

        assert first.catalog.entries == second.catalog.entries
        assert first.catalog.revision.catalog_id == second.catalog.revision.catalog_id
        assert first.subject_fingerprint == second.subject_fingerprint
        assert first.catalog.generation is not None
        assert second.catalog.generation is not None
        assert first.catalog.generation.is_same_generation(second.catalog.generation)

    def test_a_different_connector_scope_is_a_different_generation(self) -> None:
        composer = self.composer()
        wide = self.compose(composer, self.context())
        narrow = self.compose(
            composer,
            self.context(
                connector_scopes={
                    "drive": frozenset({"docs:read"}),
                    "mail": frozenset({"mail:read"}),
                }
            ),
        )
        assert wide is not None
        assert narrow is not None
        assert wide.catalog.generation is not None
        assert narrow.catalog.generation is not None

        assert wide.catalog.scope.connector_scope_revision != (
            narrow.catalog.scope.connector_scope_revision
        )
        assert not wide.catalog.generation.is_same_generation(narrow.catalog.generation)

    def test_the_catalog_expires_with_the_run(self) -> None:
        result = self.compose(self.composer(), self.context())

        assert result is not None
        assert result.catalog.revision.expires_at == _NOW + timedelta(
            seconds=CapabilityDiscoveryEnvironment.DEFAULT_TTL_SECONDS
        )
        assert result.catalog.is_active_for(self.context(), now=_NOW)
        assert not result.catalog.is_active_for(
            self.context(),
            now=_NOW + timedelta(days=1),
        )


class TestActivationVocabulary(CapabilityCompositionMixin):
    """The presence pre-gate must not become a second mode vocabulary."""

    @pytest.mark.parametrize(
        ("raw", "configured"),
        [(None, False), ("", False), ("   ", False), ("nonsense", True)],
    )
    def test_presence_is_all_the_pre_gate_reads(
        self,
        raw: str | None,
        configured: bool,
    ) -> None:
        environ = (
            {} if raw is None else {CapabilityDiscoveryEnvironment.ACTIVATION: raw}
        )

        assert CapabilityDiscoveryEnvironment.is_configured(environ) is configured

    def test_the_feature_mode_vocabulary_is_the_only_one_consulted(self) -> None:
        composer = self.composer()
        binding = self.binding()

        token = RunControlContext.bind_for_run(binding)
        try:
            decision = composer._activation()
        finally:
            RunControlContext.unbind(token)

        assert decision.mode.feature is AgentQualityFeature.F3_CAPABILITY_DISCOVERY
        assert decision.mode.effective_mode is FeatureMode.ENFORCE


class _RecordingRevisionResolver:
    """An F8 resolver whose answer moves, refuses, or disappears on demand.

    Only the resolver's two-method surface is modelled here. What is under test
    is what the composition root *does* with the answers -- the resolver has its
    own suite next door, and the wired-together case is proved against the
    production resolver in ``test_step8_exit_criteria``.
    """

    class _Result:
        def __init__(self, revision: object | None) -> None:
            self.revision = revision

    class _Revision:
        def __init__(self, revision: str) -> None:
            self.revision = revision
            self.profile_id = "profile-a"
            self.subject_scope_hash = "scope-a"

    def __init__(
        self,
        revisions: Mapping[str, str] | None = None,
        *,
        raises: bool = False,
    ) -> None:
        self.revisions = dict(revisions or {})
        self.raises = raises
        self.registered: list[tuple[str, str]] = []
        self.resolved: list[str] = []

    async def register(
        self, *, org_id: str, user_id: str, server_name: str, server_id: str
    ) -> None:
        del org_id, user_id
        self.registered.append((server_name, server_id))

    async def resolve(
        self, *, org_id: str, user_id: str, server_name: str
    ) -> "_RecordingRevisionResolver._Result":
        del org_id, user_id
        self.resolved.append(server_name)
        if self.raises:
            raise RuntimeError("revision authority unreachable")
        revision = self.revisions.get(server_name)
        return self._Result(None if revision is None else self._Revision(revision))


class TestTheDescriptorRevisionSourceNarrowsOnly(CapabilityCompositionMixin):
    """BUG-12's wiring: what the composition root folds, and what it refuses to.

    A composed catalog generation is only as trustworthy as the revisions it
    keys on, so every way a revision can fail to resolve has to remove
    capability rather than add it.
    """

    def composer_with(self, resolver: object | None) -> CapabilityDiscoveryComposer:
        return CapabilityDiscoveryComposer(
            descriptor_revision_resolver=resolver,  # type: ignore[arg-type]
            environ=dict(_DEFERRED_ENV),
            clock=lambda: _NOW,
        )

    async def acompose(
        self,
        composer: CapabilityDiscoveryComposer,
        *cards: McpServerCard,
    ) -> RunCapabilityDiscovery | None:
        context = self.context()
        token = RunControlContext.bind_for_run(self.binding(run_id=context.run_id))
        try:
            return await composer.acompose(
                context,
                mcp_server_cards=cards or (self.card(server_id="srv_drive"),),
            )
        finally:
            RunControlContext.unbind(token)

    async def test_a_resolved_revision_is_folded_into_the_generation(self) -> None:
        resolver = _RecordingRevisionResolver({"drive_server": "rev-1"})

        result = await self.acompose(self.composer_with(resolver))

        assert result is not None
        assert result.catalog.generation is not None
        assert result.catalog.generation.descriptor_revision_count == 1
        assert resolver.registered == [("drive_server", "srv_drive")]

    async def test_no_resolver_folds_nothing_at_all(self) -> None:
        """Feature-off parity: F8 unconfigured composes what it always did."""

        result = await self.acompose(self.composer_with(None))

        assert result is not None
        assert result.catalog.generation is not None
        assert result.catalog.generation.descriptor_revision_count == 0

    async def test_an_untracked_server_contributes_no_revision(self) -> None:
        """``not_found`` is an answer, not a revision, and is never invented."""

        result = await self.acompose(self.composer_with(_RecordingRevisionResolver()))

        assert result is not None
        assert result.catalog.generation is not None
        assert result.catalog.generation.descriptor_revision_count == 0

    async def test_an_unreachable_authority_never_fails_the_run(self) -> None:
        """A dark feature may narrow a run; it may not break one."""

        resolver = _RecordingRevisionResolver(raises=True)

        result = await self.acompose(self.composer_with(resolver))

        assert result is not None
        assert result.catalog.generation is not None
        assert result.catalog.generation.descriptor_revision_count == 0
        assert resolver.resolved == ["drive_server"]

    async def test_a_card_without_a_server_id_is_skipped_rather_than_guessed(
        self,
    ) -> None:
        """The resolver is keyed by the backend's id; there is nothing to invent."""

        resolver = _RecordingRevisionResolver({"drive_server": "rev-1"})

        result = await self.acompose(
            self.composer_with(resolver),
            self.card(server_id=None),
        )

        assert result is not None
        assert result.catalog.generation is not None
        assert result.catalog.generation.descriptor_revision_count == 0
        assert resolver.registered == []

    async def test_the_live_authority_re_reads_rather_than_replaying(self) -> None:
        """The whole point of the wiring, at the composer's own boundary."""

        resolver = _RecordingRevisionResolver({"drive_server": "rev-1"})
        result = await self.acompose(self.composer_with(resolver))
        assert result is not None
        generation = result.catalog.generation
        assert generation is not None
        scope = RevisionBoundScope(
            subject_fingerprint=generation.subject_fingerprint,
            run_id=self.RUN_A,
            catalog_generation=generation.generation_ref,
        )

        token = RunControlContext.bind_for_run(self.binding(run_id=self.RUN_A))
        try:
            before = await result.generation_source.live_generation(scope=scope)
            resolver.revisions["drive_server"] = "rev-2"
            after = await result.generation_source.live_generation(scope=scope)
        finally:
            RunControlContext.unbind(token)

        assert before.state is RevisionAuthorityState.ACTIVE
        assert after.state is RevisionAuthorityState.ACTIVE
        assert before.generation != after.generation

    async def test_an_authority_that_goes_dark_mid_run_narrows_the_answer(
        self,
    ) -> None:
        """Losing the authority removes the revision, so the ref stops matching.

        The refusal is the point: an authority that cannot be reached is not
        evidence that a reference is still good.
        """

        resolver = _RecordingRevisionResolver({"drive_server": "rev-1"})
        result = await self.acompose(self.composer_with(resolver))
        assert result is not None
        generation = result.catalog.generation
        assert generation is not None
        scope = RevisionBoundScope(
            subject_fingerprint=generation.subject_fingerprint,
            run_id=self.RUN_A,
            catalog_generation=generation.generation_ref,
        )

        token = RunControlContext.bind_for_run(self.binding(run_id=self.RUN_A))
        try:
            resolver.raises = True
            after = await result.generation_source.live_generation(scope=scope)
        finally:
            RunControlContext.unbind(token)

        assert after.state is RevisionAuthorityState.ACTIVE
        assert after.generation != generation


class TestTheExpansionLimitsReachTheBridge(CapabilityCompositionMixin):
    """BUG-13's wiring: the operator's bounds, resolved once at this root."""

    def limits_for(self, environ: Mapping[str, str]) -> object:
        composer = CapabilityDiscoveryComposer(
            card_source=self.card_source(),
            environ=dict(environ),
            clock=lambda: _NOW,
        )
        result = self.compose(composer, self.context())
        assert result is not None
        return result.bridge.expansion_limits

    def test_the_composed_bridge_carries_resolved_limits(self) -> None:
        limits = self.limits_for(
            {**_DEFERRED_ENV, "F3_DISCOVERY_MAX_EXPANDED_SERVERS": "5"}
        )

        assert limits is not None
        assert limits.max_servers == 5  # type: ignore[attr-defined]

    def test_an_unconfigured_deployment_carries_the_conservative_defaults(
        self,
    ) -> None:
        limits = self.limits_for(_DEFERRED_ENV)

        assert limits == CapabilityExpansionLimits()

    @pytest.mark.parametrize("raw", ["999", "-1", "not-a-number", "", "   "])
    def test_an_unreadable_bound_resolves_down_and_never_up(self, raw: str) -> None:
        """A typo may only remove fan-out. It may never buy more of it."""

        limits = self.limits_for(
            {**_DEFERRED_ENV, "F3_DISCOVERY_MAX_EXPANDED_SERVERS": raw}
        )

        assert limits is not None
        assert limits.max_servers == CapabilityExpansionLimits().max_servers  # type: ignore[attr-defined]
        assert limits.max_servers < CapabilityExpansionBounds.MAX_SERVERS  # type: ignore[attr-defined]
