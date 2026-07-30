"""Wiring tests for the read-only legacy workspace route.

Also pins the enablement shape the host-path lane depends on: with the broker
configured the route EXISTS even before the user has granted anything, so a
host-absolute path can reach the grant request instead of falling through to a
virtual backend that would answer it with an empty listing.
"""

from __future__ import annotations

import httpx
import pytest

from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceWriteNotSupportedError,
    _SafeMessage,
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
    RecordingConsent,
)

_ENV = {"DESKTOP_BROKER_URL": TEST_BASE_URL, "DESKTOP_BROKER_TOKEN": TEST_TOKEN}
_DOWNLOADS = "/Users/parthpahwa/Downloads"


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


class TestZeroGrantEnablement:
    """No grants yet is a zero-mount ROUTE, not an absent one."""

    async def test_zero_grants_still_builds_the_route(self) -> None:
        # Returning None here is what left ``ls /Users/<name>/Downloads`` to the
        # agent-memory backend, which answered it with an empty listing.
        broker = RecordingBroker(grants={})
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
        ).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        assert backend.mounts == ()

    async def test_zero_grant_root_listing_says_so_out_loud(self) -> None:
        broker = RecordingBroker(grants={})
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
        ).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        listing = await backend.als("/")
        assert listing.entries is None
        assert listing.error == _SafeMessage.NO_GRANTS

    async def test_ungranted_host_path_is_refused_without_an_interrupt_seam(
        self,
    ) -> None:
        # No injected handler here, so the default gate would park on the real
        # langgraph interrupt; assert the route claims the path either way.
        broker = RecordingBroker(grants={})
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
        ).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        assert backend.claims_path(_DOWNLOADS) is True


class TestSilentFallthroughRegression:
    """The live defect, at the composition level where it actually happened.

    The agent called ``ls`` with ``/Users/parthpahwa/Downloads``. The composite
    backend routes only its registered prefixes; every other path — including
    every host path — goes to the DEFAULT backend, which is the agent-memory
    virtual filesystem. Memory has nothing there, so ``ls`` returned an empty
    listing AS A SUCCESS and the agent said the folder was empty. It holds 1009
    files.

    ``BrokeredWorkspaceBackend.claims_path`` is the authority that makes that
    impossible: a claimed path must be delivered to the workspace backend, which
    answers it with a real listing, a grant request, or an explicit refusal.

    The claim cannot be expressed as a ``CompositeBackend`` route — the set of
    host paths is not enumerable as prefixes — so it is applied to the
    composite's DEFAULT via ``guarded_default`` (``capabilities/desktop/
    host_route.py``), adopted in ``_composed_deep_backend``. These tests drive
    the REAL composition the factory builds, so they fail if that adoption is
    ever dropped.

    The consent seam is injected on purpose. Without it the ungranted path still
    reaches the grant gate, but the gate parks on the real ``langgraph.interrupt``
    and raises outside a graph — which is indistinguishable, from a bare
    ``except RuntimeError``, from the ``StateBackend`` error this defect used to
    produce. Injecting the seam makes "who answered" observable rather than
    inferred from an exception type.
    """

    @staticmethod
    async def _workspace_backend(
        broker: RecordingBroker, consent: RecordingConsent | None = None
    ) -> BrokeredWorkspaceBackend:
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
            run_id="run-42",
            interrupt_handler=consent,
        ).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        return backend

    async def test_a_host_path_is_never_answered_by_agent_memory(self) -> None:
        """The defect, pinned at the level where it was actually caused.

        `ls /Users/<name>/Downloads` came back empty with a green tick because
        a host path is not a prefix of any route, so it landed on the composite
        DEFAULT — and that default was a `StateBackend`, which answers every
        path with success and nothing.

        The ASK now happens one layer up, in deepagents' filesystem permission
        rules at tool time, so this test can no longer observe consent by
        calling the backend directly. What it still owns, and what the defect
        actually was, is this: whatever sits at the default must not be agent
        memory.
        """

        from deepagents.backends.filesystem import FilesystemBackend
        from deepagents.backends.state import StateBackend

        broker = RecordingBroker(grants={})
        consent = RecordingConsent(resume={"decision": "rejected"})
        backend = await self._workspace_backend(broker, consent)
        composite = _composed_deep_backend(None, workspace_backend=backend)
        assert composite is not None
        assert not isinstance(composite.default, StateBackend), (
            "a host-absolute path would fall through to agent memory, which "
            "answers it with an empty listing and a green tick"
        )
        assert isinstance(composite.default, FilesystemBackend)
        assert composite.default.virtual_mode is False, (
            "virtual_mode=True anchors absolute paths under root_dir, so a host "
            "path would resolve somewhere else entirely rather than be read"
        )

    async def test_the_capability_and_its_boundary_ship_together(self) -> None:
        """The one combination that must never exist: disk without rules.

        `FilesystemBackend(virtual_mode=False)` is deliberately unguarded — it
        reads whatever absolute path it is handed. That is safe ONLY because
        the permission rules are applied in the tool layer before it runs. If a
        refactor ever installs the backend without the rules, the model gets
        the user's disk, and no other test in this file would notice.
        """

        from deepagents.backends.filesystem import FilesystemBackend
        from deepagents.middleware.filesystem import _check_fs_permission

        from agent_runtime.execution.factory import _host_filesystem_permissions

        broker = RecordingBroker(grants={})
        consent = RecordingConsent(resume={"decision": "rejected"})
        backend = await self._workspace_backend(broker, consent)
        composite = _composed_deep_backend(None, workspace_backend=backend)
        assert composite is not None

        if not isinstance(composite.default, FilesystemBackend):
            return  # No real disk exposed; nothing to guard.

        rules = list(_host_filesystem_permissions(backend))
        assert rules, "a real filesystem is exposed with NO permission rules"
        # Reads of an ungranted host folder must ask...
        assert _check_fs_permission(rules, "read", _DOWNLOADS) == "interrupt"
        # ...and no filesystem interrupt may ever authorize a host mutation (D7).
        assert _check_fs_permission(rules, "write", f"{_DOWNLOADS}/x") == "deny"

    async def test_non_desktop_composition_is_unchanged(self) -> None:
        # With no workspace backend the default must stay the bare StateBackend,
        # so every non-desktop run composes exactly as it did before the guard.
        from deepagents.backends.state import StateBackend

        composite = _composed_deep_backend(None, memory_routes={"/memories/": object()})
        assert composite is not None
        assert isinstance(composite.default, StateBackend)

    async def test_workspace_route_alone_still_works(self) -> None:
        # Guard against a routing fix that breaks the virtual form.
        broker = RecordingBroker(
            grants={"grant-dl": FakeBrokerFs(files={"q4.csv": b"period\n"})},
            grant_meta={"grant-dl": {"label": "Downloads", "mount": "mnt_dl"}},
        )
        backend = await self._workspace_backend(broker)
        composite = _composed_deep_backend(None, workspace_backend=backend)
        assert composite is not None
        read = await composite.aread("/workspace/downloads/q4.csv")
        assert read.file_data is not None


