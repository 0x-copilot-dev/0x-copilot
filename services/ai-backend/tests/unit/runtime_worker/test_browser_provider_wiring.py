"""Gated composition of the device-local browser MCP provider (AC8).

The browser provider is composed into the ``DynamicMcpRegistry`` provider tuple
ONLY when ``RUNTIME_ENABLE_DESKTOP_BROWSER`` + ``single_user_desktop`` + a
browser broker URL/token are all present. Off that path it is absent and the
registry is byte-identical (``EmptyMcpRegistry`` when nothing else is
configured).
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.capabilities.browser.constants import BrowserEnv
from agent_runtime.capabilities.browser.desktop_browser_provider import (
    DesktopBrowserMcpProvider,
)
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.settings import RuntimeSettings
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
    EmptyMcpRegistry,
)

_ON = {
    BrowserEnv.FLAG: "1",
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
    BrowserEnv.BROKER_URL: "http://127.0.0.1:8842",
    BrowserEnv.BROKER_TOKEN: "browser-boot-token",
}


_BROWSER_CAPABILITIES = (
    RolloutCapability.OPERATION_GATEWAY,
    RolloutCapability.EFFECT_STAGER,
    RolloutCapability.EFFECT_COMMIT,
    RolloutCapability.BROWSER_ADAPTER,
)

_MCP_CAPABILITIES = (
    RolloutCapability.OPERATION_GATEWAY,
    RolloutCapability.EFFECT_STAGER,
    RolloutCapability.EFFECT_COMMIT,
    RolloutCapability.MCP_GATEWAY,
)


def _factory(*, e2_enabled: bool = False) -> DefaultRuntimeDependenciesFactory:
    """Build the browser registry factory with an optional full E2 cohort."""

    environment: dict[str, str] = {}
    if e2_enabled:
        environment = {
            **_ON,
            "SURFACES_V2": "true",
            "ARTIFACT_EFFECTS_V2": "true",
            "ARTIFACT_DRAFTS_V2": "true",
            "OPERATION_GATEWAY_MODE": "enforce",
            "EFFECT_STAGER_MODE": "enforce",
            "EFFECT_COMMIT_MODE": "enforce",
            "BROWSER_ADAPTER_MODE": "enforce",
            "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                [
                    {
                        "capability": capability.value,
                        "org_id": "org_456",
                        "user_id": "user_123",
                    }
                    for capability in _BROWSER_CAPABILITIES
                ]
            ),
        }
    return DefaultRuntimeDependenciesFactory(RuntimeSettings.load(environ=environment))


def _admission(factory: DefaultRuntimeDependenciesFactory) -> E2RolloutAdmission:
    return E2RolloutAdmission(
        resolution=factory.settings.execution.rollout,
        cohorts=factory.settings.execution.rollout_cohorts,
        kill_switches=factory.settings.execution.rollout_kill_switches,
    )


def _mcp_factory() -> DefaultRuntimeDependenciesFactory:
    """Build a generic backend-MCP registry under explicit E2 control."""

    return DefaultRuntimeDependenciesFactory(
        RuntimeSettings.load(
            environ={
                "MCP_BACKEND_REGISTRY_URL": "http://backend.example.test",
                "OPERATION_GATEWAY_MODE": "enforce",
                "EFFECT_STAGER_MODE": "enforce",
                "EFFECT_COMMIT_MODE": "enforce",
                "MCP_GATEWAY_MODE": "enforce",
                "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                    [
                        {
                            "capability": capability.value,
                            "org_id": "org_456",
                            "user_id": "user_123",
                        }
                        for capability in _MCP_CAPABILITIES
                    ]
                ),
            }
        )
    )


def _facts(*, user_id: str = "user_123") -> PersistedRunCohortFactsProvider:
    return PersistedRunCohortFactsProvider(org_id="org_456", user_id=user_id)


def _set_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key in (
        BrowserEnv.FLAG,
        "ENTERPRISE_DEPLOYMENT_PROFILE",
        BrowserEnv.BROKER_URL,
        BrowserEnv.BROKER_TOKEN,
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


class TestBrowserProviderGating:
    def test_absent_by_default(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, {})
        assert _factory()._browser_provider(runtime_context_admin) is None
        assert isinstance(
            _factory()._mcp_registry(runtime_context_admin), EmptyMcpRegistry
        )

    def test_composed_when_flag_desktop_and_broker(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, _ON)
        factory = _factory(e2_enabled=True)
        admission = _admission(factory)
        provider = factory._browser_provider(
            runtime_context_admin,
            rollout_admission=admission,
            rollout_facts=_facts(),
        )
        assert isinstance(provider, DesktopBrowserMcpProvider)

        registry = factory._mcp_registry(
            runtime_context_admin,
            rollout_admission=admission,
            rollout_facts=_facts(),
        )
        assert isinstance(registry, DynamicMcpRegistry)
        assert any(isinstance(p, DesktopBrowserMcpProvider) for p in registry.providers)

    def test_no_direct_browser_activation_path_without_persisted_rollout_facts(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, _ON)
        factory = _factory(e2_enabled=True)

        assert factory._browser_provider(runtime_context_admin) is None
        assert isinstance(
            factory._mcp_registry(runtime_context_admin), EmptyMcpRegistry
        )

    def test_nonmatching_cohort_cannot_expose_browser_provider(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, _ON)
        factory = _factory(e2_enabled=True)

        assert (
            factory._browser_provider(
                runtime_context_admin,
                rollout_admission=_admission(factory),
                rollout_facts=_facts(user_id="user_not_enrolled"),
            )
            is None
        )

    def test_absent_off_desktop_profile(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, {**_ON, "ENTERPRISE_DEPLOYMENT_PROFILE": "server"})
        factory = _factory(e2_enabled=True)
        assert (
            factory._browser_provider(
                runtime_context_admin,
                rollout_admission=_admission(factory),
                rollout_facts=_facts(),
            )
            is None
        )

    def test_absent_without_broker_credentials(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        env = {k: v for k, v in _ON.items() if k != BrowserEnv.BROKER_TOKEN}
        _set_env(monkeypatch, env)
        factory = _factory(e2_enabled=True)
        assert (
            factory._browser_provider(
                runtime_context_admin,
                rollout_admission=_admission(factory),
                rollout_facts=_facts(),
            )
            is None
        )

    def test_absent_when_flag_off(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, {**_ON, BrowserEnv.FLAG: "0"})
        factory = _factory(e2_enabled=True)
        assert (
            factory._browser_provider(
                runtime_context_admin,
                rollout_admission=_admission(factory),
                rollout_facts=_facts(),
            )
            is None
        )


class TestBackendMcpProviderRolloutGating:
    """Generic MCP card discovery is an exposure boundary, not just invocation."""

    def test_generic_factory_cannot_expose_cards_without_verified_cohort_facts(
        self, runtime_context_admin
    ) -> None:
        factory = _mcp_factory()

        # ``__call__`` is intentionally subject-less. Once E2 controls MCP, it
        # must not leak the backend registry into an unscoped composition path.
        dependencies = factory(runtime_context_admin)
        assert isinstance(dependencies.mcp_registry, EmptyMcpRegistry)

    def test_nonmatching_cohort_hides_backend_mcp_cards_on_run_and_resume(
        self, runtime_context_admin
    ) -> None:
        factory = _mcp_factory()
        admission = _admission(factory)
        denied_facts = _facts(user_id="user_not_enrolled")

        registry = factory._mcp_registry(
            runtime_context_admin,
            rollout_admission=admission,
            rollout_facts=denied_facts,
        )
        resumed = factory.for_run(
            runtime_context_admin,
            rollout_admission=admission,
            rollout_facts=denied_facts,
        )

        assert isinstance(registry, EmptyMcpRegistry)
        # ``RuntimeApprovalHandler._dependencies_for_resume`` calls ``for_run``;
        # this pins card discovery on approval resume as well as initial setup.
        assert isinstance(resumed.mcp_registry, EmptyMcpRegistry)

    def test_admitted_cohort_exposes_backend_mcp_cards_once(
        self, runtime_context_admin
    ) -> None:
        factory = _mcp_factory()
        admission = _admission(factory)

        registry = factory._mcp_registry(
            runtime_context_admin,
            rollout_admission=admission,
            rollout_facts=_facts(),
        )

        assert isinstance(registry, DynamicMcpRegistry)
        assert [type(provider) for provider in registry.providers] == [
            BackendMcpProvider
        ]
