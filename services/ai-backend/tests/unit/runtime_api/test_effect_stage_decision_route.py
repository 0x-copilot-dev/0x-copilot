"""Production-route tests for generic, owner-scoped MCP effect decisions."""

from __future__ import annotations

import asyncio

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


def _settings(*, artifact_drafts_v2: bool) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "SURFACES_V2": "true",
            "ARTIFACT_EFFECTS_V2": "true",
            "ARTIFACT_DRAFTS_V2": "true" if artifact_drafts_v2 else "false",
        }
    )


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


def _headers(*, user_id: str = _USER) -> dict[str, str]:
    return {"x-enterprise-org-id": _ORG, "x-enterprise-user-id": user_id}


class _Bundle:
    def __init__(self) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.store.runs[_RUN] = _run()
        self.store.events_by_run[_RUN] = []
        self.ports = RuntimeAdapterFactory.from_store(
            self.store, artifact_effects_v2=True
        )
        self.app = RuntimeApiAppFactory.create_app(
            ports=self.ports,
            settings=_settings(artifact_drafts_v2=True),
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
            ),
        )
        self.client = TestClient(self.app)

    def stage_artifact_draft(self) -> dict[str, object]:
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
