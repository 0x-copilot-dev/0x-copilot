"""F-006: external draft sends bind immutable Artifact revisions end-to-end."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from agent_runtime.api.artifact_draft_send import (
    ArtifactDraftSendForbidden,
    ArtifactDraftSendStager,
)
from agent_runtime.api.draft_service import DraftService
from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from tests.unit.rollout_testkit import legacy_staged_write_gate
from agent_runtime.artifacts import ArtifactScope, ArtifactService
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthOutcome,
)
from agent_runtime.capabilities.backends.artifact_draft_backend import (
    ArtifactDraftBackend,
    ArtifactDraftPathBinding,
)
from agent_runtime.capabilities.backends.artifact_draft_effect import (
    ArtifactDraftMcpEffectMaterialResolver,
    ArtifactDraftRevisionForbidden,
    ArtifactDraftSendTargetStore,
)
from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationArgumentMaterialResolver,
)
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectCommitCommand,
    EffectStageScope,
)
from agent_runtime.effects.coordinator import EffectCoordinator
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.executor_registry import EffectExecutorRegistry
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.commit_engine import StageCommitRequest
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectExecutorKind,
)
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, DraftSendRequest, RunRecord
from runtime_api.schemas import ApprovalDecision
from runtime_api.http.errors import RuntimeApiError
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.effect_commit import RuntimeEffectCommitHandler
from runtime_worker.mcp_effect_executor import McpEffectExecutor
from runtime_worker.mcp_operation_storage import RuntimeMcpEffectCoordinatorFactory

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_CONVERSATION = "conv_1"
_RUN = "run_1"
_DRAFT_ID = "deadbeefcafe1234deadbeefcafe1234"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _Scopes:
    by_run: dict[str, ArtifactScope]

    async def resolve_run(self, *, org_id: str, user_id: str, run_id: str):
        scope = self.by_run.get(run_id)
        if scope is None or (org_id, user_id) != (scope.org_id, scope.user_id):
            return None
        return scope


class _Authenticated:
    async def check(self, **_kwargs: object) -> CapabilityAuthCheck:
        return CapabilityAuthCheck(outcome=CapabilityAuthOutcome.AUTHENTICATED)


@dataclass
class _RecordingConnector:
    requests: list[StageCommitRequest]

    async def execute(self, request: StageCommitRequest):
        self.requests.append(request)
        from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult

        return ConnectorCommitResult(external_ref="mail_1")


@dataclass(frozen=True)
class _ScopeResolver:
    scope: EffectExecutionScope

    async def resolve(self, *, run_id: str) -> EffectExecutionScope | None:
        return self.scope if run_id == self.scope.run_id else None


@dataclass(frozen=True)
class _CoordinatorFactory:
    coordinator: EffectCoordinator

    def for_run(self, *, run: RunRecord) -> EffectCoordinator:
        assert run.run_id == _RUN
        return self.coordinator


class _FailingStageLookup:
    async def list_events_after(self, **_kwargs: object) -> tuple[object, ...]:
        raise RuntimeError("simulated event-store outage")


class _FailingSupersessionLookup:
    """Decorate the draft store but make the canonical safety lookup unavailable."""

    def __init__(self, delegate: InMemoryDraftStore) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def has_effect_supersession(self, **_kwargs: object) -> bool:
        raise RuntimeError("simulated draft-to-stage correlation outage")


class _UnexpectedGenericMaterial:
    """Fails the test if an Artifact denial falls into generic MCP material."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, **_kwargs: object) -> bytes | None:
        self.calls += 1
        return b"{}"


@dataclass(frozen=True)
class _References:
    material: ArtifactDraftMcpEffectMaterialResolver
    targets: ArtifactDraftSendTargetStore
    scope: EffectExecutionScope

    def open(
        self, *, scope: EffectExecutionScope, reference: str
    ) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            if scope != self.scope:
                return
            if reference.startswith("artifact://"):
                async for chunk in self.material.open_artifact_reference(
                    reference=reference
                ):
                    yield chunk
                return
            if reference.startswith("draft-send-target://"):
                async for chunk in self.targets.open_reference(reference=reference):
                    yield chunk

        return _stream()


