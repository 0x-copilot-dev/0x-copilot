"""End-to-end draft flow integration test (PR 1.3.5).

Composes the four seams PR 1.3.5 wires together:

* :class:`DraftBackend` — the agent's deepagents-side write path.
* :class:`DraftService` — the API-side send + auth-gate pre-check.
* :class:`RuntimeApprovalHandler` — the worker-side resolution branch
  that flips the draft to ``sent`` (or ``draft`` on reject) and audits.
* :class:`InMemoryDraftStore` — the persistence boundary.

Three scenarios mirror the PRD's AC-2 / AC-4 / AC-9:

1. ``test_draft_flow`` — agent ``write_file`` lands a versioned record +
   emits a ``DRAFT_UPDATED`` event projection; the API list returns it.
2. ``test_draft_send`` — POST send → APPROVAL_REQUESTED → handler approves
   → ``status=sent`` + ``draft.send.completed`` audit + RUN_COMPLETED.
3. ``test_draft_send_unauth`` — non-authenticated connector returns 409
   with a ``mcp_server_id`` hint; no DB writes; once authenticated, the
   retry succeeds.

Stubs the bits that don't belong in the seam test (Postgres, FastAPI,
LangGraph) — the in-memory store is the persistence boundary so the
contract under test is the seam, not the database.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.api.draft_service import DraftService
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthGate,
    CapabilityAuthOutcome,
)
from agent_runtime.capabilities.backends.draft_backend import DraftBackend
from agent_runtime.persistence.records import DraftPath, DraftRecord, DraftStatus
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    DraftSendRequest,
    RuntimeApiEventType,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_ORG = "org_acme"
_CONV = "conv_launch"
_HOST_RUN = "run_host"
_USER = "user_sarah"
_DRAFT_ID = "abcdef0123456789abcdef0123456789"
_DRAFT_PATH = DraftPath.for_draft_id(_DRAFT_ID)


class _ConfigurableAuthGate(CapabilityAuthGate):
    """Auth gate that flips between AUTHENTICATED / NOT_AUTHENTICATED at will.

    Subclasses :class:`CapabilityAuthGate` so the service's static type
    contract stays satisfied; ``check`` is overridden to ignore the real
    capability registry.
    """

    def __init__(self, outcome: CapabilityAuthOutcome) -> None:
        # Skip the parent constructor — we never consult the real registry.
        self.outcome = outcome
        self.calls: list[str] = []

    async def check(  # type: ignore[override]
        self, *, target_connector: str, runtime_context: object
    ) -> CapabilityAuthCheck:
        self.calls.append(target_connector)
        if self.outcome is CapabilityAuthOutcome.AUTHENTICATED:
            return CapabilityAuthCheck(outcome=self.outcome)
        if self.outcome is CapabilityAuthOutcome.NOT_AUTHENTICATED:
            return CapabilityAuthCheck(
                outcome=self.outcome,
                mcp_server_id="srv_slack",
                safe_message="Not authenticated",
            )
        return CapabilityAuthCheck(
            outcome=self.outcome, safe_message="Unknown connector"
        )


class _RecordingPersistence:
    """Minimal persistence the service + worker handler probe via duck typing."""

    def __init__(self) -> None:
        self.audit_calls: list[tuple[str, dict]] = []
        self.approvals: list[ApprovalRequestRecord] = []
        self.run_status_updates: list[AgentRunStatus] = []

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        return _RUN_RECORD

    async def list_messages(
        self, *, org_id: str, conversation_id: str, limit: int
    ) -> tuple[object, ...]:
        return ()

    async def create_approval_request(
        self, *, record: ApprovalRequestRecord
    ) -> ApprovalRequestRecord:
        self.approvals.append(record)
        return record

    # Async — matches the unified async PersistencePort surface.
    async def write_audit_log(self, *, event_type: str, record: dict) -> None:
        self.audit_calls.append((event_type, record))

    async def update_run_status(self, *, run_id: str, status: AgentRunStatus) -> object:
        self.run_status_updates.append(status)
        return _RUN_RECORD


class _RecordingEventProducer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def append_api_event(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


# Duck-typed RunRecord; the service + handler only read these four fields.
_RUN_RECORD = SimpleNamespace(
    run_id=_HOST_RUN,
    conversation_id=_CONV,
    org_id=_ORG,
    user_id=_USER,
    status=AgentRunStatus.WAITING_FOR_APPROVAL,
)


def _draft_backend(
    store: InMemoryDraftStore, *, captured: list[DraftRecord]
) -> DraftBackend:
    async def _emit(record: DraftRecord) -> None:
        captured.append(record)

    return DraftBackend(
        store=store,
        org_id=_ORG,
        conversation_id=_CONV,
        run_id=_HOST_RUN,
        user_id=_USER,
        emit_event=_emit,
    )


def _service(
    *,
    store: InMemoryDraftStore,
    auth_outcome: CapabilityAuthOutcome,
    persistence: _RecordingPersistence,
    producer: _RecordingEventProducer,
) -> tuple[DraftService, _ConfigurableAuthGate]:
    gate = _ConfigurableAuthGate(auth_outcome)
    service = DraftService(
        store=store,
        persistence=persistence,
        auth_gate=gate,
        event_producer=producer,
    )
    return service, gate


def _approval_handler(
    store: InMemoryDraftStore, persistence: _RecordingPersistence
) -> RuntimeApprovalHandler:
    handler = RuntimeApprovalHandler(
        persistence=persistence,
        event_store=SimpleNamespace(events=[]),
        draft_store=store,
    )
    return handler


class TestDraftFlow:
    """AC-2 — agent's ``write_file`` lands a draft + emits DRAFT_UPDATED."""

    async def test_agent_write_persists_and_emits_event(self) -> None:
        store = InMemoryDraftStore()
        captured: list[DraftRecord] = []
        backend = _draft_backend(store, captured=captured)

        result = await backend.awrite(
            _DRAFT_PATH, "# Aurora 4.0\n\nLaunch announcement body."
        )

        assert result.error is None
        # One version persisted; tenant identity bound from the run handler.
        latest = await store.latest(org_id=_ORG, draft_id=_DRAFT_ID)
        assert latest is not None
        assert latest.version == 1
        assert latest.status is DraftStatus.DRAFT
        assert latest.org_id == _ORG and latest.conversation_id == _CONV
        # The emit_event callback fired exactly once with the same record.
        assert len(captured) == 1
        assert captured[0].draft_id == _DRAFT_ID
        assert captured[0].version == 1

    async def test_agent_edit_appends_a_new_version(self) -> None:
        store = InMemoryDraftStore()
        captured: list[DraftRecord] = []
        backend = _draft_backend(store, captured=captured)

        first = await backend.awrite(_DRAFT_PATH, "# v1 body\n")
        assert first.error is None
        edit = await backend.aedit(_DRAFT_PATH, "v1 body", "v2 body — refined")
        assert edit.error is None

        latest = await store.latest(org_id=_ORG, draft_id=_DRAFT_ID)
        assert latest is not None
        assert latest.version == 2
        # Both writes streamed an event projection.
        versions = [record.version for record in captured]
        assert versions == [1, 2]


