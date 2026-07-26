"""Production-route tests for generic, owner-scoped MCP effect decisions."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from agent_runtime.api.artifact_draft_send import ArtifactDraftSendStager
from agent_runtime.api.draft_service import DraftService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthOutcome,
)
from agent_runtime.capabilities.backends.artifact_draft_backend import (
    ArtifactDraftBackend,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.rollout import RolloutCapability
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord

_ORG = "org_effect_decision"
_USER = "user_effect_owner"
_OTHER_USER = "user_effect_other"
_RUN = "run_effect_decision"
_CONVERSATION = "conv_effect_decision"
_DRAFT = "deadbeefcafe1234deadbeefcafe1234"


class _Authenticated:
    async def check(self, **_kwargs: object) -> CapabilityAuthCheck:
        return CapabilityAuthCheck(outcome=CapabilityAuthOutcome.AUTHENTICATED)


def _settings(
    *, artifact_drafts_v2: bool, enrolled_user_id: str | None = None
) -> RuntimeSettings:
    environment = {
        "OPENAI_API_KEY": "sk-test",
        "RUNTIME_DEFAULT_PROVIDER": "openai",
        "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        "SURFACES_V2": "true",
        "ARTIFACT_EFFECTS_V2": "true",
        "ARTIFACT_DRAFTS_V2": "true" if artifact_drafts_v2 else "false",
    }
    if enrolled_user_id is not None:
        capabilities = (
            RolloutCapability.OPERATION_GATEWAY,
            RolloutCapability.EFFECT_STAGER,
            RolloutCapability.EFFECT_COMMIT,
            RolloutCapability.MCP_GATEWAY,
        )
        environment.update(
            {
                "OPERATION_GATEWAY_MODE": "enforce",
                "EFFECT_STAGER_MODE": "enforce",
                "EFFECT_COMMIT_MODE": "enforce",
                "MCP_GATEWAY_MODE": "enforce",
                "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                    [
                        {
                            "capability": capability.value,
                            "org_id": _ORG,
                            "user_id": enrolled_user_id,
                        }
                        for capability in capabilities
                    ]
                ),
            }
        )
    return RuntimeSettings.load(environ=environment)


def _run() -> RunRecord:
    return RunRecord(
        run_id=_RUN,
        conversation_id=_CONVERSATION,
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_effect_decision",
        trace_id="trace_effect_decision",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_effect_decision",
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


def _headers(*, org_id: str = _ORG, user_id: str = _USER) -> dict[str, str]:
    return {"x-enterprise-org-id": org_id, "x-enterprise-user-id": user_id}


def _stable_error(payload: dict[str, object]) -> dict[str, object]:
    """Compare only opaque error fields, excluding per-request diagnostics."""

    return {field: payload[field] for field in ("code", "safe_message", "retryable")}


class _Bundle:
    def __init__(self, *, settings: RuntimeSettings | None = None) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.store.runs[_RUN] = _run()
        self.store.events_by_run[_RUN] = []
        self.ports = RuntimeAdapterFactory.from_store(
            self.store, artifact_effects_v2=True
        )
        self.app = RuntimeApiAppFactory.create_app(
            ports=self.ports,
            settings=settings or _settings(artifact_drafts_v2=True),
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
        )
        artifacts = self.app.state.artifact_service
        assert artifacts is not None
        self.app.state.draft_service = DraftService(
            store=self.ports.draft_store,
            persistence=self.store,
            auth_gate=_Authenticated(),
            event_producer=RuntimeEventProducer(
                persistence=self.store,
                event_store=self.store,
            ),
            artifact_draft_send_stager=ArtifactDraftSendStager(
                artifacts=artifacts,
                event_producer=RuntimeEventProducer(
                    persistence=self.store,
                    event_store=self.store,
                ),
                queue=self.ports.queue,
                blobs=self.ports.artifact_blob_store,
                references=self.ports.artifact_reference_provider,
                supersessions=self.ports.draft_store,
            ),
        )
        self.client = TestClient(self.app)

    def import_artifact_draft(self) -> None:
        asyncio.run(
            self.ports.draft_store.insert_version(
                DraftRecord(
                    draft_id=_DRAFT,
                    version=1,
                    org_id=_ORG,
                    conversation_id=_CONVERSATION,
                    run_id=_RUN,
                    user_id=_USER,
                    title="Launch update",
                    content_text="The exact immutable Artifact revision.",
                    status=DraftStatus.DRAFT,
                )
            )
        )
        backend = ArtifactDraftBackend(
            artifacts=self.app.state.artifact_service,
            org_id=_ORG,
            user_id=_USER,
            conversation_id=_CONVERSATION,
            run_id=_RUN,
            legacy_store=self.ports.draft_store,
        )
        imported = asyncio.run(backend.aread(f"/drafts/{_DRAFT}.md"))
        assert imported.file_data is not None

    def stage_artifact_draft(self) -> dict[str, object]:
        self.import_artifact_draft()
        response = self.client.post(
            f"/v1/agent/drafts/{_DRAFT}/send",
            headers=_headers(),
            json={
                "expected_version": 1,
                "target_connector": "gmail",
                "target_metadata": {
                    "op": "send_email",
                    "recipient": "team@example.test",
                },
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload["stage_id"], str)
        return payload


def _decision_body(event: object) -> dict[str, object]:
    payload = getattr(event, "payload")
    return {
        "revision": 1,
        "decision": "approve",
        "proposal_digest": payload["proposal_digest"],
        "target_digest": payload["target_digest"],
    }


class TestEffectStageDecisionRoute:
    def test_nonmatching_e2_cohort_cannot_append_a_decision_or_enqueue_a5(
        self,
    ) -> None:
        bundle = _Bundle(
            settings=_settings(
                artifact_drafts_v2=True,
                enrolled_user_id="other_rollout_user",
            )
        )
        staged = bundle.stage_artifact_draft()
        stage_id = str(staged["stage_id"])
        body = _decision_body(bundle.store.events_by_run[_RUN][-1])

        response = bundle.client.post(
            f"/v1/agent/effect-stages/{stage_id}/decision?run_id={_RUN}",
            headers=_headers(),
            json=body,
        )

        assert response.status_code == 404
        assert [
            event.event_type.value for event in bundle.store.events_by_run[_RUN]
        ] == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_staged_artifact_draft_is_approveable_and_enqueues_exactly_one_a5_command(
        self,
    ) -> None:
        bundle = _Bundle()
        staged = bundle.stage_artifact_draft()
        stage_id = str(staged["stage_id"])
        staged_event = bundle.store.events_by_run[_RUN][-1]
        body = _decision_body(staged_event)

        first = bundle.client.post(
            f"/v1/agent/effect-stages/{stage_id}/decision?run_id={_RUN}",
            headers=_headers(),
            json=body,
        )
        retry = bundle.client.post(
            f"/v1/agent/effect-stages/{stage_id}/decision?run_id={_RUN}",
            headers=_headers(),
            json=body,
        )

        assert first.status_code == 200, first.text
        assert retry.status_code == 200, retry.text
        assert retry.json() == first.json()
        assert first.json()["status"] == "approved"
        assert [
            event.event_type.value for event in bundle.store.events_by_run[_RUN]
        ] == ["effect.staged", "effect.decision_recorded"]
        assert len(bundle.store.effect_commit_commands) == 1
        command = bundle.store.effect_commit_commands[0]
        assert command.stage_id == stage_id
        assert command.proposal_digest == body["proposal_digest"]
        assert command.target_digest == body["target_digest"]

    def test_ordinary_non_owner_cannot_decide_an_artifact_effect(self) -> None:
        bundle = _Bundle()
        staged = bundle.stage_artifact_draft()
        stage_id = str(staged["stage_id"])
        body = _decision_body(bundle.store.events_by_run[_RUN][-1])

        response = bundle.client.post(
            f"/v1/agent/effect-stages/{stage_id}/decision?run_id={_RUN}",
            headers=_headers(user_id=_OTHER_USER),
            json=body,
        )

        assert response.status_code == 404
        assert [
            event.event_type.value for event in bundle.store.events_by_run[_RUN]
        ] == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_same_org_non_owner_cannot_fall_back_from_imported_artifact_send(
        self,
    ) -> None:
        """The DraftService boundary denies before versions, approvals, or stages."""

        bundle = _Bundle()
        bundle.import_artifact_draft()
        body = {
            "expected_version": 1,
            "target_connector": "gmail",
            "target_metadata": {
                "op": "send_email",
                "recipient": "team@example.test",
            },
        }

        denied = bundle.client.post(
            f"/v1/agent/drafts/{_DRAFT}/send",
            headers=_headers(user_id=_OTHER_USER),
            json=body,
        )
        absent = bundle.client.post(
            "/v1/agent/drafts/cafebabecafebabecafebabecafebabe/send",
            headers=_headers(user_id=_OTHER_USER),
            json=body,
        )

        assert denied.status_code == 404
        assert _stable_error(denied.json()) == _stable_error(absent.json())
        latest = asyncio.run(
            bundle.ports.draft_store.latest(org_id=_ORG, draft_id=_DRAFT)
        )
        assert latest is not None
        assert latest.version == 1
        assert latest.status is DraftStatus.DRAFT
        assert bundle.store.events_by_run[_RUN] == []
        assert bundle.store.approval_requests == {}
        assert bundle.store.approval_commands == []
        assert bundle.store.effect_commit_commands == []

    def test_same_org_peer_cannot_patch_then_send_an_imported_artifact_draft(
        self,
    ) -> None:
        """A failed PATCH cannot re-stamp ownership and unlock a later send."""

        bundle = _Bundle()
        bundle.import_artifact_draft()
        patch_body = {"expected_version": 1, "content_text": "Peer takeover"}
        send_body = {
            "expected_version": 1,
            "target_connector": "gmail",
            "target_metadata": {"op": "send_email"},
        }

        denied_patch = bundle.client.patch(
            f"/v1/agent/drafts/{_DRAFT}",
            headers=_headers(user_id=_OTHER_USER),
            json=patch_body,
        )
        absent_patch = bundle.client.patch(
            "/v1/agent/drafts/cafebabecafebabecafebabecafebabe",
            headers=_headers(user_id=_OTHER_USER),
            json=patch_body,
        )
        denied_send = bundle.client.post(
            f"/v1/agent/drafts/{_DRAFT}/send",
            headers=_headers(user_id=_OTHER_USER),
            json=send_body,
        )

        assert denied_patch.status_code == 404
        assert _stable_error(denied_patch.json()) == _stable_error(absent_patch.json())
        assert denied_send.status_code == 404
        latest = asyncio.run(
            bundle.ports.draft_store.latest(org_id=_ORG, draft_id=_DRAFT)
        )
        assert latest is not None
        assert latest.version == 1
        assert latest.user_id == _USER
        assert latest.content_text == "The exact immutable Artifact revision."
        assert bundle.store.events_by_run[_RUN] == []
        assert bundle.store.approval_requests == {}
        assert bundle.store.approval_commands == []
        assert bundle.store.effect_commit_commands == []

    def test_same_org_peer_cannot_discard_an_imported_artifact_draft(self) -> None:
        """Discard is an owner-only terminal transition with opaque denial."""

        bundle = _Bundle()
        bundle.import_artifact_draft()
        body = {"expected_version": 1}

        denied = bundle.client.post(
            f"/v1/agent/drafts/{_DRAFT}/discard",
            headers=_headers(user_id=_OTHER_USER),
            json=body,
        )
        absent = bundle.client.post(
            "/v1/agent/drafts/cafebabecafebabecafebabecafebabe/discard",
            headers=_headers(user_id=_OTHER_USER),
            json=body,
        )

        assert denied.status_code == 404
        assert _stable_error(denied.json()) == _stable_error(absent.json())
        latest = asyncio.run(
            bundle.ports.draft_store.latest(org_id=_ORG, draft_id=_DRAFT)
        )
        assert latest is not None
        assert latest.version == 1
        assert latest.user_id == _USER
        assert latest.status is DraftStatus.DRAFT
        assert bundle.store.events_by_run[_RUN] == []
        assert bundle.store.approval_requests == {}
        assert bundle.store.approval_commands == []
        assert bundle.store.effect_commit_commands == []

    def test_cross_org_request_is_an_opaque_404_without_a_decision_or_command(
        self,
    ) -> None:
        bundle = _Bundle()
        staged = bundle.stage_artifact_draft()
        stage_id = str(staged["stage_id"])
        body = _decision_body(bundle.store.events_by_run[_RUN][-1])

        response = bundle.client.post(
            f"/v1/agent/effect-stages/{stage_id}/decision?run_id={_RUN}",
            headers=_headers(org_id="org_effect_other", user_id=_USER),
            json=body,
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "resource not found"}
        assert [
            event.event_type.value for event in bundle.store.events_by_run[_RUN]
        ] == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_digest_mismatch_does_not_append_or_enqueue(self) -> None:
        bundle = _Bundle()
        staged = bundle.stage_artifact_draft()
        stage_id = str(staged["stage_id"])
        body = _decision_body(bundle.store.events_by_run[_RUN][-1])
        body["proposal_digest"] = "0" * 64

        response = bundle.client.post(
            f"/v1/agent/effect-stages/{stage_id}/decision?run_id={_RUN}",
            headers=_headers(),
            json=body,
        )

        assert response.status_code == 409
        assert [
            event.event_type.value for event in bundle.store.events_by_run[_RUN]
        ] == ["effect.staged"]
        assert bundle.store.effect_commit_commands == []

    def test_route_is_absent_when_artifact_draft_cohort_is_off(self) -> None:
        store = InMemoryRuntimeApiStore()
        ports = RuntimeAdapterFactory.from_store(store, artifact_effects_v2=True)
        app = RuntimeApiAppFactory.create_app(
            ports=ports,
            settings=_settings(artifact_drafts_v2=False),
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
        )

        response = TestClient(app).post(
            "/v1/agent/effect-stages/stg_00000000-0000-4000-8000-000000000001/decision?run_id=run_dark",
            headers=_headers(),
            json={
                "revision": 1,
                "decision": "approve",
                "proposal_digest": "a" * 64,
                "target_digest": "b" * 64,
            },
        )

        assert response.status_code == 404
