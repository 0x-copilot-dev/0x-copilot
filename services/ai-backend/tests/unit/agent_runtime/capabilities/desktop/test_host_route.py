r"""Unit tests for the claim-aware default backend.

The property under test is the one the live defect violated: a host-shaped path
must never be answered by the agent-memory backend. It is tested against a
``CompositeBackend`` composed exactly as the factory composes it, because that
composition — prefix routes plus a memory default — IS what turned
``ls /Users/parthpahwa/Downloads`` into an empty listing with a green tick.
"""

from __future__ import annotations

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.capabilities.desktop.host_route import (
    HostPathGuardBackend,
    guarded_default,
)
from agent_runtime.capabilities.desktop.workspace_backend import (
    ROUTE_PREFIX,
    BrokeredWorkspaceBackend,
    WorkspaceMount,
    WorkspaceWriteNotSupportedError,
    _SafeMessage,
)
from agent_runtime.capabilities.desktop.workspace_grant import (
    WorkspaceGrantGate,
    WorkspaceGrantMessages,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    FakeBrokerFs,
    RecordingBroker,
    RecordingConsent,
)

DOWNLOADS = "/Users/parthpahwa/Downloads"
WINDOWS_DOWNLOADS = "C:\\Users\\parthpahwa\\Downloads"


class MemorySpy:
    """Stands in for the agent-memory ``StateBackend`` default.

    Every method records that it was reached and returns the shape memory really
    returns for a path it does not hold: an EMPTY SUCCESS. A test that finds a
    host path here has reproduced the defect.
    """

    def __init__(self) -> None:
        self.seen: list[str | None] = []

    def ls(self, path: str) -> object:
        self.seen.append(path)
        return LsResult(entries=[])

    async def als(self, path: str) -> object:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> object:
        self.seen.append(file_path)
        return ReadResult(file_data={"content": "", "encoding": "utf-8"})

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> object:
        return self.read(file_path, offset, limit)

    def glob(self, pattern: str, path: str | None = None) -> object:
        self.seen.append(path)
        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> object:
        return self.glob(pattern, path)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> object:
        self.seen.append(path)
        return GrepResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> object:
        return self.grep(pattern, path, glob)

    def write(self, file_path: str, content: str) -> object:
        self.seen.append(file_path)
        return WriteResult()

    async def awrite(self, file_path: str, content: str) -> object:
        return self.write(file_path, content)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> object:
        self.seen.append(file_path)
        return EditResult()

    # Deliberately NOT routed by the guard — proves delegation stays total.
    def download_files(self, paths: list[str]) -> list[str]:
        self.seen.append(paths[0] if paths else None)
        return list(paths)

    @property
    def id(self) -> str:
        return "memory-spy"


class GuardMixin:
    """Builds guards and composites over one fake broker."""

    @staticmethod
    def broker(**files: bytes) -> RecordingBroker:
        return RecordingBroker(
            grants={"grant-dl": FakeBrokerFs(files=dict(files))},
            grant_meta={"grant-dl": {"label": "Downloads", "mount": "mnt_dl"}},
        )

    @classmethod
    def workspace(
        cls,
        *,
        broker: RecordingBroker | None = None,
        host_root: str | None = DOWNLOADS,
        consent: RecordingConsent | None = None,
    ) -> BrokeredWorkspaceBackend:
        resolved = broker or cls.broker(**{"q4.csv": b"period,revenue\n"})
        mounts = (
            WorkspaceMount(name="downloads", grant_id="grant-dl", host_root=host_root),
        )
        return BrokeredWorkspaceBackend(
            client=resolved.client(),
            mounts=mounts,
            grant_gate=(
                None
                if consent is None
                else WorkspaceGrantGate(
                    grants=resolved.client(), interrupt_handler=consent, run_id="run-1"
                )
            ),
        )

    @classmethod
    def guard(cls, **kwargs: object) -> tuple[HostPathGuardBackend, MemorySpy]:
        memory = MemorySpy()
        workspace = cls.workspace(**kwargs)  # type: ignore[arg-type]
        return (
            HostPathGuardBackend(default=memory, workspace=workspace),
            memory,
        )

    @classmethod
    def composite(cls, **kwargs: object) -> tuple[CompositeBackend, MemorySpy]:
        """The factory's composition shape: prefix routes over a guarded default."""
        memory = MemorySpy()
        workspace = cls.workspace(**kwargs)  # type: ignore[arg-type]
        return (
            CompositeBackend(
                default=guarded_default(memory, workspace),
                routes={ROUTE_PREFIX: workspace},  # type: ignore[dict-item]
            ),
            memory,
        )