class _Harness:
    def __init__(self) -> None:
        publication = InMemoryArtifactPublicationCoordinator()
        self.blobs = InMemoryArtifactBlobStore(publication)
        self.references = InMemoryArtifactReferenceStore(publication)
        self.scopes = _Scopes(
            {
                _RUN: ArtifactScope(
                    org_id=_ORG,
                    user_id=_USER,
                    conversation_id=_CONVERSATION,
                    run_id=_RUN,
                    trace_id="trace_1",
                )
            }
        )
        self.metadata = InMemoryArtifactMetadataStore(publication)
        self.artifacts = ArtifactService(
            metadata=self.metadata,
            blobs=self.blobs,
            run_scopes=self.scopes,
        )
        self.runtime = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        self.producer = RuntimeEventProducer(
            persistence=self.runtime, event_store=self.runtime
        )
        self.run = RunRecord(
            run_id=_RUN,
            conversation_id=_CONVERSATION,
            org_id=_ORG,
            user_id=_USER,
            user_message_id="msg_1",
            trace_id="trace_1",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            status=AgentRunStatus.RUNNING,
            runtime_context=AgentRuntimeContext(
                user_id=_USER,
                org_id=_ORG,
                roles=["employee"],
                run_id=_RUN,
                trace_id="trace_1",
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
            ),
        )
        self.runtime.runs[_RUN] = self.run
        self.runtime.events_by_run[_RUN] = []
        self.artifact_stager = ArtifactDraftSendStager(
            artifacts=self.artifacts,
            event_producer=self.producer,
            queue=self.runtime,
            blobs=self.blobs,
            references=self.references,
            supersessions=self.drafts,
        )
        self.service = DraftService(
            store=self.drafts,
            persistence=self.runtime,
            auth_gate=_Authenticated(),
            event_producer=self.producer,
            artifact_draft_send_stager=self.artifact_stager,
        )

    async def seed_and_import(self, *, content: str = "Version one") -> None:
        await self.drafts.insert_version(
            DraftRecord(
                draft_id=_DRAFT_ID,
                version=1,
                org_id=_ORG,
                conversation_id=_CONVERSATION,
                run_id=_RUN,
                user_id=_USER,
                title="Launch message",
                content_text=content,
                status=DraftStatus.DRAFT,
            )
        )
        backend = ArtifactDraftBackend(
            artifacts=self.artifacts,
            org_id=_ORG,
            conversation_id=_CONVERSATION,
            run_id=_RUN,
            user_id=_USER,
            legacy_store=self.drafts,
        )
        # Read-through is the one-way legacy migration; it deliberately makes
        # no new legacy DraftRecord version.
        imported = await backend.aread(f"/drafts/{_DRAFT_ID}.md")
        assert imported.file_data is not None
        self.backend = backend

    async def stage(self):
        return await self.service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=_DRAFT_ID,
            request=DraftSendRequest(
                expected_version=1,
                target_connector="gmail",
                target_metadata={"op": "send_email", "recipient": "team@example.test"},
            ),
        )

    def add_host_run(self, *, run_id: str) -> RunRecord:
        """Add another trusted host run for the same draft owner/conversation."""

        context = self.run.runtime_context.model_copy(
            update={"run_id": run_id, "trace_id": f"trace_{run_id}"}
        )
        run = self.run.model_copy(
            update={
                "run_id": run_id,
                "user_message_id": f"msg_{run_id}",
                "trace_id": f"trace_{run_id}",
                "runtime_context": context,
            }
        )
        self.runtime.runs[run_id] = run
        self.runtime.events_by_run[run_id] = []
        self.scopes.by_run[run_id] = ArtifactScope(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=_CONVERSATION,
            run_id=run_id,
            trace_id=f"trace_{run_id}",
        )
        return run

    def artifact_backend_for(self, run: RunRecord) -> ArtifactDraftBackend:
        return ArtifactDraftBackend(
            artifacts=self.artifacts,
            org_id=_ORG,
            conversation_id=_CONVERSATION,
            run_id=run.run_id,
            user_id=_USER,
            legacy_store=self.drafts,
        )

    def effect_stager(self) -> EffectStager:
        owner_ref = f"principal://users/{_USER}"
        return EffectStager(
            ledger=RuntimeEffectLedger(
                event_producer=self.producer,
                run=self.run,
                owner_ref=owner_ref,
            ),
            outbox=RuntimeEffectCommitOutbox(
                queue=self.runtime,
                scope=EffectExecutionScope(
                    org_id=_ORG,
                    user_id=_USER,
                    conversation_id=_CONVERSATION,
                    run_id=_RUN,
                    owner_ref=owner_ref,
                ),
            ),
        )


