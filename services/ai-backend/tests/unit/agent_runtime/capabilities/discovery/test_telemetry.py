"""F3.7 — the discovery path is measured without becoming a leak or an outage.

A telemetry lane has three ways to do damage, and each one has a section here.
It can copy user content into a durable record; it can label a metric with
something unbounded and take the metrics pipeline down; and it can change what
the thing it observes returns.  The tests are organised around those failures
rather than around the module's classes.

The body-free proof is deliberately *not* a review of field names.  A secret is
seeded into the query, into the invocation arguments, and into the run itself,
the whole real bridge is driven end to end, and then every persisted run event
and every observation record is serialised and searched for it — the shape lane
F6.2 used, because a field-name review passes on exactly the record that leaks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.discovery import (
    CapabilityActivationMode,
    CapabilityActivationResolver,
    CapabilityBridgeRegistrar,
    CapabilityBridgeSeam,
    CapabilityBridgeToolName,
    CapabilityDiscoveryErrorCode,
    CapabilityDescribeToolResult,
    CapabilityExpansionState,
    CapabilityInvokeToolResult,
    CapabilitySearchToolResult,
    HmacCapabilityReferenceMinter,
)
from agent_runtime.capabilities.discovery.executor import GatewayCapabilityExecutor
from agent_runtime.capabilities.discovery.registration import (
    CapabilityBridgeToolAdapter,
)
from agent_runtime.capabilities.discovery.telemetry import (
    CapabilityDiscoveryMetrics,
    CapabilityDiscoveryObservation,
    CapabilityDiscoveryObserver,
    CapabilityDiscoveryObserverGroup,
    CapabilityDiscoveryOutcome,
    CapabilityDiscoveryPhase,
    CapabilityExpansionObservation,
    CapabilitySelectionCorrelator,
    ObservedCapabilityBridgeTool,
    RunJournalDiscoveryDecisionRecorder,
    digest_request,
    estimate_answer_tokens,
)
from agent_runtime.control_plane.contracts import DECISION_COUNT_CEILINGS
from agent_runtime.control_plane import (
    AgentQualityFeature,
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlSnapshot,
    RunControlSnapshotWrite,
    RunPolicyRevisions,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)

from tests.unit.agent_runtime.capabilities.discovery.test_bridge_chain import (
    BridgeChainHarness,
    _NOW,
    _READ_TOOL,
    _REFERENCE_KEY,
    _SERVER,
)

# Seeded into the query, the arguments, and the run so that *any* record that
# copies a body instead of digesting it fails a single grep.
_SECRET_QUERY = "linear issues for PROJECT-ZEPHYR-CONFIDENTIAL"
_SECRET_ARGUMENT = "TEAM-SECRET-DO-NOT-PERSIST"
_ORG = "org_bug08"
_USER = "user_bug08"
_SUBJECT = "c" * 64


@dataclass
class _CapturingObserver:
    """Hold every observation, so a test can inspect what was recorded."""

    observations: list[CapabilityDiscoveryObservation] = field(default_factory=list)
    expansions: list[CapabilityExpansionObservation] = field(default_factory=list)

    async def observe(self, observation: CapabilityDiscoveryObservation) -> None:
        self.observations.append(observation)

    async def observe_expansion(
        self,
        observation: CapabilityExpansionObservation,
    ) -> None:
        self.expansions.append(observation)


@dataclass
class _ExplodingObserver:
    """An observer that ignores its contract and raises on every call."""

    calls: int = 0

    async def observe(self, observation: CapabilityDiscoveryObservation) -> None:
        del observation
        self.calls += 1
        raise RuntimeError("postgres://secret-host/telemetry")


@dataclass
class _RecordedMetric:
    name: str
    value: float
    labels: dict[str, str]


class _FakeInstrument:
    def __init__(self, name: str, sink: list[_RecordedMetric]) -> None:
        self._name = name
        self._sink = sink

    def add(self, amount: float, labels: Mapping[str, str] | None = None) -> None:
        self._sink.append(_RecordedMetric(self._name, amount, dict(labels or {})))

    def record(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        self._sink.append(_RecordedMetric(self._name, value, dict(labels or {})))


class _FakeMeter:
    def __init__(self, sink: list[_RecordedMetric]) -> None:
        self._sink = sink

    def create_counter(self, name: str) -> _FakeInstrument:
        return _FakeInstrument(name, self._sink)

    def create_histogram(self, name: str, **_kwargs: object) -> _FakeInstrument:
        return _FakeInstrument(name, self._sink)


class _RecordingMetrics(CapabilityDiscoveryMetrics):
    """The real meter facade over a fake OTel meter, so labels are inspectable."""

    def __init__(self) -> None:
        self.recorded: list[_RecordedMetric] = []
        super().__init__()

    def _build_meter(self) -> Any:  # type: ignore[override]
        return _FakeMeter(self.recorded)


@dataclass
class _AnswerStub:
    """A minimal adapter that answers with whatever a test hands it."""

    answer: object
    name: str = "search_capabilities"
    description: str = "stub"
    calls: list[Any] = field(default_factory=list)

    async def ainvoke(self, raw_input: Any) -> Any:
        self.calls.append(raw_input)
        return self.answer


@dataclass
class _RaisingStub:
    name: str = "search_capabilities"
    description: str = "stub"

    async def ainvoke(self, raw_input: Any) -> Any:
        del raw_input
        raise RuntimeError("connector exploded")


def _observation(
    *,
    phase: CapabilityDiscoveryPhase = CapabilityDiscoveryPhase.SEARCH,
    tool: CapabilityBridgeToolName = (CapabilityBridgeToolName.SEARCH_CAPABILITIES),
    outcome: CapabilityDiscoveryOutcome = CapabilityDiscoveryOutcome.OK,
) -> CapabilityDiscoveryObservation:
    return CapabilityDiscoveryObservation(
        phase=phase,
        tool=tool,
        outcome=outcome,
        input_digest="a" * 64,
        latency_ms=12,
        result_tokens=40,
        candidate_count=3,
        scanned_count=9,
    )


class ObservedBridgeHarness(BridgeChainHarness):
    """The real bridge from ``test_bridge_chain``, mounted with an observer."""

    def observed_adapters(
        self,
        context,  # type: ignore[no-untyped-def]
        catalog,  # type: ignore[no-untyped-def]
        *,
        seam: CapabilityBridgeSeam | None,
        observer: CapabilityDiscoveryObserver | None,
        executor: object | None = None,
        revalidation: object | None = None,
    ) -> dict[str, Any]:
        registrations = CapabilityBridgeRegistrar.registrations_for(
            activation=CapabilityActivationResolver().resolve_configured(
                raw_mode=FeatureMode.ENFORCE.value,
                raw_activation=CapabilityActivationMode.DEFERRED.value,
            ),
            catalog=catalog,
            runtime_context=context,
            executor=executor,  # type: ignore[arg-type]
            revalidation=revalidation,  # type: ignore[arg-type]
            seam=seam,
            observer=observer,
            clock=lambda: _NOW,
        )
        return {
            registration.name.value: registration.adapter
            for registration in registrations
        }

    async def observed(
        self,
        context,  # type: ignore[no-untyped-def]
        observer: CapabilityDiscoveryObserver,
        *,
        expansion_observer: object | None = None,
    ):  # type: ignore[no-untyped-def]
        """Mount the whole real chain with telemetry threaded through it."""

        catalog = self.catalog(context)
        client, loader, dispatcher = self.mcp(context)
        seam = CapabilityBridgeSeam.compose(
            catalog=catalog,
            loader=loader,
            minter=HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY),
            observer=expansion_observer,  # type: ignore[arg-type]
        )
        adapters = self.observed_adapters(
            context,
            catalog,
            seam=seam,
            observer=observer,
            executor=GatewayCapabilityExecutor(
                bindings=seam.disclosure,
                loader=loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )
        return adapters, client, seam, catalog

    async def drive_chain(
        self,
        adapters: Mapping[str, Any],
        context,  # type: ignore[no-untyped-def]
        *,
        query: str = _SECRET_QUERY,
        argument: str = _SECRET_ARGUMENT,
    ) -> dict[str, Any]:
        """Search, describe, and invoke through the real Operation Gateway."""

        operation_token, service_token = self.bind_gateway(context)
        try:
            found = await adapters[
                CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
            ].ainvoke({"query": query, "limit": 10})
            capability_ref = self.ref_for(found, _READ_TOOL)
            described = await adapters[
                CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value
            ].ainvoke({"capability_ref": capability_ref})
            invoked = await adapters[
                CapabilityBridgeToolName.INVOKE_CAPABILITY.value
            ].ainvoke(
                {
                    "capability_ref": capability_ref,
                    "arguments": {"team": argument},
                }
            )
        finally:
            from agent_runtime.capabilities.mcp.gateway_context import (  # noqa: PLC0415
                McpOperationGatewayContext,
            )
            from agent_runtime.capabilities.operations.context import (  # noqa: PLC0415
                OperationContext,
            )

            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
        return {
            "search": found,
            "describe": described,
            "invoke": invoked,
            "capability_ref": capability_ref,
        }


class TestObservationCannotCarryABody(ObservedBridgeHarness):
    """The record has no field a body could enter through, and none does."""

    def test_every_observation_field_is_a_closed_value_a_count_or_a_digest(
        self,
    ) -> None:
        """Structural, not a review: free text is unrepresentable here.

        Reviewing field *names* is what lets a leak survive — the field that
        leaks is always the one that looked innocent. This asserts on the
        declared types instead, so a future field that could hold prose fails
        the test the moment it is added.

        ``int | None`` is admitted alongside ``int`` because a measurement this
        call did not take has to be expressible as *absent* rather than as a
        zero somebody could mistake for an observation. The permission is
        deliberately narrow: the only widening is "or nothing", so ``str |
        None`` — the shape a leak would actually need — still fails.
        """

        for name, info in CapabilityDiscoveryObservation.model_fields.items():
            annotation = info.annotation
            if name == "input_digest":
                continue
            if name == "schema_version":
                continue
            assert (annotation is not None) and (
                (isinstance(annotation, type) and issubclass(annotation, StrEnum))
                or annotation is int
                or annotation == (int | None)
            ), f"{name} is neither a closed vocabulary nor a count: {annotation}"

    async def test_no_seeded_secret_survives_into_the_journal_or_the_records(
        self,
    ) -> None:
        """Drive the real chain with secrets, then grep everything it wrote.

        The query, the invocation argument, the connector, and the tool name are
        all searched for. The last two matter as much as the first: a record
        that named the connector would be body-free by the letter and still tell
        an auditor which system a run touched, which is exactly what the opaque
        reference design exists to avoid.
        """

        store, context, recorder = await _bound_run(self)
        capture = _CapturingObserver()
        observer = CapabilityDiscoveryObserverGroup(observers=(recorder, capture))
        adapters, _client, _seam, _catalog = await self.observed(context, observer)

        await self.drive_chain(adapters, context)

        events = await store.list_events_after(
            org_id=_ORG,
            run_id=context.run_id,
            after_sequence=0,
        )
        # Grepping an empty journal proves nothing, so the material this test
        # searches is asserted to exist before it is searched.
        assert (
            len(
                [
                    event
                    for event in events
                    if event.event_type is RuntimeApiEventType.QUALITY_DECISION
                ]
            )
            == 3
        )
        assert len(capture.observations) == 3
        serialized = "".join(event.model_dump_json() for event in events)
        serialized += "".join(
            observation.model_dump_json() for observation in capture.observations
        )
        for forbidden in (
            _SECRET_QUERY,
            _SECRET_ARGUMENT,
            "PROJECT-ZEPHYR",
            "CONFIDENTIAL",
            _SERVER,
            _READ_TOOL,
            "arguments",
            "credential",
            "https://",
            "/Users/",
        ):
            assert forbidden not in serialized, forbidden

    async def test_the_digest_is_stable_and_reveals_nothing(self) -> None:
        """Identical requests digest alike; a changed one does not."""

        first = digest_request({"query": _SECRET_QUERY, "limit": 5})
        again = digest_request({"query": _SECRET_QUERY, "limit": 5})
        other = digest_request({"query": "something else", "limit": 5})

        assert first == again
        assert first != other
        assert len(first) == 64
        assert _SECRET_QUERY not in first

    def test_an_unserialisable_request_is_still_digested(self) -> None:
        """A request with no canonical JSON form must not go unidentified."""

        digest = digest_request({"query": object()})

        assert len(digest) == 64


class TestObservationDoesNotChangeBehaviour(ObservedBridgeHarness):
    """Whatever the bridge would have answered, it still answers."""

    async def test_the_wrapper_returns_the_adapter_s_own_answer_object(self) -> None:
        answer = {"search": {"candidates": []}}
        stub = _AnswerStub(answer=answer)
        wrapped = ObservedCapabilityBridgeTool(
            inner=stub,
            observer=_CapturingObserver(),
            tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
        )

        returned = await wrapped.ainvoke({"query": "q"})

        assert returned is answer
        assert stub.calls == [{"query": "q"}]

    def test_the_wrapper_is_still_a_bridge_tool_adapter(self) -> None:
        wrapped = ObservedCapabilityBridgeTool(
            inner=_AnswerStub(answer={}, name="n", description="d"),
            observer=_CapturingObserver(),
            tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
        )

        assert isinstance(wrapped, CapabilityBridgeToolAdapter)
        assert (wrapped.name, wrapped.description) == ("n", "d")

    async def test_an_observer_that_raises_neither_fails_nor_alters_the_call(
        self,
    ) -> None:
        """The stated rule, tested at the seam rather than trusted."""

        answer = {"search": {"candidates": []}}
        observer = _ExplodingObserver()
        wrapped = ObservedCapabilityBridgeTool(
            inner=_AnswerStub(answer=answer),
            observer=observer,
            tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
        )

        returned = await wrapped.ainvoke({"query": "q"})

        assert returned is answer
        assert observer.calls == 1

    async def test_a_group_survives_a_broken_member_and_still_feeds_the_rest(
        self,
    ) -> None:
        """Metrics and decision lineage are independently useful, so they fail
        independently."""

        capture = _CapturingObserver()
        group = CapabilityDiscoveryObserverGroup(
            observers=(_ExplodingObserver(), capture)
        )

        await group.observe(_observation())

        assert len(capture.observations) == 1

    async def test_an_adapter_that_raises_still_raises(self) -> None:
        """Swallowing the adapter's own failure would be changing behaviour."""

        capture = _CapturingObserver()
        wrapped = ObservedCapabilityBridgeTool(
            inner=_RaisingStub(),
            observer=capture,
            tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
        )

        with pytest.raises(RuntimeError, match="connector exploded"):
            await wrapped.ainvoke({"query": "q"})

        assert [item.outcome for item in capture.observations] == [
            CapabilityDiscoveryOutcome.TOOL_RAISED
        ]

    def test_mounting_an_observer_does_not_move_the_model_facing_surface(
        self,
    ) -> None:
        """The three bridge schemas are a measured prompt cost; observation is
        not allowed to touch them."""

        context = self.context()
        catalog = self.catalog(context)
        _client, loader, dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        executor = GatewayCapabilityExecutor(
            bindings=seam.disclosure,
            loader=loader,
            dispatcher=dispatcher,
        )
        revalidation = self.revalidation(context, catalog)

        def signature(*, observer: CapabilityDiscoveryObserver | None):  # type: ignore[no-untyped-def]
            return tuple(
                (
                    registration.name.value,
                    registration.args_schema.model_json_schema(),
                    registration.adapter.name,
                    registration.adapter.description,
                )
                for registration in CapabilityBridgeRegistrar.registrations_for(
                    activation=CapabilityActivationResolver().resolve_configured(
                        raw_mode=FeatureMode.ENFORCE.value,
                        raw_activation=CapabilityActivationMode.DEFERRED.value,
                    ),
                    catalog=catalog,
                    runtime_context=context,
                    executor=executor,
                    revalidation=revalidation,
                    seam=seam,
                    observer=observer,
                    clock=lambda: _NOW,
                )
            )

        assert signature(observer=_CapturingObserver()) == signature(observer=None)

    async def test_the_observed_chain_still_searches_describes_and_invokes(
        self,
    ) -> None:
        """The BUG-08 chain assertion, re-run with telemetry mounted."""

        context = self.context()
        capture = _CapturingObserver()
        adapters, client, _seam, _catalog = await self.observed(context, capture)

        result = await self.drive_chain(adapters, context)

        assert "error" not in result["search"], result["search"]
        assert "error" not in result["describe"], result["describe"]
        assert "error" not in result["invoke"], result["invoke"]
        assert client.calls == [(_READ_TOOL, {"team": _SECRET_ARGUMENT})]
        assert [item.phase for item in capture.observations] == [
            CapabilityDiscoveryPhase.SEARCH,
            CapabilityDiscoveryPhase.DESCRIBE,
            CapabilityDiscoveryPhase.INVOKE,
        ]
        assert all(
            item.outcome is CapabilityDiscoveryOutcome.OK
            for item in capture.observations
        )

    async def test_every_bridge_call_counts_as_exactly_one_model_turn(self) -> None:
        """The PRD's budget rule: one bridge call is one model-visible F4 call."""

        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(context, capture)

        await self.drive_chain(adapters, context)

        assert [item.model_turns for item in capture.observations] == [1, 1, 1]
        assert all(item.result_tokens > 0 for item in capture.observations)
        assert capture.observations[0].candidate_count >= 1


