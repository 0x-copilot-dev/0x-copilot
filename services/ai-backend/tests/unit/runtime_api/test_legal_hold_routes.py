"""Adversarial HTTP tests for the D11 legal-hold control plane."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from copilot_service_contracts.scopes import ADMIN_RETENTION, RUNTIME_USE
from fastapi.testclient import TestClient

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    LegalHoldReasonCode,
    LegalHoldRecord,
    LegalHoldScope,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, ConversationRecord, RunRecord

_ORG = "org_legal_hold"
_OTHER_ORG = "org_other"
_ADMIN = "user_retention_admin"
_OTHER_ADMIN = "user_other_admin"
_SUBJECT = "user_subject"
_OTHER_SUBJECT = "user_other_subject"


def _client() -> tuple[TestClient, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    ports = RuntimeAdapterFactory.from_store(store)
    return TestClient(
        RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
    ), store


def _headers(
    *,
    org_id: str = _ORG,
    user_id: str = _ADMIN,
    admin: bool = True,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    scopes = [RUNTIME_USE]
    if admin:
        scopes.append(ADMIN_RETENTION)
    values = {
        "x-enterprise-org-id": org_id,
        "x-enterprise-user-id": user_id,
        "x-enterprise-permission-scopes": ",".join(scopes),
        "x-enterprise-connector-scopes": "{}",
    }
    if idempotency_key is not None:
        values["Idempotency-Key"] = idempotency_key
    return values


def _seed_conversation(
    store: InMemoryRuntimeApiStore,
    *,
    conversation_id: str,
    org_id: str = _ORG,
    user_id: str = _SUBJECT,
) -> None:
    store.conversations[conversation_id] = ConversationRecord(
        conversation_id=conversation_id,
        org_id=org_id,
        user_id=user_id,
        assistant_id="assistant_legal_hold",
        title="internal test only",
    )


class TestLegalHoldRoutes:
    @staticmethod
    def _active_run(*, run_id: str, conversation_id: str) -> RunRecord:
        return RunRecord(
            run_id=run_id,
            conversation_id=conversation_id,
            org_id=_ORG,
            user_id=_SUBJECT,
            user_message_id=f"msg_{run_id}",
            trace_id=f"trace_{run_id}",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            status=AgentRunStatus.RUNNING,
            runtime_context=AgentRuntimeContext(
                user_id=_SUBJECT,
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

    def test_create_retry_list_and_audit_access_without_target_enumeration(
        self,
    ) -> None:
        client, store = _client()
        _seed_conversation(store, conversation_id="conv_hold")
        payload = {
            "scope": "conversation",
            "target_conversation_id": "conv_hold",
            "reason_code": "legal_request",
        }
        headers = _headers(idempotency_key="create-hold-001")

        created = client.post(
            "/v1/retention/legal-holds", headers=headers, json=payload
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["scope"] == "conversation"
        assert body["target_conversation_id"] == "conv_hold"
        assert "resource_id" not in body

        retry = client.post("/v1/retention/legal-holds", headers=headers, json=payload)
        assert retry.status_code == 201, retry.text
        assert retry.json()["id"] == body["id"]
        assert retry.json()["replayed"] is True

        listing = client.get("/v1/retention/legal-holds", headers=_headers())
        assert listing.status_code == 200, listing.text
        assert [row["id"] for row in listing.json()["holds"]] == [body["id"]]

        audited = [
            record
            for event_type, record in store.audit_log
            if event_type == "legal_hold.accessed"
        ]
        assert len(audited) == 1
        access = audited[0]
        assert access["resource_type"] == "legal_hold_collection"
        assert access["resource_id"] == "tenant_collection"
        assert access["metadata"] == {"include_released": False, "limit": 50}
        assert "conv_hold" not in str(access)
        assert body["id"] not in str(access)

        actions = [event_type for event_type, _record in store.audit_log]
        assert actions.count("legal_hold.created") == 1

    def test_closed_target_contract_and_unauthorized_access_fail_closed(self) -> None:
        client, store = _client()
        _seed_conversation(store, conversation_id="conv_hold")
        invalid = client.post(
            "/v1/retention/legal-holds",
            headers=_headers(idempotency_key="bad-target-001"),
            json={
                "scope": "conversation",
                "target_conversation_id": "conv_hold",
                "reason_code": "legal_request",
                "resource_id": "arbitrary://must-not-be-accepted",
            },
        )
        assert invalid.status_code == 400

        # This explicit check is independent of RBAC_MODE=audit: a retention
        # control-plane endpoint must not inherit audit-mode pass-through.
        denied = client.get("/v1/retention/legal-holds", headers=_headers(admin=False))
        assert denied.status_code == 403
        assert store.audit_log == []

    def test_cross_tenant_target_release_and_list_do_not_enumerate(self) -> None:
        client, store = _client()
        _seed_conversation(
            store,
            conversation_id="conv_other",
            org_id=_OTHER_ORG,
            user_id=_OTHER_SUBJECT,
        )
        _seed_conversation(store, conversation_id="conv_local")
        other_created = client.post(
            "/v1/retention/legal-holds",
            headers=_headers(
                org_id=_OTHER_ORG,
                user_id=_OTHER_ADMIN,
                idempotency_key="other-hold-001",
            ),
            json={
                "scope": "conversation",
                "target_conversation_id": "conv_other",
                "reason_code": "investigation",
            },
        )
        assert other_created.status_code == 201, other_created.text
        other_id = other_created.json()["id"]

        foreign_target = client.post(
            "/v1/retention/legal-holds",
            headers=_headers(idempotency_key="foreign-target-001"),
            json={
                "scope": "conversation",
                "target_conversation_id": "conv_other",
                "reason_code": "legal_request",
            },
        )
        absent_target = client.post(
            "/v1/retention/legal-holds",
            headers=_headers(idempotency_key="absent-target-001"),
            json={
                "scope": "conversation",
                "target_conversation_id": "conv_absent",
                "reason_code": "legal_request",
            },
        )
        assert foreign_target.status_code == absent_target.status_code == 404
        assert foreign_target.json() == absent_target.json()

        own_listing = client.get("/v1/retention/legal-holds", headers=_headers())
        assert own_listing.status_code == 200
        assert own_listing.json()["holds"] == []
        assert "conv_other" not in own_listing.text
        assert other_id not in own_listing.text

        foreign_release = client.post(
            f"/v1/retention/legal-holds/{other_id}/release",
            headers=_headers(idempotency_key="foreign-release-001"),
            json={"expected_revision": 1},
        )
        absent_release = client.post(
            "/v1/retention/legal-holds/lh_absent/release",
            headers=_headers(idempotency_key="absent-release-001"),
            json={"expected_revision": 1},
        )
        assert foreign_release.status_code == absent_release.status_code == 404
        assert foreign_release.json() == absent_release.json()
        assert other_id not in foreign_release.text

    async def test_release_is_cas_idempotent_and_unblocks_normal_soft_delete(
        self,
    ) -> None:
        client, store = _client()
        _seed_conversation(store, conversation_id="conv_hold")
        created = client.post(
            "/v1/retention/legal-holds",
            headers=_headers(idempotency_key="create-release-001"),
            json={
                "scope": "conversation",
                "target_conversation_id": "conv_hold",
                "reason_code": "compliance",
            },
        )
        assert created.status_code == 201, created.text
        hold_id = created.json()["id"]

        # An active hold wins immediately over the lifecycle delete path.
        before_release = await store.soft_delete_conversation(
            org_id=_ORG,
            user_id=_SUBJECT,
            conversation_id="conv_hold",
            now=datetime.now(timezone.utc),
        )
        assert before_release is not None
        assert before_release.deleted_at is None

        stale = client.post(
            f"/v1/retention/legal-holds/{hold_id}/release",
            headers=_headers(idempotency_key="release-stale-001"),
            json={"expected_revision": 2},
        )
        assert stale.status_code == 409

        released = client.post(
            f"/v1/retention/legal-holds/{hold_id}/release",
            headers=_headers(idempotency_key="release-hold-001"),
            json={"expected_revision": 1},
        )
        assert released.status_code == 200, released.text
        assert released.json()["revision"] == 2

        replay = client.post(
            f"/v1/retention/legal-holds/{hold_id}/release",
            headers=_headers(idempotency_key="release-hold-001"),
            json={"expected_revision": 1},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True

        # Idempotency is scoped to the actor as well as the tenant. A second
        # administrator cannot replay the first actor's opaque request key.
        foreign_actor_replay = client.post(
            f"/v1/retention/legal-holds/{hold_id}/release",
            headers=_headers(
                user_id=_OTHER_ADMIN,
                idempotency_key="release-hold-001",
            ),
            json={"expected_revision": 1},
        )
        assert foreign_actor_replay.status_code == 409

        # Releasing only removes the hold; it does not itself erase anything.
        after_release = await store.soft_delete_conversation(
            org_id=_ORG,
            user_id=_SUBJECT,
            conversation_id="conv_hold",
            now=datetime.now(timezone.utc),
        )
        assert after_release is not None
        assert after_release.deleted_at is not None

    async def test_user_history_never_cancels_a_run_on_a_held_conversation(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_conversation(store, conversation_id="conv_held")
        _seed_conversation(store, conversation_id="conv_open")
        hold = LegalHoldRecord(
            id="lh_history_held",
            org_id=_ORG,
            scope=LegalHoldScope.CONVERSATION,
            resource_id="conv_held",
            subject_user_id=_SUBJECT,
            reason_code=LegalHoldReasonCode.LEGAL_REQUEST,
            created_by_user_id=_ADMIN,
            create_idempotency_key="history-hold-create-001",
            create_request_digest=hashlib.sha256(b"history-held").hexdigest(),
        )
        await store.create_legal_hold(
            record=hold,
            audit_event={
                "org_id": _ORG,
                "user_id": _ADMIN,
                "actor_type": "user",
                "action": "legal_hold.created",
                "resource_type": "legal_hold",
                "resource_id": hold.id,
                "outcome": "success",
                "metadata": {"scope": "conversation"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        held_run = self._active_run(run_id="run_held", conversation_id="conv_held")
        open_run = self._active_run(run_id="run_open", conversation_id="conv_open")
        store.runs[held_run.run_id] = held_run
        store.runs[open_run.run_id] = open_run

        result = await store.delete_user_history(org_id=_ORG, user_id=_SUBJECT)

        assert result.runs_cancelled == 1
        assert store.runs[held_run.run_id].status is AgentRunStatus.RUNNING
        assert store.runs[open_run.run_id].status is AgentRunStatus.CANCELLED
        assert store.conversations["conv_held"].deleted_at is None
        assert store.conversations["conv_open"].deleted_at is not None
