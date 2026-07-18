"""Provider registry, session store, and factory-seam tests."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
)
from agent_runtime.capabilities.sandbox.seam import build_sandbox_backend
from tests.unit.agent_runtime.capabilities.sandbox.fakes import (
    FakeSandboxProvider,
    make_request,
)


def _active_config() -> RemoteSandboxConfig:
    return RemoteSandboxConfig.from_env(
        {
            "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
            "RUNTIME_SANDBOX_PROVIDER": "langsmith",
        }
    )


class TestRegistry:
    def test_disabled_config_raises(self) -> None:
        with pytest.raises(SandboxError) as excinfo:
            SandboxProviderRegistry.from_config(RemoteSandboxConfig.from_env({}))
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_DISABLED

    def test_override_binds_fake(self) -> None:
        provider = FakeSandboxProvider()
        registry = SandboxProviderRegistry.from_config(
            _active_config(), overrides={SandboxProviderId.LANGSMITH: provider}
        )
        assert registry.provider is provider
        assert registry.provider_id is SandboxProviderId.LANGSMITH


class TestSessionStore:
    async def test_upsert_get_list_delete(self) -> None:
        store = InMemorySandboxSessionStore()
        provider = FakeSandboxProvider()
        handle = await provider.create(make_request())
        await store.upsert(handle.session)
        assert (await store.get("run-1")) is not None
        assert len(await store.list_non_terminal()) == 1
        await store.upsert(handle.session.with_state("deleted"))
        assert len(await store.list_non_terminal()) == 0
        await store.delete("run-1")
        assert (await store.get("run-1")) is None


class TestSeam:
    def test_returns_none_when_disabled(self) -> None:
        assert build_sandbox_backend(RemoteSandboxConfig.from_env({})) is None

    def test_returns_service_when_active(self) -> None:
        service = build_sandbox_backend(
            _active_config(),
            provider_overrides={SandboxProviderId.LANGSMITH: FakeSandboxProvider()},
        )
        assert isinstance(service, RemoteExecutionService)
