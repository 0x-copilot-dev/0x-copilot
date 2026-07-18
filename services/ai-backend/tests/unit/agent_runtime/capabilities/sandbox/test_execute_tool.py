"""The ``run_in_sandbox`` execute-only tool provisions, runs, and tears down."""

from __future__ import annotations

import json

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import SandboxProviderId
from agent_runtime.capabilities.sandbox.execute_tool import (
    TOOL_NAME,
    SandboxExecuteToolFactory,
    SandboxRunIdentity,
)
from agent_runtime.capabilities.sandbox.seam import build_sandbox_backend
from tests.unit.agent_runtime.capabilities.sandbox.fakes import FakeSandboxProvider


def _active_config() -> RemoteSandboxConfig:
    return RemoteSandboxConfig.from_env(
        {
            "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
            "RUNTIME_SANDBOX_PROVIDER": "langsmith",
        }
    )


def _build_tool(provider: FakeSandboxProvider):
    config = _active_config()
    service = build_sandbox_backend(
        config,
        provider_overrides={SandboxProviderId.LANGSMITH: provider},
    )
    assert service is not None
    return SandboxExecuteToolFactory.build(
        service=service,
        identity_provider=lambda: SandboxRunIdentity(run_id="run-1"),
        config=config,
    )


class TestRunInSandbox:
    def test_tool_identity(self) -> None:
        tool = _build_tool(FakeSandboxProvider())
        assert tool.name == TOOL_NAME
        assert set(tool.args_schema.model_fields) == {"command"}

    async def test_executes_and_tears_down(self) -> None:
        provider = FakeSandboxProvider()
        tool = _build_tool(provider)

        raw = await tool.ainvoke({"command": "echo:hi"})
        payload = json.loads(raw)

        assert payload["status"] == "completed"
        assert payload["output"] == "hi"
        assert payload["exit_code"] == 0
        # session_scope guaranteed teardown ran (the run's session was reaped).
        assert provider.terminated_refs == ["fake-" + _last_idem(provider)]

    async def test_nonzero_exit_code_surfaced(self) -> None:
        tool = _build_tool(FakeSandboxProvider())
        payload = json.loads(await tool.ainvoke({"command": "exit:3"}))
        assert payload["status"] == "completed"
        assert payload["exit_code"] == 3


def _last_idem(provider: FakeSandboxProvider) -> str:
    # The fake keys handles by idempotency key; the tool mints one per call.
    return next(iter(provider._by_idempotency))
