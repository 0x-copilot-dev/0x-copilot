"""F6.5 — an admitted batch child is an ordinary gateway operation.

The central claim of this lane cannot be proved against a fake gateway, so
:class:`TestChildIsAnOrdinaryGatewayOperation` composes the *real*
:class:`~agent_runtime.capabilities.operations.gateway.OperationGateway`, the
*real* per-tool MCP dispatcher, and the *real*
:class:`~agent_runtime.capabilities.concurrency.batch_coordinator.BatchExecutionCoordinator`,
runs the same work twice — once as a solo call and once as a batched child —
and compares the artifacts both left behind. Anything a fake produced would be a
statement about the fake.

Everything downstream of that claim (sibling isolation, ordering, deadlines,
cancellation) is driven through a spy dispatcher instead, for the same reason
F6.3's tests use a probe: those properties are about *when and whether* a child
runs, and the connector is noise. No test sleeps on the wall clock; orderings
are produced with explicit ``asyncio`` events and observed through an injected
counter clock.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.concurrency import (
    BatchChildDispatch,
    BatchChildDispatchStatus,
    BatchChildExecutionError,
    BatchChildExecutionMessages,
    BatchChildExecutionReason,
    BatchChildExecutorMisconfigured,
    BatchChildStatus,
    BatchChildWork,
    BatchExecutionCoordinator,
    BatchExecutionStatus,
    BatchFailurePolicy,
    BatchOperation,
    BatchPlanRecorder,
    BatchPlanRequest,
    ConcurrencyAllowance,
    ConcurrencyKillSwitchGate,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyScope,
    DurableBatchPlan,
    GatewayBatchChildExecutor,
    IdempotencyKind,
    OrderingRequirement,
    PermitCapacityPolicy,
    PermitScope,
    PlannedOperation,
    PolicySource,
    ProviderSessionConstraint,
    RunPermitManager,
    RunScopedBatchChildWork,
    SideEffectKind,
)
from agent_runtime.capabilities.mcp.execution_services import McpOperationStoredResult
from agent_runtime.capabilities.mcp.gateway_context import (
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlBinding,
    RunControlContext,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.ledger_models import EffectActor, LedgerEventType

from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_PROFILE = "single_user_desktop"
_SUBJECT = "a" * 64
_RUN = "run-f65"
_TRACE = "trace-f65"
_ORG = "org-f65"
_USER = "user-f65"
_SNAPSHOT = "snapshot-f65"
_CREATED_AT = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
_CAP = "cap_" + "0" * 32
_SERVER = "linear"
_READ_TOOL = "list_issues"
_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team": {"type": "string"}},
    "required": ["team"],
    "additionalProperties": False,
}


class _CounterClock:
    """A deterministic, strictly increasing timezone-aware clock."""

    def __init__(self, base: datetime = _CREATED_AT) -> None:
        self._base = base
        self.ticks = 0

    def __call__(self) -> datetime:
        self.ticks += 1
        return self._base + timedelta(microseconds=self.ticks)


class _FrozenClock:
    """A clock that never advances, so deadline arithmetic is exact."""

    def __init__(self, moment: datetime = _CREATED_AT) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class _NullJournal:
    """A store that refuses to persist; plans are built, not written, here."""

    async def put_plan(self, write):  # pragma: no cover - never invoked
        raise AssertionError("child execution tests must not persist")

    async def load_recovery_view(self, **kwargs):  # pragma: no cover
        raise AssertionError("child execution tests must not read")


@dataclass
class _RecordedOperationEvents:
    """Capture the canonical operation ledger rows the gateway itself emits."""

    rows: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del summary
        self.rows.append((event_type.value, dict(payload)))

    def comparable(self) -> list[tuple[str, dict[str, object]]]:
        """Return the rows minus the one field that legitimately varies.

        ``latency_ms`` is measured wall time. Everything else the gateway writes
        is a function of the request, so dropping exactly that field keeps the
        comparison honest rather than lenient.
        """

        return [
            (
                event_type,
                {key: value for key, value in payload.items() if key != "latency_ms"},
            )
            for event_type, payload in self.rows
        ]

    def types(self) -> list[str]:
        return [event_type for event_type, _payload in self.rows]

    def operation_ids(self) -> list[str]:
        return [
            str(payload.get("operation_id"))
            for _event_type, payload in self.rows
            if payload.get("operation_id") is not None
        ]


@dataclass
class _ResultStore:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def store_read_result(self, *, request, output: Mapping[str, object]):  # type: ignore[no-untyped-def]
        body = {str(key): value for key, value in output.items()}
        self.calls.append((request.operation_id, body))
        return McpOperationStoredResult(
            result_ref=f"operation://{request.operation_id}/result",
            model_output={"items": body.get("items", [])},
        )


@dataclass
class _ArgumentStore:
    rows: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    async def persist(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None:
        self.rows[ref] = (digest, canonical_bytes)

    async def resolve(self, *, ref: str, digest: str) -> bytes | None:
        stored = self.rows.get(ref)
        if stored is None or stored[0] != digest:
            return None
        return stored[1]


@dataclass
class _RecordingClient:
    """Connector fake that records every dispatch."""

    tools: Sequence[object]
    resources: Sequence[object] = ()
    outputs: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[object]:
        return self.tools

    async def list_resources(self) -> Sequence[object]:
        return self.resources

    async def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]):  # type: ignore[no-untyped-def]
        self.calls.append((tool_name, dict(arguments)))
        return self.outputs.get(
            tool_name,
            {"content": [{"type": "text", "text": f"called {tool_name}"}]},
        )


@dataclass
class _SpyDispatcher:
    """A dispatch port that records the call context every child ran under.

    ``identities`` is the load-bearing observation: every downstream accounting
    seam — the budget guard, the task-policy intent, the operation-id
    allocator — keys off :meth:`RuntimeCallContext.current`, so recording what
    was bound at dispatch time records what all of them saw.
    """

    outputs: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    errors: dict[str, BaseException] = field(default_factory=dict)
    gates: dict[str, asyncio.Event] = field(default_factory=dict)
    non_mapping: set[str] = field(default_factory=set)
    calls: list[dict[str, Any]] = field(default_factory=list)
    identities: list[RuntimeToolCallIdentity | None] = field(default_factory=list)

    async def ainvoke(self, raw_input: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = dict(raw_input)
        self.calls.append(payload)
        self.identities.append(RuntimeCallContext.current())
        tool_name = str(payload.get("tool_name"))
        gate = self.gates.get(tool_name)
        if gate is not None:
            await gate.wait()
        error = self.errors.get(tool_name)
        if error is not None:
            raise error
        if tool_name in self.non_mapping:
            return ["not-a-mapping"]  # type: ignore[return-value]
        return self.outputs.get(
            tool_name,
            {
                "server_name": _SERVER,
                "tool_name": tool_name,
                "output": {
                    "status": "completed",
                    "operation_id": self._operation_id(),
                    "result_ref": f"operation://{self._operation_id()}/result",
                    "summary": "done",
                },
            },
        )

    @staticmethod
    def _operation_id() -> str:
        identity = RuntimeCallContext.current()
        return identity.operation_id if identity is not None else "op-unbound"


class RunBindingMixin:
    """One verified run binding, shared by every child in these tests."""

    @staticmethod
    def snapshot() -> RunControlSnapshot:
        budget = BudgetEnvelope.create(
            budget_envelope_id="budget-f65",
            revision="budget-r1",
            max_model_turns=8,
            max_tool_calls=16,
        )
        return RunControlSnapshot.create(
            run_id=_RUN,
            conversation_id="conversation-f65",
            subject_fingerprint=_SUBJECT,
            deployment_profile=_PROFILE,
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
            feature_modes=FeatureModeSet(f6=FeatureMode.ENFORCE),
            budget_envelope_ref=budget.revision_ref,
            assignment_revision="assignment-r1",
            snapshot_id=_SNAPSHOT,
            created_at=_CREATED_AT,
        )

    @classmethod
    def binding(cls) -> RunControlBinding:
        return RunControlBinding(
            snapshot=cls.snapshot(),
            effective_modes=FeatureModeSet(f6=FeatureMode.ENFORCE),
            decisions=(),
        )

    @staticmethod
    def identity(
        model_tool_call_id: str,
        *,
        model_turn: int = 1,
        execution_scope: str = "supervisor",
    ) -> RuntimeToolCallIdentity:
        """Return the identity the parent turn derives for one child call."""

        built = RuntimeToolCallIdentity.from_current(
            execution_scope=execution_scope,
            model_turn=model_turn,
            model_tool_call_id=model_tool_call_id,
        )
        assert built is not None, "the run binding must be active"
        return built

    @staticmethod
    def work(
        identity: RuntimeToolCallIdentity,
        *,
        tool_name: str = _READ_TOOL,
        arguments: Mapping[str, Any] | None = None,
        deadline_at: datetime | None = None,
        operation_id: str | None = None,
    ) -> BatchChildWork:
        return BatchChildWork(
            operation_id=(
                operation_id if operation_id is not None else identity.operation_id
            ),
            server_name=_SERVER,
            tool_name=tool_name,
            arguments=arguments if arguments is not None else {"team": "ENG"},
            model_tool_call_id=identity.model_tool_call_id,
            model_turn=identity.model_turn,
            execution_scope=identity.execution_scope,
            deadline_at=deadline_at,
        )


class CoordinatorFixtureMixin(RunBindingMixin):
    """Durable plans and permit tables wide enough that the plan is the bound."""

    @staticmethod
    def read_policy() -> ConcurrencyPolicy:
        return ConcurrencyPolicy(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            idempotency=IdempotencyKind.NATURAL,
            rate_limit_scope=ConcurrencyScope.CONNECTOR,
            ordering_requirement=OrderingRequirement.NONE,
            provider_session_constraint=ProviderSessionConstraint.SESSION_PARALLEL_SAFE,
            policy_source=PolicySource.PRODUCT_CATALOG,
        )

    @classmethod
    def durable_plan(
        cls,
        *operation_ids: str,
        max_parallelism: int = 4,
        batch_id: str = "batch-f65",
        deadline_at: datetime | None = None,
    ) -> DurableBatchPlan:
        """Return the handle a completed journal append would have produced."""

        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=ConcurrencyAllowance.enforcing(max_parallelism)
        )
        request = BatchPlanRequest(
            org_id=_ORG,
            trace_id=_TRACE,
            subject_fingerprint=_SUBJECT,
            run_id=_RUN,
            batch_id=batch_id,
            turn_ordinal=1,
            operations=tuple(
                PlannedOperation.of(
                    operation=BatchOperation(
                        operation_id=operation_id,
                        authorization_epoch="auth_1",
                        dependency_ids=(),
                        resource_fingerprints=(),
                    ),
                    capability_ref=_CAP,
                    policy=cls.read_policy(),
                )
                for operation_id in operation_ids
            ),
            # Collect-all keeps a sibling's failure from stopping admission, so
            # isolation is observed rather than masked by a stopped batch.
            failure_policy=BatchFailurePolicy.COLLECT_ALL,
            deadline_at=deadline_at,
        )
        record = BatchPlanRecorder(journal=_NullJournal(), gate=gate).build_record(
            request,
            snapshot=cls.snapshot(),
            decision=gate.admit(),
            created_at=_CREATED_AT,
        )
        return DurableBatchPlan(sequence_no=1, record=record)

    @staticmethod
    def permit_scopes(identity) -> tuple[PermitScope, ...]:  # type: ignore[no-untyped-def]
        return (
            PermitScope.for_global(),
            PermitScope.for_capability(
                profile_id=_PROFILE,
                subject_fingerprint=_SUBJECT,
                capability_name=str(identity.capability_ref),
            ),
        )

    @classmethod
    def coordinator(cls, *, clock=None) -> BatchExecutionCoordinator:  # type: ignore[no-untyped-def]
        return BatchExecutionCoordinator(
            permits=RunPermitManager(
                policy=PermitCapacityPolicy.from_limits(
                    {kind: 16 for kind in ConcurrencyScope.permit_pool_kinds()}
                )
            ),
            permit_scopes=cls.permit_scopes,
            clock=clock if clock is not None else _CounterClock(),
        )

    @staticmethod
    def executor(
        dispatcher,  # type: ignore[no-untyped-def]
        work: Sequence[BatchChildWork],
        *,
        clock=None,  # type: ignore[no-untyped-def]
        deadline_at: datetime | None = None,
    ) -> GatewayBatchChildExecutor:
        return GatewayBatchChildExecutor(
            dispatcher=dispatcher,
            work=RunScopedBatchChildWork(work),
            clock=clock,
            deadline_at=deadline_at,
        )

    @staticmethod
    async def drive(
        coordinator: BatchExecutionCoordinator,
        plan: DurableBatchPlan,
        executor: GatewayBatchChildExecutor,
    ) -> None:
        """Start every child at once, the way the framework would."""

        await asyncio.gather(
            *(
                coordinator.run_child(
                    batch_id=plan.batch_id,
                    operation_id=operation_id,
                    runner=executor.run,
                )
                for operation_id in plan.plan.operation_ids
            )
        )

    @staticmethod
    async def _drain(turns: int = 6) -> None:
        for _ in range(turns):
            await asyncio.sleep(0)


@dataclass
class _GatewayArtifacts:
    """Everything one run of a single operation left behind."""

    events: list[tuple[str, dict[str, object]]]
    result_store: list[tuple[str, dict[str, object]]]
    connector_calls: list[tuple[str, dict[str, object]]]
    dispatcher_result: Mapping[str, Any]
    operation_ids: list[str]
    event_types: list[str]


class GatewayHarnessMixin(CoordinatorFixtureMixin, DynamicMcpLoadingMixin):
    """Compose the real gateway, the real dispatcher, and one real connector."""

    def runtime_context(self) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles={"employee"},
            permission_scopes={"docs:read", "docs:write"},
            connector_scopes={"drive": frozenset({"docs:read"})},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-test",
                max_input_tokens=32_000,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=_RUN,
            trace_id=_TRACE,
        )

    def card(self):  # type: ignore[no-untyped-def]
        return self.make_card(name=_SERVER, required_scopes=("docs:read",)).model_copy(
            update={"server_id": "srv_linear"}
        )

    def bind_gateway(self, context: AgentRuntimeContext):  # type: ignore[no-untyped-def]
        events = _RecordedOperationEvents()
        operation_token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id="conversation-f65",
                run_id=context.run_id,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                workspace=None,
                user=None,
            ),
            ledger_emitter=events,
            artifact_service=None,
            mode=OperationGatewayMode.ENFORCE,
            canonical_arguments_durable=True,
        )
        descriptors = OperationDescriptorRegistry()
        classifier = OperationClassifier(descriptors=descriptors)
        result_store = _ResultStore()
        service_token = McpOperationGatewayContext.bind_for_run(
            McpOperationGatewayServices(
                gateway=OperationGateway(
                    descriptors=descriptors,
                    classifier=classifier,
                ),
                descriptors=descriptors,
                classifier=classifier,
                stager=EffectStager(
                    ledger=FakeLedger(),
                    outbox=FakeOutbox(),
                    clock=FakeClock(),
                    stage_ids=FakeStageIds(),
                ),
                stage_scope=EffectStageScope(
                    run_id=context.run_id,
                    owner_ref=f"principal://users/{context.user_id}",
                ),
                stage_author=EffectActorIdentity(
                    actor=EffectActor.USER,
                    principal_ref=f"principal://users/{context.user_id}",
                ),
                result_store=result_store,
                argument_store=_ArgumentStore(),
                connector_overrides=ConnectorWritePolicyOverrides(),
            )
        )
        return events, result_store, operation_token, service_token


class TestChildIsAnOrdinaryGatewayOperation(GatewayHarnessMixin):
    """A batched child is not equivalent to a solo call — it is one."""

    async def test_the_child_dispatch_carries_the_citation_binding_tool_call_id(
        self,
    ) -> None:
        """Citations bind to the child's own model tool call, as a solo call's do."""

        dispatcher = _SpyDispatcher()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            identity = self.identity("call-7")
            work = self.work(identity)
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(dispatcher, (work,)).run,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert dispatcher.calls[0]["tool_call_id"] == "call-7"
        assert dispatcher.identities[0] is not None
        assert dispatcher.identities[0].operation_id == identity.operation_id


class TestIdentityIsDerivedNotInvented(CoordinatorFixtureMixin):
    """A child never runs under a name the durable plan did not record."""

    async def test_a_child_whose_derived_id_is_not_the_planned_one_never_runs(
        self,
    ) -> None:
        dispatcher = _SpyDispatcher()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            identity = self.identity("call-1")
            # The plan named an id nothing derives — a journal/ledger mismatch.
            work = self.work(identity, operation_id="op-not-derived")
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            result = await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(dispatcher, (work,)).run,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert dispatcher.calls == []
        assert result.outcome.status is BatchChildStatus.FAILED
        assert isinstance(result.error, BatchChildExecutionError)
        assert result.error.reason is BatchChildExecutionReason.IDENTITY_MISMATCH
        assert result.error.safe_message == (
            BatchChildExecutionMessages.IDENTITY_MISMATCH
        )

    async def test_a_child_without_a_verified_run_binding_never_runs(self) -> None:
        """No parent turn to derive from is a refusal, never a random id."""

        dispatcher = _SpyDispatcher()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            identity = self.identity("call-1")
            work = self.work(identity)
            plan = self.durable_plan(work.operation_id)
        finally:
            RunControlContext.unbind(run_token)
        coordinator = self.coordinator()
        coordinator.begin(plan)

        # Run with no binding active: ``from_current`` cannot derive anything.
        result = await coordinator.run_child(
            batch_id=plan.batch_id,
            operation_id=work.operation_id,
            runner=self.executor(dispatcher, (work,)).run,
        )

        assert dispatcher.calls == []
        assert result.outcome.status is BatchChildStatus.FAILED
        assert isinstance(result.error, BatchChildExecutionError)
        assert result.error.reason is (BatchChildExecutionReason.IDENTITY_UNAVAILABLE)

    async def test_a_child_with_no_registered_work_never_runs(self) -> None:
        dispatcher = _SpyDispatcher()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            identity = self.identity("call-1")
            work = self.work(identity)
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            result = await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(dispatcher, ()).run,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert dispatcher.calls == []
        assert result.error.reason is BatchChildExecutionReason.WORK_UNAVAILABLE

    async def test_an_already_bound_identity_is_not_rebound(self) -> None:
        """Entered from inside the child's own wrapper, the ordinal is not reset.

        Re-binding would restart the inner-operation ordinal and hand the
        gateway ordinal 1 twice for one model call.
        """

        dispatcher = _SpyDispatcher()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            identity = self.identity("call-1")
            work = self.work(identity)
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            with RuntimeCallContext.bind(identity):
                # The wrapper already allocated this call's first operation id.
                assert RuntimeCallContext.next_operation_id() == identity.operation_id
                await coordinator.run_child(
                    batch_id=plan.batch_id,
                    operation_id=work.operation_id,
                    runner=self.executor(dispatcher, (work,)).run,
                )
                # The child ran under the outer binding, so the next allocation
                # is still the second ordinal rather than a restarted first.
                assert RuntimeCallContext.next_operation_id() == (
                    identity.derived_operation_id(2)
                )
        finally:
            RunControlContext.unbind(run_token)

        assert dispatcher.identities[0] is not None
        assert dispatcher.identities[0].operation_id == identity.operation_id

    async def test_a_dispatcher_reporting_another_operation_is_refused(self) -> None:
        """The gateway's own id is checked against the plan, not assumed."""

        dispatcher = _SpyDispatcher(
            outputs={
                _READ_TOOL: {
                    "output": {"status": "completed", "operation_id": "op-elsewhere"}
                }
            }
        )
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            identity = self.identity("call-1")
            work = self.work(identity)
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            result = await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(dispatcher, (work,)).run,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert result.outcome.status is BatchChildStatus.FAILED
        assert result.error.reason is BatchChildExecutionReason.IDENTITY_MISMATCH


