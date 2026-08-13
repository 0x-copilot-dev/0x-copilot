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
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
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
from langchain_core.tools import BaseTool
from pydantic import create_model

from agent_runtime.capabilities.mcp.connection import McpServerConnectionConfig
from agent_runtime.capabilities.mcp.per_tool_registration import (
    McpPerToolCollaborators,
)
from agent_runtime.execution import factory as factory_module
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

_ORG_ID = "org_123"
_USER_ID = "user_123"
_SERVER = "linear"
_READ_TOOL = "get_issues"
_WRITE_TOOL = "create_issue"
#: The MODEL-SURFACE register. Per-tool registration namespaces every
#: connector tool, so this is the name the model emits in a tool call; the
#: connector still answers to the bare name above, which is what
#: ``provider.client.calls`` is asserted in.
_READ_REGISTERED = McpToolName.compose(server=_SERVER, tool=_READ_TOOL)
_WRITE_REGISTERED = McpToolName.compose(server=_SERVER, tool=_WRITE_TOOL)
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


class _PerToolPlane:
    """The credential plane the per-tool path needs, over the same fake client.

    Production builds this from the registry's ``backend_url`` (see
    ``factory._proxy_collaborators``); a fake registry has none, so the plane
    resolves to ``None`` and NO MCP tool registers — which is why this file went
    quiet after the umbrella was retired rather than failing loudly. Injecting it
    here is what restores the altitude these tests exist for: a real worker, a
    real graph, and a real approval coordinator over a connector that answers.
    """

    @staticmethod
    def tools_for(client: "_RecordingFakeClient") -> list[BaseTool]:
        """One ``BaseTool`` per connector tool, dispatching to the fake client.

        ``read_only`` puts the camelCase ``readOnlyHint`` on ``metadata`` — the
        shape ``langchain-mcp-adapters`` actually returns, and what the source
        ingests to derive ``action``. Without it the read derives WRITE
        fail-closed and gets gated, which is the very bug T1 exists to catch.
        """

        def make(connector_tool: str, *, read_only: bool = False) -> BaseTool:
            # `connector_tool` is deliberately NOT called `name`: a class body
            # that assigns `name` makes it class-local, so `name: str = name`
            # raises `NameError` instead of closing over the parameter.
            # A real `args_schema` per tool, not a shared/absent one: the
            # display wrapper subclasses whatever schema it is given, and a
            # missing schema makes it build a model with a duplicate base.
            # Each tool declares exactly the arguments its callers send. A
            # schema carrying a field the model did not supply would have
            # pydantic fill the default, and the connector would then record an
            # argument nobody asked for — which is what the recorded-call
            # assertions below would (correctly) flag.
            fields = (
                {"team": (str, "")}
                if connector_tool == _READ_TOOL
                else {"title": (str, ""), "team": (str, "")}
            )
            schema = create_model(f"{connector_tool}_Args", **fields)

            class _ConnectorTool(BaseTool):
                # `content_and_artifact` is what langchain-mcp-adapters returns,
                # and every wrapper in the pipeline depends on the two-tuple.
                name: str = connector_tool
                description: str = f"fake {connector_tool}"
                response_format: str = "content_and_artifact"
                args_schema: type = schema
                metadata: dict = {"readOnlyHint": True} if read_only else {}

                def _run(self, *a: object, **k: object) -> object:
                    raise AssertionError("MCP tools are driven asynchronously.")

                async def _arun(self, *a: object, **k: object) -> object:
                    payload = {
                        key: value for key, value in k.items() if key != "tool_call_id"
                    }
                    out = await client.call_tool(
                        tool_name=connector_tool, arguments=payload
                    )
                    return (json.dumps(out), dict(out))

            return _ConnectorTool()

        return [make(_READ_TOOL, read_only=True), make(_WRITE_TOOL)]

    @classmethod
    def install(cls, monkeypatch, client: "_RecordingFakeClient") -> None:
        """Point the factory's credential-plane seam at this fake."""

        tools = cls.tools_for(client)

        class _Directory:
            async def connection_for(self, server_id: str) -> object:
                return McpServerConnectionConfig(
                    url="https://mcp.invalid/mcp", transport=McpTransport.HTTP
                )

        class _Credentials:
            async def auth_for(self, server_id: str) -> Mapping[str, str]:
                return {}

        class _Client:
            async def get_tools(self, *, server_name: str | None = None):
                return list(tools)

        class _Factory:
            def create(self, connections: Mapping[str, object]) -> object:
                return _Client()

        monkeypatch.setattr(
            factory_module,
            "_mcp_per_tool_collaborators",
            lambda dependencies, *, runtime_context: McpPerToolCollaborators(
                directory=_Directory(),
                credentials=_Credentials(),
                client_factory=_Factory(),
            ),
        )


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
    def _patch_registry(monkeypatch, provider) -> None:
        registry = DynamicMcpRegistry(providers=(provider,))
        monkeypatch.setattr(
            DefaultRuntimeDependenciesFactory,
            "_mcp_registry",
            lambda self, context, **kwargs: registry,
        )
        # The registry alone is no longer enough: per-tool registration also
        # needs a credential plane, which production derives from the registry's
        # `backend_url`. A fake registry has none, so without this the run gets
        # NO MCP tools and every assertion below goes quiet rather than red.
        _PerToolPlane.install(monkeypatch, provider.client)

    @staticmethod
    def _script_one_mcp_call(monkeypatch, *, tool_name: str, arguments: dict) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_CALLS, "1")
        # Per-tool registration means the model calls the connector's own tool
        # by name with its own arguments. There is no umbrella to address, and
        # no `{server_name, tool_name, arguments}` envelope to decode.
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_NAME, tool_name)
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_ARGS, json.dumps(arguments))

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
            monkeypatch, tool_name=_READ_REGISTERED, arguments={"team": "ENG"}
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
        # The read actually dispatched to the connector exactly once.
        assert provider.client.calls == [("get_issues", {"team": "ENG"})]


