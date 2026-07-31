"""`<granted root>/.copilot` — the one host write the agent may make, made real.

The rule set allows it (rule 2) and `HostFilesystemFloor.permits_write` admits
it, and BOTH of those were already green. What no test asserted is that the
directory EXISTS: every scratch test in `test_host_floor.py` runs
`mkdir(parents=True)` in its own fixture, so the suite proved the permission and
never the directory. In the packaged app nothing created it, and the agent's own
working area therefore did not exist — `ls("<root>/.copilot")` answered
`path_not_found` until something happened to write into it (deepagents' `write`
creates parents; nothing else does).

So these tests deliberately do NOT pre-create anything. They drive
`factory._host_default_backend` — the production composition point where a run's
grants are bound — over a real temporary filesystem, then look at the disk. The
mount table is built by the real `WorkspaceMountTable` from real `BrokerGrant`
rows, so a regression anywhere along
`grant → mount → GrantedRoot(writable) → scratch` fails here rather than in an
after-the-fact log.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agent_runtime.capabilities.desktop.broker_client import BrokerGrant
from agent_runtime.capabilities.desktop.host_filesystem import (
    SCRATCH_DIR_NAME,
    GrantedRoot,
    HostScratchDirectory,
)
from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceMountTable,
)
from agent_runtime.execution import factory

from .fakes import RecordingBroker


class DesktopCompositionMixin:
    """Builds the desktop lane exactly as a run does, over a real directory."""

    @staticmethod
    def workspace_backend(
        *grants: tuple[str, str],
    ) -> BrokeredWorkspaceBackend:
        """A real workspace backend over `(host_root, mode)` pairs.

        Goes through `WorkspaceMountTable.from_broker_grants` rather than
        hand-built mounts so the `mode -> GrantedRoot.writable` mapping under
        test is the production one.
        """

        rows = [
            BrokerGrant(
                grantId=f"grant-{index}",
                mode=mode,  # type: ignore[arg-type]
                label=f"folder-{index}",
                status="active",
                mount=f"mount-{index}",
                root=root,
            )
            for index, (root, mode) in enumerate(grants)
        ]
        return BrokeredWorkspaceBackend(
            client=RecordingBroker(grants={}).client(),
            mounts=WorkspaceMountTable.from_broker_grants(rows),
        )

    @classmethod
    def compose(cls, *grants: tuple[str, str]) -> object:
        """Run the production composition point for the desktop default backend."""

        return factory._host_default_backend(cls.workspace_backend(*grants))


class TestScratchIsCreatedWhenGrantsAreBound(DesktopCompositionMixin):
    """The defect: allowed to write, with nowhere to write."""

    def test_composing_a_writable_grant_creates_its_scratch_directory(
        self, tmp_path: Path
    ) -> None:
        """FAILS before the fix — nothing ever created `.copilot`."""

        root = tmp_path / "Reports"
        root.mkdir()

        self.compose((str(root), "read_write"))

        scratch = root / SCRATCH_DIR_NAME
        assert scratch.is_dir()

    def test_the_agent_can_list_its_own_scratch_before_writing_anything(
        self, tmp_path: Path
    ) -> None:
        """FAILS before the fix — and this is the operation that really broke.

        The obvious claim ("the first write fails on a missing parent") is NOT
        true against deepagents 0.6.12: `FilesystemBackend.write` runs
        `parent.mkdir(parents=True)` first, so a write conjures the directory as
        a side effect. Reading does not. `ls` of a scratch directory nothing has
        written to yet answers `path_not_found`, so the agent's own working area
        does not exist until it happens to guess a filename — which is the same
        "your folder is empty" shape of lie this subsystem exists to kill,
        pointed at the agent instead of the user.

        Asserted through the composed backend (the object the deepagents tool
        layer calls), so a directory that exists but is not reachable through the
        floor still fails.
        """

        root = tmp_path / "Reports"
        root.mkdir()
        backend = self.compose((str(root), "read_write"))

        listing = backend.ls(f"{root}/{SCRATCH_DIR_NAME}")  # type: ignore[attr-defined]

        assert getattr(listing, "error", None) is None
        # And it is usable, not merely present.
        result = backend.write(  # type: ignore[attr-defined]
            f"{root}/{SCRATCH_DIR_NAME}/notes.json", '{"seen": 1}'
        )
        assert getattr(result, "error", None) is None
        assert (root / SCRATCH_DIR_NAME / "notes.json").read_text() == '{"seen": 1}'

    def test_an_existing_scratch_directory_is_left_alone(self, tmp_path: Path) -> None:
        """Idempotent: a second run must not wipe the first run's working files."""

        root = tmp_path / "Reports"
        scratch = root / SCRATCH_DIR_NAME
        scratch.mkdir(parents=True)
        (scratch / "kept.json").write_text("{}")

        self.compose((str(root), "read_write"))

        assert (scratch / "kept.json").read_text() == "{}"