class TestUnauthorizedNamesAreNotObservable(ObservedBridgeHarness):
    """Probing an unauthorized name is recorded, and recorded as not found."""

    async def test_search_describe_and_invoke_all_record_capability_not_found(
        self,
    ) -> None:
        """The Step 8 exit criterion, asserted through the telemetry it emits.

        The same closed outcome answers all three probes, so the recorded
        decision lineage is no more of an existence oracle than the model-facing
        answer is.
        """

        context = self.context()
        capture = _CapturingObserver()
        adapters, client, _seam, _catalog = await self.observed(context, capture)
        unauthorized = "cap_" + "0" * 32

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": "payroll wire transfer approvals", "limit": 10})
        described = await adapters[
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value
        ].ainvoke({"capability_ref": unauthorized})
        invoked = await adapters[
            CapabilityBridgeToolName.INVOKE_CAPABILITY.value
        ].ainvoke({"capability_ref": unauthorized, "arguments": {}})

        assert described["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND.value
        )
        assert invoked["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND.value
        )
        assert client.calls == []
        assert not any(
            candidate["capability_ref"] == unauthorized
            for candidate in found["search"]["candidates"]
        )
        assert [item.outcome for item in capture.observations] == [
            CapabilityDiscoveryOutcome.OK,
            CapabilityDiscoveryOutcome.CAPABILITY_NOT_FOUND,
            CapabilityDiscoveryOutcome.CAPABILITY_NOT_FOUND,
        ]


