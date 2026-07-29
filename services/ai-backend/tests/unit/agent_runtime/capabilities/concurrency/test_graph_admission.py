"""W3 — the three joins SMELL-01 named as missing, and the seam they enable.

The SMELL-01 lane could see the shape of the graph seam but could not build it,
because the three edges it needed all lived inside ``capabilities/concurrency/``:

1. no ``model_tool_call_id`` → planned-child lookup;
2. no pre-admission width query; and
3. no ``ConcurrencyKillSwitchGate`` → ``BatchAllowanceSupplier`` bridge.

Each is exercised here on its own, so a regression names the edge it broke
rather than only the feature that stopped working.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_runtime.capabilities.concurrency import (
    ApprovalRequirement,
    BatchChildExecutionError,
    BatchChildExecutionReason,
    BatchChildTransitionWrite,
    BatchChildWork,
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
    RunScopedBatchChildWork,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    DeclaredConcurrencyPolicySource,
    RunBatchAdmission,
    graph_capability_key,
    graph_capability_ref,
)
from agent_runtime.capabilities.concurrency.child_execution import (
    BatchChildExecutorMisconfigured,
)
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import (
    BudgetEnvelope,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.control_plane.parallel_admission import ToolAdmissionRequest

_ORG = "org-w3"
_TRACE = "trace-w3"
_RUN = "run-w3g"
_SNAPSHOT = "snapshot-w3g"
_PROFILE = "single_user_desktop"
_SUBJECT = "c" * 64
_CREATED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
_TOOL = "read_thing"
_WIDTH = 3


def _work(operation_id: str, model_tool_call_id: str) -> BatchChildWork:
    return BatchChildWork(
        operation_id=operation_id,
        server_name="server",
        tool_name=_TOOL,
        arguments={},
        model_tool_call_id=model_tool_call_id,
        model_turn=1,
    )


class TestModelToolCallLookup:
    """Gap 1 — the graph's own id now reaches the durable plan's child."""

    def test_a_provider_tool_call_id_resolves_its_planned_child(self) -> None:
        table = RunScopedBatchChildWork(
            [_work("op-1", "call-a"), _work("op-2", "call-b")]
        )

        resolved = table.work_for_model_tool_call("call-b")

        assert resolved is not None
        assert resolved.operation_id == "op-2"
        # The two indexes are two readings of one sequence, never two sources.
        assert table.work_for("op-2") is resolved

    def test_an_unknown_or_blank_id_resolves_to_nothing(self) -> None:
        table = RunScopedBatchChildWork([_work("op-1", "call-a")])

        assert table.work_for_model_tool_call("call-z") is None
        assert table.work_for_model_tool_call("") is None

    def test_a_duplicated_provider_id_refuses_the_whole_table(self) -> None:
        """Ambiguity is refused rather than resolved by whichever entry wins."""

        with pytest.raises(BatchChildExecutorMisconfigured):
            RunScopedBatchChildWork([_work("op-1", "call-a"), _work("op-2", "call-a")])


class _Journal:
    def __init__(self) -> None:
        self.plans: list[BatchJournalWrite] = []
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
        return DurableChildTransition(sequence_no=self._next(), record=write.record)

    async def load_recovery_view(self, **_kwargs: object) -> object:
        raise NotImplementedError


def _snapshot() -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-w3g",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=16,
    )
    return RunControlSnapshot.create(
        run_id=_RUN,
        conversation_id="conversation-w3g",
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


def _catalog() -> dict[str, tuple[CapabilityConcurrencyDeclaration, ...]]:
    return {
        _TOOL: (
            CapabilityConcurrencyDeclaration(
                capability_ref=graph_capability_ref(_TOOL),
                source=PolicySource.PRODUCT_CATALOG,
                mode=ConcurrencyMode.PARALLEL_SAFE,
                side_effect=SideEffectKind.READ,
                approval_requirement=ApprovalRequirement.NEVER,
                max_parallelism=_WIDTH,
            ),
        )
    }


def _admission(
    journal: _Journal,
    *,
    source: Any | None = None,
    ceiling: int = _WIDTH,
) -> RunBatchAdmission:
    gate = ConcurrencyKillSwitchGate(
        snapshot_allowance=ConcurrencyAllowance(
            mode=FeatureMode.ENFORCE,
            max_parallelism=ceiling,
        ),
        source=source,
    )
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
        policies=DeclaredConcurrencyPolicySource(_catalog()),
        snapshot=_snapshot(),
        org_id=_ORG,
        trace_id=_TRACE,
    )


def _calls(count: int = _WIDTH) -> list[dict[str, Any]]:
    return [
        {"name": _TOOL, "args": {"value": index}, "id": f"call-{index}"}
        for index in range(count)
    ]


class TestPreAdmissionWidth:
    """Gap 2 — the gate can now ask how wide a child may run, before permitting."""

    async def test_a_planned_child_is_granted_the_plan_width(self) -> None:
        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            grant = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="call-1",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
        finally:
            RunControlContext.unbind(token)

        assert grant is not None
        assert grant.max_parallelism == _WIDTH
        # A cohort is one segment of one batch — never a whole batch, whose
        # segments carry different widths.
        assert grant.cohort_id.endswith("#0")
        assert grant.tool_call_id == "call-1"

    async def test_every_member_of_one_segment_shares_one_cohort(self) -> None:
        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            cohorts = {
                admission.grant_for(
                    ToolAdmissionRequest(
                        tool_call_id=f"call-{index}",
                        tool_name=_TOOL,
                        execution_scope="supervisor",
                    )
                ).cohort_id  # type: ignore[union-attr]
                for index in range(_WIDTH)
            }
        finally:
            RunControlContext.unbind(token)

        assert len(cohorts) == 1

    async def test_an_unplanned_call_is_granted_nothing(self) -> None:
        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            unknown = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="call-elsewhere",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
            blank = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
        finally:
            RunControlContext.unbind(token)

        assert unknown is None
        assert blank is None

    async def test_the_width_is_read_before_any_permit_is_leased(self) -> None:
        """A grant exists while the permit table is still completely idle."""

        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            grant = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="call-0",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
        finally:
            RunControlContext.unbind(token)

        assert grant is not None and grant.max_parallelism == _WIDTH


class TestKillSwitchReachesTheCoordinator:
    """Gap 3 — a live switch can now narrow a batch that is already planned."""

    def test_the_bridge_reports_the_live_allowance(self) -> None:
        class _Switch:
            raw: object = None

            def current_kill_switch_directives(self) -> object:
                return self.raw

        switch = _Switch()
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=ConcurrencyAllowance(
                mode=FeatureMode.ENFORCE,
                max_parallelism=4,
            ),
            source=switch,
        )
        supplier = ConcurrencyKillSwitchAllowanceSupplier(gate)

        assert supplier().effective_max_parallelism == 4
        switch.raw = "global"
        # Re-read, not captured: a switch flipped after construction narrows.
        assert supplier().is_serial

    def test_an_unreadable_switch_narrows_to_serial_rather_than_raising(self) -> None:
        class _Broken:
            def current_kill_switch_directives(self) -> object:
                raise RuntimeError("switch source is down")

        supplier = ConcurrencyKillSwitchAllowanceSupplier(
            ConcurrencyKillSwitchGate(
                snapshot_allowance=ConcurrencyAllowance(
                    mode=FeatureMode.ENFORCE,
                    max_parallelism=4,
                ),
                source=_Broken(),
            )
        )

        assert supplier().is_serial

    async def test_a_switch_flipped_after_planning_narrows_the_grant(self) -> None:
        class _Switch:
            raw: object = None

            def current_kill_switch_directives(self) -> object:
                return self.raw

        switch = _Switch()
        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal, source=switch)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            before = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="call-0",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
            switch.raw = "global"
            after = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="call-1",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
        finally:
            RunControlContext.unbind(token)

        assert before is not None and before.max_parallelism == _WIDTH
        # The plan is unchanged and still durable; the live width is not.
        assert after is None


class TestPlanningIsTotalAndDurableFirst:
    """Every unresolved input leaves the turn exactly as serial as it was."""

    async def test_a_turn_with_one_call_is_never_planned(self) -> None:
        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(count=1),
            )
        finally:
            RunControlContext.unbind(token)

        assert journal.plans == []
        assert admission.tracked_children == 0

    async def test_no_verified_run_binding_plans_nothing(self) -> None:
        journal = _Journal()
        admission = _admission(journal)

        await admission.aplan_model_batch(
            execution_scope="supervisor",
            model_turn=1,
            tool_calls=_calls(),
        )

        assert journal.plans == []
        assert admission.tracked_children == 0

    async def test_a_malformed_call_makes_the_whole_turn_serial(self) -> None:
        """Partially planning a turn would leave one call outside every decision."""

        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=[*_calls(count=2), {"name": _TOOL, "args": {}, "id": ""}],
            )
        finally:
            RunControlContext.unbind(token)

        assert journal.plans == []
        assert admission.tracked_children == 0

    async def test_a_journal_that_refuses_the_plan_leaves_the_turn_serial(
        self,
    ) -> None:
        class _Refusing(_Journal):
            async def put_plan(self, write: BatchJournalWrite) -> DurableBatchPlan:
                raise RuntimeError("journal is unavailable")

        journal = _Refusing()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            grant = admission.grant_for(
                ToolAdmissionRequest(
                    tool_call_id="call-0",
                    tool_name=_TOOL,
                    execution_scope="supervisor",
                )
            )
        finally:
            RunControlContext.unbind(token)

        assert grant is None
        assert admission.tracked_children == 0

    async def test_the_plan_is_durable_before_any_child_can_be_claimed(self) -> None:
        """The index is only populated by an append that already returned."""

        observed: list[str] = []

        class _Watching(_Journal):
            async def put_plan(self, write: BatchJournalWrite) -> DurableBatchPlan:
                observed.append("append_started")
                await asyncio.sleep(0)
                durable = await super().put_plan(write)
                observed.append("append_returned")
                return durable

        journal = _Watching()
        token = RunControlContext.bind_for_run(_binding())
        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            observed.append("claimable")
            assert admission.tracked_children == _WIDTH
        finally:
            RunControlContext.unbind(token)

        assert observed == ["append_started", "append_returned", "claimable"]


class TestBodyRouting:
    """``arun_tool_body`` is the only route, and it never invents an outcome."""

    async def test_an_unplanned_call_runs_its_body_unmediated(self) -> None:
        journal = _Journal()
        admission = _admission(journal)
        ran = 0

        async def body() -> str:
            nonlocal ran
            ran += 1
            return "value"

        result = await admission.arun_tool_body(tool_call_id="call-x", body=body)

        assert result == "value"
        assert ran == 1

    async def test_a_planned_call_is_claimed_exactly_once(self) -> None:
        """A replayed wrapper is not a second child; it is simply not planned."""

        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())

        async def body() -> str:
            return "value"

        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            first = await admission.arun_tool_body(tool_call_id="call-0", body=body)
            remaining = admission.tracked_children
            second = await admission.arun_tool_body(tool_call_id="call-0", body=body)
        finally:
            RunControlContext.unbind(token)

        assert first == "value"
        assert second == "value"
        assert remaining == _WIDTH - 1

    async def test_a_body_error_reaches_the_caller_unchanged(self) -> None:
        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())

        async def body() -> str:
            raise ValueError("connector said no")

        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            with pytest.raises(ValueError, match="connector said no"):
                await admission.arun_tool_body(tool_call_id="call-0", body=body)
        finally:
            RunControlContext.unbind(token)

    async def test_a_refused_child_never_runs_its_body(self) -> None:
        """A durable refusal and a dispatched body would be a lie in the journal."""

        journal = _Journal()
        token = RunControlContext.bind_for_run(_binding())
        ran = 0

        async def body() -> str:
            nonlocal ran
            ran += 1
            return "value"

        try:
            admission = _admission(journal)
            await admission.aplan_model_batch(
                execution_scope="supervisor",
                model_turn=1,
                tool_calls=_calls(),
            )
            # Disposing the coordinator makes every later admission a refusal,
            # which is the shape a cancelled or torn-down run produces.
            admission._coordinator.dispose()  # noqa: SLF001 - state under test.
            with pytest.raises(BatchChildExecutionError) as caught:
                await admission.arun_tool_body(tool_call_id="call-0", body=body)
        finally:
            RunControlContext.unbind(token)

        assert caught.value.reason is BatchChildExecutionReason.NOT_ADMITTED
        assert ran == 0


class TestCapabilityKeys:
    """Keys and references are opaque, stable, and body-free."""

    def test_an_mcp_call_is_keyed_by_its_server_and_inner_tool(self) -> None:
        key = graph_capability_key(
            tool_name="call_mcp_tool",
            arguments={"server_name": "github", "tool_name": "search", "q": "secret"},
        )

        assert key == "github:search"
        assert "secret" not in key

    def test_a_local_tool_is_keyed_by_its_own_name(self) -> None:
        assert graph_capability_key(tool_name=_TOOL, arguments={"a": 1}) == _TOOL

    def test_a_reference_is_opaque_stable_and_well_formed(self) -> None:
        first = graph_capability_ref("github:search")
        second = graph_capability_ref("github:search")

        assert first == second
        assert first.startswith("cap_") and len(first) == 36
        assert "github" not in first