class TestGrantRequestThroughTheWiring:
    """The whole lane, from env to a served read, over one fake broker."""

    @staticmethod
    async def _backend(
        broker: RecordingBroker, consent: RecordingConsent
    ) -> BrokeredWorkspaceBackend:
        backend = await WorkspaceBackendWorkerWiring(
            env=_ENV,
            http_client=httpx.AsyncClient(transport=broker.transport()),
            run_id="run-42",
            interrupt_handler=consent,
        ).workspace_backend()
        assert isinstance(backend, BrokeredWorkspaceBackend)
        return backend

    async def test_ungranted_folder_asks_then_reads(self) -> None:
        broker = RecordingBroker(grants={})
        consent = RecordingConsent(
            resume={
                "decision": "approved",
                "grant_id": "grant-dl",
                "root": _DOWNLOADS,
            },
            on_ask=lambda _p: broker.add_grant(
                "grant-dl", {"q4.csv": b"period,revenue\n"}, label="Downloads"
            ),
        )
        backend = await self._backend(broker, consent)
        listing = await backend.als(_DOWNLOADS)
        approval_id = str(consent.payload["approval_id"])
        # The run scope reaches the gate, and the id stays path-free.
        assert approval_id.startswith("workspace_grant:run-42:")
        assert "Downloads" not in approval_id
        assert listing.error is None
        assert {entry["path"] for entry in (listing.entries or [])} == {
            "/downloads/q4.csv"
        }

    async def test_the_broker_still_never_sees_a_host_path(self) -> None:
        broker = RecordingBroker(grants={})
        consent = RecordingConsent(
            resume={
                "decision": "approved",
                "grant_id": "grant-dl",
                "root": _DOWNLOADS,
            },
            on_ask=lambda _p: broker.add_grant(
                "grant-dl", {"q4.csv": b"period,revenue\n"}, label="Downloads"
            ),
        )
        backend = await self._backend(broker, consent)
        read = await backend.aread(f"{_DOWNLOADS}/q4.csv")
        assert read.error is None
        sent = broker.bodies()
        assert "/Users" not in sent
        assert "Downloads" not in sent

    async def test_declined_grant_reads_nothing(self) -> None:
        broker = RecordingBroker(grants={})
        consent = RecordingConsent(resume={"decision": "rejected"})
        backend = await self._backend(broker, consent)
        listing = await backend.als(_DOWNLOADS)
        assert listing.entries is None
        assert listing.error
        assert not [route for route, _, _ in broker.requests if "/v1/fs/" in route]


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
