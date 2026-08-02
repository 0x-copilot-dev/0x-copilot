"""Hermetic real-graph proof of the P1b MCP tool-policy fixes.

Two production bugs, one wiring, driven through the REAL worker + REAL Deep
Agents graph + REAL streaming executor + REAL approval coordinator — only the
chat model is the env-gated deterministic fake, and the ``linear`` MCP server is
an in-memory fake injected into the worker's registry:

* **T1 — trusted READ auto-runs.** ``get_issues`` (absent from the catalog, but
  the connector is authenticated and advertises ``readOnlyHint:true``) used to
  be classified fail-closed and staged as a "PROPOSED CHANGE". The PDP derives
  READ+TRUSTED and ALLOWs it: it dispatches now, returns a real result, and
  raises no approval.

* **T2 — WRITE parks, approve executes in the SAME run.** ``create_issue`` (a
  catalog write) GATEs. The old fire-and-return staging returned a normal tool
  result, the run completed + sealed its ledger, and the approved effect could
  never append (the hang). Now the run parks on an interrupt BEFORE any external
  change; approving it resumes the same run and the write executes exactly once.

The MCP registry is injected by patching ``DefaultRuntimeDependenciesFactory._mcp_registry``
(the single seam every worker-built dependency graph goes through), so the run
and approval handlers the worker builds internally both see the fake connector.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.membership import InMemoryWorkspaceMembershipResolver
from agent_runtime.api.notifications import LoggingNotificationDispatcher
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.execution.fake_model import FakeModelProvider
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRequest,
    CreateConversationRequest,
    CreateRunRequest,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

_ORG_ID = "org_123"
_USER_ID = "user_123"
_SERVER = "linear"
# The four capabilities the model-facing MCP operation gateway requires to
# compose for an enforced run (mirrors ``test_mcp_operation_composition``).
_GATEWAY_CAPABILITIES = (
    "operation_gateway",
    "effect_stager",
    "effect_commit",
    "mcp_gateway",
)


@dataclass
class _RecordingFakeClient:
    """MCP client that records every dispatched ``call_tool``."""

    tool_outputs: Mapping[str, Mapping[str, object]]
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[object]:
        return ()

    async def list_resources(self) -> Sequence[object]:
        return ()

    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.calls.append((tool_name, dict(arguments)))
        return self.tool_outputs.get(tool_name, {"ok": True})


@dataclass
class _LinearMcpProvider:
    """OAuth-capable fake provider for a single authenticated ``linear`` server.

    ``create_auth_session`` exists (never called by the write gate) purely so the
    factory's OAuth probe builds a ``ToolAccessGate`` — the object the write GATE
    parks on. ``list_server_cards`` registers the ``get_issues`` read-only
    annotation into the run's per-run registry so the descriptor source derives
    READ for it (the connector publishes no catalog entry for it).
    """

    card: McpServerCard
    client: _RecordingFakeClient
    created_clients: list[str] = field(default_factory=list)

    async def list_server_cards(self) -> Sequence[McpServerCard]:
        McpToolAnnotationsRegistry.register(
            self.card.name, "get_issues", McpToolAnnotations(read_only_hint=True)
        )
        return (self.card,)

    def create_client(self, card: McpServerCard) -> _RecordingFakeClient:
        self.created_clients.append(card.name)
        return self.client

    async def create_auth_session(
        self, **_kwargs: object
    ) -> object:  # pragma: no cover
        raise AssertionError("the write-approval gate never opens an auth session")


@dataclass
class _LocalMcpProvider:
    """Non-OAuth fake provider (stdio / local): it has NO ``create_auth_session``.

    Models the Manage-MCP-config direction — a local/stdio MCP server the
    factory's OAuth probe (``_auth_session_creator``) finds no session factory
    for, so the run's ``ToolAccessGate`` is built with ``auth_session_creator=None``.
    The ABSENCE of ``create_auth_session`` is the whole point: it is what made
    ``_tool_access_gate`` return ``gate=None`` before the fix, so every write was
    refused ("approval not available") instead of parked.
    """

    card: McpServerCard
    client: _RecordingFakeClient
    created_clients: list[str] = field(default_factory=list)

    async def list_server_cards(self) -> Sequence[McpServerCard]:
        return (self.card,)

    def create_client(self, card: McpServerCard) -> _RecordingFakeClient:
        self.created_clients.append(card.name)
        return self.client


class LinearMcpRunMixin:
    """Drive a real queued run whose one turn calls a fake ``linear`` MCP tool."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        # Compose the model-facing MCP operation gateway for this run: only then
        # is ``call_mcp_tool`` a delegated tool (the name-keyed HITL enforcer is
        # OFF for it) and the PDP owns the decision. Enroll org_123/user_123 in
        # every gateway capability cohort.
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
                "SURFACES_V2": "true",
                "OPERATION_GATEWAY_MODE": OperationGatewayMode.ENFORCE.value,
                "EFFECT_STAGER_MODE": "enforce",
                "EFFECT_COMMIT_MODE": "enforce",
                "MCP_GATEWAY_MODE": "enforce",
                "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                    [
                        {
                            "capability": capability,
                            "org_id": _ORG_ID,
                            "user_id": _USER_ID,
                        }
                        for capability in _GATEWAY_CAPABILITIES
                    ]
                ),
            }
        )

    @staticmethod
    def _card() -> McpServerCard:
        return McpServerCard(
            name=_SERVER,
            server_id="srv_linear",
            short_description="Linear MCP connector.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=McpAuthState.AUTHENTICATED,
            required_scopes=frozenset(),
            health=McpServerHealth.HEALTHY,
            load_cost=10,
            enabled=True,
        )

    @classmethod
    def _provider(cls) -> _LinearMcpProvider:
        return _LinearMcpProvider(
            card=cls._card(),
            client=_RecordingFakeClient(
                tool_outputs={
                    "get_issues": {"issues": [{"id": "L-1", "title": "First"}]},
                    "create_issue": {"issue": {"id": "L-99", "title": "Created"}},
                }
            ),
        )

    @staticmethod
    def _patch_registry(monkeypatch, provider: _LinearMcpProvider) -> None:
        registry = DynamicMcpRegistry(providers=(provider,))
        monkeypatch.setattr(
            DefaultRuntimeDependenciesFactory,
            "_mcp_registry",
            lambda self, context, **kwargs: registry,
        )

    @staticmethod
    def _script_one_mcp_call(monkeypatch, *, tool_name: str, arguments: dict) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_CALLS, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_NAME, "call_mcp_tool")
        monkeypatch.setenv(
            FakeModelProvider.ENV_TOOL_ARGS,
            json.dumps(
                {"server_name": _SERVER, "tool_name": tool_name, "arguments": arguments}
            ),
        )

    @classmethod
    def _coordinators(cls, store, settings):
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        runs = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversations = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=runs
        )
        approvals = ApprovalCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            membership_resolver=InMemoryWorkspaceMembershipResolver(),
            notification_dispatcher=LoggingNotificationDispatcher(),
        )
        return runs, conversations, approvals

    @classmethod
    async def _enqueue_run(cls, runs, conversations) -> str:
        conversation = await conversations.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID, user_id=_USER_ID, assistant_id="assistant_123"
            )
        )
        created = await runs.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="Do the linear thing.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return created.run_id

    @staticmethod
    def _artifact_stores():
        coordinator = InMemoryArtifactPublicationCoordinator()
        return InMemoryArtifactBlobStore(coordinator), InMemoryArtifactReferenceStore(
            coordinator
        )

    @staticmethod
    def _drain(store, settings, *, blobs, references) -> None:
        return RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            artifact_blob_store=blobs,
            artifact_reference_store=references,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        ).run_until_idle()

    @staticmethod
    def _event_types(store, run_id: str) -> list[str]:
        return [event.event_type for event in store.events_by_run[run_id]]

    @staticmethod
    def _pending_approvals(store, run_id: str):
        return [a for a in store.approval_requests.values() if a.run_id == run_id]