class TestDraftSend:
    """AC-4 — POST send → APPROVAL_REQUESTED → approve → status=sent."""

    async def test_send_then_approve_transitions_to_sent(self) -> None:
        store = InMemoryDraftStore()
        # Seed a v1 draft via the agent path — that's how production gets
        # there too. ``run_id`` matches ``_HOST_RUN`` so the API resolves
        # the host run from the draft itself.
        backend = _draft_backend(store, captured=[])
        await backend.awrite(_DRAFT_PATH, "# Aurora 4.0\n\nLaunch body.")

        persistence = _RecordingPersistence()
        producer = _RecordingEventProducer()
        service, gate = _service(
            store=store,
            auth_outcome=CapabilityAuthOutcome.AUTHENTICATED,
            persistence=persistence,
            producer=producer,
        )

        send_response = await service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=_DRAFT_ID,
            request=DraftSendRequest(
                expected_version=1,
                target_connector="slack",
                target_metadata={"channel": "#launch-aurora"},
            ),
        )

        # Auth gate consulted exactly once.
        assert gate.calls == ["slack"]
        # v2 persisted as send_pending_approval; approval row created;
        # APPROVAL_REQUESTED event on the host run; audit chain has the
        # ``draft.send.proposed`` entry.
        assert send_response.draft.version == 2
        assert send_response.draft.status is DraftStatus.SEND_PENDING_APPROVAL
        assert send_response.run_id == _HOST_RUN
        assert send_response.approval_id is not None
        assert len(persistence.approvals) == 1
        approval = persistence.approvals[0]
        assert approval.metadata["kind"] == "draft_send"
        assert approval.metadata["draft_id"] == _DRAFT_ID
        assert any(call[0] == "draft.send.proposed" for call in persistence.audit_calls)
        assert producer.calls
        assert (
            producer.calls[-1]["event_type"] is RuntimeApiEventType.APPROVAL_REQUESTED
        )

        # Worker resolves the approval as APPROVED — flips to SENT v3 +
        # writes the completion audit + completes the host run.
        handler = _approval_handler(store, persistence)
        captured_handler_events: list[dict] = []

        async def _capture(**kwargs: object) -> None:
            captured_handler_events.append(dict(kwargs))

        handler.event_producer.append_api_event = _capture  # type: ignore[assignment]

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=approval,
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_marcus",
        )

        latest = await store.latest(org_id=_ORG, draft_id=_DRAFT_ID)
        assert latest is not None
        assert latest.version == 3
        assert latest.status is DraftStatus.SENT
        assert any(
            call[0] == "draft.send.completed" for call in persistence.audit_calls
        )
        emitted_types = {call["event_type"] for call in captured_handler_events}
        assert RuntimeApiEventType.DRAFT_UPDATED in emitted_types
        assert RuntimeApiEventType.RUN_COMPLETED in emitted_types
        assert AgentRunStatus.COMPLETED in persistence.run_status_updates

    async def test_reject_reverts_to_draft(self) -> None:
        store = InMemoryDraftStore()
        backend = _draft_backend(store, captured=[])
        await backend.awrite(_DRAFT_PATH, "# Body\n")

        persistence = _RecordingPersistence()
        producer = _RecordingEventProducer()
        service, _gate = _service(
            store=store,
            auth_outcome=CapabilityAuthOutcome.AUTHENTICATED,
            persistence=persistence,
            producer=producer,
        )
        await service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=_DRAFT_ID,
            request=DraftSendRequest(
                expected_version=1,
                target_connector="slack",
                target_metadata={},
            ),
        )

        handler = _approval_handler(store, persistence)

        async def _noop(**_: object) -> None:
            return None

        handler.event_producer.append_api_event = _noop  # type: ignore[assignment]

        await handler._resolve_draft_send_approval(
            run=_RUN_RECORD,
            approval=persistence.approvals[0],
            decision=ApprovalDecision.REJECTED,
            decided_by_user_id="user_marcus",
        )

        latest = await store.latest(org_id=_ORG, draft_id=_DRAFT_ID)
        assert latest is not None
        assert latest.version == 3
        assert latest.status is DraftStatus.DRAFT
        assert any(call[0] == "draft.send.rejected" for call in persistence.audit_calls)


