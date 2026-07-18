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
    WorkspaceMutationSnapshot,
)
from agent_runtime.execution.deep_agent_builder import (
    WORKSPACE_ACCESS_GUIDANCE,
    WORKSPACE_WRITE_GUIDANCE,
)
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


def _writable_broker() -> RecordingBroker:
    """A fake broker exposing one writable + one read-only grant."""
    return RecordingBroker(
        grants={
            "grant-rw": FakeBrokerFs(files={"a.txt": b"OLD"}),
            "grant-ro": FakeBrokerFs(files={"readme.md": b"hello\n"}),
        },
        grant_meta={
            "grant-rw": {"mode": "read_write", "label": "Project"},
            "grant-ro": {"mode": "read_only", "label": "Docs"},
        },
    )


def _readonly_broker() -> RecordingBroker:
    return RecordingBroker(
        grants={"grant-ro": FakeBrokerFs(files={"readme.md": b"hello\n"})},
        grant_meta={"grant-ro": {"mode": "read_only", "label": "Docs"}},
    )


class _Emitter:
    def __init__(self) -> None:
        self.records: list[WorkspaceMutationSnapshot] = []

    async def __call__(self, record: WorkspaceMutationSnapshot) -> None:
        self.records.append(record)


class _Store:
    def put(self, data: bytes, *, media_type: str = "", preview: str | None = None):
        raise AssertionError("store.put is not exercised by the wiring test")


def _write_wiring(broker: RecordingBroker, *, store: object, emitter: object):
    return WorkspaceBackendWorkerWiring(
        env=_ENV,
        http_client=httpx.AsyncClient(transport=broker.transport()),
        snapshot_store=store,
        snapshot_emitter=emitter,
    )


class TestWorkspaceWriteActivation:
    """The wiring mints a run context + write triple ONLY for a writable grant."""

    async def test_writable_grant_mints_context_and_enables_writes(self) -> None:
        broker = _writable_broker()
        store, emitter = _Store(), _Emitter()
        backend = await _write_wiring(
            broker, store=store, emitter=emitter
        ).workspace_backend()

        assert isinstance(backend, BrokeredWorkspaceBackend)
        # A run context was pinned via /v1/runs/begin at build time…
        begin_calls = [r for r, _h, _b in broker.requests if r == "/v1/runs/begin"]
        assert begin_calls == ["/v1/runs/begin"]
        # …and threaded into the backend so the write path is live.
        assert backend.run_capability_context in broker.run_contexts
        assert backend.supports_writes is True

    async def test_release_backend_ends_the_run_context(self) -> None:
        broker = _writable_broker()
        backend = await _write_wiring(
            broker, store=_Store(), emitter=_Emitter()
        ).workspace_backend()
        context = backend.run_capability_context
        assert context in broker.run_contexts

        await WorkspaceBackendWorkerWiring.release_backend(backend)
        # /v1/runs/end released the pinned snapshot.
        assert context not in broker.run_contexts
        assert any(r == "/v1/runs/end" for r, _h, _b in broker.requests)

    async def test_read_only_grant_does_not_mint_context(self) -> None:
        broker = _readonly_broker()
        backend = await _write_wiring(
            broker, store=_Store(), emitter=_Emitter()
        ).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        assert backend.supports_writes is False
        assert backend.run_capability_context is None
        assert not any(r == "/v1/runs/begin" for r, _h, _b in broker.requests)

    async def test_missing_store_or_emitter_stays_read_only(self) -> None:
        broker = _writable_broker()
        # Writable grant, but no snapshot store/emitter supplied → read-only.
        backend = await _wiring(broker).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        assert backend.supports_writes is False
        assert not any(r == "/v1/runs/begin" for r, _h, _b in broker.requests)

    async def test_release_backend_is_none_safe(self) -> None:
        # A read-only backend has no aclose; None is a no-op — neither raises.
        await WorkspaceBackendWorkerWiring.release_backend(None)
        broker = _readonly_broker()
        backend = await _wiring(broker).workspace_backend()
        await WorkspaceBackendWorkerWiring.release_backend(backend)


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

    def test_writable_guidance_replaces_readonly_when_writable(self) -> None:
        out = _instructions_with_workspace(
            instructions="BASE", workspace_active=True, workspace_writable=True
        )
        assert WORKSPACE_WRITE_GUIDANCE in out
        assert WORKSPACE_ACCESS_GUIDANCE not in out
