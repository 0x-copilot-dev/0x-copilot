"""Hermetic real-graph proof of the P2-8 per-tool MCP registration flip.

The twin of ``test_mcp_write_gate_e2e`` (P1b), one increment later. That file
proved the PDP owns the decision when the model calls the single
``call_mcp_tool`` gateway; this one proves the same run behaves the same way
when ``MCP_PER_TOOL_ENABLED`` is on and the model calls the **real MCP tool by
name** — through the REAL worker, the REAL Deep Agents graph, the REAL streaming
executor and the REAL :class:`ApprovalCoordinator`. Only two things are fakes:
the env-gated deterministic chat model, and the ``linear`` connector itself,
whose tools are recording ``StructuredTool``s at the exact shape
``langchain-mcp-adapters`` returns them (``args_schema`` = the server's raw
JSON-Schema **dict**, ``response_format="content_and_artifact"``, tri-state hints
flattened onto ``metadata``).

Four cases, and each one is a claim about production behaviour:

* **T1 — a per-tool trusted READ auto-runs.** ``get_issues`` advertises
  ``readOnlyHint: true``, so the descriptor derives READ+TRUSTED and the PDP
  ALLOWs. The run completes, no approval is raised, and the recording connector
  shows exactly one call with the model's arguments.
* **T2 — a per-tool WRITE parks, and approval executes it once.** ``create_issue``
  advertises no hint, so it is the fail-closed WRITE and the POLICY stage GATEs
  on the same :class:`ToolAccessGate` P1b used. The run reaches
  ``WAITING_FOR_APPROVAL`` with **no** connector call and **no** ``run_completed``
  (the orphan guard). A real approval decision then resumes the same run, which
  completes with exactly ONE connector call — no double dispatch across the
  interrupt replay.
* **T3 — provenance survives per-tool.** The streamed tool events for the call
  carry ``provenance.server_name`` and ``access_mode``, resolved through the run's
  published registration map rather than by unwrapping a dispatcher payload
  (there is none). This is P2-6's fallback under a real per-tool call.
* **T4 — flag OFF is unchanged.** The identical errand, with the identical
  wiring, and only ``MCP_PER_TOOL_ENABLED`` flipped off, still runs through
  ``call_mcp_tool`` and reaches the same connector with the same arguments. The
  flip cannot regress today's behaviour, because today's behaviour is re-proved
  against the same fixtures in the same file.

**Two seams are injected, and both are injected because production has nothing
to inject yet.** ``RuntimeDependencies.mcp_per_tool_collaborators`` (the
credential plane) and ``.mcp_connector_resolver_sink`` (the worker's stream
processor) are read by ``execution.factory`` but written by nobody on the worker
path — P2-8 ships the flip inert on purpose, and P2-9 owns the wiring. So this
file supplies both through ``DefaultRuntimeDependenciesFactory``, the single seam
every worker-built dependency graph goes through, exactly as P1b patches
``_mcp_registry``. Everything downstream of those two values — the source, the
five middleware stages, the PDP, the gate, the graph, the stream projection — is
production code driven for real.

**STATUS: T1 / T2 / T3 are RED on a blocker this file found.** With the flag on
and any connector loaded, ``_assemble_harness`` cannot build the graph at all:
``wrap_tools_with_display`` →
``capabilities/middleware/display_metadata.wrap_args_schema`` reads
``args_schema.__name__``, and every per-tool MCP tool carries the server's raw
JSON-Schema **dict** there (``langchain-mcp-adapters`` sets
``args_schema=tool.inputSchema``, and ``mcp.types.Tool.inputSchema`` is
``dict[str, Any]``, required). The ``AttributeError`` is caught by the factory's
generic handler and re-raised as ``AgentRuntimeError`` — so the RUN fails, before
the model is ever called, not just the connector call. Substituting a
``BaseModel`` schema and changing nothing else turns all four cases green, which
is how the blocker is isolated to that one line. Do not "fix" this file by giving
the fixture a ``BaseModel``: no real connector tool has one, and the fixture is
the production shape on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
import pytest

from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.constants import Keys
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
from agent_runtime.capabilities.mcp.connection import McpServerConnectionConfig
from agent_runtime.capabilities.mcp.per_tool_registration import (
    McpPerToolCollaborators,
)
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.execution.contracts import ConnectorAccessMode
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
_SERVER_ID = "srv_linear"
_READ_TOOL = "get_issues"
_WRITE_TOOL = "create_issue"
_READ_ARGS = {"team": "ENG"}
_WRITE_ARGS = {"title": "Ship it", "team": "ENG"}
# The four capabilities the model-facing MCP operation gateway requires to
# compose for an enforced run — carried over from the P1b proof verbatim so T4
# re-runs that exact configuration and T1 differs from it by the flag alone.
_GATEWAY_CAPABILITIES = (
    "operation_gateway",
    "effect_stager",
    "effect_commit",
    "mcp_gateway",
)


class _RecordingConnector:
    """One fake ``linear`` MCP server, reachable by BOTH registrations.

    The same ``calls`` list backs the per-tool lane (discovered ``BaseTool``s
    handed over by the client factory) and the legacy lane (a proxy client the
    registry provider hands to ``call_mcp_tool``), which is what lets T1 and T4
    assert the identical connector traffic for the identical errand.
    """

    #: What each tool returns. The per-tool lane must answer in the
    #: ``content_and_artifact`` two-tuple every adapter-built MCP tool declares;
    #: the legacy proxy lane answers with the mapping ``McpClient`` returns.
    _OUTPUTS: Mapping[str, Mapping[str, object]] = {
        _READ_TOOL: {"issues": [{"id": "L-1", "title": "First"}]},
        _WRITE_TOOL: {"issue": {"id": "L-99", "title": "Created"}},
    }

    #: The raw JSON-Schema ``inputSchema`` a server publishes. Held as a dict on
    #: purpose: ``langchain-mcp-adapters`` assigns it to ``args_schema``
    #: verbatim, and a pipeline stage that assumes a ``BaseModel`` there erases
    #: every argument of every real connector tool.
    _SCHEMAS: Mapping[str, Mapping[str, object]] = {
        _READ_TOOL: {
            "type": "object",
            "properties": {"team": {"type": "string"}},
            "required": ["team"],
        },
        _WRITE_TOOL: {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "team": {"type": "string"},
            },
            "required": ["title"],
        },
    }

    #: ``readOnlyHint`` is the connector's own untrusted tri-state hint, flattened
    #: onto ``metadata`` exactly as the adapter flattens ``ToolAnnotations``.
    _READ_ONLY = {_READ_TOOL: True, _WRITE_TOOL: False}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.proxy_clients: list[str] = []

    def langchain_tools(self) -> tuple[BaseTool, ...]:
        """The discovered per-tool surface, at the adapter's exact shape."""

        return tuple(self._langchain_tool(name) for name in self._OUTPUTS)

    def _langchain_tool(self, name: str) -> BaseTool:
        outputs = self._OUTPUTS[name]

        async def call_tool(**arguments: Any) -> tuple[str, dict[str, object]]:
            self.calls.append((name, dict(arguments)))
            return (json.dumps(outputs), dict(outputs))

        metadata: dict[str, object] | None = (
            {"readOnlyHint": True} if self._READ_ONLY[name] else None
        )
        return StructuredTool(
            name=name,
            description=f"{name} on the {_SERVER} connector.",
            args_schema=dict(self._SCHEMAS[name]),
            coroutine=call_tool,
            response_format="content_and_artifact",
            metadata=metadata,
        )

    def proxy_client(self, card: McpServerCard) -> "_RecordingProxyClient":
        """The legacy lane's client — same recorder, different registration."""

        self.proxy_clients.append(card.name)
        return _RecordingProxyClient(connector=self)

    def result_for(self, tool_name: str) -> Mapping[str, object]:
        """The canned payload ``tool_name`` answers with."""

        return self._OUTPUTS[tool_name]