async def _approved_command(harness: _Harness, *, stage_id: str) -> EffectCommitCommand:
    owner_ref = f"principal://users/{_USER}"
    stager = harness.effect_stager()
    scope = EffectStageScope(run_id=_RUN, owner_ref=owner_ref)
    state = await stager.get_state(scope=scope, stage_id=stage_id)
    approved = await stager.decide(
        scope=scope,
        stage_id=stage_id,
        revision=state.current_revision.revision,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=state.current_revision.proposal_digest,
        target_digest=state.target_digest,
        actor=EffectActorIdentity(actor=EffectActor.USER, principal_ref=owner_ref),
        idempotency_key=f"approve:{stage_id}",
    )
    assert approved.decision is not None
    return EffectCommitCommand(
        run_id=_RUN,
        stage_id=stage_id,
        revision=approved.current_revision.revision,
        decision_ledger_id=approved.decision.ledger_id,
        proposal_digest=approved.current_revision.proposal_digest,
        target_digest=approved.target_digest,
        idempotency_key=f"effect-commit:{stage_id}:1",
    )


async def _coordinator(harness: _Harness, connector: _RecordingConnector):
    owner_ref = f"principal://users/{_USER}"
    scope = EffectExecutionScope(
        org_id=_ORG,
        user_id=_USER,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
        owner_ref=owner_ref,
    )
    targets = ArtifactDraftSendTargetStore(
        blobs=harness.blobs,
        references=harness.references,
        org_id=_ORG,
        user_id=_USER,
    )
    material = ArtifactDraftMcpEffectMaterialResolver(
        artifacts=harness.artifacts,
        targets=targets,
        org_id=_ORG,
        user_id=_USER,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
    )
    executor = McpEffectExecutor(
        scope=scope,
        connector=connector,  # type: ignore[arg-type] -- test transport is structural.
        material_resolver=material,
        enabled=True,
    )
    return EffectCoordinator(
        ledger=RuntimeEffectLedger(
            event_producer=harness.producer,
            run=harness.run,
            owner_ref=owner_ref,
        ),
        claims=InMemoryEffectClaimStore(),
        scopes=_ScopeResolver(scope),
        references=_References(material=material, targets=targets, scope=scope),
        executors=EffectExecutorRegistry({EffectExecutorKind.MCP: lambda _: executor}),
    ), material


async def test_artifact_send_stages_exact_revision_without_copying_legacy_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.seed_and_import(content="Original immutable body")

    first = await h.stage()
    retry = await h.stage()

    assert first.stage_id is not None
    assert retry.stage_id == first.stage_id
    # The read-through import is the only legacy mutation. Sending does not
    # create a `send_pending_approval` body copy.
    assert len(h.drafts.versions[(_ORG, _DRAFT_ID)]) == 1
    state = await h.effect_stager().get_state(
        scope=EffectStageScope(run_id=_RUN, owner_ref=f"principal://users/{_USER}"),
        stage_id=first.stage_id,
    )
    revision = state.current_revision
    assert revision.proposal_content_ref.startswith("artifact://")
    assert revision.proposal_content_ref.endswith("/revisions/1")
    assert revision.proposal_digest != ""
    assert state.target.target_ref.startswith("draft-send-target://sha256/")