class TestDownloadsFolderNeverReachesMemory(GuardMixin):
    """The live defect, at the exact composition layer where it happened."""

    async def test_host_listing_is_answered_by_the_workspace_not_memory(self) -> None:
        composite, memory = self.composite()
        result = await composite.als(DOWNLOADS)
        assert memory.seen == [], (
            "a host path reached the agent-memory backend — the composition that "
            "answered ls /Users/parthpahwa/Downloads with an empty listing"
        )
        assert result.entries, "a granted host folder listed as empty"

    async def test_ungranted_host_listing_refuses_instead_of_emptying(self) -> None:
        # No bound host root, no gate: the honest answer is a refusal.
        composite, memory = self.composite(host_root=None)
        result = await composite.als(DOWNLOADS)
        assert memory.seen == []
        assert result.entries is None
        assert result.error == WorkspaceGrantMessages.NOT_GRANTED

    async def test_ungranted_host_listing_can_ask_for_a_grant(self) -> None:
        consent = RecordingConsent(resume={"decision": "rejected"})
        composite, memory = self.composite(host_root=None, consent=consent)
        result = await composite.als(DOWNLOADS)
        assert memory.seen == []
        assert consent.asked is True
        assert result.error == WorkspaceGrantMessages.DECLINED

    async def test_windows_host_listing_is_claimed_too(self) -> None:
        composite, memory = self.composite(host_root=WINDOWS_DOWNLOADS)
        result = await composite.als(WINDOWS_DOWNLOADS)
        assert memory.seen == []
        assert result.entries

    @pytest.mark.parametrize(
        "path",
        [
            DOWNLOADS,
            f"{DOWNLOADS}/q4.csv",
            WINDOWS_DOWNLOADS,
            "\\\\server\\share\\reports",
            "/Users/parthpahwa/../etc/passwd",  # unsafe, still claimed
            "~/Downloads",  # ambiguous, still claimed
            "C:relative",  # drive-relative, still claimed
        ],
    )
    async def test_every_host_shape_is_kept_away_from_memory(self, path: str) -> None:
        # Unsafe and ambiguous shapes are claimed too: only the workspace backend
        # can refuse them out loud, and memory would answer them with silence.
        composite, memory = self.composite(host_root=None)
        await composite.als(path)
        assert memory.seen == []

    async def test_a_host_write_is_refused_rather_than_written_to_memory(self) -> None:
        composite, memory = self.composite()
        with pytest.raises(WorkspaceWriteNotSupportedError):
            await composite.awrite(f"{DOWNLOADS}/new.csv", "x")
        assert memory.seen == [], (
            "a host-absolute write landed in agent memory, which would report "
            "success for a file the host never received"
        )


class TestVirtualPathsAreUnchanged(GuardMixin):
    """The guard must not capture anything that was working before."""

    @pytest.mark.parametrize(
        "path",
        ["/memories/notes.md", "/policies/p.json", "/drafts/d.md", "/", "relative.txt"],
    )
    async def test_virtual_paths_still_reach_the_default(self, path: str) -> None:
        guard, memory = self.guard()
        await guard.als(path)
        assert memory.seen == [path]

    async def test_workspace_prefix_still_routes_through_the_composite(self) -> None:
        composite, memory = self.composite()
        read = await composite.aread("/workspace/downloads/q4.csv")
        assert read.file_data is not None
        assert memory.seen == []

    async def test_globs_and_greps_with_no_path_stay_on_the_default(self) -> None:
        guard, memory = self.guard()
        await guard.aglob("*.md")
        await guard.agrep("needle")
        assert memory.seen == [None, None]

    async def test_unrouted_ops_delegate_to_the_default(self) -> None:
        guard, memory = self.guard()
        assert guard.download_files(["/memories/a.md"]) == ["/memories/a.md"]
        assert guard.id == "memory-spy"
        assert memory.seen == ["/memories/a.md"]