class TestReadOnlyGrantsGetNothing(DesktopCompositionMixin):
    """Creating a directory is a WRITE, and a read-only grant authorised none."""

    def test_a_read_only_grant_gets_no_scratch_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "Reports"
        root.mkdir()

        self.compose((str(root), "read_only"))

        assert not (root / SCRATCH_DIR_NAME).exists()
        # And the folder itself is untouched — no side effect at all.
        assert list(root.iterdir()) == []

    def test_a_mixed_grant_set_only_touches_the_writable_folders(
        self, tmp_path: Path
    ) -> None:
        writable = tmp_path / "Reports"
        readable = tmp_path / "Archive"
        writable.mkdir()
        readable.mkdir()

        self.compose(
            (str(writable), "read_write_no_delete"),
            (str(readable), "read_only"),
        )

        assert (writable / SCRATCH_DIR_NAME).is_dir()
        assert not (readable / SCRATCH_DIR_NAME).exists()


class TestFailureNeverBreaksTheRun(DesktopCompositionMixin):
    """A scratch directory is convenience; a dead run is not a trade worth making."""

    def test_a_granted_root_that_no_longer_exists_is_survived(
        self, tmp_path: Path
    ) -> None:
        """The folder was attached, then deleted/unmounted before this run."""

        missing = tmp_path / "Vanished"

        backend = self.compose((str(missing), "read_write"))

        assert backend is not None
        # And crucially: the ancestor chain is NOT rebuilt. `parents=True` here
        # would materialise a folder the user never granted.
        assert not missing.exists()

    def test_a_root_that_is_a_file_is_survived(self, tmp_path: Path) -> None:
        """A grant whose root got replaced by a file: refuse, do not crash."""

        impostor = tmp_path / "Reports"
        impostor.write_text("not a directory")

        backend = self.compose((str(impostor), "read_write"))

        assert backend is not None
        assert impostor.read_text() == "not a directory"

    def test_an_unwritable_parent_is_survived_and_warned(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A read-only volume is the real-world shape of this failure."""

        root = tmp_path / "Reports"
        root.mkdir(mode=0o500)
        try:
            with caplog.at_level(logging.WARNING):
                backend = self.compose((str(root), "read_write"))
            assert backend is not None
            assert not (root / SCRATCH_DIR_NAME).exists()
            assert any(
                "host_filesystem.scratch_unavailable" in record.message
                for record in caplog.records
            )
        finally:
            # Restore so pytest's tmp_path cleanup can remove it.
            root.chmod(0o700)

    def test_the_warning_never_carries_the_host_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log lines in this neighbourhood must not become a path oracle."""

        secret = tmp_path / "Very-Private-Client-Files"
        with caplog.at_level(logging.WARNING):
            HostScratchDirectory.ensure((GrantedRoot(path=str(secret)),))

        emitted = " ".join(record.getMessage() for record in caplog.records)
        assert "host_filesystem.scratch_unavailable" in emitted
        assert "Very-Private-Client-Files" not in emitted
        assert str(secret) not in emitted


class TestUsableScratchReport:
    """`ensure` reports what exists, not what it attempted."""

    def test_only_usable_roots_are_returned(self, tmp_path: Path) -> None:
        ok = tmp_path / "ok"
        ok.mkdir()
        gone = tmp_path / "gone"
        read_only = tmp_path / "ro"
        read_only.mkdir()

        usable = HostScratchDirectory.ensure(
            (
                GrantedRoot(path=str(ok)),
                GrantedRoot(path=str(gone)),
                GrantedRoot(path=str(read_only), writable=False),
            )
        )

        assert usable == (f"{ok}/{SCRATCH_DIR_NAME}",)


class TestTheScratchDirectoryIsCreatedWhereTheHostCanOpenIt:
    """The one place in this class that touches a real filesystem must decode.

    Neither lane could see this. `GrantedRoot.path` holds the CANONICAL POSIX
    spelling (`/C:/Users/p`) because that is what the tool layer produces and
    what the rules and the floor match against — a Windows lane decision. The
    scratch directory is a Windows-lane-unaware `mkdir` on
    :attr:`GrantedRoot.scratch_path`, which is that same encoded spelling. On
    Windows `/C:/Users/p/.copilot` is not a path the operating system can
    create, so the whole feature would have been a warning line on that
    platform: authorised, reported working, and absent.

    A POSIX test run cannot prove this by creating anything, so the decode is
    split out and asserted directly, and the `mkdir` target is captured to prove
    ``ensure`` actually uses it.
    """

    def test_a_windows_root_decodes_to_the_hosts_own_spelling(self) -> None:
        """FAILS before the fix: the mkdir target was `/C:/Users/p/.copilot`."""

        root = GrantedRoot.from_host_path(r"C:\Users\p\Downloads")

        assert (
            HostScratchDirectory.native_scratch_path(root)
            == rf"C:\Users\p\Downloads\{SCRATCH_DIR_NAME}"
        )

    def test_a_posix_root_is_unchanged(self, tmp_path: Path) -> None:
        """The decode is the identity everywhere else — no POSIX behaviour moves."""

        root = GrantedRoot.from_host_path(str(tmp_path / "Reports"))

        assert HostScratchDirectory.native_scratch_path(root) == str(
            tmp_path / "Reports" / SCRATCH_DIR_NAME
        )

    def test_ensure_creates_at_the_decoded_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And `ensure` uses it, rather than merely being able to compute it."""

        attempted: list[str] = []

        def record(self: Path, **_: object) -> None:
            attempted.append(str(self))

        monkeypatch.setattr(Path, "mkdir", record)

        HostScratchDirectory.ensure(
            (
                GrantedRoot.from_host_path(r"C:\Users\p\Downloads"),
                GrantedRoot.from_host_path("/Users/p/Reports"),
            )
        )

        assert attempted == [
            rf"C:\Users\p\Downloads\{SCRATCH_DIR_NAME}",
            f"/Users/p/Reports/{SCRATCH_DIR_NAME}",
        ]

    def test_what_is_reported_stays_in_the_canonical_spelling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The native form lives for the duration of one `mkdir` and no longer.

        Every other consumer in this neighbourhood — the rules, the floor —
        speaks the canonical POSIX encoding, so leaking the decoded form out of
        `ensure` would hand them a string their comparisons cannot read.
        """

        monkeypatch.setattr(Path, "mkdir", lambda self, **_: None)

        usable = HostScratchDirectory.ensure(
            (GrantedRoot.from_host_path(r"C:\Users\p\Downloads"),)
        )

        assert usable == (f"/C:/Users/p/Downloads/{SCRATCH_DIR_NAME}",)


class TestNonDesktopRunsAreUntouched(DesktopCompositionMixin):
    """No workspace backend means no host lane, and therefore no host write."""

    def test_no_workspace_backend_creates_nothing(self, tmp_path: Path) -> None:
        # A web / postgres / in-memory image composes with `workspace_backend
        # is None`; the assertion that matters is that this path performs no
        # filesystem side effect at all.
        factory._host_default_backend(None)

        assert list(tmp_path.iterdir()) == []