async def test_artifact_stager_distinguishes_forbidden_from_unmigrated_legacy_row() -> (
    None
):
    """A foreign run scope is never the ``None`` migration compatibility signal."""

    h = _Harness()
    await h.seed_and_import()

    with pytest.raises(ArtifactDraftSendForbidden):
        await h.artifact_stager.stage(
            org_id=_ORG,
            user_id="user_same_org_not_owner",
            run=h.run,
            draft_id=_DRAFT_ID,
            target_connector="gmail",
            target_op="send_email",
            target_metadata={"recipient": "team@example.test"},
        )

    assert h.runtime.events_by_run[_RUN] == []
    assert h.runtime.effect_commit_commands == []


@pytest.mark.parametrize("failure", ("inaccessible", "mismatched"))
async def test_canonical_binding_failure_is_not_a_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Existing-but-unusable canonical records fail closed before side effects."""

    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.seed_and_import()
    binding = ArtifactDraftPathBinding(
        org_id=_ORG,
        user_id=_USER,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
        draft_id=_DRAFT_ID,
    )
    record = h.metadata._records[(_ORG, binding.artifact_id)]  # noqa: SLF001
    artifact_update = (
        {"user_id": "user_same_org_peer"}
        if failure == "inaccessible"
        else {"conversation_id": "conv_other"}
    )
    h.metadata._records[(_ORG, binding.artifact_id)] = record.model_copy(  # noqa: SLF001
        update={"artifact": record.artifact.model_copy(update=artifact_update)}
    )

    with pytest.raises(RuntimeApiError) as exc:
        await h.stage()

    assert exc.value.http_status == 404
    latest = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert latest is not None
    assert latest.version == 1
    assert latest.status is DraftStatus.DRAFT
    assert h.runtime.events_by_run[_RUN] == []
    assert h.runtime.approval_requests == {}
    assert h.runtime.approval_commands == []
    assert h.runtime.effect_commit_commands == []
    assert h.drafts.effect_supersessions == {}


async def test_mcp_material_resolver_raises_for_an_inaccessible_canonical_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic MCP material chain cannot fall through to mutable arguments."""

    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.seed_and_import()
    response = await h.stage()
    assert response.stage_id is not None
    state = await h.effect_stager().get_state(
        scope=EffectStageScope(run_id=_RUN, owner_ref=f"principal://users/{_USER}"),
        stage_id=response.stage_id,
    )
    request = EffectExecutionRequest(
        stage_id=response.stage_id,
        revision=state.current_revision.revision,
        idempotency_key="resolver-inaccessible",
        target_ref=state.target.target_ref,
        target_digest=state.target_digest,
        proposal_ref=state.current_revision.proposal_ref,
        proposal_content_ref=state.current_revision.proposal_content_ref,
        proposal_digest=state.current_revision.proposal_digest,
        actor=EffectActor.USER,
        decision_ledger_id="led_resolver_inaccessible",
    )
    binding = ArtifactDraftPathBinding(
        org_id=_ORG,
        user_id=_USER,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
        draft_id=_DRAFT_ID,
    )
    record = h.metadata._records[(_ORG, binding.artifact_id)]  # noqa: SLF001
    h.metadata._records[(_ORG, binding.artifact_id)] = record.model_copy(  # noqa: SLF001
        update={
            "artifact": record.artifact.model_copy(
                update={"user_id": "user_same_org_peer"}
            )
        }
    )
    _, material = await _coordinator(h, _RecordingConnector(requests=[]))

    with pytest.raises(ArtifactDraftRevisionForbidden):
        await material.resolve(request)

    generic_material = _UnexpectedGenericMaterial()
    resolver = McpOperationArgumentMaterialResolver(
        arguments=generic_material,
        additional_material_resolvers=(material,),
    )
    with pytest.raises(ArtifactDraftRevisionForbidden):
        await resolver.resolve(request)
    assert generic_material.calls == 0