class TestMetricLabelsAreBounded:
    """The cardinality rule, enforced by driving every outcome there is."""

    async def test_no_label_value_is_ever_outside_a_closed_vocabulary(self) -> None:
        metrics = _RecordingMetrics()

        for tool in CapabilityBridgeToolName:
            for outcome in CapabilityDiscoveryOutcome:
                await metrics.observe(
                    _observation(
                        phase=CapabilityDiscoveryPhase.for_tool(tool),
                        tool=tool,
                        outcome=outcome,
                    )
                )
        for state in CapabilityExpansionState:
            await metrics.observe_expansion(
                CapabilityExpansionObservation(
                    latency_ms=5,
                    servers_by_state={state: 1},
                )
            )

        tools = {member.value for member in CapabilityBridgeToolName}
        outcomes = {member.value for member in CapabilityDiscoveryOutcome}
        states = {member.value for member in CapabilityExpansionState}
        for recorded in metrics.recorded:
            assert set(recorded.labels) <= CapabilityDiscoveryMetrics.LABEL_KEYS
            assert recorded.labels.get("tool", next(iter(tools))) in tools
            assert recorded.labels.get("outcome", "ok") in outcomes
            assert recorded.labels.get("state", "expanded") in states

    async def test_the_whole_signal_set_is_bounded_by_a_small_series_count(
        self,
    ) -> None:
        """The number that decides whether this lane can cause an outage.

        3 tools x 9 outcomes for the decision counter, 3 tools each for the
        turn/latency/token/candidate signals, 3 states plus one unlabelled
        histogram for expansion. Stated as a hard ceiling so a label added
        without thought fails here instead of in production.
        """

        metrics = _RecordingMetrics()

        for tool in CapabilityBridgeToolName:
            for outcome in CapabilityDiscoveryOutcome:
                await metrics.observe(
                    _observation(
                        phase=CapabilityDiscoveryPhase.for_tool(tool),
                        tool=tool,
                        outcome=outcome,
                    )
                )
        for state in CapabilityExpansionState:
            await metrics.observe_expansion(
                CapabilityExpansionObservation(
                    latency_ms=5,
                    servers_by_state={state: 1},
                )
            )

        series = {
            (recorded.name, tuple(sorted(recorded.labels.items())))
            for recorded in metrics.recorded
        }
        assert len(series) <= 64
        assert not any(
            "cap_" in value or "://" in value
            for recorded in metrics.recorded
            for value in recorded.labels.values()
        )

    async def test_a_metrics_facade_without_otel_records_nothing_and_raises_nothing(
        self,
    ) -> None:
        """Absent OTel degrades to silence; it must never degrade to an error."""

        metrics = CapabilityDiscoveryMetrics()
        metrics._meter = None  # noqa: SLF001 - the no-OTel state, set directly

        await metrics.observe(_observation())
        await metrics.observe_expansion(CapabilityExpansionObservation(latency_ms=1))

    async def test_search_is_the_only_phase_that_reports_candidates(self) -> None:
        metrics = _RecordingMetrics()

        await metrics.observe(
            _observation(
                phase=CapabilityDiscoveryPhase.DESCRIBE,
                tool=CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
            )
        )

        assert not any(
            recorded.name == "capability_discovery_candidates"
            for recorded in metrics.recorded
        )


