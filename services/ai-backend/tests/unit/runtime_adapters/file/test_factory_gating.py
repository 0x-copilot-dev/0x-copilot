"""The ``file`` backend fails closed unless profile + root preconditions hold."""

from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from copilot_service_contracts.deployment_profile import ENV_DEPLOYMENT_PROFILE
from runtime_adapters.factory import RuntimeAdapterFactory


def _file_settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_STORE_BACKEND": "file",
            "RUNTIME_FILE_STORE_ROOT": str(tmp_path / "store"),
        }
    )


class TestFileBackendFactoryGating:
    def test_builds_file_ports_on_desktop_profile(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "single_user_desktop")
        ports = RuntimeAdapterFactory.from_settings(_file_settings(tmp_path))
        assert ports.backend == "file"
        assert ports.persistence is ports.event_store is ports.queue
        assert ports.postgres_store is None
        # Satellite ports are all wired.
        assert ports.draft_store is not None
        assert ports.share_store is not None
        assert ports.subagent_store is not None
        assert ports.source_store is not None
        assert ports.conversation_tool_ordinal_store is not None

    def test_rejects_non_desktop_profile(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "saas_multi_tenant")
        with pytest.raises(AgentRuntimeError) as excinfo:
            RuntimeAdapterFactory.from_settings(_file_settings(tmp_path))
        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR

    def test_rejects_missing_profile(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv(ENV_DEPLOYMENT_PROFILE, raising=False)
        with pytest.raises(AgentRuntimeError) as excinfo:
            RuntimeAdapterFactory.from_settings(_file_settings(tmp_path))
        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR

    def test_rejects_missing_root(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "single_user_desktop")
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_STORE_BACKEND": "file",
            }
        )
        with pytest.raises(AgentRuntimeError) as excinfo:
            RuntimeAdapterFactory.from_settings(settings)
        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR

    def test_in_memory_backend_unaffected(self, monkeypatch) -> None:
        # A non-file backend never reaches the profile gate.
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "saas_multi_tenant")
        settings = RuntimeSettings.load(environ={"OPENAI_API_KEY": "sk-test"})
        ports = RuntimeAdapterFactory.from_settings(settings)
        assert ports.backend == "in_memory"
