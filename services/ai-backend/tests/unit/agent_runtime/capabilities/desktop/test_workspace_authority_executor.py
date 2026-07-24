"""C2 effect-executor tests: A5 never gets a workspace mutation bypass."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.capabilities.desktop.broker_client import (
    WorkspaceCommitResult,
    WorkspacePreparedEffect,
    WorkspaceUploadSlot,
)
from agent_runtime.capabilities.desktop.workspace_authority import (
    WorkspaceAuthorityPort,
    WorkspaceChangeEntry,
    WorkspaceCommitPermitSource,
    WorkspaceEffectExecutor,
    WorkspacePrecondition,
    WorkspacePrepareCommand,
    WorkspaceProposalMaterial,
    WorkspaceProposalResolver,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest
from agent_runtime.surfaces_v2.ledger_models import EffectActor

STAGE = "stg_00000000-0000-4000-8000-000000000001"
ARTIFACT = "art_00000000-0000-4000-8000-000000000001"
DIGEST = "a" * 64


def scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org_1",
        user_id="user_1",
        conversation_id="conv_1",
        run_id="run_1",
        owner_ref="principal://users/user_1",
    )


def request() -> EffectExecutionRequest:
    return EffectExecutionRequest(
        stage_id=STAGE,
        revision=1,
        idempotency_key="workspace:run_1:stg_1:1",
        target_ref="workspace-target://grant_1/path_token_1",
        target_digest="b" * 64,
        proposal_ref=f"proposal://{STAGE}/revisions/1",
        proposal_content_ref=f"artifact://{ARTIFACT}/revisions/1",
        proposal_digest=DIGEST,
        actor=EffectActor.USER,
        decision_ledger_id="rrun1·7",
    )


def material() -> WorkspaceProposalMaterial:
    return WorkspaceProposalMaterial(
        grant_id="grant_1",
        mount="mnt_1",
        change_set_digest="c" * 64,
        target_digest="b" * 64,
        proposal_digest=DIGEST,
        entries=(
            WorkspaceChangeEntry(
                operation="create",
                relative_path="notes.md",
                content_slot="slot_1",
                content_ref=f"artifact://{ARTIFACT}/revisions/2",
                content_digest="d" * 64,
                content_size=5,
                precondition=WorkspacePrecondition(exists=False),
            ),
        ),
    )


@dataclass
class Authority(WorkspaceAuthorityPort):
    uploaded: list[tuple[str, str]] = field(default_factory=list)
    committed: list[tuple[str, str]] = field(default_factory=list)
    aborted: list[str] = field(default_factory=list)

    async def prepare(
        self, _command: WorkspacePrepareCommand
    ) -> WorkspacePreparedEffect:
        return WorkspacePreparedEffect(
            prepared_ref="workspace-prepared://prepared_1",
            expires_at=1_900_000_000_000,
            observed_target_digest="b" * 64,
            upload_slots=(WorkspaceUploadSlot(slot="slot_1", digest="d" * 64, size=5),),
        )

    async def upload(self, prepared_ref: str, content_ref: str) -> None:
        self.uploaded.append((prepared_ref, content_ref))

    async def commit(
        self, prepared_ref: str, commit_permit: str
    ) -> WorkspaceCommitResult:
        self.committed.append((prepared_ref, commit_permit))
        return WorkspaceCommitResult(
            outcome="applied",
            receipt_ref="workspace-receipt://private",
            result_digest="e" * 64,
        )

    async def reconcile(self, _claim_id: str) -> WorkspaceCommitResult:
        return WorkspaceCommitResult(
            outcome="indeterminate", receipt_ref="workspace-receipt://private"
        )

    async def abort(self, prepared_ref: str) -> None:
        self.aborted.append(prepared_ref)


class Resolver(WorkspaceProposalResolver):
    async def resolve(
        self, *, scope: EffectExecutionScope, request: EffectExecutionRequest
    ) -> WorkspacePrepareCommand:
        return WorkspacePrepareCommand(
            scope=scope,
            request=request,
            read_capability="wrc_main_issued",
            material=material(),
        )


class Permits(WorkspaceCommitPermitSource):
    def __init__(self, permit: str | None) -> None:
        self.permit = permit
        self.calls = 0

    async def take(
        self,
        *,
        scope: EffectExecutionScope,
        request: EffectExecutionRequest,
        prepared_ref: str,
    ) -> str | None:
        del scope, request, prepared_ref
        self.calls += 1
        return self.permit


class TestWorkspaceEffectExecutor:
    async def test_no_main_issued_permit_means_no_commit(self) -> None:
        authority = Authority()
        executor = WorkspaceEffectExecutor(
            scope=scope(),
            authority=authority,
            proposal_resolver=Resolver(),
            permit_source=Permits(None),
        )
        prepared = await executor.prepare(request())
        result = await executor.apply(prepared)
        assert authority.uploaded == [
            ("workspace-prepared://prepared_1", f"artifact://{ARTIFACT}/revisions/2")
        ]
        assert result.outcome.value == "failed"
        assert authority.committed == []

    async def test_exact_main_issued_permit_commits_the_prepared_effect_once(
        self,
    ) -> None:
        authority = Authority()
        executor = WorkspaceEffectExecutor(
            scope=scope(),
            authority=authority,
            proposal_resolver=Resolver(),
            permit_source=Permits("wcp_from_electron_main"),
        )
        prepared = await executor.prepare(request())
        result = await executor.apply(prepared)
        assert result.outcome.value == "applied"
        assert (
            result.receipt_ref is None
        )  # local native receipt never enters A5 ledger data.
        assert authority.committed == [
            ("workspace-prepared://prepared_1", "wcp_from_electron_main")
        ]

    async def test_abort_releases_only_the_prepared_reservation(self) -> None:
        authority = Authority()
        executor = WorkspaceEffectExecutor(
            scope=scope(),
            authority=authority,
            proposal_resolver=Resolver(),
            permit_source=Permits("wcp_from_electron_main"),
        )
        prepared = await executor.prepare(request())
        await executor.abort(prepared)
        assert authority.aborted == ["workspace-prepared://prepared_1"]
        assert authority.committed == []