class TestTheOutcomeVocabularyStaysClosed:
    """Guards that fail loudly when the bridge contract moves underneath us."""

    def test_every_bridge_error_code_has_an_outcome_member(self) -> None:
        """A new bridge failure class must not silently become ``unrecognized``."""

        codes = {member.value for member in CapabilityDiscoveryErrorCode}
        outcomes = {member.value for member in CapabilityDiscoveryOutcome}

        assert codes <= outcomes

    def test_every_bridge_tool_has_a_decision_kind(self) -> None:
        for tool in CapabilityBridgeToolName:
            assert CapabilityDiscoveryPhase.for_tool(tool) is not None

    def test_the_success_keys_match_the_real_result_envelopes(self) -> None:
        """Pins the classifier to the contracts rather than to a memory of them.

        If a success payload key is renamed in ``contracts.py``, every answered
        call would start being classified ``unrecognized``. That is a silent
        telemetry outage, so it is caught here.
        """

        assert "search" in CapabilitySearchToolResult.model_fields
        assert "description" in CapabilityDescribeToolResult.model_fields
        assert "invocation" in CapabilityInvokeToolResult.model_fields

        assert (
            CapabilityDiscoveryOutcome.for_answer(
                tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
                answer={"search": {}},
            )
            is CapabilityDiscoveryOutcome.OK
        )
        assert (
            CapabilityDiscoveryOutcome.for_answer(
                tool=CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
                answer={"description": {}},
            )
            is CapabilityDiscoveryOutcome.OK
        )
        assert (
            CapabilityDiscoveryOutcome.for_answer(
                tool=CapabilityBridgeToolName.INVOKE_CAPABILITY,
                answer={"invocation": {}},
            )
            is CapabilityDiscoveryOutcome.OK
        )

    @pytest.mark.parametrize(
        "answer",
        [
            "not a mapping",
            {"error": "not a mapping"},
            {"error": {"code": "something_new_from_downstream"}},
            {"unexpected": {}},
        ],
    )
    def test_an_unknown_answer_shape_becomes_one_bounded_label(
        self,
        answer: object,
    ) -> None:
        """The cardinality failure mode, closed at its source."""

        assert (
            CapabilityDiscoveryOutcome.for_answer(
                tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
                answer=answer,
            )
            is CapabilityDiscoveryOutcome.UNRECOGNIZED
        )

    def test_an_answer_with_no_content_costs_no_tokens(self) -> None:
        assert estimate_answer_tokens({}) >= 0
        assert estimate_answer_tokens(object()) == 0
        assert estimate_answer_tokens({"search": {"candidates": []}}) > 0