class TestDraftSendUnauth:
    """AC-9 — unauth send returns 409 with mcp_server_id; retry after auth."""

    async def test_unauth_returns_409_then_authenticated_retry_succeeds(
        self,
    ) -> None:
        store = InMemoryDraftStore()
        backend = _draft_backend(store, captured=[])
        await backend.awrite(_DRAFT_PATH, "# Body\n")

        persistence = _RecordingPersistence()
        producer = _RecordingEventProducer()
        service, gate = _service(
            store=store,
            auth_outcome=CapabilityAuthOutcome.NOT_AUTHENTICATED,
            persistence=persistence,
            producer=producer,
        )

        with pytest.raises(RuntimeApiError) as exc:
            await service.send(
                org_id=_ORG,
                user_id=_USER,
                draft_id=_DRAFT_ID,
                request=DraftSendRequest(
                    expected_version=1,
                    target_connector="slack",
                    target_metadata={},
                ),
            )

        assert exc.value.http_status == 409
        assert exc.value.details.get("error_code") == "connector_auth_required"
        assert exc.value.details.get("mcp_server_id") == "srv_slack"

        # No DB write; no approval row; no event; no audit. Draft stays at v1.
        latest_before = await store.latest(org_id=_ORG, draft_id=_DRAFT_ID)
        assert latest_before is not None
        assert latest_before.version == 1
        assert persistence.approvals == []
        assert persistence.audit_calls == []
        assert producer.calls == []

        # User authenticates the connector → gate flips → retry succeeds.
        gate.outcome = CapabilityAuthOutcome.AUTHENTICATED

        retry = await service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=_DRAFT_ID,
            request=DraftSendRequest(
                expected_version=1,
                target_connector="slack",
                target_metadata={},
            ),
        )

        assert retry.draft.version == 2
        assert retry.draft.status is DraftStatus.SEND_PENDING_APPROVAL
        assert len(persistence.approvals) == 1
        assert any(call[0] == "draft.send.proposed" for call in persistence.audit_calls)
