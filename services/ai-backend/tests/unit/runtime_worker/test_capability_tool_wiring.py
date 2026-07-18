"""Per-run gating for the Wave-1 capability tools.

Each tool is built ONLY when its server-side gate holds (flag(s) + the
``single_user_desktop`` profile, plus — for Monty — the file object store) and is
``None`` otherwise. ``None`` everywhere is what keeps non-desktop / disabled runs
byte-identical.
"""

from __future__ import annotations

from types import SimpleNamespace

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


def _wiring(env, *, file_store=None) -> CapabilityToolWiring:
    return CapabilityToolWiring(
        runtime_context=_context(), file_store=file_store, env=env
    )


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


class TestSandboxGating:
    def test_built_when_flag_and_desktop_and_provider(self) -> None:
        tool = _wiring(_SANDBOX_ON).sandbox_execute_tool()
        assert tool is not None
        assert getattr(tool, "name", None) == "run_in_sandbox"

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
