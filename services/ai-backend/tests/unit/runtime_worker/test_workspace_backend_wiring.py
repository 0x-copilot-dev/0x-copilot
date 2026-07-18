"""End-to-end wiring for the read-only ``/workspace/`` route (AC5 slice 3a).

Drives the whole desktop path against the in-memory fake broker:

* :class:`WorkspaceBackendWorkerWiring` reads broker env, fetches the active
  grant snapshot, resolves the mount table, and builds the backend — reusing one
  broker client so the resulting backend reads through the same fake transport;
* the factory composes ``{/workspace/: backend}`` into the deepagents
  ``CompositeBackend`` and the agent reads a granted file through
  ``/workspace/<mount>/<path>``;
* the route is ABSENT (and the supervisor prompt unchanged) when the broker is
  unconfigured, unreachable, or the user has granted no folders — so every
  non-desktop image stays byte-identical.
"""

from __future__ import annotations

import httpx

from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
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
    """A two-grant fake broker with readable labels → mount names."""
    return RecordingBroker(
        grants={
            "grant-proj": FakeBrokerFs(
                files={"a.txt": b"L1\nL2\nL3\n", "sub/b.py": b"x = 1\n"}
            ),
            "grant-docs": FakeBrokerFs(files={"readme.md": b"hello\n"}),
        },
        grant_meta={
            "grant-proj": {"label": "Project Notes", "mount": "mnt_proj"},
            "grant-docs": {"label": "Docs", "mount": "mnt_docs"},
        },
    )


def _wiring(broker: RecordingBroker, *, env: dict[str, str] | None = _ENV):
    return WorkspaceBackendWorkerWiring(
        env=env,
        http_client=httpx.AsyncClient(transport=broker.transport()),
    )


class TestWorkspaceBackendWiring:
    """`WorkspaceBackendWorkerWiring` gates + builds the per-run backend."""

    async def test_builds_backend_with_mounts_from_grant_snapshot(self) -> None:
        broker = _broker()
        backend = await _wiring(broker).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        # Root lists one mount per active grant, named from the label slug.
        listing = await backend.als("/")
        names = {e["path"] for e in (listing.entries or [])}
        assert names == {"/project-notes/", "/docs/"}
        # A grant snapshot was fetched before any fs op.
        assert broker.requests[0][0] == "/v1/grants/snapshot"

    async def test_absent_broker_env_returns_none(self) -> None:
        broker = _broker()
        assert await _wiring(broker, env={}).workspace_backend() is None
        # No broker call is made when unconfigured.
        assert broker.requests == []

    async def test_zero_active_grants_returns_none(self) -> None:
        broker = RecordingBroker(grants={})
        assert await _wiring(broker).workspace_backend() is None

    async def test_broker_unreachable_fails_soft_to_none(self) -> None:
        def _raise(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("broker down")

        wiring = WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(_raise)),
        )
        assert await wiring.workspace_backend() is None


class TestComposedWorkspaceRoute:
    """The factory routes `/workspace/` only when a backend is supplied."""

    async def test_agent_reads_granted_file_through_composite(self) -> None:
        broker = _broker()
        backend = await _wiring(broker).workspace_backend()

        composite = _composed_deep_backend(None, workspace_backend=backend)
        assert "/workspace/" in composite.routes

        # The agent reads a granted file via /workspace/<mount>/<path>. The
        # CompositeBackend strips the /workspace/ prefix before delegating.
        read = await composite.aread("/workspace/project-notes/a.txt")
        assert read.error is None
        assert read.file_data["content"] == "L1\nL2\nL3\n"

        # Listing the workspace root surfaces the mount as a directory.
        listing = await composite.als("/workspace/")
        paths = {e["path"] for e in (listing.entries or [])}
        assert "/workspace/project-notes/" in paths
        assert "/workspace/docs/" in paths

        # Reads never send a host path — only grant_id + virtual path.
        fs_reads = [b for r, _h, b in broker.requests if r == "/v1/fs/read"]
        assert fs_reads == [
            {"grant_id": "grant-proj", "path": "a.txt", "max_bytes": 1048576}
        ]

    def test_route_absent_when_no_workspace_backend(self) -> None:
        # No subagent/draft/large/workspace backends → no composite at all.
        assert _composed_deep_backend(None, workspace_backend=None) is None

    def test_route_absent_but_others_present(self) -> None:
        # Another route present, workspace absent → /workspace/ not registered.
        composite = _composed_deep_backend(object(), workspace_backend=None)
        assert "/workspace/" not in composite.routes
        assert "/subagents/" in composite.routes


class TestWorkspacePromptGating:
    """The `/workspace/` guidance rides the prompt only when the route is live."""

    def test_guidance_appended_when_active(self) -> None:
        out = _instructions_with_workspace(instructions="BASE", workspace_active=True)
        assert out.startswith("BASE")
        assert WORKSPACE_ACCESS_GUIDANCE in out
        assert "/workspace/" in out

    def test_guidance_omitted_when_inactive(self) -> None:
        out = _instructions_with_workspace(instructions="BASE", workspace_active=False)
        assert out == "BASE"