class TestDecisionsRideTheExistingEventFamily(ObservedBridgeHarness):
    """No new event family: these are ordinary ``quality.decision.v1`` rows."""

    async def test_each_bridge_call_appends_one_f3_quality_decision(self) -> None:
        store, context, recorder = await _bound_run(self)
        adapters, _client, _seam, _catalog = await self.observed(context, recorder)

        await self.drive_chain(adapters, context)

        decisions = [
            event
            for event in await store.list_events_after(
                org_id=_ORG,
                run_id=context.run_id,
                after_sequence=0,
            )
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
        ]
        assert len(decisions) == 3
        assert [event.payload["phase"] for event in decisions] == [
            CapabilityDiscoveryPhase.SEARCH.value,
            CapabilityDiscoveryPhase.DESCRIBE.value,
            CapabilityDiscoveryPhase.INVOKE.value,
        ]
        assert all(
            event.payload["feature"]
            == AgentQualityFeature.F3_CAPABILITY_DISCOVERY.value
            for event in decisions
        )
        assert all(
            event.payload["outcome_code"] == CapabilityDiscoveryOutcome.OK.value
            for event in decisions
        )

    async def test_the_rows_survive_the_projector_that_guards_the_family(
        self,
    ) -> None:
        """A payload the strict projector would drop is not a durable decision."""

        store, context, recorder = await _bound_run(self)
        adapters, _client, _seam, _catalog = await self.observed(context, recorder)

        await self.drive_chain(adapters, context)

        decisions = [
            event
            for event in await store.list_events_after(
                org_id=_ORG,
                run_id=context.run_id,
                after_sequence=0,
            )
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
        ]
        assert decisions
        for event in decisions:
            assert (
                RuntimeEventPresentationProjector.payload_for_event(
                    event_type=event.event_type,
                    payload=event.payload,
                )
                == event.payload
            )

    async def test_two_identical_searches_are_two_decisions(self) -> None:
        """A digest-derived id would collapse repetition; an ordinal does not."""

        store, context, recorder = await _bound_run(self)
        adapters, _client, _seam, _catalog = await self.observed(context, recorder)
        search = adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]

        await search.ainvoke({"query": _SECRET_QUERY, "limit": 10})
        await search.ainvoke({"query": _SECRET_QUERY, "limit": 10})

        decisions = [
            event
            for event in await store.list_events_after(
                org_id=_ORG,
                run_id=context.run_id,
                after_sequence=0,
            )
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
        ]
        assert len(decisions) == 2
        assert (
            decisions[0].payload["input_digest"]
            == (decisions[1].payload["input_digest"])
        )
        assert (
            decisions[0].payload["decision_id"] != (decisions[1].payload["decision_id"])
        )

    async def test_an_unreachable_journal_does_not_fail_the_bridge(self) -> None:
        """A run that cannot record its decisions still answers the model."""

        context = self.context()
        recorder = RunJournalDiscoveryDecisionRecorder(
            store=_BrokenStore(),
            org_id=_ORG,
            run_id=context.run_id,
            trace_id="trace_bug08",
            subject_fingerprint=_SUBJECT,
            snapshot_id="snapshot-telemetry",
            policy_revision="capability-r1",
        )
        adapters, _client, _seam, _catalog = await self.observed(context, recorder)

        result = await self.drive_chain(adapters, context)

        assert "error" not in result["search"], result["search"]
        assert "error" not in result["invoke"], result["invoke"]


