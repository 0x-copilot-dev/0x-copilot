"""PR 4.4.6.4 — approval undo window + endpoint protocol."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status as http_status

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalRequestRecord,
    UNDO_WINDOW_SECONDS,
)


class _Values:
    ORG_ID = "org_acme"
    USER_ID = "user_sarah"
    OTHER_USER_ID = "user_marcus"
    RUN_ID = "run_undo_window"
    CONVERSATION_ID = "conv_undo"
    APPROVAL_ID = "approval_undo_1"
    USER_MESSAGE_ID = "msg_user"


async def _seed(
    store: InMemoryRuntimeApiStore,
    *,
    reversible: str | None = "yes",
) -> ApprovalRequestRecord:
    """Stand up a run + pending approval flagged ``reversible="yes"``."""

    from agent_runtime.execution.contracts import AgentRuntimeContext
    from runtime_api.schemas import MessageRecord, MessageRole, RunRecord

    await store.append_message(
        MessageRecord(
            message_id=_Values.USER_MESSAGE_ID,
            conversation_id=_Values.CONVERSATION_ID,
            org_id=_Values.ORG_ID,
            role=MessageRole.USER,
            content_text="Post the launch announcement",
        )
    )
    store.runs[_Values.RUN_ID] = RunRecord(
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.USER_ID,
        user_message_id=_Values.USER_MESSAGE_ID,
        trace_id="trace_undo",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_Values.USER_ID,
            org_id=_Values.ORG_ID,
            roles=["employee"],
            run_id=_Values.RUN_ID,
            trace_id="trace_undo",
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
    store.events_by_run.setdefault(_Values.RUN_ID, [])
    metadata: dict[str, object] = {
        "approval_kind": "mcp_tool",
        "tool_name": "post_message",
        "vendor": "SLACK",
    }
    if reversible is not None:
        metadata["reversible"] = reversible
    record = ApprovalRequestRecord(
        approval_id=_Values.APPROVAL_ID,
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.USER_ID,
        metadata=metadata,
    )
    await store.seed_approval_request(record)
    return record


def _make_service(store: InMemoryRuntimeApiStore) -> RuntimeApiService:
    from agent_runtime.api.membership import InMemoryWorkspaceMembershipResolver

    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_PARALLEL_TASKS": "4",
        }
    )
    return RuntimeApiService(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        membership_resolver=InMemoryWorkspaceMembershipResolver(
            {(_Values.ORG_ID, _Values.USER_ID): True}
        ),
    )


async def _approve(service: RuntimeApiService) -> None:
    await service.record_approval_decision(
        org_id=_Values.ORG_ID,
        approval_id=_Values.APPROVAL_ID,
        request=ApprovalDecisionRequest(
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id=_Values.USER_ID,
        ),
    )


class TestDecisionResponseUndoExpiresAt:
    async def test_populated_when_reversible_yes_and_approved(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="yes")
        service = _make_service(store)
        before = datetime.now(timezone.utc)
        response = await service.record_approval_decision(
            org_id=_Values.ORG_ID,
            approval_id=_Values.APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id=_Values.USER_ID,
            ),
        )
        assert response.undo_expires_at is not None
        # Window is decided_at + UNDO_WINDOW_SECONDS, both monotonic.
        delta = response.undo_expires_at - response.decided_at
        assert delta == timedelta(seconds=UNDO_WINDOW_SECONDS)
        assert response.undo_expires_at >= before + timedelta(
            seconds=UNDO_WINDOW_SECONDS - 5
        )

    async def test_omitted_when_reversible_no(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="no")
        service = _make_service(store)
        response = await service.record_approval_decision(
            org_id=_Values.ORG_ID,
            approval_id=_Values.APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id=_Values.USER_ID,
            ),
        )
        assert response.undo_expires_at is None

    async def test_omitted_when_reversible_absent(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible=None)
        service = _make_service(store)
        response = await service.record_approval_decision(
            org_id=_Values.ORG_ID,
            approval_id=_Values.APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id=_Values.USER_ID,
            ),
        )
        assert response.undo_expires_at is None

    async def test_omitted_when_rejected(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="yes")
        service = _make_service(store)
        response = await service.record_approval_decision(
            org_id=_Values.ORG_ID,
            approval_id=_Values.APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.REJECTED,
                decided_by_user_id=_Values.USER_ID,
            ),
        )
        assert response.undo_expires_at is None


class TestRequestApprovalUndo:
    async def test_records_intent_within_window(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="yes")
        service = _make_service(store)
        await _approve(service)
        response = await service.request_approval_undo(
            org_id=_Values.ORG_ID,
            approval_id=_Values.APPROVAL_ID,
            decided_by_user_id=_Values.USER_ID,
        )
        assert response.approval_id == _Values.APPROVAL_ID
        assert response.run_id == _Values.RUN_ID
        # Window is `decided_at + UNDO_WINDOW_SECONDS`.
        assert (response.undo_expires_at - response.undo_requested_at) <= timedelta(
            seconds=UNDO_WINDOW_SECONDS
        )

    def test_404_when_missing(self) -> None:
        store = InMemoryRuntimeApiStore()
        service = _make_service(store)
        with pytest.raises(RuntimeApiError) as excinfo:
            asyncio.run(
                service.request_approval_undo(
                    org_id=_Values.ORG_ID,
                    approval_id="nonexistent",
                    decided_by_user_id=_Values.USER_ID,
                )
            )
        assert excinfo.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_403_when_cross_user(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="yes")
        service = _make_service(store)
        await _approve(service)
        with pytest.raises(RuntimeApiError) as excinfo:
            await service.request_approval_undo(
                org_id=_Values.ORG_ID,
                approval_id=_Values.APPROVAL_ID,
                decided_by_user_id=_Values.OTHER_USER_ID,
            )
        assert excinfo.value.http_status == http_status.HTTP_403_FORBIDDEN

    async def test_422_when_not_reversible(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="no")
        service = _make_service(store)
        await _approve(service)
        with pytest.raises(RuntimeApiError) as excinfo:
            await service.request_approval_undo(
                org_id=_Values.ORG_ID,
                approval_id=_Values.APPROVAL_ID,
                decided_by_user_id=_Values.USER_ID,
            )
        assert excinfo.value.http_status == http_status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_422_when_no_decision_yet(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="yes")
        service = _make_service(store)
        # No call to record_approval_decision; status remains PENDING.
        with pytest.raises(RuntimeApiError) as excinfo:
            await service.request_approval_undo(
                org_id=_Values.ORG_ID,
                approval_id=_Values.APPROVAL_ID,
                decided_by_user_id=_Values.USER_ID,
            )
        assert excinfo.value.http_status == http_status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_410_when_window_expired(self) -> None:
        store = InMemoryRuntimeApiStore()
        record = await _seed(store, reversible="yes")
        service = _make_service(store)
        await _approve(service)
        # Mutate the persisted ``decided_at`` to the past so the window
        # has already elapsed by the time we ask for an undo.
        approval_now = store.approval_requests[_Values.APPROVAL_ID]
        long_ago = (
            datetime.now(timezone.utc) - timedelta(seconds=UNDO_WINDOW_SECONDS + 5)
        ).isoformat()
        store.approval_requests[_Values.APPROVAL_ID] = approval_now.model_copy(
            update={
                "metadata": {
                    **approval_now.metadata,
                    "decided_at": long_ago,
                }
            }
        )
        del record  # silence linter; the seeded record is mutated above.
        with pytest.raises(RuntimeApiError) as excinfo:
            await service.request_approval_undo(
                org_id=_Values.ORG_ID,
                approval_id=_Values.APPROVAL_ID,
                decided_by_user_id=_Values.USER_ID,
            )
        assert excinfo.value.http_status == http_status.HTTP_410_GONE

    async def test_writes_audit_row(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed(store, reversible="yes")
        service = _make_service(store)
        await _approve(service)
        await service.request_approval_undo(
            org_id=_Values.ORG_ID,
            approval_id=_Values.APPROVAL_ID,
            decided_by_user_id=_Values.USER_ID,
        )
        kinds = [event_type for event_type, _record in store.audit_log]
        assert "approval_undo_requested" in kinds
