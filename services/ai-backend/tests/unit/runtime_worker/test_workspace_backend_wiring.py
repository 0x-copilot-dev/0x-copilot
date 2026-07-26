"""Wiring tests for the read-only legacy workspace route."""

from __future__ import annotations

import httpx
import pytest

from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceWriteNotSupportedError,
)
from agent_runtime.execution.deep_agent_builder import WORKSPACE_ACCESS_GUIDANCE
from agent_runtime.execution.factory import (
    _composed_deep_backend,
    _instructions_with_workspace,
)
from runtime_worker.workspace_backend_wiring import WorkspaceBackendWorkerWiring
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    TEST_BASE_URL,
    TEST_TOKEN,
    FakeBrokerFs,
    RecordingBroker,
)

_ENV = {"DESKTOP_BROKER_URL": TEST_BASE_URL, "DESKTOP_BROKER_TOKEN": TEST_TOKEN}


def _broker() -> RecordingBroker:
    return RecordingBroker(
        grants={
            "grant-proj": FakeBrokerFs(files={"a.txt": b"L1\nL2\n"}),
        },
        grant_meta={"grant-proj": {"label": "Project Notes", "mount": "mnt_proj"}},
    )


class TestWorkspaceBackendWiring:
    async def test_builds_read_only_backend_from_grant_snapshot(self) -> None:
        broker = _broker()
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
        ).workspace_backend()

        assert isinstance(backend, BrokeredWorkspaceBackend)
        assert backend.supports_writes is False
        read = await backend.aread("/project-notes/a.txt")
        assert read.file_data["content"] == "L1\nL2\n"
        with pytest.raises(WorkspaceWriteNotSupportedError):
            await backend.awrite("/project-notes/a.txt", "must not write")
        assert not any(
            "/v1/fs/" in route and route.endswith("write")
            for route, _, _ in broker.requests
        )

    async def test_absent_broker_env_returns_none(self) -> None:
        broker = _broker()
        backend = await WorkspaceBackendWorkerWiring(
            env={},
            http_client=httpx.AsyncClient(transport=broker.transport()),
        ).workspace_backend()
        assert backend is None
        assert broker.requests == []


class TestComposedWorkspaceRoute:
    async def test_agent_reads_granted_file_through_composite(self) -> None:
        broker = _broker()
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
        ).workspace_backend()
        composite = _composed_deep_backend(None, workspace_backend=backend)
        assert composite is not None
        read = await composite.aread("/workspace/project-notes/a.txt")
        assert read.file_data["content"] == "L1\nL2\n"

    def test_prompt_describes_only_read_access_when_not_staged(self) -> None:
        out = _instructions_with_workspace(
            instructions="BASE",
            workspace_active=True,
            workspace_writable=True,
        )
        assert WORKSPACE_ACCESS_GUIDANCE in out
        assert "WRITABLE" not in out
