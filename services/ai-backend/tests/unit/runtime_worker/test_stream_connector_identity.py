"""P2-6 — connector identity at the stream seams, under BOTH registrations.

Three seams in ``StreamMessageProcessor`` name the connector that owns a tool
call: the in-flight ledger entry, the persisted ``ToolInvocationRecord`` (which
backs Activity's step/app counters), and the public event's ``provenance`` /
``access_mode``. All three had exactly one source — unwrapping the nested
``args.server_name`` of a ``call_mcp_tool`` dispatch.

Per-tool registration deletes that wrapper: the tool name IS the MCP tool, so
the unwrap returns ``None`` and, unpatched, every MCP call would project as a
native step — no connector on the ledger row, no ``provenance``, no
``access_mode``, and Activity would count apps as steps.

P2 flips the two registrations one deployment at a time, so both must resolve
simultaneously. These tests pin that: the legacy dispatcher payload keeps
resolving and keeps *winning* (the unwrap is consulted first), a per-tool
payload resolves through the run's published registration map, and an
unregistered name still resolves to no connector — MCP identity is never
inferred from the shape of a tool name.
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.connector_resolver import ToolConnectorResolver
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import ToolInvocationRecord
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.stream_tools import StreamMessageProcessor


class _CapturingPersistence:
    """Collects the tool-invocation rows the started-seam writes."""

    def __init__(self) -> None:
        self.records: list[ToolInvocationRecord] = []

    async def record_tool_invocation(self, record: ToolInvocationRecord) -> None:
        self.records.append(record)


class _RecordingEventProducer:
    """Captures emitted API events and carries the persistence the writer uses."""

    def __init__(self, persistence: _CapturingPersistence) -> None:
        self.persistence = persistence
        self.events: list[dict[str, object]] = []

    async def append_api_event(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class ConnectorIdentityMixin:
    """Fakes, builders, drivers, and the constants every case below shares."""

    run_id = "run_p26"
    other_run_id = "run_p26_other"
    dispatcher_tool = "call_mcp_tool"
    # ``{server}_{tool}`` is the exact name ``langchain-mcp-adapters`` gives a
    # prefixed per-tool ``BaseTool`` (``load_mcp_tools`` →
    # ``lc_tool_name = f"{server_name}_{tool.name}"``). It is also the only
    # shape the ledger accepts: ``ToolInvocationRecord.tool_name`` normalizes
    # through ``normalize_slug`` (``^[a-z0-9][a-z0-9_-]*$``), so a dotted
    # ``linear.create_issue`` is rejected at the invocation-writer seam — a
    # constraint that predates this change and binds whatever P2-8 registers.
    per_tool_name = "linear_create_issue"
    connector_slug = "linear"
    access_mode = "read_act"
    dispatcher_connector = "github"

    def build_processor(
        self,
    ) -> tuple[StreamMessageProcessor, _RecordingEventProducer, _CapturingPersistence]:
        """A processor over recording fakes, with no resolver published yet."""

        persistence = _CapturingPersistence()
        producer = _RecordingEventProducer(persistence)
        processor = StreamMessageProcessor(
            producer,  # type: ignore[arg-type]
            StreamUpdateProcessor(producer),  # type: ignore[arg-type]
        )
        return processor, producer, persistence

    def build_run(self, *, run_id: str | None = None) -> RunRecord:
        """A run whose frozen context grants ``read_act`` on both connectors."""

        resolved_run_id = run_id or self.run_id
        return RunRecord(
            run_id=resolved_run_id,
            conversation_id="conv_p26",
            org_id="org_p26",
            user_id="user_p26",
            user_message_id="msg_p26",
            trace_id="trace_p26",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                user_id="user_p26",
                org_id="org_p26",
                roles=["employee"],
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id=resolved_run_id,
                trace_id="trace_p26",
                connector_access_modes={
                    self.connector_slug: self.access_mode,
                    self.dispatcher_connector: self.access_mode,
                },
            ),
        )

    def publish_per_tool_map(
        self,
        processor: StreamMessageProcessor,
        *,
        run_id: str | None = None,
        tool_to_server: dict[str, str] | None = None,
    ) -> None:
        """Publish the registration snapshot the per-tool flip will publish."""

        processor.publish_connector_resolver(
            run_id or self.run_id,
            ToolConnectorResolver(
                tool_to_server or {self.per_tool_name: self.connector_slug}
            ),
        )

    async def drive_started(
        self,
        processor: StreamMessageProcessor,
        run: RunRecord,
        *,
        tool_name: str,
        call_id: str,
        args: dict[str, object],
    ) -> None:
        """Drive one TOOL_CALL_STARTED through the real projection path."""

        await processor.append_tool_call_chunk_event(
            run=run,
            namespace=StreamNamespace.from_value(()),
            tool_call={"name": tool_name, "id": call_id, "index": 0, "args": args},
            metadata={},
            parent_task_id=None,
        )

    async def drive_dispatcher_call(
        self,
        processor: StreamMessageProcessor,
        run: RunRecord,
        *,
        call_id: str = "call_legacy_1",
    ) -> None:
        """Drive the legacy single-gateway shape: ``call_mcp_tool`` + nested args."""

        await self.drive_started(
            processor,
            run,
            tool_name=self.dispatcher_tool,
            call_id=call_id,
            args={
                "server_name": self.dispatcher_connector,
                "tool_name": "create_issue",
                "arguments": {"title": "ship it"},
            },
        )

    async def drive_per_tool_call(
        self,
        processor: StreamMessageProcessor,
        run: RunRecord,
        *,
        tool_name: str | None = None,
        call_id: str = "call_per_tool_1",
    ) -> None:
        """Drive the per-tool shape: the tool name IS the MCP tool, nothing nested."""

        await self.drive_started(
            processor,
            run,
            tool_name=tool_name or self.per_tool_name,
            call_id=call_id,
            args={"title": "ship it"},
        )

    @staticmethod
    def started_payload(producer: _RecordingEventProducer) -> dict[str, object]:
        """The one TOOL_CALL_STARTED payload the drive emitted."""

        payloads = [
            event["payload"]
            for event in producer.events
            if event["event_type"] is RuntimeApiEventType.TOOL_CALL_STARTED
        ]
        assert len(payloads) == 1
        payload = payloads[0]
        assert isinstance(payload, dict)
        return payload

    @staticmethod
    def ledger_slug(processor: StreamMessageProcessor, run: RunRecord) -> str | None:
        """The connector recorded on the single in-flight ledger entry."""

        entries = processor.ledger_for_run(run.run_id).unsettled()
        assert len(entries) == 1
        return entries[0].connector_slug

    def assert_connector_disclosed(
        self,
        *,
        processor: StreamMessageProcessor,
        producer: _RecordingEventProducer,
        persistence: _CapturingPersistence,
        run: RunRecord,
        expected_slug: str,
    ) -> None:
        """All three seams name ``expected_slug``, and the access mode rides along."""

        payload = self.started_payload(producer)
        assert payload["provenance"] == {
            "source": "mcp",
            "server_name": expected_slug,
        }
        assert payload["access_mode"] == self.access_mode
        assert len(persistence.records) == 1
        assert persistence.records[0].connector_slug == expected_slug
        assert self.ledger_slug(processor, run) == expected_slug

    def assert_no_connector_disclosed(
        self,
        *,
        processor: StreamMessageProcessor,
        producer: _RecordingEventProducer,
        persistence: _CapturingPersistence,
        run: RunRecord,
    ) -> None:
        """All three seams report a native step — absent, not guessed."""

        payload = self.started_payload(producer)
        assert "provenance" not in payload
        assert "access_mode" not in payload
        assert len(persistence.records) == 1
        assert persistence.records[0].connector_slug is None
        assert self.ledger_slug(processor, run) is None


class TestLegacyDispatcherConnectorIdentity(ConnectorIdentityMixin):
    """The single-gateway path must survive the fallback verbatim."""

    async def test_dispatcher_payload_still_resolves_its_connector(self) -> None:
        """REGRESSION: unwrap-only behaviour is unchanged when no map is published."""

        processor, producer, persistence = self.build_processor()
        run = self.build_run()

        await self.drive_dispatcher_call(processor, run)

        self.assert_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
            expected_slug=self.dispatcher_connector,
        )

    async def test_dispatcher_payload_wins_over_a_published_per_tool_map(self) -> None:
        """The unwrap is consulted FIRST: a per-tool map cannot rename a dispatch.

        Mid-flip a run may hold both shapes. A map that claims the dispatcher's
        own name must never override the connector the payload itself carries.
        """

        processor, producer, persistence = self.build_processor()
        run = self.build_run()
        self.publish_per_tool_map(
            processor,
            tool_to_server={self.dispatcher_tool: "decoy_connector"},
        )

        await self.drive_dispatcher_call(processor, run)

        self.assert_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
            expected_slug=self.dispatcher_connector,
        )


class TestPerToolConnectorIdentity(ConnectorIdentityMixin):
    """The per-tool path resolves through the run's registration snapshot."""

    async def test_registered_tool_resolves_connector_provenance_and_access_mode(
        self,
    ) -> None:
        """A per-tool call carries full connector identity once the map is published."""

        processor, producer, persistence = self.build_processor()
        run = self.build_run()
        self.publish_per_tool_map(processor)

        await self.drive_per_tool_call(processor, run)

        self.assert_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
            expected_slug=self.connector_slug,
        )

    async def test_unregistered_tool_stays_a_native_step(self) -> None:
        """An unknown name is a step, not an app — identity is never guessed."""

        processor, producer, persistence = self.build_processor()
        run = self.build_run()
        self.publish_per_tool_map(processor)

        await self.drive_per_tool_call(
            processor,
            run,
            tool_name="linear_lookalike",
            call_id="call_unknown_1",
        )

        self.assert_no_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
        )

    async def test_seam_is_inert_until_the_run_publishes_a_map(self) -> None:
        """Pre-flip: no map is published, so the fallback cannot change anything."""

        processor, producer, persistence = self.build_processor()
        run = self.build_run()

        await self.drive_per_tool_call(processor, run)

        self.assert_no_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
        )

    async def test_map_is_scoped_to_the_publishing_run(self) -> None:
        """One worker serves many users: another run's map must not attribute here."""

        processor, producer, persistence = self.build_processor()
        run = self.build_run()
        self.publish_per_tool_map(processor, run_id=self.other_run_id)

        await self.drive_per_tool_call(processor, run)

        self.assert_no_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
        )

    async def test_discarding_the_run_frees_its_published_map(self) -> None:
        """Terminal cleanup drops the snapshot with the rest of the per-run state."""

        processor, producer, persistence = self.build_processor()
        run = self.build_run()
        self.publish_per_tool_map(processor)
        processor.discard_ledger(run.run_id)

        await self.drive_per_tool_call(processor, run)

        self.assert_no_connector_disclosed(
            processor=processor,
            producer=producer,
            persistence=persistence,
            run=run,
        )