async def test_changes_after_stage_cannot_alter_approved_payload_and_retry_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.seed_and_import(content="Legacy migration body")
    # This is the actual agent-authoring lane: the Artifact Draft backend, not
    # DraftService, writes the exact revision that the send will stage.
    authored = await h.backend.awrite(f"/drafts/{_DRAFT_ID}.md", "Approved revision")
    assert authored.error is None
    response = await h.stage()
    assert response.stage_id is not None

    # A later agent edit produces revision 2, but approval/execution is pinned
    # to the Artifact-authored revision. This is the exact F-006 adversary.
    changed = await h.backend.awrite(f"/drafts/{_DRAFT_ID}.md", "New mutable body")
    assert changed.error is None
    # A legacy row may still be edited by an old client or migration replay.
    # It is deliberately NOT consulted by the generic effect coordinator.
    legacy = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert legacy is not None
    await h.drafts.insert_version(
        legacy.model_copy(
            update={
                "version": legacy.version + 1,
                "content_text": "Legacy mutable body",
            }
        )
    )
    command = await _approved_command(h, stage_id=response.stage_id)
    connector = _RecordingConnector(requests=[])
    coordinator, _ = await _coordinator(h, connector)
    # Exercise the production worker boundary, not an alternate direct send
    # path. The queued command remains body-free and the A5 coordinator is the
    # only component that reopens the pinned Artifact revision.
    queued = h.runtime.effect_commit_commands[0]
    handler = RuntimeEffectCommitHandler(
        persistence=h.runtime,
        coordinator_factory=_CoordinatorFactory(coordinator),
    )
    first = await handler.handle(queued)
    replay = await handler.handle(queued)

    assert first is None
    assert replay is None
    assert command.proposal_digest == queued.proposal_digest
    assert len(connector.requests) == 1
    assert connector.requests[0].tool_arguments() == {
        "body": "Approved revision",
        "title": "Launch message",
        "target_metadata": {"recipient": "team@example.test"},
    }


async def test_stale_v1_approval_fails_closed_when_effect_stage_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replay outage cannot authorize a legacy latest-body send."""

    monkeypatch.setenv("SURFACES_V2", "false")
    h = _Harness()
    await h.seed_and_import(content="Legacy body under review")
    v1 = await h.stage()
    assert v1.approval_id is not None
    approval = await h.runtime.get_approval_request(
        org_id=_ORG, approval_id=v1.approval_id
    )
    assert approval is not None

    worker = RuntimeApprovalHandler(
        persistence=h.runtime,
        event_store=_FailingStageLookup(),
        draft_store=h.drafts,
    )
    await worker._resolve_draft_send_approval(  # noqa: SLF001 - adversarial seam.
        run=h.run,
        approval=approval,
        decision=ApprovalDecision.APPROVED,
        decided_by_user_id=_USER,
    )

    latest = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert latest is not None
    assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
    assert not any(
        version.status is DraftStatus.SENT
        for version in h.drafts.versions[(_ORG, _DRAFT_ID)]
    )


async def test_stale_v1_approval_fails_closed_when_effect_supersession_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable canonical correlation is unsafe, even with a healthy ledger."""

    monkeypatch.setenv("SURFACES_V2", "false")
    h = _Harness()
    await h.seed_and_import(content="Legacy body under review")
    v1 = await h.stage()
    assert v1.approval_id is not None
    approval = await h.runtime.get_approval_request(
        org_id=_ORG, approval_id=v1.approval_id
    )
    assert approval is not None

    worker = RuntimeApprovalHandler(
        persistence=h.runtime,
        event_store=h.runtime,
        draft_store=_FailingSupersessionLookup(h.drafts),  # type: ignore[arg-type]
    )
    await worker._resolve_draft_send_approval(  # noqa: SLF001 - adversarial seam.
        run=h.run,
        approval=approval,
        decision=ApprovalDecision.APPROVED,
        decided_by_user_id=_USER,
    )

    latest = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert latest is not None
    assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
    assert not any(
        version.status is DraftStatus.SENT
        for version in h.drafts.versions[(_ORG, _DRAFT_ID)]
    )


