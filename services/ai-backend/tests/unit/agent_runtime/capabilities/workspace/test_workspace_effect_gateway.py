"""C3 gateway/stager integration with an intentionally non-writable base."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import importlib
import posixpath
import sys
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.operations.errors import OperationStageCapabilityError
from agent_runtime.capabilities.operations.stage_authority import GatewayStageCapability
import agent_runtime.capabilities.operations.stage_authority as stage_authority
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
    GatewayProposedEffect,
    WorkspaceGatewayServices,
    WorkspaceGrantBinding,
    WorkspaceGrantGate,
    WorkspaceOperationAdapter,
    WorkspaceProposalStorePort,
    WorkspaceStoredProposal,
)
from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.operation_port import WorkspaceOperationPort
from agent_runtime.capabilities.workspace.ports import (
    WorkspaceOverlayReadPort,
    WorkspaceOverlayStorePort,
)
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectStageScope,
    EffectStageStatus,
)
from agent_runtime.effects.errors import (
    EffectStageProjectionUnbound,
    EffectStageStaleRevision,
)
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassDecision,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import OperationRequest
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


class ExplodingProposalStore:
    """Failure injection: no visible projection may precede durable material."""

    async def persist(
        self,
        *,
        operation_id: str,
        grant: WorkspaceGrantBinding,
        entries: tuple[OverlayEntry, ...],
    ) -> WorkspaceStoredProposal:
        del operation_id, grant, entries
        raise RuntimeError("proposal persistence unavailable")


class ExplodingStageLedger(FakeLedger):
    """Failure injection: a failed ledger append must leave no stage or overlay."""

    async def append_stage_event(self, **kwargs: object) -> object:
        self.append_calls += 1
        raise RuntimeError("stage ledger unavailable")


class FailOnceProjectionBindingLedger(FakeLedger):
    """Leaves a visible overlay with an unbound stage once, then permits recovery."""

    def __init__(self) -> None:
        super().__init__()
        self.binding_failed = False

    async def append_stage_event(self, **kwargs: object) -> object:
        if (
            kwargs.get("event_type") == LedgerEventType.EFFECT_PROJECTION_BOUND.value
            and not self.binding_failed
        ):
            self.binding_failed = True
            raise RuntimeError("projection binding unavailable")
        return await super().append_stage_event(**kwargs)  # type: ignore[arg-type]


class ExplodingProjectionCancellationLedger(FakeLedger):
    """Prove the missing binding stays fail-closed when cleanup also fails."""

    def __init__(self) -> None:
        super().__init__()
        self.cancel_attempts = 0

    async def append_stage_event(self, **kwargs: object) -> object:
        payload = kwargs.get("payload")
        if (
            kwargs.get("event_type") == LedgerEventType.EFFECT_DECISION_RECORDED.value
            and isinstance(payload, dict)
            and payload.get("decision") == EffectDecisionKind.CANCEL.value
        ):
            self.cancel_attempts += 1
            raise RuntimeError("stage cancellation unavailable")
        return await super().append_stage_event(**kwargs)  # type: ignore[arg-type]


class ExplodingOutbox(FakeOutbox):
    """A pre-approval workspace stage must never reach the command outbox."""

    async def enqueue_after_decision(self, command: object) -> None:
        del command
        self.enqueue_calls += 1
        raise AssertionError("a staged workspace mutation must not enqueue a command")


class ExplodingProjectionOverlayStore:
    """Fail only the final durable projection, never request-local planning."""

    def __init__(self, delegate: InMemoryWorkspaceOverlayStore) -> None:
        self._delegate = delegate
        self.append_calls = 0

    async def get_manifest(self, *, run_id: str):
        return await self._delegate.get_manifest(run_id=run_id)

    async def get_manifest_version(self, *, run_id: str, version: int):
        return await self._delegate.get_manifest_version(run_id=run_id, version=version)

    async def append_revision(self, **kwargs: object):
        del kwargs
        self.append_calls += 1
        raise RuntimeError("overlay projection unavailable")

    async def compact(self, *, run_id: str):
        return await self._delegate.compact(run_id=run_id)


@dataclass
class Harness:
    backend: WorkspaceGatewayBackend
    adapter: WorkspaceOperationAdapter
    base: ExplodingWorkspaceBase
    overlays: InMemoryWorkspaceOverlayStore
    ledger: FakeLedger
    outbox: FakeOutbox
    emitter: RecordingEmitter
    proposals: WorkspaceProposalStorePort
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
    adapter_type: type[WorkspaceOperationAdapter] = WorkspaceOperationAdapter,
    proposal_store: WorkspaceProposalStorePort | None = None,
    ledger: FakeLedger | None = None,
    outbox: FakeOutbox | None = None,
    overlay_store: WorkspaceOverlayStorePort | None = None,
    bypass: FilesystemBypassDecision = MANUAL_FILESYSTEM_BYPASS,
) -> Harness:
    base = ExplodingWorkspaceBase(files)
    overlays = InMemoryWorkspaceOverlayStore()
    active_overlay_store = overlay_store or overlays
    blobs = InMemoryArtifactBlobStore()
    merged = MergedWorkspaceBackend(
        run_id=RUN_ID,
        base_read=base,
        overlay_store=WorkspaceOverlayReadPort.bind(active_overlay_store),
        blob_store=blobs,
    )
    active_ledger = ledger or FakeLedger()
    active_outbox = outbox or FakeOutbox()
    stager = EffectStager(
        ledger=active_ledger,
        outbox=active_outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    resolved_grant = grant or _grant()
    grants = (resolved_grant,) if expose_grant else ()
    proposals = proposal_store or RecordingProposalStore()
    gateway = OperationGateway(
        descriptors=DEFAULT_OPERATION_DESCRIPTORS,
        gates=WorkspaceGrantGate(grants=grants),
    )
    adapter = adapter_type(
        services=WorkspaceGatewayServices(
            stager=stager,
            scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=OWNER,
            ),
            proposals=proposals,
            grants=grants,
            bypass=bypass,
        ),
        run_id=RUN_ID,
        base_read=base,
        overlay_store=active_overlay_store,
        blob_store=blobs,
    )
    return Harness(
        backend=WorkspaceGatewayBackend(
            merged=merged,
            operations=WorkspaceOperationPort.bind(gateway=gateway, adapter=adapter),
            grants=grants,
        ),
        adapter=adapter,
        base=base,
        overlays=overlays,
        ledger=active_ledger,
        outbox=active_outbox,
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
    assert harness.ledger.append_calls == 2
    assert [
        event.event_type
        for event in harness.ledger.events_by_stage[
            next(iter(harness.ledger.events_by_stage))
        ]
    ] == [
        "effect.staged",
        "effect.projection_bound",
    ]
    event = next(iter(harness.ledger.events_by_stage.values()))[0]
    assert event.event_type == LedgerEventType.EFFECT_STAGED.value
    assert event.payload["executor"] == "workspace"
    assert event.payload["proposal_kind"] == "workspace_change_set"
    assert event.payload["policy"] in {"ask", "require"}
    assert not any(
        isinstance(value, str) and value.startswith(("/", "file://", "filesystem://"))
        for value in event.payload.values()
    )


async def test_model_cannot_use_the_read_facade_to_mutate_without_a_stage() -> None:
    """A model gets the Deep Agents backend, never an overlay write capability."""

    path = f"/workspace/{MOUNT}/report.csv"
    harness = _harness()
    for mutator in ("awrite", "aedit", "adelete", "amove", "amkdir"):
        assert not hasattr(harness.backend._merged, mutator)

    token = harness.bind()
    try:
        result = await harness.backend.awrite(path, "account,total\nAcme,10\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert (
        result.error == "Workspace change staged for review; the host was not modified."
    )
    manifest = await harness.overlays.get_manifest(run_id=RUN_ID)
    entry = manifest.entry_at(path)
    assert entry is not None and entry.stage_id is not None
    assert harness.ledger.append_calls == 2
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


async def test_model_backend_object_graph_exposes_only_the_narrow_operation_port() -> (
    None
):
    """Normal model/backend traversal cannot reach an adapter or raw engine."""

    harness = _harness()
    backend = harness.backend
    port = getattr(backend, "_operations")
    assert not hasattr(backend, "_adapter")
    assert not hasattr(backend, "_gateway")
    for forbidden in ("_adapter", "_gateway", "_mutations", "_overlay_store"):
        with pytest.raises(AttributeError):
            getattr(port, forbidden)
    assert isinstance(getattr(port, "_queue"), asyncio.Queue)
    overlay_read = getattr(backend, "_merged")._overlay_store
    with pytest.raises(AttributeError):
        getattr(overlay_read, "append_revision")


async def test_failed_proposal_persistence_leaves_workspace_reads_byte_identical() -> (
    None
):
    path = f"/workspace/{MOUNT}/report.csv"
    original = b"account,total\nAcme,10\n"
    harness = _harness(
        files={path: original},
        proposal_store=ExplodingProposalStore(),
    )
    token = harness.bind()
    try:
        before = await harness.backend.aread(path)
        result = await harness.backend.awrite(path, "account,total\nAcme,20\n")
        after = await harness.backend.aread(path)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert after == before
    assert (await harness.overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert harness.ledger.events_by_stage == {}
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.files[path] == original
    assert harness.base.mutation_calls == []


async def test_failed_stage_append_leaves_workspace_reads_byte_identical() -> None:
    path = f"/workspace/{MOUNT}/report.csv"
    original = b"account,total\nAcme,10\n"
    ledger = ExplodingStageLedger()
    harness = _harness(files={path: original}, ledger=ledger)
    token = harness.bind()
    try:
        before = await harness.backend.aread(path)
        result = await harness.backend.awrite(path, "account,total\nAcme,20\n")
        after = await harness.backend.aread(path)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert after == before
    assert (await harness.overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert ledger.events_by_stage == {}
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.files[path] == original
    assert harness.base.mutation_calls == []


async def test_preapproval_workspace_stage_cannot_reach_an_exploding_outbox() -> None:
    """Outbox persistence is structurally unavailable until a later approval."""

    path = f"/workspace/{MOUNT}/outbox.csv"
    outbox = ExplodingOutbox()
    harness = _harness(outbox=outbox)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(path, "account,total\nAcme,20\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert (
        result.error == "Workspace change staged for review; the host was not modified."
    )
    assert (await harness.overlays.get_manifest(run_id=RUN_ID)).entry_at(
        path
    ) is not None
    assert harness.ledger.append_calls == 2
    assert outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


async def test_failed_overlay_projection_never_exposes_content_or_an_approvable_stage() -> (
    None
):
    path = f"/workspace/{MOUNT}/report.csv"
    original = b"account,total\nAcme,10\n"
    overlays = InMemoryWorkspaceOverlayStore()
    projection = ExplodingProjectionOverlayStore(overlays)
    harness = _harness(
        files={path: original},
        overlay_store=projection,
    )
    token = harness.bind()
    try:
        before = await harness.backend.aread(path)
        result = await harness.backend.awrite(path, "account,total\nAcme,20\n")
        after = await harness.backend.aread(path)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert after == before
    assert (await overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert harness.outbox.enqueue_calls == 0
    assert projection.append_calls == 1
    states = [
        await harness.stager.get_state(
            scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER), stage_id=stage_id
        )
        for stage_id in harness.ledger.events_by_stage
    ]
    assert states and all(
        state.status is EffectStageStatus.CANCELLED for state in states
    )
    assert harness.base.files[path] == original
    assert harness.base.mutation_calls == []


async def test_projection_and_cleanup_failure_leaves_an_unapprovable_stage() -> None:
    path = f"/workspace/{MOUNT}/double-failure.csv"
    overlays = InMemoryWorkspaceOverlayStore()
    projection = ExplodingProjectionOverlayStore(overlays)
    ledger = ExplodingProjectionCancellationLedger()
    harness = _harness(ledger=ledger, overlay_store=projection)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(path, "account,total\nAcme,20\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert (await overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert ledger.cancel_attempts == 1
    assert harness.outbox.enqueue_calls == 0
    assert len(ledger.events_by_stage) == 1
    stage_id = next(iter(ledger.events_by_stage))
    state = await harness.stager.get_state(
        scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
        stage_id=stage_id,
    )
    assert state.status is EffectStageStatus.HELD
    assert state.projection_required
    assert not state.approval_ready
    with pytest.raises(EffectStageProjectionUnbound):
        await harness.stager.decide(
            scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
            stage_id=stage_id,
            revision=state.current_revision.revision,
            decision=EffectDecisionKind.APPROVE,
            proposal_digest=state.current_revision.proposal_digest,
            target_digest=state.target_digest,
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=OWNER,
            ),
            idempotency_key="double-failure-must-stay-unbound",
        )


async def test_retry_recovers_an_overlay_after_its_projection_binding_append_failed() -> (
    None
):
    """A retry completes the exact existing stage; it never creates a second one."""

    path = f"/workspace/{MOUNT}/binding-retry.csv"
    ledger = FailOnceProjectionBindingLedger()
    harness = _harness(ledger=ledger)
    token = harness.bind()
    try:
        request = OperationRequestFactory.create(
            capability="workspace",
            op="create",
            arguments={
                "virtual_path": path,
                "content": "account,total\\nAcme,20\\n",
            },
        )
        first = await harness.backend._operations.invoke(request)
        assert first.stage_ids == ()
        assert ledger.binding_failed

        manifest = await harness.overlays.get_manifest(run_id=RUN_ID)
        entry = manifest.entry_at(path)
        assert entry is not None and entry.stage_id is not None
        unbound = await harness.stager.get_state(
            scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
            stage_id=entry.stage_id,
        )
        assert unbound.projection_required and not unbound.approval_ready
        assert unbound.target.op == "create"
        with pytest.raises(EffectStageProjectionUnbound):
            await harness.stager.decide(
                scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
                stage_id=entry.stage_id,
                revision=unbound.current_revision.revision,
                decision=EffectDecisionKind.APPROVE,
                proposal_digest=unbound.current_revision.proposal_digest,
                target_digest=unbound.target_digest,
                actor=EffectActorIdentity(actor=EffectActor.USER, principal_ref=OWNER),
                idempotency_key="must-stay-unbound",
            )
        assert harness.outbox.enqueue_calls == 0

        recovered_effect = await harness.adapter._recover_unbound_projection(
            request=request,
            paths=(path,),
        )
        assert recovered_effect is not None
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert recovered_effect.stage_id == entry.stage_id
    repaired = await harness.stager.get_state(
        scope=EffectStageScope(run_id=RUN_ID, owner_ref=OWNER),
        stage_id=entry.stage_id,
    )
    assert repaired.approval_ready
    assert list(ledger.events_by_stage) == [entry.stage_id]
    assert [event.event_type for event in ledger.events_by_stage[entry.stage_id]] == [
        "effect.staged",
        "effect.projection_bound",
    ]


async def test_forged_scope_dynamic_activation_and_reflection_cannot_stage_mutation() -> (
    None
):
    """The former public-string scope bypass cannot substitute gateway authority."""

    path = f"/workspace/{MOUNT}/bypass.csv"
    harness = _harness()
    token = harness.bind()
    try:
        request = OperationRequestFactory.create(
            capability="workspace",
            op="write",
            arguments={"virtual_path": path, "content": "must stage"},
        )
        authority_module = importlib.import_module(
            "agent_runtime.capabilities.operations.stage_authority"
        )

        def function_local_activation() -> object:
            from agent_runtime.capabilities.operations.stage_authority import (
                _activate_gateway_stage_capability,
            )

            return _activate_gateway_stage_capability

        activate = getattr(
            sys.modules[authority_module.__name__],
            "_activate_gateway_stage_capability",
        )
        assert activate is function_local_activation()
        with OperationContext.operation_scope(request.operation_id):
            with pytest.raises(OperationStageCapabilityError):
                await harness.adapter.build_proposal(request)

            # A caller can forge the former string scope, dynamically locate
            # private module state, and allocate a lookalike object. Activation
            # still requires the direct OperationGateway invocation frame.
            with pytest.raises(OperationStageCapabilityError):
                activate(
                    request,
                    issuing_code=OperationGateway._invoke_once.__code__,
                )
            forged = object.__new__(GatewayStageCapability)
            with pytest.raises(OperationStageCapabilityError):
                await harness.adapter.build_proposal_with_capability(request, forged)
        with pytest.raises(TypeError, match="activated only by OperationGateway"):
            GatewayStageCapability()
        assert not hasattr(stage_authority, "_mint_gateway_stage_capability")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert (await harness.overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert harness.ledger.append_calls == 0
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


class ChildTaskWorkspaceAdapter(WorkspaceOperationAdapter):
    """Adversarial adapter that tries to consume the parent's capability in a child."""

    async def build_proposal_with_capability(
        self,
        request: OperationRequest,
        capability: GatewayStageCapability,
    ) -> GatewayProposedEffect:
        return await asyncio.create_task(
            super().build_proposal_with_capability(request, capability)
        )


