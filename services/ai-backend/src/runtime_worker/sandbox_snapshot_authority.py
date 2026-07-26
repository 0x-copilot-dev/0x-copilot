"""Worker-owned authority that pins C1 overlay state for D3 snapshots.

The worker is the composition boundary that already owns verified run identity.
Keeping this adapter here prevents the workspace domain from importing sandbox
contracts and makes it impossible for a model tool to select a live workspace
manifest or a host filesystem path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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


class SandboxVerifiedRunStorePort(Protocol):
    """Read the persisted run that anchors C1 snapshot ownership."""

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        """Return the persisted run only within its authoritative org scope."""
        ...


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
    run_store: SandboxVerifiedRunStorePort

    async def load_plan(
        self, *, identity: SandboxSnapshotIdentity
    ) -> SandboxSnapshotPlan | None:
        try:
            # The queue/model never chooses C1 ownership.  Resolve the run
            # through persisted org scope first, then require all three identity
            # facts to agree with the sealed runtime context before reading its
            # overlay.  A run id alone is not a tenant boundary.
            run = await self.run_store.get_run(
                org_id=identity.org_id, run_id=identity.run_id
            )
            if not _run_matches_identity(run, identity):
                return None
            current = await self.overlay_store.get_manifest(run_id=identity.run_id)
            if current.run_id != identity.run_id:
                return None
            # An absent C1 overlay is an absent D3 selection, not approval for
            # an empty provider workspace.  This is intentionally distinct
            # from a nonzero retained-version lookup failure below: both fail
            # closed, but only the latter represents pointer/history loss.
            if current.version == 0:
                return None
            retained = await self.overlay_store.get_manifest_version(
                run_id=identity.run_id, version=current.version
            )
        except WorkspaceOverlayConflictError:
            return None
        except Exception:  # noqa: BLE001 - authority/storage failures fail closed
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


def _run_matches_identity(
    run: object | None, identity: SandboxSnapshotIdentity
) -> bool:
    """Check persisted run and persisted runtime context agree with D3 scope."""

    if run is None:
        return False
    if (
        getattr(run, "run_id", None) != identity.run_id
        or getattr(run, "org_id", None) != identity.org_id
        or getattr(run, "user_id", None) != identity.user_id
    ):
        return False
    context = getattr(run, "runtime_context", None)
    return (
        getattr(context, "run_id", None) == identity.run_id
        and getattr(context, "org_id", None) == identity.org_id
        and getattr(context, "user_id", None) == identity.user_id
    )


__all__ = (
    "RuntimeWorkerOverlaySnapshotPlanAuthority",
    "SandboxVerifiedRunStorePort",
)
