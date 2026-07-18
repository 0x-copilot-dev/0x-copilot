"""The registration seam gates code mode off by default and wires it on demand."""

from __future__ import annotations

from agent_runtime.capabilities.interpreter.code_mode_tool import (
    TOOL_NAME,
    RunIdentity,
)
from agent_runtime.capabilities.interpreter.contracts import ExternalFunctionSpec
from agent_runtime.capabilities.interpreter.monty_adapter import MontyInterpreterPort
from agent_runtime.capabilities.interpreter.ports import (
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
)
from agent_runtime.capabilities.interpreter.registration import (
    MontyCodeModeConfig,
    build_code_mode_tool,
    build_monty_interpreter,
    build_snapshot_store,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ObjectStoreSnapshotStore,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore


def _snapshot_store(tmp_path) -> ObjectStoreSnapshotStore:
    return build_snapshot_store(FileObjectStore(FileStoreLayout(tmp_path)))


_ENABLED = MontyCodeModeConfig(
    runtime_enable_monty=True,
    deployment_profile="single_user_desktop",
    interpreter_provider="monty",
)


class TestGateState:
    def test_default_config_is_disabled(self) -> None:
        assert MontyCodeModeConfig().enabled is False

    def test_all_gates_required(self) -> None:
        assert _ENABLED.enabled is True
        assert (
            MontyCodeModeConfig(
                runtime_enable_monty=True,
                deployment_profile="server",  # wrong profile
                interpreter_provider="monty",
            ).enabled
            is False
        )
        assert (
            MontyCodeModeConfig(
                runtime_enable_monty=True,
                deployment_profile="single_user_desktop",
                interpreter_provider="quickjs",  # wrong provider
            ).enabled
            is False
        )
        assert (
            MontyCodeModeConfig(
                runtime_enable_monty=False,  # master flag off
                deployment_profile="single_user_desktop",
                interpreter_provider="monty",
            ).enabled
            is False
        )

    def test_from_env_reads_and_defaults_off(self) -> None:
        assert MontyCodeModeConfig.from_env({}).enabled is False
        cfg = MontyCodeModeConfig.from_env(
            {
                "RUNTIME_ENABLE_MONTY": "true",
                "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
                "RUNTIME_INTERPRETER_PROVIDER": "monty",
                "RUNTIME_MONTY_LIMIT_PROFILE": "desktop_v1",
            }
        )
        assert cfg.enabled is True
        assert cfg.limit_profile_name == "desktop_v1"


class TestBuildInterpreter:
    def test_disabled_config_returns_none(self, tmp_path) -> None:
        port = build_monty_interpreter(
            MontyCodeModeConfig(), snapshot_store=_snapshot_store(tmp_path)
        )
        assert port is None

    def test_enabled_config_returns_port(self, tmp_path) -> None:
        # Monty is a pinned dependency, so availability holds in this suite.
        port = build_monty_interpreter(
            _ENABLED, snapshot_store=_snapshot_store(tmp_path)
        )
        assert isinstance(port, MontyInterpreterPort)


class _AllowInvoker:
    async def invoke(self, *, call, context: PolicyInvocationContext):
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.ALLOWED,
            invocation_id="inv",
            return_value=0,
        )


class _Resolver:
    def resolve(self, alias: str) -> ExternalFunctionSpec | None:
        return ExternalFunctionSpec(alias=alias, tool_name=f"tools.{alias}")


class TestBuildTool:
    def test_tool_has_stable_name_and_schema(self, tmp_path) -> None:
        port = build_monty_interpreter(
            _ENABLED, snapshot_store=_snapshot_store(tmp_path)
        )
        assert port is not None
        tool = build_code_mode_tool(
            port=port,
            policy_invoker=_AllowInvoker(),
            resolver=_Resolver(),
            identity_provider=lambda: RunIdentity(run_id="run-1"),
            config=_ENABLED,
        )
        assert tool.name == TOOL_NAME
        # The model-facing schema exposes only code/inputs/external_functions.
        fields = set(tool.args_schema.model_fields)
        assert fields == {"code", "inputs", "external_functions"}
