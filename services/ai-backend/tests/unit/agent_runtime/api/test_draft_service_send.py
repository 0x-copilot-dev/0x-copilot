"""Unit tests for the PR 1.3.5 ``DraftService.send`` rewrite.

Covers the auth-gate pre-check, host-run resolution, approval-row creation,
APPROVAL_REQUESTED event emission, and audit chain entries.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.draft_service import DraftService
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthOutcome,
)
from agent_runtime.persistence.records import DraftStatus
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ApprovalRequestRecord,
    DraftSendRequest,
)

from tests.unit.agent_runtime.persistence.test_drafts import _draft_id, _record

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _StubAuthGate:
    def __init__(self, outcome: CapabilityAuthOutcome) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    def check(
        self, *, target_connector: str, runtime_context: object
    ) -> CapabilityAuthCheck:
        self.calls.append(target_connector)
        if self.outcome is CapabilityAuthOutcome.AUTHENTICATED:
            return CapabilityAuthCheck(outcome=self.outcome)
        if self.outcome is CapabilityAuthOutcome.NOT_AUTHENTICATED:
            return CapabilityAuthCheck(
                outcome=self.outcome,
                mcp_server_id="srv_acme",
                safe_message="Not authenticated",
            )
        return CapabilityAuthCheck(
            outcome=self.outcome, safe_message="Unknown connector"
        )


class _StubPersistence:
    def __init__(self, *, run_record: object | None = None) -> None:
        self.run_record = run_record
        self.audit_calls: list[tuple[str, dict]] = []
        self.approvals: list[ApprovalRequestRecord] = []

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        return self.run_record

    async def list_messages(
        self, *, org_id: str, conversation_id: str, limit: int
    ) -> tuple[object, ...]:
        return ()

    async def create_approval_request(
        self, *, record: ApprovalRequestRecord
    ) -> ApprovalRequestRecord:
        self.approvals.append(record)
        return record

    async def write_audit_log(self, *, event_type: str, record: dict) -> None:
        self.audit_calls.append((event_type, record))


class _CaptureEventProducer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def append_api_event(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


class _MinimalRun:
    org_id = "org_acme"
    run_id = "run_1"
    conversation_id = "conv_1"
    user_id = "user_sarah"


class TestDraftServiceSend:
    async def test_authenticated_send_creates_approval_and_emits_event(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, run_id="run_existing"))
        gate = _StubAuthGate(CapabilityAuthOutcome.AUTHENTICATED)
        persistence = _StubPersistence(run_record=_MinimalRun())
        producer = _CaptureEventProducer()
        service = DraftService(
            store=store,
            persistence=persistence,
            auth_gate=gate,
            event_producer=producer,
        )

        result = await service.send(
            org_id="org_acme",
            user_id="user_sarah",
            draft_id=_draft_id(),
            request=DraftSendRequest(
                expected_version=1,
                target_connector="slack",
                target_metadata={"channel": "#test"},
            ),
        )

        # Auth gate consulted exactly once with the target.
        assert gate.calls == ["slack"]
        # Draft persisted at v+1 with status=send_pending_approval.
        assert result.draft.version == 2
        assert result.draft.status is DraftStatus.SEND_PENDING_APPROVAL
        assert result.draft.target_connector == "slack"
        assert result.draft.target_metadata == {"channel": "#test"}
        # Real approval id surfaced (not the v1 placeholder).
        assert result.approval_id is not None
        assert not result.approval_id.startswith("draft_send:")
        # Host run id is the draft's existing run_id.
        assert result.run_id == "run_existing"
        # Approval persisted with kind="draft_send" + draft linkage in metadata.
        assert len(persistence.approvals) == 1
        approval = persistence.approvals[0]
        assert approval.metadata.get("kind") == "draft_send"
        assert approval.metadata.get("draft_id") == _draft_id()
        assert approval.metadata.get("draft_version") == 2
        assert approval.metadata.get("target_connector") == "slack"
        # Event emitted on the host run's stream.
        assert len(producer.calls) == 1
        emitted = producer.calls[0]
        assert emitted["event_type"].value == "approval_requested"
        # Audit chain has draft.send.proposed.
        assert any(call[0] == "draft.send.proposed" for call in persistence.audit_calls)

    async def test_not_authenticated_returns_409_no_db_write(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, run_id="run_existing"))
        gate = _StubAuthGate(CapabilityAuthOutcome.NOT_AUTHENTICATED)
        persistence = _StubPersistence(run_record=_MinimalRun())
        producer = _CaptureEventProducer()
        service = DraftService(
            store=store,
            persistence=persistence,
            auth_gate=gate,
            event_producer=producer,
        )

        with pytest.raises(RuntimeApiError) as exc:
            await service.send(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftSendRequest(
                    expected_version=1,
                    target_connector="linear",
                    target_metadata={},
                ),
            )

        assert exc.value.http_status == 409
        assert exc.value.details.get("mcp_server_id") == "srv_acme"
        assert exc.value.details.get("error_code") == "connector_auth_required"
        # No draft mutation, no approval, no event, no audit.
        assert store.versions[("org_acme", _draft_id())][-1].version == 1
        assert persistence.approvals == []
        assert producer.calls == []
        assert persistence.audit_calls == []

    async def test_unknown_capability_returns_400(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, run_id="run_existing"))
        service = DraftService(
            store=store,
            persistence=_StubPersistence(run_record=_MinimalRun()),
            auth_gate=_StubAuthGate(CapabilityAuthOutcome.UNKNOWN_CAPABILITY),
            event_producer=_CaptureEventProducer(),
        )

        with pytest.raises(RuntimeApiError) as exc:
            await service.send(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftSendRequest(
                    expected_version=1,
                    target_connector="ghost",
                    target_metadata={},
                ),
            )

        assert exc.value.http_status == 400
        assert exc.value.details.get("error_code") == "invalid_target_connector"

    async def test_workspace_disabled_returns_403(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, run_id="run_existing"))
        service = DraftService(
            store=store,
            persistence=_StubPersistence(run_record=_MinimalRun()),
            auth_gate=_StubAuthGate(CapabilityAuthOutcome.WORKSPACE_DISABLED),
            event_producer=_CaptureEventProducer(),
        )

        with pytest.raises(RuntimeApiError) as exc:
            await service.send(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftSendRequest(
                    expected_version=1,
                    target_connector="slack",
                    target_metadata={},
                ),
            )

        assert exc.value.http_status == 403
        assert exc.value.details.get("error_code") == "connector_workspace_disabled"

    async def test_no_host_run_returns_409(self) -> None:
        store = InMemoryDraftStore()
        # Draft has no run_id (PATCH-created) and persistence has no messages.
        record = _record(version=1, run_id=None)
        store.insert_version(record)
        service = DraftService(
            store=store,
            persistence=_StubPersistence(run_record=None),
            auth_gate=_StubAuthGate(CapabilityAuthOutcome.AUTHENTICATED),
            event_producer=_CaptureEventProducer(),
        )

        with pytest.raises(RuntimeApiError) as exc:
            await service.send(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftSendRequest(
                    expected_version=1,
                    target_connector="slack",
                    target_metadata={},
                ),
            )

        assert exc.value.http_status == 409
        assert exc.value.details.get("error_code") == "no_host_run"

    async def test_immutable_status_blocks_send(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(
            _record(version=1, run_id="run_existing", status=DraftStatus.SENT)
        )
        service = DraftService(
            store=store,
            persistence=_StubPersistence(run_record=_MinimalRun()),
            auth_gate=_StubAuthGate(CapabilityAuthOutcome.AUTHENTICATED),
            event_producer=_CaptureEventProducer(),
        )

        with pytest.raises(RuntimeApiError) as exc:
            await service.send(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftSendRequest(
                    expected_version=1,
                    target_connector="slack",
                    target_metadata={},
                ),
            )

        assert exc.value.http_status == 409
        assert exc.value.details.get("status") == "sent"