async def test_stale_v1_draft_approval_cannot_send_after_f006_effect_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An F-006 binding supersedes a pending v1 approval for the same draft.

    The old worker historically sent ``DraftRecord.latest``.  Once the exact
    Artifact revision is staged, resolving that older approval must do nothing:
    only the generic effect decision + A5 coordinator may send it.
    """

    h = _Harness()
    await h.seed_and_import(content="Original legacy approval body")

    monkeypatch.setenv("SURFACES_V2", "false")
    v1 = await h.stage()
    assert v1.approval_id is not None
    pending = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert pending is not None

    monkeypatch.setenv("SURFACES_V2", "true")
    f006 = await h.service.send(
        org_id=_ORG,
        user_id=_USER,
        draft_id=_DRAFT_ID,
        request=DraftSendRequest(
            expected_version=pending.version,
            target_connector="gmail",
            target_metadata={"op": "send_email", "recipient": "team@example.test"},
        ),
    )
    assert f006.stage_id is not None
    assert f006.approval_id is None

    approval = await h.runtime.get_approval_request(
        org_id=_ORG, approval_id=v1.approval_id
    )
    assert approval is not None
    worker = RuntimeApprovalHandler(
        persistence=h.runtime,
        event_store=h.runtime,
        draft_store=h.drafts,
    )
    await worker._resolve_draft_send_approval(  # noqa: SLF001 - adversarial seam.
        run=h.run,
        approval=approval,
        decision=ApprovalDecision.APPROVED,
        decided_by_user_id=_USER,
    )

    latest = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert latest is not None
    assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
    assert not any(
        version.status is DraftStatus.SENT
        for version in h.drafts.versions[(_ORG, _DRAFT_ID)]
    )


async def test_stale_v1_approval_is_blocked_by_f006_stage_on_a_different_host_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner-scoped correlation survives a legacy draft host-run move."""

    h = _Harness()
    await h.seed_and_import(content="Original v1 approval content")
    monkeypatch.setenv("SURFACES_V2", "false")
    v1 = await h.stage()
    assert v1.approval_id is not None
    pending = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert pending is not None

    # Simulate an old client/migration patch that re-homes a mutable legacy row
    # while preserving its pending state. The prior run's event ledger cannot
    # see the later host-run event, so only the canonical direct correlation is
    # permitted to decide whether this old approval is safe.
    moved = pending.model_copy(
        update={
            "version": pending.version + 1,
            "run_id": None,
            "content_text": "Mutated legacy bytes that v1 never reviewed",
        }
    )
    await h.drafts.insert_version(moved)
    host_run = h.add_host_run(run_id="run_f006_rehost")
    host_backend = h.artifact_backend_for(host_run)
    authored = await host_backend.awrite(
        f"/drafts/{_DRAFT_ID}.md", "Pinned Artifact revision on later host run"
    )
    assert authored.error is None

    stage = await h.artifact_stager.stage(
        org_id=_ORG,
        user_id=_USER,
        run=host_run,
        draft_id=_DRAFT_ID,
        target_connector="gmail",
        target_op="send_email",
        target_metadata={"recipient": "team@example.test"},
    )
    assert stage is not None
    assert await h.drafts.has_effect_supersession(
        org_id=_ORG, user_id=_USER, draft_id=_DRAFT_ID
    )
    assert not any(
        event.event_type.value == "effect.staged"
        for event in h.runtime.events_by_run[_RUN]
    )

    approval = await h.runtime.get_approval_request(
        org_id=_ORG, approval_id=v1.approval_id
    )
    assert approval is not None
    worker = RuntimeApprovalHandler(
        persistence=h.runtime,
        event_store=h.runtime,
        draft_store=h.drafts,
    )
    await worker._resolve_draft_send_approval(  # noqa: SLF001 - adversarial seam.
        run=h.run,
        approval=approval,
        decision=ApprovalDecision.APPROVED,
        decided_by_user_id=_USER,
    )

    latest = await h.drafts.latest(org_id=_ORG, draft_id=_DRAFT_ID)
    assert latest is not None
    assert latest.content_text == "Mutated legacy bytes that v1 never reviewed"
    assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
    assert not any(
        version.status is DraftStatus.SENT
        for version in h.drafts.versions[(_ORG, _DRAFT_ID)]
    )