class TestGuardedDefaultSeam(GuardMixin):
    """The one call the composite factory makes."""

    def test_absent_workspace_returns_the_default_untouched(self) -> None:
        memory = MemorySpy()
        assert guarded_default(memory, None) is memory

    def test_a_non_workspace_object_is_not_wrapped(self) -> None:
        # Guards the factory's ``object``-typed seam: only the real backend, which
        # owns the claim rule, may divert paths away from the default.
        memory = MemorySpy()
        assert guarded_default(memory, object()) is memory

    def test_workspace_backend_is_wrapped(self) -> None:
        memory = MemorySpy()
        guarded = guarded_default(memory, self.workspace())
        assert isinstance(guarded, HostPathGuardBackend)
        assert guarded.default is memory
        assert guarded.claims(DOWNLOADS) is True
        assert guarded.claims("/memories/a.md") is False


class TestWhatTheToolSurfaceActuallyDelivers:
    r"""What survives ``validate_path`` — the gate BEFORE any backend is consulted.

    Deep Agents' filesystem middleware validates the model's path and only then
    calls ``als`` / ``aread`` / ``aglob`` / ``agrep`` on the resolved backend
    (``deepagents/middleware/filesystem.py``). So the classifier's correctness is
    necessary but not sufficient: a shape ``validate_path`` rejects or rewrites
    never reaches this package in the form the user typed.

    These tests pin the CURRENT, measured behavior of that validator rather than
    assuming it. They are the evidence for the Windows gap: the classifier handles
    ``C:\Users\...`` correctly and is never given the chance to, because the tool
    surface refuses drive-absolute paths first. If upstream ever accepts them, these
    tests fail and the classifier is already ready.
    """

    @pytest.mark.parametrize(
        "path",
        [r"C:\Users\p\Downloads", "C:/Users/p/Downloads", r"D:\data"],
    )
    def test_windows_drive_paths_never_reach_any_backend(self, path: str) -> None:
        from deepagents.backends.utils import validate_path

        with pytest.raises(
            ValueError, match="Windows absolute paths are not supported"
        ):
            validate_path(path)

    def test_posix_host_paths_do_reach_the_backend_unchanged(self) -> None:
        # This is why the macOS defect was reachable at all, and why the guard
        # fixes it: the path arrives verbatim.
        from deepagents.backends.utils import validate_path

        assert validate_path(DOWNLOADS) == DOWNLOADS

    @pytest.mark.parametrize(
        ("path", "rewritten"),
        [
            (r"\\server\share\rep", "//server/share/rep"),
            (r"\\?\C:\x", "//?/C:/x"),
        ],
    )
    def test_unc_and_extended_paths_arrive_rewritten_not_verbatim(
        self, path: str, rewritten: str
    ) -> None:
        # They pass validation but lose their Windows spelling, so the classifier
        # reads them as POSIX. Naming a folder the host does not have is wrong, but
        # it is wrong out loud — a grant request the user declines, never an empty
        # listing. Recorded so the rewrite is a known quantity, not a surprise.
        from deepagents.backends.utils import validate_path

        assert validate_path(path) == rewritten

    @pytest.mark.parametrize("path", ["~/Downloads", "/Users/p/../etc"])
    def test_traversal_and_home_are_refused_upstream_too(self, path: str) -> None:
        # Belt and braces: the classifier fails these closed as well, so the two
        # layers agree rather than relying on either alone.
        from deepagents.backends.utils import validate_path

        with pytest.raises(ValueError, match="Path traversal not allowed"):
            validate_path(path)


class TestZeroMountGuard(GuardMixin):
    """Nothing granted is still an answer, never an empty listing."""

    @staticmethod
    def _zero_mount() -> tuple[CompositeBackend, MemorySpy]:
        memory = MemorySpy()
        workspace = BrokeredWorkspaceBackend(
            client=RecordingBroker(grants={}).client(), mounts=()
        )
        return (
            CompositeBackend(
                default=guarded_default(memory, workspace),
                routes={ROUTE_PREFIX: workspace},  # type: ignore[dict-item]
            ),
            memory,
        )

    async def test_zero_mount_host_listing_refuses(self) -> None:
        composite, memory = self._zero_mount()
        result = await composite.als(DOWNLOADS)
        assert memory.seen == []
        assert result.entries is None
        assert result.error == WorkspaceGrantMessages.NOT_GRANTED

    async def test_zero_mount_workspace_root_says_nothing_is_granted(self) -> None:
        composite, _ = self._zero_mount()
        result = await composite.als("/workspace")
        assert result.entries is None
        assert result.error == _SafeMessage.NO_GRANTS