class TestExpansionCostIsMeasuredWhereItHappens(ObservedBridgeHarness):
    """Tier two is the only place the cost of opening a server is visible."""

    async def test_a_real_expansion_reports_its_bounded_server_outcomes(
        self,
    ) -> None:
        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(
            context,
            capture,
            expansion_observer=capture,
        )

        await adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value].ainvoke(
            {"query": _SECRET_QUERY, "limit": 10}
        )

        assert len(capture.expansions) == 1
        expansion = capture.expansions[0]
        assert expansion.admitted_count >= 1
        assert expansion.capability_count >= 1
        assert expansion.servers_by_state.get(CapabilityExpansionState.EXPANDED) == 1
        assert _SECRET_QUERY not in expansion.model_dump_json()

    async def test_composing_without_an_observer_leaves_the_second_tier_alone(
        self,
    ) -> None:
        """Telemetry is opt-in; the unmeasured composition is still the default."""

        context = self.context()
        catalog = self.catalog(context)
        _client, loader, _dispatcher = self.mcp(context)

        plain = CapabilityBridgeSeam.compose(
            catalog=catalog,
            loader=loader,
            minter=HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY),
        )

        assert type(plain.expansion).__name__ == "TwoTierCapabilitySearch"

    async def test_an_expansion_observer_that_raises_does_not_fail_the_search(
        self,
    ) -> None:
        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(
            context,
            capture,
            expansion_observer=_ExplodingExpansionObserver(),
        )

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _SECRET_QUERY, "limit": 10})

        assert "error" not in found, found


