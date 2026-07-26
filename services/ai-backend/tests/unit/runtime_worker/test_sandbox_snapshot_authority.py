"""Focused C1 selection tests for the worker-owned D3 snapshot authority."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from agent_runtime.capabilities.sandbox.snapshot_file_store import (
    SandboxSnapshotIdentity,
)
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
)
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from runtime_adapters.file.workspace_overlay_store import FileWorkspaceOverlayStore
from runtime_worker.sandbox_snapshot_authority import (
    RuntimeWorkerOverlaySnapshotPlanAuthority,
)


@dataclass
class _CurrentOnlyOverlayStore:
    manifest: OverlayManifest

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        assert run_id == self.manifest.run_id
        return self.manifest

    async def get_manifest_version(
        self, *, run_id: str, version: int
    ) -> OverlayManifest | None:
        assert run_id == self.manifest.run_id
        assert version == self.manifest.version
        return None


@dataclass
class _RunStore:
    run: object

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        if self.run.org_id == org_id and self.run.run_id == run_id:
            return self.run
        return None


def _run(*, run_id: str = "run_1", org_id: str = "org_1", user_id: str = "user_1"):
    context = SimpleNamespace(run_id=run_id, org_id=org_id, user_id=user_id)
    return SimpleNamespace(
        run_id=run_id,
        org_id=org_id,
        user_id=user_id,
        runtime_context=context,
    )


async def test_authority_pins_only_a_retained_versioned_overlay_plan() -> None:
    overlays = InMemoryWorkspaceOverlayStore()
    first = await overlays.append_revision(
        run_id="run_1",
        expected_version=0,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path="/workspace/project/report.csv",
                entry=OverlayEntry(
                    virtual_path="/workspace/project/report.csv",
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
    authority = RuntimeWorkerOverlaySnapshotPlanAuthority(
        overlay_store=overlays, run_store=_RunStore(_run())
    )

    plan = await authority.load_plan(
        identity=SandboxSnapshotIdentity(
            run_id="run_1", org_id="org_1", user_id="user_1"
        )
    )

    assert plan is not None
    assert len(plan.entries) == 1
    assert plan.entries[0].source.source_ref == (
        "workspace-overlay://runs/run_1/versions/1"
    )
    assert first.version == 1


async def test_authority_refuses_a_current_manifest_without_immutable_history() -> None:
    authority = RuntimeWorkerOverlaySnapshotPlanAuthority(
        overlay_store=_CurrentOnlyOverlayStore(
            manifest=OverlayManifest(run_id="run_1", version=1)
        ),
        run_store=_RunStore(_run()),
    )
    assert (
        await authority.load_plan(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )
        )
        is None
    )


async def test_authority_treats_no_c1_selection_as_absent_not_empty_plan() -> None:
    authority = RuntimeWorkerOverlaySnapshotPlanAuthority(
        overlay_store=InMemoryWorkspaceOverlayStore(),
        run_store=_RunStore(_run()),
    )

    assert (
        await authority.load_plan(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )
        )
        is None
    )


async def test_authority_rejects_persisted_context_with_another_user_or_run() -> None:
    authority = RuntimeWorkerOverlaySnapshotPlanAuthority(
        overlay_store=InMemoryWorkspaceOverlayStore(),
        run_store=_RunStore(_run(user_id="user_other")),
    )
    assert (
        await authority.load_plan(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )
        )
        is None
    )


async def test_authority_refuses_pointer_loss_with_retained_history(tmp_path) -> None:
    overlays = FileWorkspaceOverlayStore(root=tmp_path)
    await overlays.append_revision(run_id="run_1", expected_version=0, mutations=())
    overlays._path("run_1").unlink()  # noqa: SLF001 - crash-state fixture
    authority = RuntimeWorkerOverlaySnapshotPlanAuthority(
        overlay_store=FileWorkspaceOverlayStore(root=tmp_path),
        run_store=_RunStore(_run()),
    )

    assert (
        await authority.load_plan(
            identity=SandboxSnapshotIdentity(
                run_id="run_1", org_id="org_1", user_id="user_1"
            )
        )
        is None
    )