@dataclass
class _RecordingProxyClient:
    """``McpClient`` surface for the legacy ``call_mcp_tool`` gateway (T4)."""

    connector: _RecordingConnector

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[object]:
        return ()

    async def list_resources(self) -> Sequence[object]:
        return ()

    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.connector.calls.append((tool_name, dict(arguments)))
        return self.connector.result_for(tool_name)


@dataclass
class _LinearMcpProvider:
    """OAuth-capable provider publishing the one authenticated ``linear`` card.

    ``create_auth_session`` exists (and is never called) purely so the factory's
    OAuth probe builds the :class:`ToolAccessGate` the POLICY stage parks on —
    the same reason it exists in the P1b fixture.
    """

    card: McpServerCard
    connector: _RecordingConnector

    async def list_server_cards(self) -> Sequence[McpServerCard]:
        # Registering the connector's untrusted tri-state hints is the
        # provider's job in production too (``backend_provider`` does it while
        # building each card's tool descriptors), and it is the ONLY hook the
        # legacy lane has: ``call_mcp_tool`` reads the run's annotations
        # registry, never a discovered ``BaseTool``'s metadata. The per-tool
        # lane re-registers the identical value from ``metadata`` via
        # ``McpToolSource._ingest_annotations``, so both lanes police the same
        # hint and T1 vs T4 stays an honest A/B.
        McpToolAnnotationsRegistry.register(
            self.card.name, _READ_TOOL, McpToolAnnotations(read_only_hint=True)
        )
        return (self.card,)

    def create_client(self, card: McpServerCard) -> _RecordingProxyClient:
        return self.connector.proxy_client(card)

    async def create_auth_session(
        self, **_kwargs: object
    ) -> object:  # pragma: no cover
        raise AssertionError("the write-approval gate never opens an auth session")


