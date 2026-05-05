"""Unit tests for the non-blocking MCP discovery service (PR 3.3).

Mirrors :mod:`tests.unit.agent_runtime.capabilities.test_citations` —
service is the only seam between the model-facing tool and the wire,
so these tests cover the contract end-to-end against an in-memory
registry, fake auth-session creator, recording event producer, and a
spy audit emitter.

Coverage:

  * ``suggest_mcp_connector`` emits exactly one ``mcp_auth_required``
    event with ``discovery_reason`` set, audits via the existing chain.
  * Idempotent on ``(run_id, server_id)``; second call returns
    ``already_suggested`` with the same approval_id and emits no
    further event.
  * Already-authenticated server short-circuits with no event + no audit.
  * Disabled server short-circuits with no event but DOES audit (denied).
  * Unknown server short-circuits with no event + no audit (no resource).
  * The ``offer`` classmethod resolves the active service via the
    ContextVar and returns ``discovery_disabled`` when no service is
    bound — so the tool degrades gracefully without breaking runs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from agent_runtime.api.constants import Keys, Messages
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.mcp_discovery_service import McpDiscoveryService
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSession
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------


class _RecordingPersistence:
    """Captures the worker's ``set_run_latest_sequence`` call."""

    def __init__(self) -> None:
        self.latest_sequence_no: int | None = None
        self.audit_records: list[tuple[str, dict[str, Any]]] = []

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        self.latest_sequence_no = latest_sequence_no

    async def write_audit_log(self, *, event_type: str, record: dict[str, Any]) -> None:
        self.audit_records.append((event_type, dict(record)))


class _RecordingEventStore:
    """Stand-in event store that records every draft."""

    def __init__(self) -> None:
        self.drafts: list[RuntimeEventDraft] = []

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        self.drafts.append(event)
        return RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=len(self.drafts),
            source=event.source,
            event_type=event.event_type,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_task_id=event.parent_task_id,
            task_id=event.task_id,
            subagent_id=event.subagent_id,
            display_title=event.display_title,
            summary=event.summary,
            status=event.status,
            activity_kind=event.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=event.event_type,
                source=event.source,
            ),
            visibility=event.visibility,
            redaction_state=event.redaction_state,
            presentation=event.presentation,
            payload=event.payload,
            metadata=event.metadata,
        )


class _FakeAuthSessionCreator:
    """Returns a deterministic auth session — no network."""

    def create_auth_session(
        self, *, server_id: str, runtime_context: AgentRuntimeContext
    ) -> McpAuthSession:
        return McpAuthSession(
            server_id=server_id,
            server_name=server_id,
            display_name=server_id.title(),
            auth_url=f"https://example.com/oauth/{server_id}",
            expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


class _StubRegistry:
    """Implements ``list_available_servers`` over a fixed catalog."""

    def __init__(self, cards: list[McpServerCard]) -> None:
        self._cards = cards

    def list_available_servers(
        self, context: AgentRuntimeContext
    ) -> tuple[McpServerCard, ...]:
        return tuple(self._cards)


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_disc",
        org_id="org_disc",
        roles=["employee"],
        model_profile={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "max_input_tokens": 128_000,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
        run_id="run_disc",
        trace_id="trace_disc",
    )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run_disc",
        conversation_id="conv_disc",
        org_id="org_disc",
        user_id="user_disc",
        user_message_id="msg_disc",
        trace_id="trace_disc",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=_runtime_context(),
    )


def _card(
    *,
    name: str = "linear",
    server_id: str | None = "linear",
    enabled: bool = True,
    auth_state: McpAuthState = McpAuthState.UNAUTHENTICATED,
) -> McpServerCard:
    return McpServerCard(
        name=name,
        server_id=server_id,
        display_name=name.title(),
        short_description="Linear ticket system",
        transport=McpTransport.STREAMABLE_HTTP
        if hasattr(McpTransport, "STREAMABLE_HTTP")
        else next(iter(McpTransport)),
        auth_mode=McpAuthMode.OAUTH2,
        auth_state=auth_state,
        health=McpServerHealth.HEALTHY,
        load_cost=1,
        enabled=enabled,
    )


class DiscoveryFixtureMixin:
    """Build a service instance backed by the recording fakes."""

    def _build(
        self,
        *,
        cards: list[McpServerCard] | None = None,
        with_auth_creator: bool = True,
    ) -> tuple[
        McpDiscoveryService,
        _RecordingEventStore,
        _RecordingPersistence,
    ]:
        from runtime_worker.audit import WorkerAuditEmitter

        cards = cards if cards is not None else [_card()]
        events = _RecordingEventStore()
        persistence = _RecordingPersistence()
        producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=events,
        )
        audit = WorkerAuditEmitter(persistence=persistence)
        service = McpDiscoveryService(
            run=_run_record(),
            runtime_context=_runtime_context(),
            producer=producer,
            audit_emitter=audit,
            registry=_StubRegistry(cards),
            auth_session_creator=_FakeAuthSessionCreator()
            if with_auth_creator
            else None,
        )
        return service, events, persistence


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSuggestEmits(DiscoveryFixtureMixin):
    def test_emits_event_with_discovery_payload(self) -> None:
        service, events, persistence = self._build()

        result = asyncio.run(
            service.suggest(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims about progress",
            )
        )

        assert result["status"] == "emitted"
        assert result["server_id"] == "linear"
        assert result[Keys.Field.APPROVAL_ID].startswith("mcp_discovery:run_disc:")

        # Exactly one wire event, type mcp_auth_required, payload carries
        # discovery_reason + expected_value (the two new fields).
        assert len(events.drafts) == 1
        draft = events.drafts[0]
        assert draft.event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED
        assert draft.payload[Keys.Field.DISCOVERY_REASON] == "fetch ticket statuses"
        assert (
            draft.payload[Keys.Field.EXPECTED_VALUE] == "ground claims about progress"
        )
        # Standard fields the FE already expects on this event type.
        assert draft.payload[Keys.Field.SERVER_ID] == "linear"
        assert draft.payload["display_name"] == "Linear"
        assert draft.payload[Keys.Field.AUTH_URL].startswith("https://")

        # One audit row with action=mcp.discovery.suggested.
        assert len(persistence.audit_records) == 1
        action, record = persistence.audit_records[0]
        assert action == Messages.Audit.MCP_DISCOVERY_SUGGESTED
        assert record["resource_id"] == "linear"
        assert record["outcome"] == "success"


