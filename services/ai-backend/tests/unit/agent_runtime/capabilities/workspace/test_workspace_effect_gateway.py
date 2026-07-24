"""C3 gateway/stager integration with an intentionally non-writable base."""

from __future__ import annotations

import fnmatch
import hashlib
import posixpath
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.capabilities.workspace.contracts import (
    OverlayEntry,
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
    WorkspaceEntryKind,
)
from agent_runtime.capabilities.workspace.deep_backend import (
    WorkspaceGatewayBackend,
    WorkspaceTombstoneBackend,
)
from agent_runtime.capabilities.workspace.effects import (
    WorkspaceGatewayServices,
    WorkspaceGrantBinding,
    WorkspaceGrantGate,
    WorkspaceOperationAdapter,
    WorkspaceStoredProposal,
)
from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.overlay import WorkspaceOverlayService
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectStageScope,
    EffectStageStatus,
)
from agent_runtime.effects.errors import EffectStageStaleRevision
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    LedgerEventType,
)
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from tests.unit.agent_runtime.capabilities.operations.helpers import RecordingEmitter
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)

RUN_ID = "run-1"
OWNER = "principal://users/user-1"
MOUNT = "finance"


class ExplodingWorkspaceBase:
    """Read-only base with traps proving no gateway mutation can reach the host."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.mutation_calls: list[str] = []

    async def stat(self, virtual_path: str) -> WorkspaceBaseEntry | None:
        body = self.files.get(virtual_path)
        if body is not None:
            return WorkspaceBaseEntry(
                virtual_path=virtual_path,
                entry_kind=WorkspaceEntryKind.FILE,
                content_digest=hashlib.sha256(body).hexdigest(),
                stable_file_id=f"stable:{virtual_path}",
                opaque_generation="generation-1",
                byte_size=len(body),
            )
        if any(path.startswith(f"{virtual_path.rstrip('/')}/") for path in self.files):
            return WorkspaceBaseEntry(
                virtual_path=virtual_path,
                entry_kind=WorkspaceEntryKind.DIRECTORY,
            )
        return None

    async def read(
        self,
        virtual_path: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        body = self.files[virtual_path]
        first = start or 0
        last = len(body) if end is None else end + 1

        async def _stream() -> AsyncIterator[bytes]:
            yield body[first:last]

        return _stream()

    async def list(self, virtual_path: str) -> Sequence[WorkspaceBaseEntry]:
        return tuple(
            entry
            for path in sorted(self.files)
            if posixpath.dirname(path) == virtual_path
            and (entry := await self.stat(path)) is not None
        )

    async def glob(self, pattern: str) -> Sequence[WorkspaceBaseEntry]:
        return tuple(
            entry
            for path in sorted(self.files)
            if fnmatch.fnmatchcase(path, pattern)
            and (entry := await self.stat(path)) is not None
        )

    async def grep(
        self, query: str, paths: Sequence[str] | None = None
    ) -> Sequence[WorkspaceBaseMatch]:
        matches: list[WorkspaceBaseMatch] = []
        for path, body in self.files.items():
            if paths is not None and path not in paths:
                continue
            for line_number, line in enumerate(
                body.decode("utf-8").splitlines(), start=1
            ):
                if query in line:
                    matches.append(
                        WorkspaceBaseMatch(
                            virtual_path=path,
                            line_number=line_number,
                            line_text=line,
                        )
                    )
        return tuple(matches)

    async def write(self, *_args: object, **_kwargs: object) -> None:
        self._explode("write")

    async def edit(self, *_args: object, **_kwargs: object) -> None:
        self._explode("edit")

    async def delete(self, *_args: object, **_kwargs: object) -> None:
        self._explode("delete")

    async def move(self, *_args: object, **_kwargs: object) -> None:
        self._explode("move")

    async def mkdir(self, *_args: object, **_kwargs: object) -> None:
        self._explode("mkdir")

    def _explode(self, name: str) -> None:
        self.mutation_calls.append(name)
        raise AssertionError(f"host mutation {name} bypassed A5")


@dataclass
class RecordingProposalStore:
    calls: list[tuple[OverlayEntry, ...]] = field(default_factory=list)

    async def persist(
        self,
        *,
        operation_id: str,
        grant: WorkspaceGrantBinding,
        entries: tuple[OverlayEntry, ...],
    ) -> WorkspaceStoredProposal:
        del operation_id
        self.calls.append(entries)
        target_body = [
            {
                "path": entry.virtual_path,
                "operation": entry.operation.value,
                "baseline": entry.baseline.model_dump(mode="json", exclude_none=True),
            }
            for entry in entries
        ]
        target_digest = sha256_hex(canonical_json_bytes(target_body))
        proposal_digest = sha256_hex(
            canonical_json_bytes(
                [entry.model_dump(mode="json", exclude_none=True) for entry in entries]
            )
        )
        precondition_digest = sha256_hex(
            canonical_json_bytes(
                [
                    entry.baseline.model_dump(mode="json", exclude_none=True)
                    for entry in entries
                ]
            )
        )
        return WorkspaceStoredProposal(
            proposal_content_ref=f"workspace-material://sha256/{proposal_digest}",
            proposal_digest=proposal_digest,
            target_ref=f"workspace-target://sha256/{target_digest}",
            target_digest=target_digest,
            precondition_ref=(f"workspace-precondition://sha256/{precondition_digest}"),
            precondition_digest=precondition_digest,
            display_target=f"{grant.mount_label} workspace change",
        )


@dataclass
class Harness:
    backend: WorkspaceGatewayBackend
    base: ExplodingWorkspaceBase
    overlay: WorkspaceOverlayService
    ledger: FakeLedger
    outbox: FakeOutbox
    emitter: RecordingEmitter
    proposals: RecordingProposalStore
    stager: EffectStager
    grant: WorkspaceGrantBinding

    def bind(
        self,
        *,
        write_policy: str = "ask",
        destructive_policy: str = "require",
    ) -> object:
        return OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id="org-1",
                user_id="user-1",
                conversation_id="conv-1",
                run_id=RUN_ID,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                user={
                    "write": write_policy,
                    "destructive": destructive_policy,
                }
            ),
            ledger_emitter=self.emitter,
            artifact_service=None,
            mode=OperationGatewayMode.ENFORCE,
            canonical_arguments_durable=True,
        )


def _grant(
    *,
    mode: str = "read_write",
    status: str = "active",
) -> WorkspaceGrantBinding:
    return WorkspaceGrantBinding(
        mount_name=MOUNT,
        grant_id="grant-finance",
        mount_label="Finance",
        mode=mode,
        status=status,
    )


def _harness(
    *,
    files: dict[str, bytes] | None = None,
    grant: WorkspaceGrantBinding | None = None,
    expose_grant: bool = True,
) -> Harness:
    base = ExplodingWorkspaceBase(files)
    overlays = InMemoryWorkspaceOverlayStore()
    blobs = InMemoryArtifactBlobStore()
    overlay = WorkspaceOverlayService(
        run_id=RUN_ID,
        base_read=base,
        overlay_store=overlays,
        blob_store=blobs,
    )
    merged = MergedWorkspaceBackend(
        run_id=RUN_ID,
        base_read=base,
        overlay_store=overlays,
        blob_store=blobs,
        overlay_service=overlay,
    )
    ledger = FakeLedger()
    outbox = FakeOutbox()
    stager = EffectStager(
        ledger=ledger,
        outbox=outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    resolved_grant = grant or _grant()
    grants = (resolved_grant,) if expose_grant else ()
    proposals = RecordingProposalStore()
    gateway = OperationGateway(
        descriptors=DEFAULT_OPERATION_DESCRIPTORS,
        gates=WorkspaceGrantGate(grants=grants),
    )
    adapter = WorkspaceOperationAdapter(
        services=WorkspaceGatewayServices(
            merged=merged,
            overlay=overlay,
            stager=stager,
            scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=OWNER,
            ),
            proposals=proposals,
            grants=grants,
        )
    )
    return Harness(
        backend=WorkspaceGatewayBackend(
            merged=merged,
            gateway=gateway,
            adapter=adapter,
            grants=grants,
        ),
        base=base,
        overlay=overlay,
        ledger=ledger,
        outbox=outbox,
        emitter=RecordingEmitter(),
        proposals=proposals,
        stager=stager,
        grant=resolved_grant,
    )


async def _invoke_case(harness: Harness, case: str) -> None:
    if case == "create":
        result = await harness.backend.awrite(
            f"/workspace/{MOUNT}/created.csv", "name,total\nAcme,10\n"
        )
    elif case == "replace":
        result = await harness.backend.awrite(
            f"/workspace/{MOUNT}/existing.md", "# reviewed\n"
        )
    elif case == "edit":
        result = await harness.backend.aedit(
            f"/workspace/{MOUNT}/existing.md", "old", "new"
        )
    elif case == "mkdir":
        result = await harness.backend.amkdir(f"/workspace/{MOUNT}/exports")
    elif case == "move":
        result = await harness.backend.amove(
            f"/workspace/{MOUNT}/existing.md",
            f"/workspace/{MOUNT}/moved.md",
        )
    else:
        result = await harness.backend.adelete(f"/workspace/{MOUNT}/existing.md")
    assert result.error is not None
    assert "host was not modified" in result.error


@pytest.mark.parametrize(
    "case",
    ["create", "replace", "edit", "mkdir", "move", "delete"],
)
async def test_every_workspace_mutation_stages_and_never_touches_base(
    case: str,
) -> None:
    path = f"/workspace/{MOUNT}/existing.md"
    harness = _harness(files={path: b"old body\n"})
    token = harness.bind()
    try:
        await _invoke_case(harness, case)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert harness.base.files[path] == b"old body\n"
    assert harness.base.mutation_calls == []
    assert harness.proposals.calls
    assert harness.outbox.enqueue_calls == 0
    assert harness.ledger.append_calls == 1
    event = next(iter(harness.ledger.events_by_stage.values()))[0]
    assert event.event_type == LedgerEventType.EFFECT_STAGED.value
    assert event.payload["executor"] == "workspace"
    assert event.payload["proposal_kind"] == "workspace_change_set"
    assert event.payload["policy"] in {"ask", "require"}
    assert not any(
        isinstance(value, str) and value.startswith(("/", "file://", "filesystem://"))
        for value in event.payload.values()
    )


@pytest.mark.parametrize(
    ("grant", "expose_grant", "expected_reason"),
    [
        (_grant(), False, "workspace_grant_missing_or_revoked"),
        (_grant(status="revoked"), True, "workspace_grant_missing_or_revoked"),
        (_grant(mode="read_only"), True, "workspace_grant_read_only"),
    ],
)
async def test_missing_revoked_or_read_only_grant_parks_without_fallthrough(
    grant: WorkspaceGrantBinding,
    expose_grant: bool,
    expected_reason: str,
) -> None:
    harness = _harness(grant=grant, expose_grant=expose_grant)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(
            f"/workspace/{MOUNT}/blocked.txt", "must not stage"
        )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert "no host change was made" in result.error
    assert harness.base.mutation_calls == []
    assert harness.proposals.calls == []
    assert harness.ledger.append_calls == 0
    gate_events = [
        event
        for event in harness.emitter.events
        if event[0] is LedgerEventType.GATE_OPENED_V2
    ]
    assert len(gate_events) == 1
    assert gate_events[0][1]["reason"] == expected_reason


async def test_no_delete_grant_cannot_stage_delete_or_move() -> None:
    path = f"/workspace/{MOUNT}/existing.md"
    harness = _harness(
        files={path: b"old\n"},
        grant=_grant(mode="read_write_no_delete"),
    )
    token = harness.bind()
    try:
        deleted = await harness.backend.adelete(path)
        moved = await harness.backend.amove(
            path,
            f"/workspace/{MOUNT}/other.md",
        )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert deleted.error is not None and "does not allow" in deleted.error
    assert moved.error is not None and "does not allow" in moved.error
    assert harness.base.mutation_calls == []
    assert harness.proposals.calls == []
    assert harness.ledger.append_calls == 0


async def test_destructive_workspace_policy_cannot_auto_apply() -> None:
    path = f"/workspace/{MOUNT}/existing.md"
    harness = _harness(files={path: b"old\n"})
    token = harness.bind(write_policy="auto", destructive_policy="auto")
    try:
        result = await harness.backend.adelete(path)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    event = next(iter(harness.ledger.events_by_stage.values()))[0]
    assert event.payload["effect_class"] == "external_destructive"
    assert event.payload["policy"] == "require"
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


async def test_edit_revises_same_stage_and_invalidates_old_revision() -> None:
    path = f"/workspace/{MOUNT}/report.md"
    harness = _harness()
    token = harness.bind()
    try:
        await harness.backend.awrite(path, "draft one\n")
        first_manifest = await harness.overlay.manifest()
        first = first_manifest.entry_at(path)
        assert first is not None
        await harness.backend.aedit(path, "one", "two")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    current = (await harness.overlay.manifest()).entry_at(path)
    assert current is not None
    assert current.stage_id == first.stage_id
    assert current.stage_revision == 2
    state = await harness.stager.get_state(
        scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
        stage_id=current.stage_id or "",
    )
    assert state.status is EffectStageStatus.REVISED
    assert state.current_revision.revision == 2
    with pytest.raises(EffectStageStaleRevision):
        await harness.stager.decide(
            scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
            stage_id=state.stage_id,
            revision=1,
            decision=EffectDecisionKind.APPROVE,
            proposal_digest=state.revisions[0].proposal_digest,
            target_digest=state.target_digest,
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=OWNER,
            ),
            idempotency_key="stale-workspace-approval",
        )
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


async def test_tombstone_backend_never_claims_local_success() -> None:
    backend = WorkspaceTombstoneBackend()

    write = await backend.awrite("report.csv", "secret")
    edit = await backend.aedit("report.csv", "a", "b")
    delete = await backend.adelete("report.csv")

    for result in (write, edit, delete):
        assert result.error is not None
        assert "artifact or download" in result.error
        assert "no local file was changed" in result.error