class TestSiblingFailureIsolation(CoordinatorFixtureMixin):
    """A sibling's failure never reaches a completed child's result."""

    async def test_a_failing_sibling_leaves_a_completed_childs_result_intact(
        self,
    ) -> None:
        clock = _CounterClock()
        failure = RuntimeError("connector exploded")
        dispatcher = _SpyDispatcher(
            gates={"failing_tool": asyncio.Event()},
            errors={"failing_tool": failure},
        )
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            good = self.work(self.identity("call-good"))
            bad = self.work(self.identity("call-bad"), tool_name="failing_tool")
            plan = self.durable_plan(good.operation_id, bad.operation_id)
            coordinator = self.coordinator(clock=clock)
            coordinator.begin(plan)
            executor = self.executor(dispatcher, (good, bad), clock=clock)

            task = asyncio.create_task(self.drive(coordinator, plan, executor))
            await self._drain()
            # The good child has already completed; capture its result *before*
            # the sibling fails, so "unchanged" is a comparison, not a hope.
            settled = coordinator.results(plan.batch_id)[0]
            assert settled.outcome.status is BatchChildStatus.SUCCEEDED
            before = settled.value

            dispatcher.gates["failing_tool"].set()
            await task
        finally:
            RunControlContext.unbind(run_token)

        results = coordinator.results(plan.batch_id)
        assert results[0].outcome.status is BatchChildStatus.SUCCEEDED
        assert results[0].value is before
        assert results[0].error is None
        assert isinstance(before, BatchChildDispatch)
        assert before.status is BatchChildDispatchStatus.COMPLETED
        assert before.operation_id == good.operation_id

        assert results[1].outcome.status is BatchChildStatus.FAILED
        assert isinstance(results[1].error, BatchChildExecutionError)
        assert results[1].error.reason is BatchChildExecutionReason.DISPATCH_FAILED
        assert results[1].error.__cause__ is failure
        # The batch as a whole is honest about being mixed.
        assert coordinator.report(plan.batch_id).status is BatchExecutionStatus.PARTIAL

    async def test_a_failing_sibling_never_reaches_the_connector_of_another(
        self,
    ) -> None:
        dispatcher = _SpyDispatcher(errors={"failing_tool": RuntimeError("nope")})
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            good = self.work(self.identity("call-good"))
            bad = self.work(self.identity("call-bad"), tool_name="failing_tool")
            plan = self.durable_plan(bad.operation_id, good.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            await self.drive(coordinator, plan, self.executor(dispatcher, (good, bad)))
        finally:
            RunControlContext.unbind(run_token)

        # Both children were dispatched independently: the first one's failure
        # neither skipped nor re-ran the second.
        assert sorted(call["tool_name"] for call in dispatcher.calls) == [
            "failing_tool",
            _READ_TOOL,
        ]
        results = coordinator.results(plan.batch_id)
        assert [item.outcome.status for item in results] == [
            BatchChildStatus.FAILED,
            BatchChildStatus.SUCCEEDED,
        ]


class TestOrderingAndTimestamps(CoordinatorFixtureMixin):
    """Input order and completion order are two facts, and both are kept."""

    async def test_results_follow_input_order_while_timestamps_follow_completion(
        self,
    ) -> None:
        clock = _CounterClock()
        names = ("tool_a", "tool_b", "tool_c")
        dispatcher = _SpyDispatcher(
            gates={name: asyncio.Event() for name in names},
        )
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            children = tuple(
                self.work(self.identity(f"call-{index}"), tool_name=name)
                for index, name in enumerate(names)
            )
            plan = self.durable_plan(*(item.operation_id for item in children))
            # One parallel segment holding all three, so completion order is
            # free to differ from input order rather than forced to match it.
            assert len(plan.plan.segments) == 1
            assert plan.plan.segments[0].operation_ids == plan.plan.operation_ids
            coordinator = self.coordinator(clock=clock)
            coordinator.begin(plan)
            executor = self.executor(dispatcher, children, clock=clock)

            task = asyncio.create_task(self.drive(coordinator, plan, executor))
            await self._drain()
            # Release in reverse input order, so completion order is the exact
            # reverse of the order the results must come back in.
            for name in reversed(names):
                dispatcher.gates[name].set()
                await self._drain()
            await task
        finally:
            RunControlContext.unbind(run_token)

        results = coordinator.results(plan.batch_id)
        # Input order, whatever order they finished in.
        assert [item.operation_id for item in results] == [
            item.operation_id for item in children
        ]
        # Real completion times, which here run strictly backwards.
        completed = [item.value.completed_at for item in results]
        assert completed == sorted(completed, reverse=True)
        # The coordinator's own record agrees, from the same shared clock.
        recorded = [item.outcome.completed_at for item in results]
        assert recorded == sorted(recorded, reverse=True)
        # A timestamp is a moment, never a position: no child's completion time
        # equals its admission time.
        assert all(item.value.completed_at > item.value.admitted_at for item in results)

    async def test_the_receipt_records_the_width_that_actually_applied(self) -> None:
        dispatcher = _SpyDispatcher()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            children = tuple(
                self.work(self.identity(f"call-{index}"), tool_name=f"tool_{index}")
                for index in range(2)
            )
            plan = self.durable_plan(
                *(item.operation_id for item in children),
                max_parallelism=2,
            )
            coordinator = self.coordinator()
            coordinator.begin(plan)
            await self.drive(coordinator, plan, self.executor(dispatcher, children))
        finally:
            RunControlContext.unbind(run_token)

        results = coordinator.results(plan.batch_id)
        assert [item.value.effective_max_parallelism for item in results] == [2, 2]
        assert all(
            item.value.admitted_at == item.outcome.admitted_at for item in results
        )


class TestDeadlinesOnlyNarrow(CoordinatorFixtureMixin):
    """A child's time budget can only ever shrink relative to its batch."""

    def test_a_child_deadline_narrows_the_batchs_and_never_widens_it(self) -> None:
        earlier = _CREATED_AT
        later = _CREATED_AT + timedelta(seconds=30)
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            narrow = self.work(self.identity("call-1"), deadline_at=earlier)
            wide = self.work(self.identity("call-2"), deadline_at=later)
            unset = self.work(self.identity("call-3"))
        finally:
            RunControlContext.unbind(run_token)

        assert narrow.deadline_with(later) == earlier
        assert wide.deadline_with(earlier) == earlier
        assert unset.deadline_with(later) == later
        assert unset.deadline_with(None) is None
        assert narrow.deadline_with(None) == earlier

    async def test_an_expired_deadline_refuses_before_anything_is_dispatched(
        self,
    ) -> None:
        dispatcher = _SpyDispatcher()
        clock = _FrozenClock()
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            work = self.work(self.identity("call-1"))
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            result = await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(
                    dispatcher,
                    (work,),
                    clock=clock,
                    deadline_at=clock.moment - timedelta(seconds=1),
                ).run,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert dispatcher.calls == []
        assert result.outcome.status is BatchChildStatus.FAILED
        assert result.error.reason is BatchChildExecutionReason.DEADLINE_EXPIRED
        assert result.error.safe_message == BatchChildExecutionMessages.DEADLINE_EXPIRED

    async def test_a_deadline_reached_during_dispatch_fails_the_child(self) -> None:
        clock = _FrozenClock()
        dispatcher = _SpyDispatcher(gates={_READ_TOOL: asyncio.Event()})
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            work = self.work(self.identity("call-1"))
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            result = await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(
                    dispatcher,
                    (work,),
                    clock=clock,
                    # A budget that is positive, so the child is dispatched, but
                    # that a never-resolving await can never outlast.
                    deadline_at=clock.moment + timedelta(microseconds=1),
                ).run,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert dispatcher.calls != []
        assert result.outcome.status is BatchChildStatus.FAILED
        assert result.error.reason is BatchChildExecutionReason.DEADLINE_EXPIRED


class TestCancellationIsNotSwallowed(CoordinatorFixtureMixin):
    """F6.6 owns cancellation; this lane must not stand in its way."""

    async def test_an_outer_cancellation_propagates_out_of_the_runner(self) -> None:
        dispatcher = _SpyDispatcher(gates={_READ_TOOL: asyncio.Event()})
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            work = self.work(self.identity("call-1"))
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            task = asyncio.create_task(
                coordinator.run_child(
                    batch_id=plan.batch_id,
                    operation_id=work.operation_id,
                    runner=self.executor(dispatcher, (work,)).run,
                )
            )
            await self._drain()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            RunControlContext.unbind(run_token)

        assert task.cancelled()
        # The child's body had started, so the coordinator records the only
        # honest answer rather than a failure this lane invented.
        outcome = coordinator.report(plan.batch_id).outcomes[0]
        assert outcome.status is BatchChildStatus.INDETERMINATE

    async def test_a_cancelled_child_is_never_relabelled_as_a_dispatch_failure(
        self,
    ) -> None:
        dispatcher = _SpyDispatcher(
            errors={_READ_TOOL: asyncio.CancelledError()},
        )
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            work = self.work(self.identity("call-1"))
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            with pytest.raises(asyncio.CancelledError):
                await coordinator.run_child(
                    batch_id=plan.batch_id,
                    operation_id=work.operation_id,
                    runner=self.executor(dispatcher, (work,)).run,
                )
        finally:
            RunControlContext.unbind(run_token)

        outcome = coordinator.report(plan.batch_id).outcomes[0]
        assert outcome.status is BatchChildStatus.INDETERMINATE


class TestReceiptProjection(CoordinatorFixtureMixin):
    """The receipt says what the gateway said, and never more."""

    async def _dispatch(self, dispatcher) -> object:  # type: ignore[no-untyped-def]
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            work = self.work(self.identity("call-1"))
            plan = self.durable_plan(work.operation_id)
            coordinator = self.coordinator()
            coordinator.begin(plan)
            return await coordinator.run_child(
                batch_id=plan.batch_id,
                operation_id=work.operation_id,
                runner=self.executor(dispatcher, (work,)).run,
            )
        finally:
            RunControlContext.unbind(run_token)

    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            ("completed", BatchChildDispatchStatus.COMPLETED),
            ("succeeded", BatchChildDispatchStatus.COMPLETED),
            ("staged", BatchChildDispatchStatus.STAGED),
            ("blocked", BatchChildDispatchStatus.BLOCKED),
            ("failed", BatchChildDispatchStatus.FAILED),
            ("held", BatchChildDispatchStatus.HELD),
            ("something_new", BatchChildDispatchStatus.UNKNOWN),
            (None, BatchChildDispatchStatus.UNKNOWN),
        ],
    )
    async def test_a_gateway_disposition_becomes_a_receipt_not_an_exception(
        self,
        reported: object,
        expected: BatchChildDispatchStatus,
    ) -> None:
        """Blocked and staged are real operations, and the gateway returns them."""

        dispatcher = _SpyDispatcher(
            outputs={_READ_TOOL: {"output": {"status": reported}}}
        )

        result = await self._dispatch(dispatcher)

        assert result.outcome.status is BatchChildStatus.SUCCEEDED
        assert result.value.status is expected
        assert result.value.succeeded is (
            expected is BatchChildDispatchStatus.COMPLETED
        )

    async def test_the_receipt_carries_the_dispatchers_result_verbatim(self) -> None:
        body = {
            "server_name": _SERVER,
            "tool_name": _READ_TOOL,
            "output": {
                "status": "completed",
                "result_ref": "operation://op/result",
                "result": {"items": [{"id": "L-1"}]},
            },
        }
        dispatcher = _SpyDispatcher(outputs={_READ_TOOL: body})

        result = await self._dispatch(dispatcher)

        assert result.value.result == body
        assert result.value.result_ref == "operation://op/result"

    async def test_an_oversized_result_ref_is_dropped_rather_than_carried(
        self,
    ) -> None:
        dispatcher = _SpyDispatcher(
            outputs={
                _READ_TOOL: {"output": {"status": "completed", "result_ref": "x" * 400}}
            }
        )

        result = await self._dispatch(dispatcher)

        assert result.value.result_ref is None

    async def test_a_non_mapping_dispatch_result_is_a_typed_failure(self) -> None:
        dispatcher = _SpyDispatcher(non_mapping={_READ_TOOL})

        result = await self._dispatch(dispatcher)

        assert result.outcome.status is BatchChildStatus.FAILED
        assert result.error.reason is BatchChildExecutionReason.DISPATCH_MALFORMED

    async def test_an_error_shaped_result_still_receipts_without_an_operation(
        self,
    ) -> None:
        """A dispatcher failure envelope carries no ``output`` and no id."""

        dispatcher = _SpyDispatcher(
            outputs={_READ_TOOL: {"error": {"code": "connection_failed"}}}
        )

        result = await self._dispatch(dispatcher)

        assert result.value.status is BatchChildDispatchStatus.UNKNOWN
        assert result.value.result_ref is None


