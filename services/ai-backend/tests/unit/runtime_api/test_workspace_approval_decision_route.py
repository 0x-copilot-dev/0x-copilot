"""C3 contract tests for the canonical workspace approval receipt route."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.ledger_ids import WorkspaceTargetRefCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
)
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord

_ORG = "org_workspace_receipt"
_USER = "user_workspace_receipt"
_RUN = "run_workspace_receipt"
_CONVERSATION = "conv_workspace_receipt"
_OPERATION = "op_00000000-0000-4000-8000-000000000123"
_ARTIFACT = "art_00000000-0000-4000-8000-000000000123"
_WORKSPACE_ENTRIES = [
    {
        "operation": "create",
        "relative_path": "receipt.csv",
        "content_slot": "content_0",
        "content_digest": "c" * 64,
        "content_size": 0,
        "precondition": {"exists": False},
    }
]
_WORKSPACE_CHANGE_SET_DIGEST = sha256_hex(
    canonical_json_bytes(
        {
            "grant_id": "grant_receipt",
            "mount": "receipt",
            "entries": _WORKSPACE_ENTRIES,
        }
    )
)


async def _one_chunk(body: bytes):
    yield body


async def _workspace_material(
    ports: object, *, user_id: str, target_digest: str
) -> tuple[str, str]:
    material = {
        "grant_id": "grant_receipt",
        "mount": "receipt",
        "change_set_digest": _WORKSPACE_CHANGE_SET_DIGEST,
        "target_digest": target_digest,
        "entries": _WORKSPACE_ENTRIES,
    }
    body = canonical_json_bytes(material)
    proposal_digest = sha256_hex(body)
    blobs = ports.artifact_blob_store
    references = ports.artifact_reference_provider
    await blobs.put_stream(
        expected_digest=proposal_digest,
        chunks=_one_chunk(body),
        byte_limit=2 * 1024 * 1024,
    )
    await references.acquire(
        ArtifactReferenceEdge(
            org_id=_ORG,
            edge_id=f"workspace-receipt-material-{proposal_digest}",
            user_id=user_id,
            blob_key=proposal_digest,
            reference_kind=ArtifactReferenceKind.EFFECT,
            reference_id=f"workspace-material://sha256/{proposal_digest}",
            created_at=datetime.now(timezone.utc),
        )
    )
    return f"workspace-material://sha256/{proposal_digest}", proposal_digest


def _settings(*, enabled: bool) -> RuntimeSettings:
    environ = {
        "OPENAI_API_KEY": "sk-test",
        "RUNTIME_DEFAULT_PROVIDER": "openai",
        "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
    }
    if enabled:
        environ.update(
            {
                "SURFACES_V2": "true",
                "ARTIFACT_EFFECTS_V2": "true",
                "OPERATION_GATEWAY_MODE": "enforce",
                "WORKSPACE_EFFECT_MODE": "enforce",
            }
        )
    return RuntimeSettings.load(environ=environ)


def _headers(*, user_id: str = _USER) -> dict[str, str]:
    return {"x-enterprise-org-id": _ORG, "x-enterprise-user-id": user_id}


def _run(*, run_id: str, user_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id=f"conv_{run_id}",
        org_id=_ORG,
        user_id=user_id,
        user_message_id=f"msg_{run_id}",
        trace_id=f"trace_{run_id}",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=user_id,
            org_id=_ORG,
            roles=["employee"],
            run_id=run_id,
            trace_id=f"trace_{run_id}",
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


class _Bundle:
    def __init__(
        self,
        *,
        client: TestClient,
        store: InMemoryRuntimeApiStore,
        ports: object,
    ) -> None:
        self.client = client
        self.store = store
        self.ports = ports

    def stage_workspace(self, *, run_id: str = _RUN, user_id: str = _USER):
        return asyncio.run(
            _stage_effect(
                store=self.store,
                ports=self.ports,
                run=self.store.runs[run_id],
                user_id=user_id,
                executor=EffectExecutorKind.WORKSPACE,
            )
        )

    def stage_mcp(self):
        return asyncio.run(
            _stage_effect(
                store=self.store,
                ports=self.ports,
                run=self.store.runs[_RUN],
                user_id=_USER,
                executor=EffectExecutorKind.MCP,
            )
        )


def _bundle(*, enabled: bool = True) -> _Bundle:
    store = InMemoryRuntimeApiStore()
    store.runs[_RUN] = _run(run_id=_RUN, user_id=_USER)
    store.events_by_run.setdefault(_RUN, [])
    ports = RuntimeAdapterFactory.from_store(store, artifact_effects_v2=True)
    app = RuntimeApiAppFactory.create_app(
        ports=ports, settings=_settings(enabled=enabled)
    )
    return _Bundle(client=TestClient(app), store=store, ports=ports)


async def _stage_effect(*, store, ports, run, user_id: str, executor):  # noqa: ANN001
    owner_ref = f"principal://users/{user_id}"
    scope = EffectStageScope(run_id=run.run_id, owner_ref=owner_ref)
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    stager = EffectStager(
        ledger=RuntimeEffectLedger(
            event_producer=producer,
            run=run,
            owner_ref=owner_ref,
        ),
        outbox=RuntimeEffectCommitOutbox(
            queue=ports.queue,
            scope=EffectExecutionScope(
                org_id=_ORG,
                user_id=user_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                owner_ref=owner_ref,
            ),
        ),
    )
    workspace = executor is EffectExecutorKind.WORKSPACE
    target_digest = "b" * 64
    proposal_content_ref = f"operation://{_OPERATION}/args"
    proposal_digest = "a" * 64
    if workspace:
        proposal_content_ref, proposal_digest = await _workspace_material(
            ports,
            user_id=user_id,
            target_digest=target_digest,
        )
    return await stager.stage(
        scope=scope,
        proposed_effect=ProposedEffect(
            operation_id=_OPERATION,
            executor=executor,
            target=EffectTarget(
                executor=executor,
                capability="workspace" if workspace else "mcp-test",
                op="replace" if workspace else "update",
                target_ref=(
                    WorkspaceTargetRefCodec.format("grant_receipt", "path_token")
                    if workspace
                    else "mcp-target://record/opaque-target"
                ),
                display_label="Finance workspace" if workspace else "Managed record",
            ),
            target_digest=target_digest,
            display_target="Finance workspace" if workspace else "Managed record",
            proposal_kind=(
                EffectProposalKind.WORKSPACE_CHANGE_SET
                if workspace
                else EffectProposalKind.CANONICAL_ARGUMENTS
            ),
            proposal_content_ref=proposal_content_ref,
            proposal_digest=proposal_digest,
            proposal_media_type=(
                "application/vnd.0xcopilot.workspace-change-set+json"
                if workspace
                else "application/json"
            ),
            effect_class=EffectClass.EXTERNAL_REVERSIBLE,
            policy_snapshot_ref="policy://workspace-receipt/snapshot",
        ),
        policy_snapshot=EffectPolicySnapshot(
            snapshot_ref="policy://workspace-receipt/snapshot",
            descriptor_known=True,
            user_policy=EffectPolicy.ASK,
        ),
        actor=EffectActorIdentity(actor=EffectActor.USER, principal_ref=owner_ref),
        idempotency_key=f"stage-{executor.value}-{run.run_id}",
    )


def _body(state, **overrides):  # noqa: ANN001
    return {
        "revision": state.current_revision.revision,
        "decision": "approve",
        "proposal_digest": state.current_revision.proposal_digest,
        "target_digest": state.target_digest,
        **overrides,
    }


def _url(stage_id: str, *, run_id: str = _RUN) -> str:
    return f"/v1/agent/effect-stages/{stage_id}/decisions?run_id={run_id}"


def _generic_url(stage_id: str, *, run_id: str = _RUN) -> str:
    return f"/v1/agent/effect-stages/{stage_id}/decision?run_id={run_id}"


def _event_types(store: InMemoryRuntimeApiStore, run_id: str = _RUN) -> list[str]:
    return [event.event_type.value for event in store.events_by_run.get(run_id, [])]


class TestWorkspaceApprovalDecisionReceipt:
    def test_generic_mcp_route_hides_workspace_stage_as_opaque_404(self) -> None:
        """Executor kind is an authorization boundary, not a UI hint."""

        bundle = _bundle()
        stage = bundle.stage_workspace()

        response = bundle.client.post(
            _generic_url(stage.stage_id), headers=_headers(), json=_body(stage)
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "resource not found"}
        assert _event_types(bundle.store) == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_approved_receipt_is_ledger_derived_and_contains_only_safe_fields(
        self,
    ) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()

        response = bundle.client.post(
            _url(stage.stage_id), headers=_headers(), json=_body(stage)
        )

        assert response.status_code == 200, response.text
        receipt = response.json()
        decision_event = bundle.store.events_by_run[_RUN][-1]
        assert decision_event.event_type.value == "effect.decision_recorded"
        assert receipt == {
            "stage_id": stage.stage_id,
            "revision": stage.current_revision.revision,
            "decision_ledger_id": LedgerIdCodec.format(
                decision_event.run_id, decision_event.sequence_no
            ),
            "change_set_digest": _WORKSPACE_CHANGE_SET_DIGEST,
            "proposal_digest": decision_event.payload["proposal_digest"],
            "target_digest": decision_event.payload["target_digest"],
            "decision": decision_event.payload["decision"],
            "status": "approved",
        }
        assert len(bundle.store.effect_commit_commands) == 1
        command = bundle.store.effect_commit_commands[0]
        assert command.decision_ledger_id == receipt["decision_ledger_id"]
        assert command.proposal_digest == receipt["proposal_digest"]
        assert command.target_digest == receipt["target_digest"]

        # The public receipt is an intentionally tiny allowlist, not a stage
        # dump. None of C1/C2 private identifiers or any physical path/content
        # can cross this route.
        assert set(receipt) == {
            "stage_id",
            "revision",
            "decision_ledger_id",
            "change_set_digest",
            "proposal_digest",
            "target_digest",
            "decision",
            "status",
        }
        forbidden = {
            "path",
            "root",
            "permit",
            "prepared_ref",
            "proposal_content_ref",
            "target_ref",
            "content",
        }
        assert not (set(receipt) & forbidden)
        wire = response.text
        assert "workspace-target://" not in wire
        assert "artifact://" not in wire
        assert "workspace-prepared://" not in wire
        assert "wcp_" not in wire
        assert "/Users/" not in wire

    def test_reject_receipt_is_safe_and_never_enqueues_a5_work(self) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()

        response = bundle.client.post(
            _url(stage.stage_id),
            headers=_headers(),
            json=_body(stage, decision="reject"),
        )

        assert response.status_code == 200, response.text
        assert response.json()["decision"] == "reject"
        assert response.json()["status"] == "rejected"
        assert bundle.store.effect_commit_commands == []

    def test_identical_retry_replays_the_persisted_receipt_once(self) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()
        body = _body(stage)

        first = bundle.client.post(_url(stage.stage_id), headers=_headers(), json=body)
        second = bundle.client.post(_url(stage.stage_id), headers=_headers(), json=body)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json() == first.json()
        assert _event_types(bundle.store) == [
            "effect.staged",
            "effect.decision_recorded",
        ]
        assert len(bundle.store.effect_commit_commands) == 1

    def test_stale_revision_is_rejected_before_a_decision_or_command(self) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()

        response = bundle.client.post(
            _url(stage.stage_id),
            headers=_headers(),
            json=_body(stage, revision=stage.current_revision.revision + 1),
        )

        assert response.status_code == 409
        assert _event_types(bundle.store) == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    @pytest.mark.parametrize("field", ["proposal_digest", "target_digest"])
    def test_tampered_digest_is_rejected_before_a_decision_or_command(
        self, field: str
    ) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()
        body = _body(stage)
        body[field] = "f" * 64

        response = bundle.client.post(
            _url(stage.stage_id), headers=_headers(), json=body
        )

        assert response.status_code == 409
        assert _event_types(bundle.store) == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_unverifiable_workspace_material_is_rejected_before_decision(
        self,
    ) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()
        proposal_digest = stage.current_revision.proposal_digest
        asyncio.run(
            bundle.ports.artifact_reference_provider.release(
                org_id=_ORG,
                edge_id=f"workspace-receipt-material-{proposal_digest}",
            )
        )

        response = bundle.client.post(
            _url(stage.stage_id), headers=_headers(), json=_body(stage)
        )

        assert response.status_code == 409
        assert _event_types(bundle.store) == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    @pytest.mark.parametrize(
        "body_override",
        [
            {"target_digest": None},
            {"proposal_digest": "not-a-digest"},
            {"physical_path": "/Users/alice/private/project"},
            {"decision_ledger_id": "renderer-forged-ledger"},
        ],
    )
    def test_missing_or_untrusted_receipt_inputs_are_rejected_without_mutation(
        self, body_override: dict[str, object]
    ) -> None:
        bundle = _bundle()
        stage = bundle.stage_workspace()
        body = _body(stage)
        body.update(body_override)

        response = bundle.client.post(
            _url(stage.stage_id), headers=_headers(), json=body
        )

        # Runtime API request validation is normalized to the existing public
        # 400 response shape; malformed renderer input must still be rejected
        # before it reaches the canonical stage ledger.
        assert response.status_code == 400
        assert _event_types(bundle.store) == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_unknown_foreign_and_nonworkspace_stages_all_fail_closed_as_404(
        self,
    ) -> None:
        bundle = _bundle()
        unknown = "stg_00000000-0000-4000-8000-000000000999"
        unknown_response = bundle.client.post(
            _url(unknown),
            headers=_headers(),
            json={
                "revision": 1,
                "decision": "approve",
                "proposal_digest": "a" * 64,
                "target_digest": "b" * 64,
            },
        )
        assert unknown_response.status_code == 404

        foreign_user = "user_workspace_foreign"
        foreign_run = "run_workspace_foreign"
        bundle.store.runs[foreign_run] = _run(run_id=foreign_run, user_id=foreign_user)
        bundle.store.events_by_run.setdefault(foreign_run, [])
        foreign_stage = bundle.stage_workspace(run_id=foreign_run, user_id=foreign_user)
        foreign_response = bundle.client.post(
            _url(foreign_stage.stage_id, run_id=foreign_run),
            headers=_headers(),
            json=_body(foreign_stage),
        )
        assert foreign_response.status_code == 404

        mcp_stage = bundle.stage_mcp()
        mcp_response = bundle.client.post(
            _url(mcp_stage.stage_id), headers=_headers(), json=_body(mcp_stage)
        )
        assert mcp_response.status_code == 404
        assert (
            unknown_response.json()
            == foreign_response.json()
            == mcp_response.json()
            == {"detail": "resource not found"}
        )
        assert bundle.store.effect_commit_commands == []

    def test_route_is_absent_when_workspace_effects_are_not_enforced(self) -> None:
        bundle = _bundle(enabled=False)
        response = bundle.client.post(
            _url("stg_00000000-0000-4000-8000-000000000999"),
            headers=_headers(),
            json={
                "revision": 1,
                "decision": "approve",
                "proposal_digest": "a" * 64,
                "target_digest": "b" * 64,
            },
        )
        assert response.status_code == 404