class _FakeConnectionDirectory:
    """Credential-plane endpoint lookup for the one known server."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig:
        self.requested.append(server_id)
        return McpServerConnectionConfig(
            url="https://mcp.linear.app/mcp", transport=McpTransport.HTTP
        )


class _StubAuth(httpx.Auth):
    """Stand-in for the rotating bearer; the fake client never authenticates."""


class _FakeCredentials:
    """P0 ``CredentialProvider`` fake — the plane P2-7a has yet to deliver."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def auth_for(self, server_id: str) -> httpx.Auth:
        self.requested.append(server_id)
        return _StubAuth()


@dataclass
class _FakeMcpClient:
    """A ``MultiServerMCPClient`` stand-in over the recording connector."""

    connector: _RecordingConnector
    discovered: list[str] = field(default_factory=list)

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        self.discovered.append(str(server_name))
        return list(self.connector.langchain_tools())


@dataclass
class _FakeClientFactory:
    """Hands back the fake client instead of opening a real connection."""

    connector: _RecordingConnector

    def create(self, connections: Mapping[str, Any]) -> _FakeMcpClient:
        del connections
        return _FakeMcpClient(connector=self.connector)


class _WorkerStreamSink:
    """Fan the run's registration map out to the worker's REAL stream processors.

    ``RuntimeDependencies.mcp_connector_resolver_sink`` is the port the factory
    publishes through, and the worker does not populate it yet (P2-9). The run
    handler and the approval handler each own a ``StreamOrchestrator``, so a run
    that parks projects its pre-approval events through one and its post-approval
    events through the other; both are registered here so the published map
    reaches whichever handler is executing.

    Nothing about the resolution itself is faked: the resolver is the one the
    real ``McpToolCatalog`` built, and the lookup is
    ``StreamMessageProcessor.connector_slug_for_payload``.
    """

    def __init__(self) -> None:
        self._processors: list[object] = []
        self.published: list[tuple[str, object]] = []

    def attach(self, worker: RuntimeWorker) -> None:
        """Register a worker's message processors as publication targets."""

        self._processors = [
            worker.run_handler.stream_event_mapper.message_processor,
            worker.approval_handler.stream_event_mapper.message_processor,
        ]
        for run_id, resolver in self.published:
            self._publish(run_id, resolver)

    def publish_connector_resolver(self, run_id: str, resolver: object) -> None:
        """The one method ``execution.factory`` calls on this port."""

        self.published.append((run_id, resolver))
        self._publish(run_id, resolver)

    def _publish(self, run_id: str, resolver: object) -> None:
        for processor in self._processors:
            processor.publish_connector_resolver(run_id, resolver)  # type: ignore[attr-defined]


@dataclass
class _StaticAccessModesResolver:
    """The PRD-06 run-start port, answering from a fixed snapshot.

    Production fetches ``/internal/v1/mcp/cards`` here. Supplying the answer
    directly is what puts a real ``connector_access_modes`` entry on the
    persisted run, so the stream seam's disclosure is exercised rather than
    skipped for want of a snapshot.
    """

    modes: Mapping[str, str]

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, str]:
        del org_id, user_id
        return dict(self.modes)