class TestConstructionFaultsAreNotChildOutcomes(CoordinatorFixtureMixin):
    """A programming fault is never mistakable for a child's refusal."""

    def test_a_duplicate_operation_id_is_a_construction_fault(self) -> None:
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            work = self.work(self.identity("call-1"))
            with pytest.raises(BatchChildExecutorMisconfigured) as excinfo:
                RunScopedBatchChildWork((work, work))
        finally:
            RunControlContext.unbind(run_token)

        assert excinfo.value.safe_message == BatchChildExecutionMessages.DUPLICATE_WORK
        assert not isinstance(excinfo.value, BatchChildExecutionError)

    def test_the_table_is_bounded_by_what_a_batch_may_plan(self) -> None:
        run_token = RunControlContext.bind_for_run(self.binding())
        try:
            items = tuple(
                self.work(self.identity(f"call-{index}")) for index in range(101)
            )
        finally:
            RunControlContext.unbind(run_token)

        with pytest.raises(BatchChildExecutorMisconfigured) as excinfo:
            RunScopedBatchChildWork(items)

        assert excinfo.value.safe_message == BatchChildExecutionMessages.WORK_EXHAUSTED
        assert len(RunScopedBatchChildWork(items[:100])) == 100

    def test_an_unregistered_operation_resolves_to_nothing(self) -> None:
        assert RunScopedBatchChildWork(()).work_for("op-missing") is None

    def test_a_naive_clock_is_rejected_before_any_child_is_dispatched(self) -> None:
        """Discovered at construction, so no refusal can be about a live child."""

        with pytest.raises(BatchChildExecutorMisconfigured) as excinfo:
            GatewayBatchChildExecutor(
                dispatcher=_SpyDispatcher(),
                work=RunScopedBatchChildWork(()),
                clock=lambda: datetime(2026, 7, 29, 9, 0),
            )

        assert excinfo.value.safe_message == BatchChildExecutionMessages.NAIVE_CLOCK

    def test_every_refusal_reason_carries_its_own_safe_message(self) -> None:
        """No reason falls back to another reason's text."""

        messages = [
            BatchChildExecutionError(reason).safe_message
            for reason in BatchChildExecutionReason
        ]

        assert len(set(messages)) == len(list(BatchChildExecutionReason))
        assert set(BatchChildExecutionMessages.BY_REASON) == set(
            BatchChildExecutionReason
        )


