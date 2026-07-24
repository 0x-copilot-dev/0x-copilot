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