class TestSelectionRankIsMeasuredNotAssumed(ObservedBridgeHarness):
    """BUG-14b — the one count that spans two bridge calls.

    The rest of the numeric extension is read off a single answer. The rank is
    not: it is a fact about a reference a search offered and a *later* call
    chose, so it only exists if the two calls share something. These are the
    properties of that shared thing.
    """

    async def test_a_search_offers_and_reports_no_rank_of_its_own(self) -> None:
        """Offering ten references is not selecting one.

        Reporting a rank here — "my best candidate is at position 1" — would be
        true and useless, and because trajectory recall is scored over the
        *lowest* positive rank it would also mask a genuinely bad rank reported
        later by the call that did the selecting.
        """

        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(context, capture)

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": "linear issues", "limit": 10})

        assert found["search"]["candidates"]
        assert capture.observations[0].selection_rank is None

    async def test_a_describe_reports_where_the_search_offered_the_reference(
        self,
    ) -> None:
        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(context, capture)

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": "linear issues", "limit": 10})
        second = found["search"]["candidates"][1]["capability_ref"]
        await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
            {"capability_ref": second}
        )

        assert capture.observations[1].selection_rank == 2

    async def test_a_reference_no_search_offered_reports_zero_not_nothing(
        self,
    ) -> None:
        """``0`` is the guessed-reference measurement the probe case grades."""

        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(context, capture)

        await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
            {"capability_ref": "cap_" + "0" * 32}
        )

        assert capture.observations[0].selection_rank == 0

    async def test_a_request_with_no_readable_reference_reports_nothing(self) -> None:
        """An unparseable request selected nothing; it did not select a miss."""

        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(context, capture)

        await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
            {"not_a_reference": 1}
        )

        assert capture.observations[0].selection_rank is None

    async def test_two_runs_do_not_pool_their_offers(self) -> None:
        """Correlation is keyed on the shared observer, so it is run-scoped.

        The second run describes a reference the *first* run's search offered.
        If the map were process-wide it would answer 1; it answers 0, because
        that reference was never offered in the run doing the describing.
        """

        context = self.context()
        first = _CapturingObserver()
        first_adapters, _c1, _s1, _cat1 = await self.observed(context, first)
        found = await first_adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": "linear issues", "limit": 10})
        ref = found["search"]["candidates"][0]["capability_ref"]

        second = _CapturingObserver()
        second_adapters, _c2, _s2, _cat2 = await self.observed(context, second)
        await second_adapters[
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value
        ].ainvoke({"capability_ref": ref})

        assert first.observations[0].selection_rank is None
        assert second.observations[0].selection_rank == 0

    async def test_the_first_search_to_offer_a_reference_owns_its_rank(self) -> None:
        """A run cannot improve its own recall by searching again.

        The second search puts the reference first. The rank stays the one it
        held when it actually became selectable to the model.
        """

        context = self.context()
        capture = _CapturingObserver()
        adapters, _client, _seam, _catalog = await self.observed(context, capture)
        search = adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]

        found = await search.ainvoke({"query": "linear issues", "limit": 10})
        second = found["search"]["candidates"][1]["capability_ref"]
        await search.ainvoke({"query": "list issues", "limit": 1})
        await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
            {"capability_ref": second}
        )

        assert capture.observations[-1].selection_rank == 2

    def test_a_rank_past_the_records_ceiling_is_clamped_not_dropped(self) -> None:
        """Clamping fails closed; dropping and refusing both fail open.

        A dropped or refused rank reads as *not observed*, and a ``maximum_``
        bound over an unobserved quantity is either satisfied by absence or
        refuses to grade. A clamped one is still far past any ceiling a case
        declares, so the bound it should fail is the bound it fails.
        """

        correlator = CapabilitySelectionCorrelator()
        ceiling = DECISION_COUNT_CEILINGS["selection_rank"]

        correlator.record_offer([f"cap_{index:032x}" for index in range(ceiling + 5)])

        assert correlator.rank_for(f"cap_{ceiling + 3:032x}") == ceiling
        assert (
            CapabilityDiscoveryObservation(
                phase=CapabilityDiscoveryPhase.INVOKE,
                tool=CapabilityBridgeToolName.INVOKE_CAPABILITY,
                outcome=CapabilityDiscoveryOutcome.OK,
                input_digest="a" * 64,
                latency_ms=1,
                selection_rank=ceiling,
            ).selection_rank
            == ceiling
        )

    def test_the_correlator_stops_growing_rather_than_leaking(self) -> None:
        """Observation must not turn a pathological run into a memory problem."""

        correlator = CapabilitySelectionCorrelator()

        correlator.record_offer([f"cap_{index:032x}" for index in range(5_000)])

        assert correlator.rank_for(f"cap_{4_999:032x}") == 0

    async def test_a_broken_correlation_neither_fails_nor_alters_the_call(
        self,
    ) -> None:
        """An answer this module cannot read is a missing rank, not a failure."""

        capture = _CapturingObserver()
        wrapped = ObservedCapabilityBridgeTool(
            inner=_StubTool({"search": {"candidates": "not-a-list"}}),
            observer=capture,
            tool=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
        )

        answer = await wrapped.ainvoke({"query": "q"})

        assert answer == {"search": {"candidates": "not-a-list"}}
        assert capture.observations[0].selection_rank is None


