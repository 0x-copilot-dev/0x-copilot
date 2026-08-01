"""W3 — F6 threaded into the graph: the first observable batch parallelism.

Every lane before this one could show that F6 *decided* correctly. None could
show that anything ran, because nothing in ``src/`` outside
``capabilities/concurrency/`` constructed a coordinator, so the effective width
of every run was one whatever a plan said.

The two tests that carry this file run **identical work through the identical
real graph** — the real ``create_agent`` compilation, the real
``RuntimeControlMiddleware``, the real ``BatchExecutionCoordinator``, the real
``RunPermitManager``, the real ``BatchPlanner``, the real kill-switch gate — and
differ in exactly one thing: whether the composition root installed an F6
admission. Configured, three independent reads are observed running at once.
Unconfigured, the same three reads are observed strictly one at a time.

Nothing here sleeps on the wall clock. Concurrency is observed with a counter
around a single ``await`` yield point, so the assertion is about the scheduler's
actual interleaving rather than about elapsed time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.concurrency import (
    ApprovalRequirement,
    BatchChildTransitionWrite,
    BatchExecutionCoordinator,
    BatchJournalWrite,
    BatchPlanRecorder,
    CapabilityConcurrencyDeclaration,
    ConcurrencyAllowance,
    ConcurrencyKillSwitchAllowanceSupplier,
    ConcurrencyKillSwitchGate,
    ConcurrencyMode,
    ConcurrencyScope,
    DurableBatchPlan,
    DurableChildTransition,
    PermitAcquisitionRequest,
    PermitCapacityPolicy,
    PolicySource,
    RunPermitManager,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    DeclaredConcurrencyPolicySource,
    RunBatchAdmission,
    graph_capability_ref,
)
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import (
    BudgetEnvelope,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.execution.contracts import RuntimeBatchAdmissionContext

_ORG = "org-w3"
_TRACE = "trace-w3"
_RUN = "run-w3"
_SNAPSHOT = "snapshot-w3"
_PROFILE = "single_user_desktop"
_SUBJECT = "b" * 64
_CREATED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
_TOOL = "observed_tool"
_FANOUT = 3


class _InMemoryBatchJournal:
    """A ``BatchPlanStorePort`` that records rather than persists.

    The durable journal adapter has its own tests over the real event stream.
    What these tests need from it is the one property the coordinator depends
    on — a plan handle exists only after an append returned — and recording that
    faithfully is what makes the dispatch ordering observable here.
    """

    def __init__(self) -> None:
        self.plans: list[BatchJournalWrite] = []
        self.transitions: list[BatchChildTransitionWrite] = []
        self._sequence = 0

    def _next(self) -> int:
        self._sequence += 1
        return self._sequence

    async def put_plan(self, write: BatchJournalWrite) -> DurableBatchPlan:
        self.plans.append(write)
        return DurableBatchPlan(sequence_no=self._next(), record=write.record)  # type: ignore[arg-type]

    async def append_child_transition(
        self,
        write: BatchChildTransitionWrite,
    ) -> DurableChildTransition:
        self.transitions.append(write)
        return DurableChildTransition(sequence_no=self._next(), record=write.record)

    async def load_recovery_view(self, **_kwargs: object) -> object:
        raise NotImplementedError


class _FanoutModel(BaseChatModel):
    """Emit three sibling calls in one turn, then a final answer."""

    @property
    def _llm_type(self) -> str:
        return "w3-batch-fanout-test"

    @staticmethod
    def _reply(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {"name": _TOOL, "args": {"value": value}, "id": f"call-{value}"}
                for value in range(_FANOUT)
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        del tools, kwargs
        return self


def _snapshot() -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-w3",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=16,
    )
    return RunControlSnapshot.create(
        run_id=_RUN,
        conversation_id="conversation-w3",
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


def _binding() -> RunControlBinding:
    return RunControlBinding(
        snapshot=_snapshot(),
        effective_modes=FeatureModeSet(f6=FeatureMode.ENFORCE),
        decisions=(),
    )


def _declarations(
    *,
    mode: ConcurrencyMode = ConcurrencyMode.PARALLEL_SAFE,
    side_effect: SideEffectKind = SideEffectKind.READ,
    approval_requirement: ApprovalRequirement = ApprovalRequirement.NEVER,
) -> dict[str, tuple[CapabilityConcurrencyDeclaration, ...]]:
    """Return the operator catalog entry that makes ``observed_tool`` overlap.

    ``PRODUCT_CATALOG`` is the only source that may *establish* a policy, which
    is why an operator declaration — not a connector's self-description — is
    what an overlapping deployment turns on.

    ``approval_requirement`` is part of that turn-on and not a detail: the
    conservative floor is ``UNKNOWN``, which narrows the mode to serial, so an
    operator who wants overlap has to state that the capability never pauses for
    a human. Declaring parallel-safe alone no longer buys anything.
    """

    return {
        _TOOL: (
            CapabilityConcurrencyDeclaration(
                capability_ref=graph_capability_ref(_TOOL),
                source=PolicySource.PRODUCT_CATALOG,
                mode=mode,
                side_effect=side_effect,
                approval_requirement=approval_requirement,
                max_parallelism=_FANOUT,
            ),
        )
    }


def _admission(
    journal: _InMemoryBatchJournal,
    *,
    declarations: Mapping[str, tuple[CapabilityConcurrencyDeclaration, ...]],
    ceiling: int = _FANOUT,
    approvals: object = None,
) -> RunBatchAdmission:
    """Compose exactly what the worker's composition root composes.

    ``approvals`` is the run-scoped approval surface. ``None`` is what the
    worker wires today, and it means the seam establishes nothing about
    approval — so only a catalog declaration can permit overlap.
    """

    gate = ConcurrencyKillSwitchGate(
        snapshot_allowance=ConcurrencyAllowance(
            mode=FeatureMode.ENFORCE,
            max_parallelism=ceiling,
        )
    )
    snapshot = _snapshot()
    coordinator = BatchExecutionCoordinator(
        permits=RunPermitManager(
            policy=PermitCapacityPolicy.from_limits(
                {kind: ceiling for kind in ConcurrencyScope.permit_pool_kinds()}
            )
        ),
        permit_scopes=lambda identity: (
            PermitAcquisitionRequest.for_operation(
                profile_id=_PROFILE,
                subject_fingerprint=_SUBJECT,
                capability_name=identity.capability_ref or identity.operation_id,
            ).scopes
        ),
        live_allowance=ConcurrencyKillSwitchAllowanceSupplier(gate),
    )
    return RunBatchAdmission(
        recorder=BatchPlanRecorder(journal=journal, gate=gate),  # type: ignore[arg-type]
        coordinator=coordinator,
        policies=DeclaredConcurrencyPolicySource(declarations),
        snapshot=snapshot,
        org_id=_ORG,
        trace_id=_TRACE,
        approvals=approvals,  # type: ignore[arg-type]
    )


class _ConcurrencyProbe:
    """Observed simultaneity across a fixed number of scheduler turns.

    The window is a count of ``asyncio`` yields, never an elapsed duration, so
    the observation is reproducible rather than timing-dependent. It is wide on
    purpose: an admitted sibling reaches this probe only after the coordinator
    has acquired its permit, and each of those steps is its own await. A window
    narrower than the longest admission path would under-report overlap that
    genuinely happened, which would make a passing serial assertion meaningless.

    Widening the window can never manufacture overlap. Under an exclusive permit
    the second caller is not merely late — it is not running at all — so any
    number of turns still observes one.
    """

    #: Comfortably more turns than the admission path spends between the gate
    #: and this probe, so every admitted sibling is inside the window at once.
    #: Widened 32 -> 1024 after CI flaked under load: a busy runner can spend
    #: more than 32 scheduler turns in the admission path, so a genuinely
    #: overlapping sibling arrived after the window closed and overlap was
    #: under-reported. Widening only gives real overlap more room to appear (see
    #: the class docstring), so it is safe to raise and must not be lowered.
    YIELDS: int = 1024

    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.completed: list[int] = []

    async def observe(self, value: int) -> str:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        for _ in range(self.YIELDS):
            await asyncio.sleep(0)
            self.maximum_active = max(self.maximum_active, self.active)
        self.active -= 1
        self.completed.append(value)
        return str(value)


async def _run_graph(probe: _ConcurrencyProbe) -> dict[str, Any]:
    tool = StructuredTool.from_function(
        name=_TOOL,
        description="Record one observed value.",
        coroutine=probe.observe,
    )
    graph = create_agent(
        model=_FanoutModel(),
        tools=[tool],
        middleware=[RuntimeToolControlMiddleware()],
    )
    return await graph.ainvoke(
        {"messages": [HumanMessage(content="Run all observations.")]}
    )


class TestF6IsObservableThroughTheRealGraph:
    """The claim W3 exists to make, stated as one A/B pair."""

    async def test_configured_run_overlaps_a_planned_parallel_segment(self) -> None:
        """Three independent reads run at once, through the real coordinator."""

        journal = _InMemoryBatchJournal()
        probe = _ConcurrencyProbe()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(journal, declarations=_declarations())
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            result = await _run_graph(probe)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert probe.maximum_active == _FANOUT
        assert sorted(probe.completed) == [0, 1, 2]
        assert result["messages"][-1].content == "done"
        # The plan was durable before any child dispatched, and it named one
        # parallel segment covering all three reads.
        assert len(journal.plans) == 1
        record = journal.plans[0].record
        assert len(record.segments) == 1
        assert record.segments[0].effective_max_parallelism == _FANOUT
        assert len(record.segments[0].operation_ids) == _FANOUT

    async def test_unconfigured_run_of_the_identical_work_stays_serial(self) -> None:
        """The same graph, the same tool, no F6 install — strictly one at a time."""

        probe = _ConcurrencyProbe()
        token = RunControlContext.bind_for_run(_binding())
        try:
            result = await _run_graph(probe)
        finally:
            RunControlContext.unbind(token)

        assert probe.maximum_active == 1
        assert sorted(probe.completed) == [0, 1, 2]
        assert result["messages"][-1].content == "done"


class TestOverlapIsGrantedAndNeverAssumed:
    """Every narrowing input independently returns the run to serial."""

    @pytest.mark.parametrize(
        ("mode", "side_effect"),
        [
            (ConcurrencyMode.SERIAL, SideEffectKind.READ),
            (ConcurrencyMode.PARALLEL_SAFE, SideEffectKind.UNKNOWN),
            (ConcurrencyMode.PARALLEL_SAFE, SideEffectKind.IRREVERSIBLE_WRITE),
        ],
    )
    async def test_a_policy_that_does_not_permit_overlap_stays_serial(
        self,
        mode: ConcurrencyMode,
        side_effect: SideEffectKind,
    ) -> None:
        journal = _InMemoryBatchJournal()
        probe = _ConcurrencyProbe()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(
            journal,
            declarations=_declarations(mode=mode, side_effect=side_effect),
        )
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            await _run_graph(probe)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert probe.maximum_active == 1
        assert len(journal.plans) == 1
        # The plan is still durable: F6 recorded that it decided serial, which
        # is what makes "why did this not overlap" answerable after the fact.
        assert len(journal.plans[0].record.segments) == _FANOUT

    async def test_an_undeclared_capability_stays_serial(self) -> None:
        """No catalog entry is the common case, and it never overlaps."""

        journal = _InMemoryBatchJournal()
        probe = _ConcurrencyProbe()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(journal, declarations={})
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            await _run_graph(probe)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert probe.maximum_active == 1

    async def test_a_deployment_ceiling_of_one_stays_serial(self) -> None:
        """The operator's own ceiling bounds a fully parallel-safe catalog."""

        journal = _InMemoryBatchJournal()
        probe = _ConcurrencyProbe()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(journal, declarations=_declarations(), ceiling=1)
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            await _run_graph(probe)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert probe.maximum_active == 1

    async def test_a_live_kill_switch_narrows_the_admitted_width(self) -> None:
        """F6.7's gate now reaches the coordinator, so a switch can narrow."""

        class _Switch:
            def __init__(self) -> None:
                self.raw: object = None

            def current_kill_switch_directives(self) -> object:
                return self.raw

        switch = _Switch()
        switch.raw = "global"
        journal = _InMemoryBatchJournal()
        probe = _ConcurrencyProbe()
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=ConcurrencyAllowance(
                mode=FeatureMode.ENFORCE,
                max_parallelism=_FANOUT,
            ),
            source=switch,
        )
        coordinator = BatchExecutionCoordinator(
            permits=RunPermitManager(
                policy=PermitCapacityPolicy.from_limits(
                    {kind: _FANOUT for kind in ConcurrencyScope.permit_pool_kinds()}
                )
            ),
            permit_scopes=lambda identity: (
                PermitAcquisitionRequest.for_operation(
                    profile_id=_PROFILE,
                    subject_fingerprint=_SUBJECT,
                    capability_name=identity.capability_ref or identity.operation_id,
                ).scopes
            ),
            live_allowance=ConcurrencyKillSwitchAllowanceSupplier(gate),
        )
        token = RunControlContext.bind_for_run(_binding())
        admission = RunBatchAdmission(
            recorder=BatchPlanRecorder(journal=journal, gate=gate),  # type: ignore[arg-type]
            coordinator=coordinator,
            policies=DeclaredConcurrencyPolicySource(_declarations()),
            snapshot=_snapshot(),
            org_id=_ORG,
            trace_id=_TRACE,
        )
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            await _run_graph(probe)
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)

        assert probe.maximum_active == 1
        assert journal.plans[0].record.effective_allowance.is_serial
