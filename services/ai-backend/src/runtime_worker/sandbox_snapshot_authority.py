"""Worker-owned authority that pins C1 overlay state for D3 snapshots.

The worker is the composition boundary that already owns verified run identity.
Keeping this adapter here prevents the workspace domain from importing sandbox
contracts and makes it impossible for a model tool to select a live workspace
manifest or a host filesystem path.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxSnapshotInput,
    SandboxSnapshotPlan,
    SandboxSnapshotSource,
    SandboxSnapshotSourceKind,
)
from agent_runtime.capabilities.sandbox.snapshot_file_store import (
    SandboxSnapshotIdentity,
    SandboxSnapshotPlanAuthorityPort,
)
from agent_runtime.capabilities.workspace.contracts import (
    WorkspaceEntryKind,
    WorkspaceOverlayVersionRef,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from agent_runtime.capabilities.workspace.ports import WorkspaceOverlayStorePort


@dataclass(frozen=True)
class RuntimeWorkerOverlaySnapshotPlanAuthority(SandboxSnapshotPlanAuthorityPort):
    """Select only self-contained C1 files from a retained run manifest.

    Selection first records the current C1 version in each returned reference,
    then verifies that exact retained version exists.  A concurrent later edit
    therefore cannot alter the plan that D3 eventually materializes.  Missing
    history, invalid C1 state, and unavailable storage all return ``None`` so
    the caller fails closed; this adapter never substitutes the current view.
    """

    overlay_store: WorkspaceOverlayStorePort

    async def load_plan(
        self, *, identity: SandboxSnapshotIdentity
    ) -> SandboxSnapshotPlan | None:
        try:
            current = await self.overlay_store.get_manifest(run_id=identity.run_id)
            if current.run_id != identity.run_id:
                return None
            if current.version == 0:
                return SandboxSnapshotPlan()
            retained = await self.overlay_store.get_manifest_version(
                run_id=identity.run_id, version=current.version
            )
        except WorkspaceOverlayConflictError:
            return None
        if (
            retained is None
            or retained.run_id != identity.run_id
            or retained.version != current.version
            or retained != current
        ):
            return None
        overlay_ref = WorkspaceOverlayVersionRef.format(
            run_id=identity.run_id, version=retained.version
        )
        return SandboxSnapshotPlan(
            entries=tuple(
                SandboxSnapshotInput(
                    virtual_path=entry.virtual_path,
                    source=SandboxSnapshotSource(
                        kind=SandboxSnapshotSourceKind.OVERLAY,
                        source_ref=overlay_ref,
                    ),
                )
                for entry in retained.entries
                if entry.entry_kind is WorkspaceEntryKind.FILE
            )
        )


__all__ = ("RuntimeWorkerOverlaySnapshotPlanAuthority",)