class TestEveryEmittedCountIsInsideTheRecordsBounds(ObservedBridgeHarness):
    """A producer the durable record would refuse writes no row at all."""

    async def test_a_real_chain_writes_a_row_for_every_call(self) -> None:
        """The end-to-end statement: nothing measured was rejected."""

        store, context, recorder = await _bound_run(self)
        adapters, _client, _seam, _catalog = await self.observed(context, recorder)

        await self.drive_chain(adapters, context)

        decisions = [
            event
            for event in await store.list_events_after(
                org_id=_ORG,
                run_id=context.run_id,
                after_sequence=0,
            )
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
        ]
        assert len(decisions) == 3
        for event in decisions:
            for name, ceiling in DECISION_COUNT_CEILINGS.items():
                value = event.payload[name]
                assert value is None or 0 <= value <= ceiling, (name, value)


@dataclass
class _StubTool:
    """An adapter that answers with one fixed object."""

    answer: Any
    name: str = "search_capabilities"
    description: str = "stub"

    async def ainvoke(self, raw_input: Any) -> Any:
        del raw_input
        return self.answer


@dataclass
class _ExplodingExpansionObserver:
    async def observe_expansion(
        self,
        observation: CapabilityExpansionObservation,
    ) -> None:
        del observation
        raise RuntimeError("meter is down")


class _BrokenStore:
    """A decision store that is never available."""

    async def append(self, write: object) -> None:
        del write
        raise RuntimeError("postgres://secret-host/run_control")

    async def list_for_run(self, **_kwargs: object) -> tuple[()]:
        raise RuntimeError("postgres://secret-host/run_control")


async def _bound_run(harness: ObservedBridgeHarness):  # type: ignore[no-untyped-def]
    """Create a real conversation, run, and bound control snapshot.

    The canonical journal only replays events for a run it actually holds, so a
    hand-built store would let every append fail silently and every assertion
    below pass for the wrong reason. Going through the real coordinators is what
    makes "the decision landed in the run's own journal" a claim rather than a
    hope.
    """

    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=None,
        ),
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )
    conversation_coordinator = ConversationCoordinator(
        persistence=store,
        settings=settings,
        run_coordinator=run_coordinator,
    )
    conversation = await conversation_coordinator.create_conversation(
        CreateConversationRequest(
            org_id=_ORG,
            user_id=_USER,
            assistant_id="assistant",
        )
    )
    run = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input="Exercise F3 discovery telemetry.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    context = harness.context(run_id=run.run_id, user_id=_USER, org_id=_ORG)
    recorder = await _journal_recorder(
        store,
        context,
        conversation_id=conversation.conversation_id,
    )
    return store, context, recorder


async def _journal_recorder(
    store: InMemoryRuntimeApiStore,
    context,  # type: ignore[no-untyped-def]
    *,
    conversation_id: str,
) -> RunJournalDiscoveryDecisionRecorder:
    """Bind a real control snapshot so real decisions can be appended to it."""

    controls = EventJournalRunControlStore(store)
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-telemetry",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=24,
    )
    snapshot = RunControlSnapshot.create(
        run_id=context.run_id,
        conversation_id=conversation_id,
        subject_fingerprint=_SUBJECT,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness://stable/r1",
        task_policy_selection_ref="task-policy://unknown.general/r1",
        policy_revisions=RunPolicyRevisions(
            prompt="prompt-r1",
            capability="capability-r1",
            context="context-r1",
            tool_controller="tool-r1",
            concurrency="concurrency-r1",
            dataflow="dataflow-r1",
            mcp_freshness="mcp-r1",
            delegation="delegation-r1",
            model_route="model-r1",
            workspace_edit="workspace-r1",
            answer_verification="answer-r1",
        ),
        feature_modes=FeatureModeSet(f3=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id="snapshot-telemetry",
        created_at=datetime(2026, 7, 29, 11, tzinfo=UTC),
    )
    await controls.get_or_create(
        RunControlSnapshotWrite(
            org_id=_ORG,
            trace_id=context.trace_id or "trace_bug08",
            snapshot=snapshot,
        )
    )
    return RunJournalDiscoveryDecisionRecorder(
        store=controls,
        org_id=_ORG,
        run_id=context.run_id,
        trace_id=context.trace_id or "trace_bug08",
        subject_fingerprint=_SUBJECT,
        snapshot_id=snapshot.snapshot_id,
        policy_revision="capability-r1",
        clock=lambda: datetime(2026, 7, 29, 12, tzinfo=UTC) + timedelta(seconds=1),
    )