async def test_child_task_cannot_consume_issuing_gateway_capability() -> None:
    path = f"/workspace/{MOUNT}/child-task.csv"
    harness = _harness(adapter_type=ChildTaskWorkspaceAdapter)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(path, "must not stage")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert (await harness.overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert harness.ledger.append_calls == 0
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


class CapturingWorkspaceAdapter(WorkspaceOperationAdapter):
    """Capture a valid capability only to prove it cannot be replayed."""

    capability: GatewayStageCapability | None = None
    request: OperationRequest | None = None

    async def build_proposal_with_capability(
        self,
        request: OperationRequest,
        capability: GatewayStageCapability,
    ) -> GatewayProposedEffect:
        self.capability = capability
        self.request = request
        return await super().build_proposal_with_capability(request, capability)


async def test_valid_gateway_capability_stages_once_and_cannot_be_replayed() -> None:
    """A capability is one-use and disappears when the gateway invocation ends."""

    path = f"/workspace/{MOUNT}/replay.csv"
    harness = _harness(adapter_type=CapturingWorkspaceAdapter)
    adapter = harness.adapter
    assert isinstance(adapter, CapturingWorkspaceAdapter)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(path, "only the gateway may stage")
        assert (
            result.error
            == "Workspace change staged for review; the host was not modified."
        )
        assert adapter.capability is not None
        assert adapter.request is not None
        with pytest.raises(OperationStageCapabilityError):
            await adapter.build_proposal_with_capability(
                adapter.request,
                adapter.capability,
            )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    manifest = await harness.overlays.get_manifest(run_id=RUN_ID)
    assert manifest.entry_at(path) is not None
    assert harness.ledger.append_calls == 2
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


class CrossDigestWorkspaceAdapter(WorkspaceOperationAdapter):
    """Adversarial adapter that substitutes a different canonical request."""

    async def build_proposal_with_capability(
        self,
        request: OperationRequest,
        capability: GatewayStageCapability,
    ) -> GatewayProposedEffect:
        forged = request.model_copy(update={"args_digest": "0" * 64})
        return await super().build_proposal_with_capability(forged, capability)


class CrossRunWorkspaceAdapter(WorkspaceOperationAdapter):
    """Adversarial adapter that substitutes a request from another run."""

    async def build_proposal_with_capability(
        self,
        request: OperationRequest,
        capability: GatewayStageCapability,
    ) -> GatewayProposedEffect:
        forged = request.model_copy(update={"run_id": "run-other"})
        return await super().build_proposal_with_capability(forged, capability)


@pytest.mark.parametrize(
    "adapter_type",
    [CrossDigestWorkspaceAdapter, CrossRunWorkspaceAdapter],
    ids=["digest", "run"],
)
async def test_gateway_capability_rejects_cross_request_substitution_without_effects(
    adapter_type: type[WorkspaceOperationAdapter],
) -> None:
    """A capability is bound to exactly the validated request and active run."""

    path = f"/workspace/{MOUNT}/substitution.csv"
    harness = _harness(adapter_type=adapter_type)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(path, "must not stage")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error is not None
    assert (await harness.overlays.get_manifest(run_id=RUN_ID)).entries == ()
    assert harness.ledger.append_calls == 0
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


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


async def test_gate_denial_survives_a_failing_ledger_emitter() -> None:
    """Evidence loss must never become a grant.

    ``_blocked`` returns the decision and emits a ``gate.opened.v2`` describing
    it. Until PRD-01 that emit raised on every call — ``gate.opened.v2`` was
    absent from ``RuntimeApiEventType``, so the conversion in the emitter closure
    threw — and the unguarded ``await`` propagated out of the gate, failing the
    operation it was denying rather than denying it cleanly.

    Making emission best-effort fixes that, but introduces the opposite hazard:
    a swallowed error must not let the caller through. This pins the denial
    itself against an emitter that always raises.
    """

    harness = _harness(grant=_grant(status="revoked"), expose_grant=True)
    harness.emitter.fail_on_event_type = LedgerEventType.GATE_OPENED_V2
    token = harness.bind()
    try:
        result = await harness.backend.awrite(
            f"/workspace/{MOUNT}/blocked.txt", "must not stage"
        )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    # Denied, with the safe summary — not a raised RuntimeError, not a write.
    assert result.error is not None
    assert "no host change was made" in result.error
    assert harness.base.mutation_calls == []
    assert harness.proposals.calls == []
    assert harness.ledger.append_calls == 0
    # The gate emit was reached and raised, so its evidence is missing — while
    # the surrounding operation rows still recorded normally.
    assert not [
        event
        for event in harness.emitter.events
        if event[0] is LedgerEventType.GATE_OPENED_V2
    ]
    # And the emitter's internal detail never reaches the caller.
    assert "telemetry-secret-must-not-escape" not in result.error


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
        first_manifest = await harness.overlays.get_manifest(run_id=RUN_ID)
        first = first_manifest.entry_at(path)
        assert first is not None
        await harness.backend.aedit(path, "one", "two")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    current = (await harness.overlays.get_manifest(run_id=RUN_ID)).entry_at(path)
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