class TestWriteParksThenApproveExecutes(LinearMcpRunMixin):
    async def test_write_parks_then_approve_executes_in_same_run(
        self, monkeypatch
    ) -> None:
        provider = self._provider()
        self._patch_registry(monkeypatch, provider)
        self._script_one_mcp_call(
            monkeypatch,
            tool_name=_WRITE_REGISTERED,
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
            tool_name=_WRITE_REGISTERED,
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
        assert provider.client.calls == [
            ("create_issue", {"title": "Ship it", "team": "ENG"})
        ]


class TestWriteGateHoldsWhenSiblingCallsRunAlongsideIt(LinearMcpRunMixin):
    """The gate under a *concurrent* turn, not a turn of one call.

    The runtime no longer serializes a turn's tool calls, so a gated write is
    now dispatched alongside its siblings rather than alone. That is exactly the
    shape in which a gate can leak: the interrupt has to propagate out of one
    branch of a concurrent tool node while the other branches are mid-flight,
    and the write must still make no external change before a human decides.

    The reads here are the control. They are trusted (``readOnlyHint``) so they
    ALLOW and dispatch; if the assertion below saw only the reads because the
    write silently never ran, that would be indistinguishable from a gate that
    worked — so the parked approval is asserted too.
    """

    @staticmethod
    def _script_concurrent_read_read_write(monkeypatch) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_CALLS, "1")
        # Per-tool: each sibling addresses the connector's own tool directly.
        # The gate's shape is unchanged — two trusted reads ALLOW and dispatch
        # concurrently while the write parks — but there is no umbrella to
        # address them through.
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_NAME, _READ_REGISTERED)
        monkeypatch.setenv(
            FakeModelProvider.ENV_PARALLEL_TOOL_CALLS,
            json.dumps(
                [
                    {"name": _READ_REGISTERED, "args": {"team": "ENG"}},
                    {"name": _READ_REGISTERED, "args": {"team": "DESIGN"}},
                    {
                        "name": _WRITE_REGISTERED,
                        "args": {"title": "Ship it", "team": "ENG"},
                    },
                ]
            ),
        )

    async def test_gated_write_parks_while_sibling_reads_dispatch(
        self, monkeypatch
    ) -> None:
        provider = self._provider()
        self._patch_registry(monkeypatch, provider)
        self._script_concurrent_read_read_write(monkeypatch)
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        # --- Act 1: one turn, three concurrent calls, one of them gated. ---
        await self._drain(store, settings, blobs=blobs, references=references)

        names = self._event_types(store, run_id)
        assert store.runs[run_id].status is AgentRunStatus.WAITING_FOR_APPROVAL, names
        pending = self._pending_approvals(store, run_id)
        assert len(pending) == 1, [a.approval_id for a in pending]
        # The gate held: the write made no external change, and the run did not
        # seal past it while its siblings were still running.
        assert ("create_issue", {"title": "Ship it", "team": "ENG"}) not in (
            provider.client.calls
        ), provider.client.calls
        assert "run_completed" not in names, names
        # …and the control: both trusted reads in the same turn DID dispatch,
        # so the gate refused one specific call rather than the whole turn
        # failing to run. Compared as a SET on purpose — the two reads are
        # concurrent, so which of them reaches the connector first is the
        # framework's business and asserting an order here would be asserting
        # the absence of the concurrency this change exists to allow.
        assert {
            (name, tuple(sorted(arguments.items())))
            for name, arguments in provider.client.calls
            if name == "get_issues"
        } == {
            ("get_issues", (("team", "ENG"),)),
            ("get_issues", (("team", "DESIGN"),)),
        }, provider.client.calls

        # --- Act 2: approve, then re-drain. --------------------------------
        await approvals.record_approval_decision(
            org_id=_ORG_ID,
            approval_id=pending[0].approval_id,
            request=ApprovalDecisionRequest(
                decision="approved", decided_by_user_id=_USER_ID, answer="yes"
            ),
        )
        await self._drain(store, settings, blobs=blobs, references=references)

        assert "run_failed" not in self._event_types(store, run_id)
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        # Exactly once, and only after the decision — the concurrent siblings
        # did not cause the approved effect to be replayed.
        writes = [call for call in provider.client.calls if call[0] == "create_issue"]
        assert writes == [("create_issue", {"title": "Ship it", "team": "ENG"})], (
            provider.client.calls
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