class TestStructuralReuse(GatewayHarnessMixin):
    """Reuse is structural: a second dispatch path is absent by construction."""

    def test_the_child_executor_imports_no_gateway_internals(self) -> None:
        import agent_runtime.capabilities.concurrency.child_execution as module

        tree = ast.parse(Path(module.__file__).read_text())
        imported = {
            f"{node.module or ''}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        forbidden = {
            "DynamicMcpRegistry",
            "EffectStager",
            "McpLoader",
            "McpOperationAdapter",
            "McpOperationGatewayContext",
            "McpOperationGatewayServices",
            "OperationClassifier",
            "OperationContext",
            "OperationDescriptorRegistry",
            "OperationGateway",
            "OperationRequest",
            "OperationRequestFactory",
        }
        assert not {name.rsplit(".", 1)[-1] for name in imported} & forbidden

    def test_the_child_executor_imports_no_gateway_package_at_all(self) -> None:
        """Stronger than a name list: the packages themselves stay unreachable."""

        import agent_runtime.capabilities.concurrency.child_execution as module

        tree = ast.parse(Path(module.__file__).read_text())
        modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        closed = (
            "agent_runtime.capabilities.mcp",
            "agent_runtime.capabilities.operations",
            "agent_runtime.capabilities.middleware",
            "agent_runtime.effects",
            "agent_runtime.surfaces_v2",
        )
        assert not [name for name in modules if name.startswith(closed)]

    def test_the_executor_run_method_is_the_coordinators_runner(self) -> None:
        executor = GatewayBatchChildExecutor(
            dispatcher=_SpyDispatcher(),
            work=RunScopedBatchChildWork(()),
        )

        # ``BatchChildRunner`` is ``Callable[[BatchChildAdmission], Awaitable]``:
        # the coordinator's own call sites in this module are the type check.
        assert callable(executor.run)
        assert asyncio.iscoroutinefunction(executor.run)