class TestTrustedReadAutoRuns(LinearMcpRunMixin):
    async def test_authenticated_read_only_hint_read_executes_without_approval(
        self, monkeypatch
    ) -> None:
        provider = self._provider()
        self._patch_registry(monkeypatch, provider)
        self._script_one_mcp_call(
            monkeypatch, tool_name="get_issues", arguments={"team": "ENG"}
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, _approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        await self._drain(store, settings, blobs=blobs, references=references)

        names = self._event_types(store, run_id)
        # The run executed and completed — never failed, never parked.
        assert "run_failed" not in names, names
        assert "run_completed" in names
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        # No approval was ever raised for a trusted read.
        assert self._pending_approvals(store, run_id) == []
        assert "mcp_auth_required" not in names
        assert "approval_requested" not in names
        # The read actually dispatched to the connector exactly once (a staged
        # op never constructs a client).
        assert provider.created_clients == [_SERVER]
        assert provider.client.calls == [("get_issues", {"team": "ENG"})]


class TestWriteParksThenApproveExecutes(LinearMcpRunMixin):
    async def test_write_parks_then_approve_executes_in_same_run(
        self, monkeypatch
    ) -> None:
        provider = self._provider()
        self._patch_registry(monkeypatch, provider)
        self._script_one_mcp_call(
            monkeypatch,
            tool_name="create_issue",
            arguments={"title": "Ship it", "team": "ENG"},
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        # --- Act 1: drive to the park. -----------------------------------
        await self._drain(store, settings, blobs=blobs, references=references)

        names = self._event_types(store, run_id)
        assert store.runs[run_id].status is AgentRunStatus.WAITING_FOR_APPROVAL, names
        pending = self._pending_approvals(store, run_id)
        assert len(pending) == 1, [a.approval_id for a in pending]
        # The orphan guard: NO external change and NO run seal before approval.
        assert provider.created_clients == []
        assert provider.client.calls == []
        assert "run_completed" not in names, names

        # --- Act 2: approve, then re-drain. ------------------------------
        await approvals.record_approval_decision(
            org_id=_ORG_ID,
            approval_id=pending[0].approval_id,
            request=ApprovalDecisionRequest(
                decision="approved", decided_by_user_id=_USER_ID, answer="yes"
            ),
        )
        await self._drain(store, settings, blobs=blobs, references=references)

        names = self._event_types(store, run_id)
        assert "run_failed" not in names, names
        assert "run_completed" in names
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        # The write executed in the SAME run, exactly once (no double-dispatch
        # across the interrupt replay), and only after approval.
        assert provider.created_clients == [_SERVER]
        assert provider.client.calls == [
            ("create_issue", {"title": "Ship it", "team": "ENG"})
        ]


class NonOAuthLocalMcpRunMixin(LinearMcpRunMixin):
    """Drive the SAME real-graph run as ``LinearMcpRunMixin`` on a NON-OAuth server.

    Only two things change versus the OAuth fixture: the provider offers no
    ``create_auth_session`` (:class:`_LocalMcpProvider`), and the card is
    ``auth_mode=NONE`` over stdio. The catalog still keys ``create_issue`` under
    "linear" → WRITE, so the descriptor + PDP decision are byte-identical and the
    write GATEs exactly as before — isolating the one variable under test: no
    OAuth provider, hence (pre-fix) ``gate=None`` and no interrupt at all.
    """

    @staticmethod
    def _card() -> McpServerCard:
        return McpServerCard(
            name=_SERVER,
            server_id="srv_linear_local",
            short_description="Local (stdio) Linear-like MCP connector.",
            transport=McpTransport.STDIO,
            auth_mode=McpAuthMode.NONE,
            auth_state=McpAuthState.AUTH_UNSUPPORTED,
            required_scopes=frozenset(),
            health=McpServerHealth.HEALTHY,
            load_cost=10,
            enabled=True,
        )

    @classmethod
    def _provider(cls) -> _LocalMcpProvider:
        return _LocalMcpProvider(
            card=cls._card(),
            client=_RecordingFakeClient(
                tool_outputs={
                    "create_issue": {"issue": {"id": "L-99", "title": "Created"}}
                }
            ),
        )


class TestNonOAuthWriteParksThenApproveExecutes(NonOAuthLocalMcpRunMixin):
    async def test_non_oauth_write_parks_then_approve_executes_in_same_run(
        self, monkeypatch
    ) -> None:
        provider = self._provider()
        # Precondition: no OAuth session factory ⇒ the factory's probe finds no
        # ``auth_session_creator`` — the exact condition that used to yield
        # ``gate=None`` and a hard "approval not available" refusal.
        assert not hasattr(provider, "create_auth_session")
        self._patch_registry(monkeypatch, provider)
        self._script_one_mcp_call(
            monkeypatch,
            tool_name="create_issue",
            arguments={"title": "Ship it", "team": "ENG"},
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        # --- Act 1: drive to the park. -----------------------------------
        await self._drain(store, settings, blobs=blobs, references=references)

        names = self._event_types(store, run_id)
        assert store.runs[run_id].status is AgentRunStatus.WAITING_FOR_APPROVAL, names
        pending = self._pending_approvals(store, run_id)
        assert len(pending) == 1, [a.approval_id for a in pending]
        # PARKED, not refused: no external change and no seal before approval.
        assert provider.created_clients == []
        assert provider.client.calls == []
        assert "run_completed" not in names, names

        # --- Act 2: approve, then re-drain. ------------------------------
        await approvals.record_approval_decision(
            org_id=_ORG_ID,
            approval_id=pending[0].approval_id,
            request=ApprovalDecisionRequest(
                decision="approved", decided_by_user_id=_USER_ID, answer="yes"
            ),
        )
        await self._drain(store, settings, blobs=blobs, references=references)

        names = self._event_types(store, run_id)
        assert "run_failed" not in names, names
        assert "run_completed" in names
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        # The write executed in the SAME run exactly once, only after approval —
        # on a connector with NO OAuth provider wired.
        assert provider.created_clients == [_SERVER]
        assert provider.client.calls == [
            ("create_issue", {"title": "Ship it", "team": "ENG"})
        ]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
