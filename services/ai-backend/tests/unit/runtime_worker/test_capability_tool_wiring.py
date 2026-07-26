"""Per-run gating for the Wave-1 capability tools.

Each tool is built ONLY when its server-side gate holds (flag(s) + the
``single_user_desktop`` profile, plus — for Monty — the file object store) and is
``None`` otherwise. ``None`` everywhere is what keeps non-desktop / disabled runs
byte-identical.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore
from runtime_worker.capability_tool_wiring import CapabilityToolWiring

_MONTY_ON = {
    "RUNTIME_ENABLE_MONTY": "true",
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
    "RUNTIME_INTERPRETER_PROVIDER": "monty",
}
_SANDBOX_ON = {
    "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
    "RUNTIME_SANDBOX_PROVIDER": "langsmith",
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
}


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_1",
        org_id="org_1",
        roles={"member"},
        model_profile=ModelConfig(
            provider="fake",
            model_name="fake-model",
            max_input_tokens=128_000,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run_1",
    )


def _file_store(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(object_store=FileObjectStore(FileStoreLayout(tmp_path)))


def _wiring(
    env,
    *,
    file_store=None,
    external_tools=None,
    sandbox_tool_factory=None,
) -> CapabilityToolWiring:
    return CapabilityToolWiring(
        runtime_context=_context(),
        file_store=file_store,
        env=env,
        external_tools_by_name=external_tools,
        sandbox_tool_factory=sandbox_tool_factory,
    )


class _SandboxToolFactory:
    def __init__(self, tool: object | None) -> None:
        self.tool = tool
        self.identity_provider: Callable[[], object] | None = None

    def build_tool(self, *, identity_provider: Callable[[], object]) -> object | None:
        self.identity_provider = identity_provider
        return self.tool


class TestCodeModeGating:
    def test_built_when_gates_on_and_store_present(self, tmp_path) -> None:
        tool = _wiring(_MONTY_ON, file_store=_file_store(tmp_path)).code_mode_tool()
        assert tool is not None
        assert getattr(tool, "name", None) == "run_code_mode"

    def test_absent_when_disabled(self, tmp_path) -> None:
        assert _wiring({}, file_store=_file_store(tmp_path)).code_mode_tool() is None

    def test_absent_off_desktop_profile(self, tmp_path) -> None:
        env = {**_MONTY_ON, "ENTERPRISE_DEPLOYMENT_PROFILE": "server"}
        assert _wiring(env, file_store=_file_store(tmp_path)).code_mode_tool() is None

    def test_absent_without_object_store(self) -> None:
        # Gates on but no file backend (no object store) → fail soft to absent.
        assert _wiring(_MONTY_ON, file_store=None).code_mode_tool() is None

    def test_absent_wrong_provider(self, tmp_path) -> None:
        env = {**_MONTY_ON, "RUNTIME_INTERPRETER_PROVIDER": "quickjs"}
        assert _wiring(env, file_store=_file_store(tmp_path)).code_mode_tool() is None


class TestCodeModePosture:
    """Option A (pure-compute) vs Option B (real external calls) selection."""

    def test_pure_compute_when_no_external_toolset(self, tmp_path) -> None:
        # No toolset supplied → fail-closed pure-compute pair.
        wiring = _wiring(_MONTY_ON, file_store=_file_store(tmp_path))
        invoker, resolver = wiring._code_mode_policy()
        from agent_runtime.capabilities.interpreter.pure_compute import (
            ClosedPolicyInvoker,
            PureComputeResolver,
        )

        assert isinstance(invoker, ClosedPolicyInvoker)
        assert isinstance(resolver, PureComputeResolver)
        # The tool still builds (gates hold); it simply has no tool surface.
        assert wiring.code_mode_tool() is not None

    def test_option_b_when_external_toolset_present(self, tmp_path) -> None:
        wiring = _wiring(
            _MONTY_ON,
            file_store=_file_store(tmp_path),
            external_tools={"search": object()},
        )
        invoker, resolver = wiring._code_mode_policy()
        from agent_runtime.capabilities.interpreter.policy_invoker import (
            AuthorizedToolResolver,
            HitlPolicyToolInvoker,
        )

        assert isinstance(invoker, HitlPolicyToolInvoker)
        assert isinstance(resolver, AuthorizedToolResolver)
        assert wiring.code_mode_tool() is not None


class TestContextBudgetGuard:
    """The interpreter budget bridge reuses the run's active ToolBudgetGuard."""

    async def test_admits_when_no_guard_bound(self) -> None:
        # Non-desktop / eval runs install no guard; parity with
        # ToolBudgetGuardedTool's ``guard is None`` path → admit.
        from runtime_worker.capability_tool_wiring import _ContextBudgetGuard

        guard = _ContextBudgetGuard()
        admitted = await guard.charge(
            tool_name="tools.search_web", arguments={"q": "x"}, context=object()
        )
        assert admitted is True


class TestSandboxGating:
    def test_uses_only_an_injected_worker_owned_gateway_factory(self) -> None:
        tool = object()
        factory = _SandboxToolFactory(tool)

        assert (
            _wiring(
                _SANDBOX_ON,
                sandbox_tool_factory=factory,
            ).sandbox_execute_tool()
            is tool
        )
        assert factory.identity_provider is not None
        identity = factory.identity_provider()
        assert getattr(identity, "run_id", None) == "run_1"

    def test_desktop_config_without_a_complete_worker_bundle_is_absent(self) -> None:
        # A flag/provider-shaped environment is never permission to restore the
        # retired direct RemoteExecutionService construction path.
        assert _wiring(_SANDBOX_ON).sandbox_execute_tool() is None

    def test_absent_when_production_provider_cannot_attest_isolation(self) -> None:
        # D3 must not register a degraded execution surface. The shipped
        # LangSmith adapter is selected by this deployment config but cannot
        # yet prove the required isolation controls, so capability discovery
        # honestly omits it.
        assert _wiring(_SANDBOX_ON).sandbox_execute_tool() is None

    def test_absent_when_disabled(self) -> None:
        assert _wiring({}).sandbox_execute_tool() is None

    def test_absent_off_desktop_profile(self) -> None:
        env = {**_SANDBOX_ON, "ENTERPRISE_DEPLOYMENT_PROFILE": "server"}
        assert _wiring(env).sandbox_execute_tool() is None

    def test_absent_without_provider(self) -> None:
        env = {
            "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
            "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
        }
        assert _wiring(env).sandbox_execute_tool() is None