class PerToolLinearRunMixin:
    """Drive a real queued run whose one turn reaches the fake ``linear`` server."""

    #: Keyed by connector slug because that is the key
    #: ``StreamMessageProcessor.add_tool_presentation_fields`` looks the frozen
    #: snapshot up by (it holds a server *name* at that seam, never an id).
    ACCESS_MODES = {_SERVER: ConnectorAccessMode.READ_ACT.value}

    @staticmethod
    def _settings() -> RuntimeSettings:
        """The P1b settings verbatim: an enforced model-facing MCP gateway.

        Held identical across all four cases so T1 and T4 differ by the per-tool
        flag and nothing else — an A/B, not two differently-configured runs.
        """

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
            server_id=_SERVER_ID,
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
    def _wire(
        cls,
        monkeypatch: pytest.MonkeyPatch,
        *,
        per_tool: bool,
    ) -> tuple[_RecordingConnector, _WorkerStreamSink]:
        """Install the fake connector on both registration lanes; set the flag.

        ``_mcp_registry`` is the P1b seam (it feeds the legacy gateway and the
        per-tool card lister alike); ``_build_dependencies`` carries the two
        fields the worker does not populate yet.
        """

        connector = _RecordingConnector()
        sink = _WorkerStreamSink()
        provider = _LinearMcpProvider(card=cls._card(), connector=connector)
        registry = DynamicMcpRegistry(providers=(provider,))
        collaborators = McpPerToolCollaborators(
            directory=_FakeConnectionDirectory(),  # type: ignore[arg-type]
            credentials=_FakeCredentials(),  # type: ignore[arg-type]
            client_factory=_FakeClientFactory(connector),  # type: ignore[arg-type]
        )
        build = DefaultRuntimeDependenciesFactory._build_dependencies

        def _with_per_tool_seams(self, context, **kwargs):  # type: ignore[no-untyped-def]
            return build(self, context, **kwargs).model_copy(
                update={
                    "mcp_per_tool_collaborators": collaborators,
                    "mcp_connector_resolver_sink": sink,
                }
            )

        monkeypatch.setattr(
            DefaultRuntimeDependenciesFactory,
            "_mcp_registry",
            lambda self, context, **kwargs: registry,
        )
        monkeypatch.setattr(
            DefaultRuntimeDependenciesFactory,
            "_build_dependencies",
            _with_per_tool_seams,
        )
        return connector, sink

    @staticmethod
    def _script_per_tool_call(
        monkeypatch: pytest.MonkeyPatch,
        *,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> None:
        """The model calls the REAL MCP tool by its own name."""

        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_CALLS, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_NAME, tool_name)
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_ARGS, json.dumps(dict(arguments)))

    @staticmethod
    def _script_gateway_call(
        monkeypatch: pytest.MonkeyPatch,
        *,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> None:
        """The same errand, addressed to the legacy umbrella tool (T4)."""

        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_CALLS, "1")
        monkeypatch.setenv(FakeModelProvider.ENV_TOOL_NAME, "call_mcp_tool")
        monkeypatch.setenv(
            FakeModelProvider.ENV_TOOL_ARGS,
            json.dumps(
                {
                    "server_name": _SERVER,
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                }
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
            connector_access_modes_resolver=_StaticAccessModesResolver(
                cls.ACCESS_MODES
            ),
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
    async def _drain(store, settings, *, blobs, references, sink) -> None:
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            artifact_blob_store=blobs,
            artifact_reference_store=references,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        sink.attach(worker)
        await worker.run_until_idle()

    @staticmethod
    def _event_types(store, run_id: str) -> list[str]:
        return [event.event_type for event in store.events_by_run[run_id]]

    @staticmethod
    def _pending_approvals(store, run_id: str):
        return [a for a in store.approval_requests.values() if a.run_id == run_id]

    @staticmethod
    def _tool_payloads(store, run_id: str, *, tool_name: str) -> list[dict]:
        """Every persisted event payload naming ``tool_name`` as its tool."""

        return [
            dict(event.payload)
            for event in store.events_by_run[run_id]
            if isinstance(event.payload, Mapping)
            and event.payload.get(Keys.Field.TOOL_NAME) == tool_name
        ]


class TestPerToolTrustedReadAutoRuns(PerToolLinearRunMixin):
    async def test_a_per_tool_read_dispatches_without_an_approval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connector, sink = self._wire(monkeypatch, per_tool=True)
        self._script_per_tool_call(
            monkeypatch, tool_name=_READ_TOOL, arguments=_READ_ARGS
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, _approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        await self._drain(
            store, settings, blobs=blobs, references=references, sink=sink
        )

        names = self._event_types(store, run_id)
        assert "run_failed" not in names, names
        assert "run_completed" in names
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        # A trusted read costs the user nothing.
        assert self._pending_approvals(store, run_id) == []
        assert "approval_requested" not in names
        assert "mcp_auth_required" not in names
        # It reached the connector once, with the model's own arguments, and it
        # went by the per-tool route: the legacy proxy client was never built.
        assert connector.calls == [(_READ_TOOL, _READ_ARGS)]
        assert connector.proxy_clients == []
        # The flip actually happened this run — the umbrella tool is gone.
        assert self._tool_payloads(store, run_id, tool_name="call_mcp_tool") == []
        assert self._tool_payloads(store, run_id, tool_name=_READ_TOOL) != []


class TestPerToolWriteParksThenApproveExecutes(PerToolLinearRunMixin):
    async def test_a_per_tool_write_parks_then_executes_once_after_approval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connector, sink = self._wire(monkeypatch, per_tool=True)
        self._script_per_tool_call(
            monkeypatch, tool_name=_WRITE_TOOL, arguments=_WRITE_ARGS
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        # --- Act 1: drive to the park. -----------------------------------
        await self._drain(
            store, settings, blobs=blobs, references=references, sink=sink
        )

        names = self._event_types(store, run_id)
        assert store.runs[run_id].status is AgentRunStatus.WAITING_FOR_APPROVAL, names
        pending = self._pending_approvals(store, run_id)
        assert len(pending) == 1, [a.approval_id for a in pending]
        # The orphan guard: no external change, and no sealed run, before the
        # user has decided anything.
        assert connector.calls == []
        assert "run_completed" not in names, names

        # --- Act 2: approve, then re-drain. ------------------------------
        await approvals.record_approval_decision(
            org_id=_ORG_ID,
            approval_id=pending[0].approval_id,
            request=ApprovalDecisionRequest(
                decision="approved", decided_by_user_id=_USER_ID, answer="yes"
            ),
        )
        await self._drain(
            store, settings, blobs=blobs, references=references, sink=sink
        )

        names = self._event_types(store, run_id)
        assert "run_failed" not in names, names
        assert "run_completed" in names
        assert store.runs[run_id].status is AgentRunStatus.COMPLETED
        # Exactly once: the interrupt replay re-enters the tool node, and the
        # write must not be sent a second time.
        assert connector.calls == [(_WRITE_TOOL, _WRITE_ARGS)]
        assert connector.proxy_clients == []


class TestPerToolConnectorProvenance(PerToolLinearRunMixin):
    async def test_streamed_events_carry_the_connector_for_a_per_tool_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connector, sink = self._wire(monkeypatch, per_tool=True)
        self._script_per_tool_call(
            monkeypatch, tool_name=_READ_TOOL, arguments=_READ_ARGS
        )
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        blobs, references = self._artifact_stores()
        runs, conversations, _approvals = self._coordinators(store, settings)
        run_id = await self._enqueue_run(runs, conversations)

        await self._drain(
            store, settings, blobs=blobs, references=references, sink=sink
        )

        assert connector.calls == [(_READ_TOOL, _READ_ARGS)]
        # The factory published the run's registration map exactly once.
        assert [published[0] for published in sink.published] == [run_id]
        payloads = self._tool_payloads(store, run_id, tool_name=_READ_TOOL)
        assert payloads, self._event_types(store, run_id)
        # There is no dispatcher payload to unwrap here — every one of these
        # resolved through the published map, which is the P2-6 fallback.
        disclosed = [
            payload for payload in payloads if Keys.Field.PROVENANCE in payload
        ]
        assert disclosed, payloads
        for payload in disclosed:
            assert payload[Keys.Field.PROVENANCE] == {
                "source": "mcp",
                Keys.Field.SERVER_NAME: _SERVER,
            }
            assert payload[Keys.Field.ACCESS_MODE] == ConnectorAccessMode.READ_ACT.value
        # The persisted invocation row names the connector too, so Activity
        # counts this as an app rather than a bare step.
        invocations = [
            record
            for record in store.tool_invocations.values()
            if record.run_id == run_id and record.tool_name == _READ_TOOL
        ]
        assert invocations, list(store.tool_invocations.values())
        assert {record.connector_slug for record in invocations} == {_SERVER}
