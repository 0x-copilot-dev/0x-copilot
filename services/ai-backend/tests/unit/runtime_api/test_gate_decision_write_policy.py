"""PRD-C2 — gate write-policy on the approval decision endpoint.

Pins the fail-closed axis: ``write_policy`` is allowed only on an mcp_auth
approve; it is persisted (PRD-C1 connectors storage) BEFORE the decision is
recorded so a persist failure 502s with the decision untouched; and a resolved
gate emits ``gate.resolved`` with the outcome + persisted policy.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalRequestRecord,
    MessageRecord,
    MessageRole,
    RunRecord,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_gate"
_CONV = "conv_gate"
_APPROVAL = "mcp_auth:run_gate:seed:linear"


class _RecordingPolicyClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    async def put_override(
        self, *, org_id: str, user_id: str, connector_slug: str, write_policy: str
    ) -> None:
        if self._fail:
            raise RuntimeError("backend 500")
        self.calls.append(
            {
                "org_id": org_id,
                "user_id": user_id,
                "connector_slug": connector_slug,
                "write_policy": write_policy,
            }
        )


async def _seed_run(store: InMemoryRuntimeApiStore) -> None:
    await store.append_message(
        MessageRecord(
            message_id="msg_user",
            conversation_id=_CONV,
            org_id=_ORG,
            role=MessageRole.USER,
            content_text="use linear",
        )
    )
    store.runs[_RUN] = RunRecord(
        run_id=_RUN,
        conversation_id=_CONV,
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_user",
        trace_id="trace_gate",
        model_provider="openai",
        model_name="gpt-4o-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_gate",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "max_input_tokens": 4096,
                "timeout_seconds": 30,
                "temperature": 0,
            },
        ),
    )
    store.events_by_run.setdefault(_RUN, [])


async def _seed_approval(
    store: InMemoryRuntimeApiStore, *, kind: str = "mcp_auth"
) -> None:
    record = ApprovalRequestRecord(
        approval_id=_APPROVAL,
        run_id=_RUN,
        conversation_id=_CONV,
        org_id=_ORG,
        user_id=_USER,
        metadata={
            "approval_kind": kind,
            "native_interrupt_id": _APPROVAL,
            "server_id": "seed:linear",
            "server_name": "linear",
        },
    )
    await store.seed_approval_request(record)


def _coordinator(
    store: InMemoryRuntimeApiStore, *, policy_client: object | None
) -> ApprovalCoordinator:
    return ApprovalCoordinator(
        persistence=store,
        queue=store,
        event_producer=RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        ),
        connector_policy_client=policy_client,  # type: ignore[arg-type]
    )


def _gate_resolved_events(store: InMemoryRuntimeApiStore) -> list[dict]:
    return [
        evt
        for evt in store.events_by_run.get(_RUN, [])
        if getattr(evt, "event_type", None) == "gate.resolved"
        or (isinstance(evt, dict) and evt.get("event_type") == "gate.resolved")
    ]


# --------------------------------------------------------------------------- #
# request validator (schema-level 422)
# --------------------------------------------------------------------------- #


class TestRequestValidator:
    def test_write_policy_requires_approved_decision_422(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(
                decision=ApprovalDecision.REJECTED,
                decided_by_user_id=_USER,
                write_policy="ask_first",
            )

    def test_write_policy_allowed_on_approve(self) -> None:
        req = ApprovalDecisionRequest(
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id=_USER,
            write_policy="allow_always",
        )
        assert req.write_policy == "allow_always"


# --------------------------------------------------------------------------- #
# coordinator-side guards + persistence + gate.resolved
# --------------------------------------------------------------------------- #


class TestCoordinatorGatePolicy:
    async def test_write_policy_on_non_mcp_auth_kind_422(self, monkeypatch) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store, kind="action")
        client = _RecordingPolicyClient()
        coordinator = _coordinator(store, policy_client=client)
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.record_approval_decision(
                org_id=_ORG,
                approval_id=_APPROVAL,
                request=ApprovalDecisionRequest(
                    decision=ApprovalDecision.APPROVED,
                    decided_by_user_id=_USER,
                    write_policy="ask_first",
                ),
            )
        assert exc.value.http_status == 422
        assert client.calls == []

    async def test_override_persisted_before_decision_and_gate_resolved_ordering(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store)
        client = _RecordingPolicyClient()
        coordinator = _coordinator(store, policy_client=client)
        await coordinator.record_approval_decision(
            org_id=_ORG,
            approval_id=_APPROVAL,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id=_USER,
                write_policy="allow_always",
            ),
        )
        # Override persisted with the connector slug + policy.
        assert client.calls == [
            {
                "org_id": _ORG,
                "user_id": _USER,
                "connector_slug": "linear",
                "write_policy": "allow_always",
            }
        ]
        # Decision recorded.
        assert _APPROVAL in store.approval_decisions
        # gate.resolved emitted with outcome + policy.
        resolved = _gate_resolved_events(store)
        assert len(resolved) == 1
        payload = resolved[0].payload  # type: ignore[union-attr]
        assert payload["outcome"] == "connected"
        assert payload["write_policy"] == "allow_always"

    async def test_policy_persist_failure_502_decision_not_recorded(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store)
        client = _RecordingPolicyClient(fail=True)
        coordinator = _coordinator(store, policy_client=client)
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.record_approval_decision(
                org_id=_ORG,
                approval_id=_APPROVAL,
                request=ApprovalDecisionRequest(
                    decision=ApprovalDecision.APPROVED,
                    decided_by_user_id=_USER,
                    write_policy="ask_first",
                ),
            )
        assert exc.value.http_status == 502
        # Fail closed: the decision was never recorded.
        assert _APPROVAL not in store.approval_decisions

    async def test_gate_resolved_cancelled_on_reject(self, monkeypatch) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store)
        coordinator = _coordinator(store, policy_client=_RecordingPolicyClient())
        await coordinator.record_approval_decision(
            org_id=_ORG,
            approval_id=_APPROVAL,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.REJECTED,
                decided_by_user_id=_USER,
            ),
        )
        resolved = _gate_resolved_events(store)
        assert len(resolved) == 1
        payload = resolved[0].payload  # type: ignore[union-attr]
        assert payload["outcome"] == "cancelled"
        assert "write_policy" not in payload

    async def test_flag_off_write_policy_rejected_422(self, monkeypatch) -> None:
        monkeypatch.delenv("SURFACES_V2", raising=False)
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        await _seed_approval(store)
        client = _RecordingPolicyClient()
        coordinator = _coordinator(store, policy_client=client)
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.record_approval_decision(
                org_id=_ORG,
                approval_id=_APPROVAL,
                request=ApprovalDecisionRequest(
                    decision=ApprovalDecision.APPROVED,
                    decided_by_user_id=_USER,
                    write_policy="ask_first",
                ),
            )
        assert exc.value.http_status == 422
        assert client.calls == []
