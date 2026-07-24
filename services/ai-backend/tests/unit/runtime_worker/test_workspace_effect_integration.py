"""End-to-end C3 proof: A4 approval -> A5 -> typed C2 workspace executor."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.desktop.broker_client import (
    WorkspaceCommitResult,
    WorkspacePreparedEffect,
    WorkspaceUploadSlot,
)
from agent_runtime.capabilities.desktop.workspace_authority import (
    WorkspaceAuthorityContractError,
    WorkspaceAuthorityPort,
    WorkspacePrepareCommand,
)
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
    content_ref_for_blob,
)
from agent_runtime.capabilities.workspace.effects import (
    WorkspaceGrantBinding,
)
from agent_runtime.effects.claims import EffectClaimState
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.effects.coordinator import (
    EffectCoordinator,
    EffectCoordinatorStatus,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.executor_registry import EffectExecutorRegistry
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest, EffectTarget
from agent_runtime.surfaces_v2.ledger_ids import ProposalUriCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectOutcome,
    EffectPolicy,
    EffectProposalKind,
)
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from runtime_worker.workspace_effect_storage import (
    InMemoryWorkspaceHostSessionRegistry,
    RuntimeWorkspaceProposalResolver,
    RuntimeWorkspaceProposalStore,
    WorkspaceHostSession,
    workspace_executor,
)
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)

RUN_ID = "run_workspace_c3"
OWNER = "principal://users/user_workspace"
OPERATION_ID = "op_00000000-0000-4000-8000-000000000321"
BODY = b"name,total\nAcme,10\n"


def _execution_scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org_workspace",
        user_id="user_workspace",
        conversation_id="conv_workspace",
        run_id=RUN_ID,
        owner_ref=OWNER,
    )


def _stage_scope() -> EffectStageScope:
    return EffectStageScope(run_id=RUN_ID, owner_ref=OWNER)


def _actor() -> EffectActorIdentity:
    return EffectActorIdentity(
        actor=EffectActor.USER,
        principal_ref=OWNER,
    )


def _grant(
    *,
    mode: str = "read_write",
    status: str = "active",
) -> WorkspaceGrantBinding:
    return WorkspaceGrantBinding(
        mount_name="finance",
        grant_id="grant-finance",
        mount_label="Finance",
        mode=mode,
        status=status,
    )


async def _single_chunk(body: bytes) -> AsyncIterator[bytes]:
    yield body


class _UnusedBase:
    """The executor receives the read-only C1 port but never a host writer."""


@dataclass
class RecordingAuthority(WorkspaceAuthorityPort):
    proposals: RuntimeWorkspaceProposalStore
    observed_precondition_digest: str
    prepare_calls: list[WorkspacePrepareCommand] = field(default_factory=list)
    uploads: list[tuple[str, str, bytes]] = field(default_factory=list)
    commits: list[tuple[str, str]] = field(default_factory=list)
    aborts: list[str] = field(default_factory=list)

    async def prepare(
        self, command: WorkspacePrepareCommand
    ) -> WorkspacePreparedEffect:
        self.prepare_calls.append(command)
        entry = command.material.entries[0]
        return WorkspacePreparedEffect(
            prepared_ref="workspace-prepared://prepared-c3",
            expires_at=1_900_000_000_000,
            observed_target_digest=self.observed_precondition_digest,
            upload_slots=(
                WorkspaceUploadSlot(
                    slot=entry.content_slot or "missing",
                    digest=entry.content_digest or ("0" * 64),
                    size=entry.content_size or 0,
                ),
            ),
        )

    async def upload(self, prepared_ref: str, content_ref: str) -> None:
        body = bytearray()
        async for chunk in self.proposals.open(
            scope=self.proposals.scope,
            reference=content_ref,
        ):
            body.extend(chunk)
        self.uploads.append((prepared_ref, content_ref, bytes(body)))

    async def commit(
        self, prepared_ref: str, commit_permit: str
    ) -> WorkspaceCommitResult:
        self.commits.append((prepared_ref, commit_permit))
        return WorkspaceCommitResult(
            outcome="applied",
            receipt_ref="workspace-receipt://private-c3",
            result_digest=hashlib.sha256(BODY).hexdigest(),
        )

    async def reconcile(self, _claim_id: str) -> WorkspaceCommitResult:
        return WorkspaceCommitResult(
            outcome="already_applied",
            receipt_ref="workspace-receipt://private-c3",
            result_digest=hashlib.sha256(BODY).hexdigest(),
        )

    async def abort(self, prepared_ref: str) -> None:
        self.aborts.append(prepared_ref)


@dataclass
class RecordingPermitSource:
    permit: str | None = "wcp_main_approved"
    requests: list[tuple[EffectExecutionRequest, str]] = field(default_factory=list)

    async def take(
        self,
        *,
        scope: EffectExecutionScope,
        request: EffectExecutionRequest,
        prepared_ref: str,
    ) -> str | None:
        assert scope == _execution_scope()
        self.requests.append((request, prepared_ref))
        return self.permit


class StaticScopeResolver:
    async def resolve(self, *, run_id: str) -> EffectExecutionScope | None:
        return _execution_scope() if run_id == RUN_ID else None


@dataclass
class IntegratedHarness:
    ledger: FakeLedger
    outbox: FakeOutbox
    claims: InMemoryEffectClaimStore
    proposals: RuntimeWorkspaceProposalStore
    overlay_store: InMemoryWorkspaceOverlayStore
    sessions: InMemoryWorkspaceHostSessionRegistry
    authority: RecordingAuthority
    permits: RecordingPermitSource
    stager: EffectStager
    stage_id: str
    precondition_digest: str

    async def approve(self):
        state = await self.stager.get_state(
            scope=_stage_scope(),
            stage_id=self.stage_id,
        )
        return await self.stager.decide(
            scope=_stage_scope(),
            stage_id=state.stage_id,
            revision=state.current_revision.revision,
            decision=EffectDecisionKind.APPROVE,
            proposal_digest=state.current_revision.proposal_digest,
            target_digest=state.target_digest,
            actor=_actor(),
            idempotency_key="workspace-approve-c3",
        )

    def coordinator(self) -> EffectCoordinator:
        return EffectCoordinator(
            ledger=self.ledger,
            claims=self.claims,
            scopes=StaticScopeResolver(),
            references=self.proposals,
            executors=EffectExecutorRegistry(
                {
                    EffectExecutorKind.WORKSPACE: lambda scope: workspace_executor(
                        scope=scope,
                        proposals=self.proposals,
                        sessions=self.sessions,
                        overlay_store=self.overlay_store,
                    )
                }
            ),
        )


async def _harness(
    *,
    grant: WorkspaceGrantBinding | None = None,
    permit: str | None = "wcp_main_approved",
    observed_digest: str | None = None,
) -> IntegratedHarness:
    publication = InMemoryArtifactPublicationCoordinator()
    blobs = InMemoryArtifactBlobStore(publication)
    references = InMemoryArtifactReferenceStore(publication)
    content = await blobs.put_stream(
        expected_digest=hashlib.sha256(BODY).hexdigest(),
        chunks=_single_chunk(BODY),
        byte_limit=1024,
    )
    entry = OverlayEntry(
        virtual_path="/workspace/finance/report.csv",
        entry_kind=WorkspaceEntryKind.FILE,
        operation=WorkspaceOperation.CREATE,
        content_ref=content_ref_for_blob(content.blob_key),
        content_digest=content.content_digest,
        byte_size=content.byte_size,
        baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
        author="agent",
    )
    scope = _execution_scope()
    proposals = RuntimeWorkspaceProposalStore(
        blobs=blobs,
        references=references,
        scope=scope,
    )
    stored = await proposals.persist(
        operation_id=OPERATION_ID,
        grant=_grant(),
        entries=(entry,),
    )
    ledger = FakeLedger()
    outbox = FakeOutbox()
    stager = EffectStager(
        ledger=ledger,
        outbox=outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    state = await stager.stage(
        scope=_stage_scope(),
        proposed_effect=ProposedEffect(
            operation_id=OPERATION_ID,
            executor=EffectExecutorKind.WORKSPACE,
            target=EffectTarget(
                executor=EffectExecutorKind.WORKSPACE,
                capability="workspace",
                op="create",
                target_ref=stored.target_ref,
                precondition_ref=stored.precondition_ref,
                display_label=stored.display_target,
            ),
            target_digest=stored.target_digest,
            display_target=stored.display_target,
            proposal_kind=EffectProposalKind.WORKSPACE_CHANGE_SET,
            proposal_content_ref=stored.proposal_content_ref,
            proposal_digest=stored.proposal_digest,
            proposal_media_type=("application/vnd.0xcopilot.workspace-change-set+json"),
            precondition_ref=stored.precondition_ref,
            precondition_digest=stored.precondition_digest,
            effect_class=EffectClass.EXTERNAL_REVERSIBLE,
            policy_snapshot_ref="policy://runs/workspace-c3/snapshot",
        ),
        policy_snapshot=EffectPolicySnapshot(
            snapshot_ref="policy://runs/workspace-c3/snapshot",
            descriptor_known=True,
            user_policy=EffectPolicy.ASK,
        ),
        actor=_actor(),
        idempotency_key="workspace-stage-c3",
    )
    overlay_store = InMemoryWorkspaceOverlayStore()
    await overlay_store.append_revision(
        run_id=RUN_ID,
        expected_version=0,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path=entry.virtual_path,
                entry=entry.model_copy(
                    update={
                        "stage_id": state.stage_id,
                        "stage_revision": state.current_revision.revision,
                    }
                ),
            ),
        ),
    )
    sessions = InMemoryWorkspaceHostSessionRegistry()
    authority = RecordingAuthority(
        proposals=proposals,
        observed_precondition_digest=(observed_digest or stored.precondition_digest),
    )
    permits = RecordingPermitSource(permit=permit)
    sessions.bind(
        scope=scope,
        session=WorkspaceHostSession(
            grants=(grant or _grant(),),
            base_read=_UnusedBase(),  # type: ignore[arg-type]
            read_capability="wrc_main_issued",
            authority=authority,
            permit_source=permits,
        ),
    )
    return IntegratedHarness(
        ledger=ledger,
        outbox=outbox,
        claims=InMemoryEffectClaimStore(),
        proposals=proposals,
        overlay_store=overlay_store,
        sessions=sessions,
        authority=authority,
        permits=permits,
        stager=stager,
        stage_id=state.stage_id,
        precondition_digest=stored.precondition_digest,
    )


async def test_exact_approved_csv_commits_once_through_workspace_executor() -> None:
    harness = await _harness()
    approved = await harness.approve()
    command = harness.outbox.commands["workspace-approve-c3"]
    coordinator = harness.coordinator()

    first = await coordinator.handle(command)
    duplicate = await coordinator.handle(command)

    assert first.status is EffectCoordinatorStatus.APPLIED
    assert first.outcome is EffectOutcome.APPLIED
    assert duplicate.status is EffectCoordinatorStatus.REPLAYED
    assert harness.authority.commits == [
        ("workspace-prepared://prepared-c3", "wcp_main_approved")
    ]
    assert harness.authority.uploads[0][2] == BODY
    assert len(harness.authority.prepare_calls) == 1
    prepared = harness.authority.prepare_calls[0]
    assert prepared.request.stage_id == approved.stage_id
    assert prepared.request.revision == approved.current_revision.revision
    assert prepared.material.entries[0].relative_path == "report.csv"
    assert (
        prepared.material.entries[0].content_digest == hashlib.sha256(BODY).hexdigest()
    )
    assert harness.permits.requests[0][0].decision_ledger_id == (
        approved.decision.ledger_id if approved.decision is not None else ""
    )
    claim = await harness.claims.get(
        org_id=_execution_scope().org_id,
        executor=EffectExecutorKind.WORKSPACE,
        idempotency_key=command.idempotency_key,
    )
    assert claim is not None and claim.state is EffectClaimState.COMPLETED


async def test_baseline_drift_aborts_before_claim_and_commit() -> None:
    harness = await _harness(observed_digest="f" * 64)
    await harness.approve()

    result = await harness.coordinator().handle(
        harness.outbox.commands["workspace-approve-c3"]
    )

    assert result.status is EffectCoordinatorStatus.PRECONDITION_DRIFT
    assert harness.authority.commits == []
    assert harness.permits.requests == []
    assert harness.authority.aborts == ["workspace-prepared://prepared-c3"]
    assert await harness.claims.list_incomplete() == ()


@pytest.mark.parametrize(
    "grant",
    [
        _grant(status="revoked"),
        _grant(mode="read_only"),
    ],
)
async def test_revoked_or_read_only_grant_after_approval_never_prepares(
    grant: WorkspaceGrantBinding,
) -> None:
    harness = await _harness(grant=grant)
    await harness.approve()

    result = await harness.coordinator().handle(
        harness.outbox.commands["workspace-approve-c3"]
    )

    assert result.status is EffectCoordinatorStatus.REFUSED
    assert result.safe_code == "prepare_failed"
    assert harness.authority.prepare_calls == []
    assert harness.authority.commits == []
    assert await harness.claims.list_incomplete() == ()


async def test_missing_main_permit_cannot_fall_through_to_commit() -> None:
    harness = await _harness(permit=None)
    await harness.approve()
    command = harness.outbox.commands["workspace-approve-c3"]

    result = await harness.coordinator().handle(command)

    assert result.status is EffectCoordinatorStatus.APPLIED
    assert result.outcome is EffectOutcome.FAILED
    assert harness.authority.commits == []
    assert len(harness.authority.prepare_calls) == 1
    assert len(harness.permits.requests) == 1


async def test_stale_overlay_binding_after_approval_never_prepares() -> None:
    harness = await _harness()
    await harness.approve()
    manifest = await harness.overlay_store.get_manifest(run_id=RUN_ID)
    entry = manifest.entry_at("/workspace/finance/report.csv")
    assert entry is not None
    await harness.overlay_store.append_revision(
        run_id=RUN_ID,
        expected_version=manifest.version,
        mutations=(
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT,
                virtual_path=entry.virtual_path,
                entry=entry.model_copy(update={"stage_revision": 2}),
            ),
        ),
    )

    result = await harness.coordinator().handle(
        harness.outbox.commands["workspace-approve-c3"]
    )

    assert result.status is EffectCoordinatorStatus.REFUSED
    assert result.safe_code == "prepare_failed"
    assert harness.authority.prepare_calls == []
    assert harness.authority.commits == []
    assert await harness.claims.list_incomplete() == ()


async def test_material_resolver_rejects_tampered_reviewed_digests() -> None:
    harness = await _harness()
    approved = await harness.approve()
    decision = approved.decision
    assert decision is not None
    request = EffectExecutionRequest(
        stage_id=approved.stage_id,
        revision=approved.current_revision.revision,
        idempotency_key="workspace-tamper-c3",
        target_ref=approved.target.target_ref,
        target_digest="f" * 64,
        proposal_ref=approved.current_revision.proposal_ref,
        proposal_content_ref=approved.current_revision.proposal_content_ref or "",
        proposal_digest=approved.current_revision.proposal_digest,
        actor=decision.actor.actor,
        decision_ledger_id=decision.ledger_id,
    )
    resolver = RuntimeWorkspaceProposalResolver(
        scope=_execution_scope(),
        proposals=harness.proposals,
        sessions=harness.sessions,
        overlay_store=harness.overlay_store,
    )

    with pytest.raises(
        WorkspaceAuthorityContractError,
        match="approved workspace digests changed",
    ):
        await resolver.resolve(scope=_execution_scope(), request=request)


def _proposal_entries(
    case: str,
    *,
    content_ref: str,
    content_digest: str,
    content_size: int,
) -> tuple[OverlayEntry, ...]:
    path = "/workspace/finance/report.csv"
    existing = BasePrecondition(
        existence=BaseExistence.MUST_EXIST,
        entry_kind=WorkspaceEntryKind.FILE,
        content_digest="e" * 64,
    )
    if case in {"create", "replace", "edit"}:
        return (
            OverlayEntry(
                virtual_path=path,
                entry_kind=WorkspaceEntryKind.FILE,
                operation=(
                    WorkspaceOperation.CREATE
                    if case == "create"
                    else WorkspaceOperation.REPLACE
                ),
                content_ref=content_ref,
                content_digest=content_digest,
                byte_size=content_size,
                baseline=(
                    BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST)
                    if case == "create"
                    else existing
                ),
                author="agent",
            ),
        )
    if case == "delete":
        return (
            OverlayEntry(
                virtual_path=path,
                entry_kind=WorkspaceEntryKind.TOMBSTONE,
                operation=WorkspaceOperation.DELETE,
                baseline=existing,
                author="agent",
            ),
        )
    if case == "mkdir":
        return (
            OverlayEntry(
                virtual_path="/workspace/finance/exports",
                entry_kind=WorkspaceEntryKind.DIRECTORY,
                operation=WorkspaceOperation.MKDIR,
                baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
                author="agent",
            ),
        )
    return (
        OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.TOMBSTONE,
            operation=WorkspaceOperation.MOVE,
            baseline=existing,
            author="agent",
        ),
        OverlayEntry(
            virtual_path="/workspace/finance/moved.csv",
            entry_kind=WorkspaceEntryKind.MOVE,
            operation=WorkspaceOperation.MOVE,
            source_virtual_path=path,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            author="agent",
        ),
    )


@pytest.mark.parametrize(
    ("case", "expected_operation"),
    [
        ("create", "create"),
        ("replace", "replace"),
        ("edit", "replace"),
        ("delete", "delete"),
        ("mkdir", "mkdir"),
        ("move", "move"),
    ],
)
async def test_proposal_store_maps_every_workspace_mutation_to_c2_material(
    case: str,
    expected_operation: str,
) -> None:
    publication = InMemoryArtifactPublicationCoordinator()
    blobs = InMemoryArtifactBlobStore(publication)
    references = InMemoryArtifactReferenceStore(publication)
    content = await blobs.put_stream(
        expected_digest=hashlib.sha256(BODY).hexdigest(),
        chunks=_single_chunk(BODY),
        byte_limit=1024,
    )
    store = RuntimeWorkspaceProposalStore(
        blobs=blobs,
        references=references,
        scope=_execution_scope(),
    )
    stored = await store.persist(
        operation_id=f"{OPERATION_ID}-{case}",
        grant=_grant(),
        entries=_proposal_entries(
            case,
            content_ref=content_ref_for_blob(content.blob_key),
            content_digest=content.content_digest,
            content_size=content.byte_size,
        ),
    )
    stage_id = "stg_00000000-0000-4000-8000-000000000321"
    request = EffectExecutionRequest(
        stage_id=stage_id,
        revision=1,
        idempotency_key=f"workspace-material-{case}",
        target_ref=stored.target_ref,
        target_digest=stored.target_digest,
        proposal_ref=ProposalUriCodec.format(stage_id, 1),
        proposal_content_ref=stored.proposal_content_ref,
        proposal_digest=stored.proposal_digest,
        actor=EffectActor.USER,
        decision_ledger_id=f"decision-{case}",
    )

    material = await store.resolve_material(request=request)

    assert material is not None
    assert [entry.operation for entry in material.entries] == [expected_operation]
    wire = material.broker_wire(request)
    assert all("content_ref" not in entry for entry in wire["entries"])
    assert "/workspace/" not in str(wire["entries"])