async def test_missing_or_digest_mismatched_artifact_revision_never_reaches_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.seed_and_import(content="Revision one")
    response = await h.stage()
    assert response.stage_id is not None
    command = await _approved_command(h, stage_id=response.stage_id)
    connector = _RecordingConnector(requests=[])
    coordinator, _ = await _coordinator(h, connector)

    # Deliver a stale/forged A5 command through the real coordinator. Its
    # digest no longer agrees with the approved folded stage, so the
    # coordinator must refuse before it prepares or calls the connector.
    forged = command.model_copy(
        update={
            "proposal_digest": "0" * 64,
            "idempotency_key": f"effect-commit-stale:{command.stage_id}",
        }
    )
    result = await coordinator.handle(forged)

    assert result.status.value == "refused"
    assert connector.requests == []


async def test_worker_composition_reopens_artifact_material_without_a_second_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.seed_and_import(content="Factory-bound body")
    response = await h.stage()
    assert response.stage_id is not None
    command = await _approved_command(h, stage_id=response.stage_id)
    state = await h.effect_stager().get_state(
        scope=EffectStageScope(run_id=_RUN, owner_ref=f"principal://users/{_USER}"),
        stage_id=response.stage_id,
    )
    request = EffectExecutionRequest(
        stage_id=command.stage_id,
        revision=command.revision,
        idempotency_key=command.idempotency_key,
        target_ref=state.target.target_ref,
        target_digest=command.target_digest,
        proposal_ref=state.current_revision.proposal_ref,
        proposal_content_ref=state.current_revision.proposal_content_ref,
        proposal_digest=command.proposal_digest,
        actor=EffectActor.USER,
        decision_ledger_id=command.decision_ledger_id,
    )
    factory = RuntimeMcpEffectCoordinatorFactory(
        event_producer=h.producer,
        claims=InMemoryEffectClaimStore(),
        blobs=h.blobs,
        references=h.references,
        dependencies_factory=object(),
        timeout_seconds=30,
        artifact_service=h.artifacts,
    )
    scope = EffectExecutionScope(
        org_id=_ORG,
        user_id=_USER,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
        owner_ref=f"principal://users/{_USER}",
    )
    executor = factory.for_run(run=h.run)._executors.resolve(  # noqa: SLF001
        kind=EffectExecutorKind.MCP,
        scope=scope,
    )

    prepared = await executor.prepare(request)

    assert prepared.request == request


async def test_flag_off_preserves_legacy_draft_send_even_when_artifact_stager_is_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURFACES_V2", "false")
    h = _Harness()
    await h.seed_and_import(content="Dark cohort draft")

    result = await h.stage()

    assert result.stage_id is None
    assert result.approval_id is not None
    assert "approval_requested" in [
        event.event_type.value for event in h.runtime.events_by_run[_RUN]
    ]
    assert not any(
        event.event_type.value == "effect.staged"
        for event in h.runtime.events_by_run[_RUN]
    )


async def test_b1_cohort_off_keeps_the_existing_legacy_v2_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent B1 stager is the app-composition shape for ARTIFACT_DRAFTS_V2=off."""

    monkeypatch.setenv("SURFACES_V2", "true")
    h = _Harness()
    await h.drafts.insert_version(
        DraftRecord(
            draft_id=_DRAFT_ID,
            version=1,
            org_id=_ORG,
            conversation_id=_CONVERSATION,
            run_id=_RUN,
            user_id=_USER,
            title="Legacy only",
            content_text="Existing v2 path",
            status=DraftStatus.DRAFT,
        )
    )
    h.service = DraftService(
        store=h.drafts,
        persistence=h.runtime,
        auth_gate=_Authenticated(),
        event_producer=h.producer,
        write_stager=WriteStager(
            draft_store=h.drafts,
            ledger=RuntimeStageLedger(event_producer=h.producer),
            rollout_gate=legacy_staged_write_gate(),
        ),
        # Deliberately no artifact_draft_send_stager.
    )

    result = await h.stage()

    assert result.stage_id is not None
    assert result.approval_id is None
    event_types = [event.event_type.value for event in h.runtime.events_by_run[_RUN]]
    assert "write.staged" in event_types
    assert "effect.staged" not in event_types