class TestSuggestIdempotent(DiscoveryFixtureMixin):
    def test_second_call_for_same_server_no_ops(self) -> None:
        service, events, persistence = self._build()

        first = asyncio.run(
            service.suggest(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims",
            )
        )
        second = asyncio.run(
            service.suggest(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims",
            )
        )

        assert first["status"] == "emitted"
        assert second["status"] == "already_suggested"
        # Same approval_id so the FE reducer dedupes the card.
        assert first[Keys.Field.APPROVAL_ID] == second[Keys.Field.APPROVAL_ID]
        # Exactly one event + one audit row across both calls.
        assert len(events.drafts) == 1
        assert len(persistence.audit_records) == 1


class TestSuggestAlreadyAuthenticated(DiscoveryFixtureMixin):
    def test_short_circuits_with_no_event_no_audit(self) -> None:
        service, events, persistence = self._build(
            cards=[_card(auth_state=McpAuthState.AUTHENTICATED)]
        )

        result = asyncio.run(
            service.suggest(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims",
            )
        )

        assert result["status"] == "already_authenticated"
        assert len(events.drafts) == 0
        # No audit row — no resource was touched.
        assert len(persistence.audit_records) == 0


class TestSuggestServerDisabled(DiscoveryFixtureMixin):
    def test_short_circuits_no_event_but_audits_denied(self) -> None:
        service, events, persistence = self._build(cards=[_card(enabled=False)])

        result = asyncio.run(
            service.suggest(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims",
            )
        )

        assert result["status"] == "server_disabled"
        assert len(events.drafts) == 0
        # SIEM still sees the attempt — outcome=denied keeps the chain
        # consistent with PR 1.4 forwarded events.
        assert len(persistence.audit_records) == 1
        action, record = persistence.audit_records[0]
        assert action == Messages.Audit.MCP_DISCOVERY_SUGGESTED
        assert record["outcome"] == "denied"


class TestSuggestUnknownServer(DiscoveryFixtureMixin):
    def test_unknown_server_returns_status_no_event_no_audit(self) -> None:
        service, events, persistence = self._build(cards=[_card(name="linear")])

        result = asyncio.run(
            service.suggest(
                server_id="zendesk",
                reason="fetch tickets",
                expected_value="ground claims",
            )
        )

        assert result["status"] == "unknown_server"
        assert len(events.drafts) == 0
        assert len(persistence.audit_records) == 0


class TestOfferContextVar(DiscoveryFixtureMixin):
    def test_offer_returns_discovery_disabled_when_no_service_bound(self) -> None:
        result = asyncio.run(
            McpDiscoveryService.offer(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims",
            )
        )
        assert result == {
            "status": "discovery_disabled",
            "server_id": "linear",
        }

    def test_offer_routes_through_active_bound_service(self) -> None:
        service, events, _ = self._build()
        token = McpDiscoveryService.bind_for_run(service)
        try:
            result = asyncio.run(
                McpDiscoveryService.offer(
                    server_id="linear",
                    reason="fetch ticket statuses",
                    expected_value="ground claims",
                )
            )
        finally:
            McpDiscoveryService.unbind(token)

        assert result["status"] == "emitted"
        assert len(events.drafts) == 1


class TestSuggestNoAuthSessionCreator(DiscoveryFixtureMixin):
    def test_emits_with_empty_auth_url_when_no_oauth_provider(self) -> None:
        # Some MCP providers (api-key only, none auth, etc.) don't expose
        # a ``create_auth_session`` callable. Discovery still works — the
        # FE Connect path falls back to ``connectors.authenticate`` which
        # kicks off OAuth via its own URL.
        service, events, _ = self._build(with_auth_creator=False)

        result = asyncio.run(
            service.suggest(
                server_id="linear",
                reason="fetch ticket statuses",
                expected_value="ground claims",
            )
        )

        assert result["status"] == "emitted"
        assert len(events.drafts) == 1
        # NB. the projector strips empty strings from the wire payload
        # (``_text`` returns None for "") so absent keys are correct.
        payload = events.drafts[0].payload
        assert Keys.Field.AUTH_URL not in payload
        assert Keys.Field.EXPIRES_AT not in payload
        # Discovery fields still present.
        assert payload[Keys.Field.DISCOVERY_REASON] == "fetch ticket statuses"
