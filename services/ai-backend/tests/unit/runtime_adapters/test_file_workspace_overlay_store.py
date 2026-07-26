"""Restart and optimistic-CAS proof for C3's desktop overlay adapter."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from runtime_adapters.file.workspace_overlay_store import FileWorkspaceOverlayStore


async def test_file_overlay_survives_restart_with_stage_binding(tmp_path: Path) -> None:
    run_id = "run_workspace_restart"
    path = "/workspace/project/report.csv"
    first = FileWorkspaceOverlayStore(root=tmp_path)
    manifest = await first.append_revision(
        run_id=run_id,
        expected_version=0,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path=path,
                entry=OverlayEntry(
                    virtual_path=path,
                    entry_kind=WorkspaceEntryKind.FILE,
                    operation=WorkspaceOperation.CREATE,
                    content_ref=f"artifact-blob://sha256/{'a' * 64}",
                    content_digest="a" * 64,
                    byte_size=7,
                    baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
                    stage_id="stg_00000000-0000-4000-8000-000000000123",
                    stage_revision=2,
                    author="agent",
                ),
            ),
        ),
    )

    reopened = FileWorkspaceOverlayStore(root=tmp_path)
    restored = await reopened.get_manifest(run_id=run_id)

    assert restored == manifest
    entry = restored.entry_at(path)
    assert entry is not None
    assert entry.stage_id == "stg_00000000-0000-4000-8000-000000000123"
    assert entry.stage_revision == 2
    assert entry.content_ref == f"artifact-blob://sha256/{'a' * 64}"


async def test_file_overlay_stale_writer_cannot_replace_current_manifest(
    tmp_path: Path,
) -> None:
    run_id = "run_workspace_cas"
    store = FileWorkspaceOverlayStore(root=tmp_path)
    await store.append_revision(
        run_id=run_id,
        expected_version=0,
        mutations=(),
    )

    with pytest.raises(WorkspaceOverlayConflictError):
        await FileWorkspaceOverlayStore(root=tmp_path).append_revision(
            run_id=run_id,
            expected_version=0,
            mutations=(),
        )

    assert (await store.get_manifest(run_id=run_id)).version == 1


async def test_file_overlay_cross_instance_cas_has_one_winner(
    tmp_path: Path,
) -> None:
    first = FileWorkspaceOverlayStore(root=tmp_path)
    second = FileWorkspaceOverlayStore(root=tmp_path)

    results = await asyncio.gather(
        first.append_revision(
            run_id="run_workspace_race",
            expected_version=0,
            mutations=(),
        ),
        second.append_revision(
            run_id="run_workspace_race",
            expected_version=0,
            mutations=(),
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert (
        sum(isinstance(result, WorkspaceOverlayConflictError) for result in results)
        == 1
    )
    assert (await first.get_manifest(run_id="run_workspace_race")).version == 1


async def test_file_overlay_retains_exact_version_after_later_edits(
    tmp_path: Path,
) -> None:
    run_id = "run_workspace_snapshot"
    path = "/workspace/project/report.csv"
    store = FileWorkspaceOverlayStore(root=tmp_path)
    first = await store.append_revision(
        run_id=run_id,
        expected_version=0,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path=path,
                entry=OverlayEntry(
                    virtual_path=path,
                    entry_kind=WorkspaceEntryKind.FILE,
                    operation=WorkspaceOperation.CREATE,
                    content_ref=f"artifact-blob://sha256/{'a' * 64}",
                    content_digest="a" * 64,
                    byte_size=7,
                    baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
                    author="agent",
                ),
            ),
        ),
    )
    current = await store.append_revision(
        run_id=run_id,
        expected_version=first.version,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path=path,
                entry=OverlayEntry(
                    virtual_path=path,
                    entry_kind=WorkspaceEntryKind.FILE,
                    operation=WorkspaceOperation.REPLACE,
                    content_ref=f"artifact-blob://sha256/{'b' * 64}",
                    content_digest="b" * 64,
                    byte_size=9,
                    baseline=BasePrecondition(
                        existence=BaseExistence.MUST_EXIST,
                        entry_kind=WorkspaceEntryKind.FILE,
                        content_digest="a" * 64,
                    ),
                    author="agent",
                ),
            ),
        ),
    )

    reopened = FileWorkspaceOverlayStore(root=tmp_path)
    retained = await reopened.get_manifest_version(run_id=run_id, version=first.version)

    assert retained == first
    assert (await reopened.get_manifest(run_id=run_id)) == current
    assert retained is not None
    retained_entry = retained.entry_at(path)
    assert retained_entry is not None
    assert retained_entry.content_digest == "a" * 64
    assert await reopened.get_manifest_version(run_id=run_id, version=99) is None


async def test_file_overlay_pointer_loss_with_retained_history_fails_closed(
    tmp_path: Path,
) -> None:
    """A crash/deletion may not reinterpret an existing overlay as empty."""

    run_id = "run_workspace_pointer_loss"
    store = FileWorkspaceOverlayStore(root=tmp_path)
    await store.append_revision(run_id=run_id, expected_version=0, mutations=())
    # Model the exact bad state: the immutable version survived, but the
    # current pointer disappeared before a later reader opened the store.
    store._path(run_id).unlink()  # noqa: SLF001 - crash-state fixture

    reopened = FileWorkspaceOverlayStore(root=tmp_path)
    with pytest.raises(WorkspaceOverlayConflictError, match="overlay changed"):
        await reopened.get_manifest(run_id=run_id)
