"""`BrokerBaseRead` — broker-served base reads for the overlay (FS collapse).

The point of this adapter is a DEGRADATION property, so that is what these
assert. `MergedWorkspaceBackend` takes its base reads as a port; the only
implementation used to come from C2's private host session, so a run without one
fell to `WorkspaceTombstoneBackend`, which refuses reads as well as writes.
Enabling the enforced lane therefore made an attached folder LESS usable.

With this port, no write authority costs the user WRITES, not reads.

The second property is what it must NOT invent. `/v1/fs/stat` supplies size
and mtime, but nothing supplies a content digest or an opaque generation — and
those are the fields the overlay compares to prove a staged write's precondition
still holds. Reporting them as `None` is what forces such a caller to obtain the
host session instead; fabricating one would let a write claim a precondition
nobody checked.
"""

from __future__ import annotations

from typing import Any

import pytest
from deepagents.backends.protocol import GrepResult, LsResult

from agent_runtime.capabilities.desktop.broker_client import FsStatResult

from agent_runtime.capabilities.desktop.workspace_backend import BrokerBaseRead
from agent_runtime.capabilities.workspace.contracts import WorkspaceEntryKind

pytestmark = pytest.mark.anyio


class FakeBrokeredBackendMixin:
    """A backend stub speaking the deepagents result shapes the adapter reads."""

    @staticmethod
    def backend(
        *,
        entries: list[dict[str, Any]] | None = None,
        error: str | None = None,
        payload: bytes = b"",
        matches: list[dict[str, Any]] | None = None,
        stat: FsStatResult | None = None,
    ) -> Any:
        class _Backend:
            def __init__(self) -> None:
                self.asked: list[str] = []

            async def als(self, path: str) -> LsResult:
                self.asked.append(path)
                if error is not None:
                    return LsResult(error=error)
                return LsResult(entries=list(entries or []))

            async def aglob(self, pattern: str, path: str | None = None) -> LsResult:
                del pattern, path
                if error is not None:
                    return LsResult(error=error)
                return LsResult(entries=list(entries or []))

            async def agrep(self, query: str, path: str | None = None) -> GrepResult:
                del query, path
                if error is not None:
                    return GrepResult(error=error)
                return GrepResult(matches=list(matches or []))

            async def astat_entry(self, path: str) -> FsStatResult | None:
                self.asked.append(f"stat:{path}")
                if stat is not None:
                    return stat
                if error is not None:
                    return None
                for info in entries or []:
                    if str(info.get("path")) == path:
                        return FsStatResult(
                            type="dir" if info.get("is_dir") else "file",
                            size=int(info.get("size", 11)),
                            mtimeMs=1_700_000_000_000.0,
                            name=path.rpartition("/")[2],
                        )
                return None

            async def abytes(
                self, path: str, *, start: int | None = None, end: int | None = None
            ) -> bytes:
                del path, start, end
                return payload

        return _Backend()


class TestReadsSurviveWithoutAHostSession(FakeBrokeredBackendMixin):
    async def test_a_directory_lists_its_children(self) -> None:
        port = BrokerBaseRead(
            self.backend(
                entries=[
                    {"path": "/mnt_x/a.csv", "is_dir": False},
                    {"path": "/mnt_x/sub", "is_dir": True},
                ]
            )
        )

        found = await port.list("/workspace/mnt_x")

        assert [entry.virtual_path for entry in found] == [
            "/workspace/mnt_x/a.csv",
            "/workspace/mnt_x/sub",
        ]
        assert [entry.entry_kind for entry in found] == [
            WorkspaceEntryKind.FILE,
            WorkspaceEntryKind.DIRECTORY,
        ]

    async def test_reading_yields_the_bytes_not_a_text_slice(self) -> None:
        """A CSV re-wrapped by the model-facing line slicer is a different file."""

        raw = b"region,q3\nnorth,120\n"
        port = BrokerBaseRead(self.backend(payload=raw))

        stream = await port.read("/workspace/mnt_x/a.csv")
        chunks = [chunk async for chunk in stream]

        assert b"".join(chunks) == raw


class TestItNeverInventsAPrecondition(FakeBrokeredBackendMixin):
    async def test_entries_carry_no_digest_generation_or_mtime(self) -> None:
        port = BrokerBaseRead(
            self.backend(entries=[{"path": "/mnt_x/a.csv", "is_dir": False}])
        )

        entry = (await port.list("/workspace/mnt_x"))[0]

        assert entry.content_digest is None
        assert entry.opaque_generation is None
        assert entry.stable_file_id is None
        # Size and mtime DO come back — `/v1/fs/stat` carries them, and a file
        # entry is invalid without a size. What stays absent is precisely the
        # write-precondition material.
        assert entry.byte_size is not None


class TestARefusalIsEmptyNotAnException(FakeBrokeredBackendMixin):
    """The overlay merges base with staged content; a raise loses both halves."""

    async def test_a_refused_listing_reports_nothing(self) -> None:
        port = BrokerBaseRead(self.backend(error="no grants"))
        assert await port.list("/workspace/mnt_x") == ()

    async def test_a_refused_glob_reports_nothing(self) -> None:
        port = BrokerBaseRead(self.backend(error="no grants"))
        assert await port.glob("/workspace/mnt_x/*.csv") == ()

    async def test_a_refused_grep_reports_nothing(self) -> None:
        port = BrokerBaseRead(self.backend(error="no grants"))
        assert await port.grep("north") == ()

    async def test_a_missing_path_stats_as_absent(self) -> None:
        port = BrokerBaseRead(self.backend(entries=[]))
        assert await port.stat("/workspace/mnt_x/gone.csv") is None


class TestStatAsksTheBrokerOnce(FakeBrokeredBackendMixin):
    """`/v1/fs/stat` carries size + mtime, so one call answers the whole entry."""

    async def test_a_present_child_is_found_with_a_single_stat(self) -> None:
        backend = self.backend(entries=[{"path": "/mnt_x/a.csv", "is_dir": False}])
        port = BrokerBaseRead(backend)

        entry = await port.stat("/workspace/mnt_x/a.csv")

        assert entry is not None
        assert entry.virtual_path == "/workspace/mnt_x/a.csv"
        assert entry.entry_kind is WorkspaceEntryKind.FILE
        assert backend.asked == ["stat:/mnt_x/a.csv"]


class TestGrepHitsKeepTheirLineNumbers(FakeBrokeredBackendMixin):
    async def test_a_hit_maps_path_line_and_text(self) -> None:
        port = BrokerBaseRead(
            self.backend(
                matches=[{"path": "/mnt_x/a.csv", "line": 2, "text": "north,120"}]
            )
        )

        hits = await port.grep("north")

        assert len(hits) == 1
        assert hits[0].virtual_path == "/workspace/mnt_x/a.csv"
        assert hits[0].line_number == 2
        assert hits[0].line_text == "north,120"

    async def test_a_hit_with_no_usable_line_number_is_dropped(self) -> None:
        """`WorkspaceBaseMatch` requires >= 1; a malformed hit must not raise."""

        port = BrokerBaseRead(
            self.backend(
                matches=[
                    {"path": "/mnt_x/a.csv", "line": 0, "text": "x"},
                    {"path": "/mnt_x/a.csv", "line": None, "text": "y"},
                    {"path": "", "line": 3, "text": "z"},
                ]
            )
        )

        assert await port.grep("north") == ()
