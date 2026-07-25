from __future__ import annotations

import fnmatch
import hashlib
import posixpath
from collections.abc import AsyncIterator, Sequence

import pytest

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
    WorkspaceEntryKind,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.overlay import WorkspaceOverlayService
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)


class ExplodingMutationBase:
    """Read-capable fake whose host mutation methods must stay completely unused."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.mutation_calls: list[str] = []

    async def stat(self, virtual_path: str) -> WorkspaceBaseEntry | None:
        if virtual_path in self.files:
            body = self.files[virtual_path]
            return WorkspaceBaseEntry(
                virtual_path=virtual_path,
                entry_kind=WorkspaceEntryKind.FILE,
                content_digest=hashlib.sha256(body).hexdigest(),
                byte_size=len(body),
                stable_file_id=f"base-{virtual_path.rsplit('/', maxsplit=1)[-1]}",
                opaque_generation="base-generation-1",
            )
        if any(path.startswith(f"{virtual_path.rstrip('/')}/") for path in self.files):
            return WorkspaceBaseEntry(
                virtual_path=virtual_path,
                entry_kind=WorkspaceEntryKind.DIRECTORY,
            )
        return None

    async def read(
        self, virtual_path: str, *, start: int | None = None, end: int | None = None
    ) -> AsyncIterator[bytes]:
        body = self.files[virtual_path]
        first = 0 if start is None else start
        last = len(body) if end is None else end + 1

        async def stream() -> AsyncIterator[bytes]:
            yield body[first:last]

        return stream()

    async def list(self, virtual_path: str) -> Sequence[WorkspaceBaseEntry]:
        entries: list[WorkspaceBaseEntry] = []
        for path, body in self.files.items():
            if posixpath.dirname(path) == virtual_path:
                entries.append(
                    WorkspaceBaseEntry(
                        virtual_path=path,
                        entry_kind=WorkspaceEntryKind.FILE,
                        content_digest=hashlib.sha256(body).hexdigest(),
                        byte_size=len(body),
                    )
                )
        return entries

    async def glob(self, pattern: str) -> Sequence[WorkspaceBaseEntry]:
        return tuple(
            entry
            for path in self.files
            if (entry := await self.stat(path)) is not None
            and fnmatch.fnmatchcase(path, pattern)
        )

    async def grep(
        self, query: str, paths: Sequence[str] | None = None
    ) -> Sequence[WorkspaceBaseMatch]:
        hits: list[WorkspaceBaseMatch] = []
        for path, body in self.files.items():
            if paths is not None and path not in paths:
                continue
            for number, line in enumerate(body.decode("utf-8").splitlines(), start=1):
                if query in line:
                    hits.append(
                        WorkspaceBaseMatch(
                            virtual_path=path,
                            line_number=number,
                            line_text=line,
                        )
                    )
        return hits

    async def write(self, *args: object, **kwargs: object) -> None:
        self._mutation("write")

    async def edit(self, *args: object, **kwargs: object) -> None:
        self._mutation("edit")

    async def delete(self, *args: object, **kwargs: object) -> None:
        self._mutation("delete")

    async def move(self, *args: object, **kwargs: object) -> None:
        self._mutation("move")

    async def mkdir(self, *args: object, **kwargs: object) -> None:
        self._mutation("mkdir")

    def _mutation(self, name: str) -> None:
        self.mutation_calls.append(name)
        raise AssertionError(f"base mutation {name} must not be called")


def _backend(
    files: dict[str, bytes], *, run_id: str = "run_overlay_1"
) -> tuple[
    MergedWorkspaceBackend, ExplodingMutationBase, InMemoryWorkspaceOverlayStore
]:
    base = ExplodingMutationBase(files)
    overlays = InMemoryWorkspaceOverlayStore()
    blobs = InMemoryArtifactBlobStore()
    service = WorkspaceOverlayService(
        run_id=run_id,
        base_read=base,
        overlay_store=overlays,
        blob_store=blobs,
    )
    return (
        MergedWorkspaceBackend(
            run_id=run_id,
            base_read=base,
            overlay_store=overlays,
            blob_store=blobs,
            overlay_service=service,
        ),
        base,
        overlays,
    )


async def test_replace_is_read_your_writes_and_never_mutates_the_base() -> None:
    path = "/workspace/project/report.txt"
    backend, base, overlays = _backend({path: b"base report\n"})

    staged = await backend.awrite(path, "overlay report\n")

    assert (
        staged.message
        == "Change staged in workspace overlay; the host was not modified."
    )
    assert await backend.aread_bytes(path) == b"overlay report\n"
    assert base.files[path] == b"base report\n"
    assert base.mutation_calls == []

    manifest = await overlays.get_manifest(run_id="run_overlay_1")
    entry = manifest.entry_at(path)
    assert entry is not None
    assert entry.baseline.existence is BaseExistence.MUST_EXIST
    assert entry.baseline.content_digest == hashlib.sha256(b"base report\n").hexdigest()
    assert entry.baseline.stable_file_id == "base-report.txt"
    assert entry.content_ref is not None
    assert "overlay report" not in entry.content_ref


async def test_create_then_edit_is_merged_and_base_write_methods_remain_unused() -> (
    None
):
    path = "/workspace/project/generated.txt"
    backend, base, overlays = _backend({"/workspace/project/base.txt": b"unchanged\n"})

    await backend.awrite(path, "first draft\n")
    await backend.aedit(path, "first", "second")

    assert await backend.aread_bytes(path) == b"second draft\n"
    listed = await backend.alist("/workspace/project")
    assert [entry.virtual_path for entry in listed] == [
        "/workspace/project/base.txt",
        "/workspace/project/generated.txt",
    ]
    assert base.files == {"/workspace/project/base.txt": b"unchanged\n"}
    assert base.mutation_calls == []

    manifest = await overlays.get_manifest(run_id="run_overlay_1")
    entry = manifest.entry_at(path)
    assert entry is not None
    assert manifest.version == 2
    assert entry.overlay_revision == 2
    assert entry.baseline.existence is BaseExistence.MUST_NOT_EXIST


async def test_stale_append_cannot_drop_a_prior_overlay_write() -> None:
    path = "/workspace/project/new.txt"
    backend, _base, overlays = _backend({})
    before = await overlays.get_manifest(run_id="run_overlay_1")

    await backend.awrite(path, "durable overlay value")

    with pytest.raises(WorkspaceOverlayConflictError):
        await overlays.append_revision(
            run_id="run_overlay_1", expected_version=before.version, mutations=()
        )
    assert await backend.aread_bytes(path) == b"durable overlay value"


async def test_move_retains_overlay_bytes_without_calling_a_base_mutation() -> None:
    source = "/workspace/project/source.txt"
    destination = "/workspace/project/destination.txt"
    backend, base, _overlays = _backend({})

    await backend.awrite(source, "overlay-only source")
    await backend.amove(source, destination)

    assert await backend.aread_bytes(destination) == b"overlay-only source"
    assert base.mutation_calls == []
