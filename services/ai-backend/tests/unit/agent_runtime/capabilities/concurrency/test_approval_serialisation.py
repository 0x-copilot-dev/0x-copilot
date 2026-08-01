"""BUG-15 — approval-gated capabilities may never join a parallel segment.

Step 10's exit criteria say writes, effects, **approvals**, and resource
conflicts never overlap improperly. Three of those four were proven. Approvals
had no coverage anywhere in the F6 lane, and the argument protecting them was
conventional: *approval-gated work is effectful, effectful work is serial*.

Nothing enforced that link. The two authorities never met. A ``PRODUCT_CATALOG``
entry declares an effect class and a concurrency mode; whether a dispatch of the
same capability *parks* is decided by the run's tool-use policy
(``ToolUsePolicySnapshot`` — whose ``read`` axis is configurable to ``ask`` and
``require``), by the connector's live auth state, or by a filesystem permission
rule. A catalog author cannot see any of them.

:class:`TestTheDefectThisLaneCloses` reproduces what that permitted, through the
real graph, and it is not a near miss: three approval-requiring reads shared one
release wave, three human decisions opened at once, and the coordinator settled
all three durably as ``FAILED`` — because a LangGraph suspend arrives at it as an
ordinary exception. The rest of the file establishes the rule that forbids it and
the properties that make the rule narrowing-only.

Nothing here sleeps on the wall clock.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from agent_runtime.capabilities.concurrency import (
    APPROVAL_REQUIREMENT_KEY,
    ApprovalRequirement,
    BatchChildStatus,
    BatchSegmentMode,
    CapabilityConcurrencyDeclaration,
    ConcurrencyDescriptorParser,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyPolicyResolver,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    DeclaredApprovalRequirementSource,
    GraphApprovalRequirementResolver,
    RunBatchAdmission,
    graph_capability_ref,
)
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.execution.contracts import RuntimeBatchAdmissionContext

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _FANOUT,
    _TOOL,
    _admission,
    _binding,
    _declarations,
    _FanoutModel,
    _InMemoryBatchJournal,
)

#: Scheduler turns a child waits before concluding no sibling will join it.
#: Same value and same reasoning as ``_ConcurrencyProbe.YIELDS``: comfortably
#: more turns than the admission path spends between the gate and the tool body.
#: Widening it can never manufacture overlap, because under an exclusive permit
#: the next child is not merely late — it is not running.
#: Widened 32 -> 1024 after CI flaked under load; kept identical in value to
#: ``_ConcurrencyProbe.YIELDS``. Widening is always safe, so do not lower it.
_QUIESCENCE_YIELDS = 1024

_CAPABILITY_REF = graph_capability_ref(_TOOL)


class _ParkProbe:
    """Counts how many tool bodies are parked on a human decision at once.

    A park is entered when the body reaches :func:`interrupt` and left when the
    resulting ``GraphInterrupt`` unwinds past it, so ``maximum_parked`` is the
    number of simultaneous human decisions this turn opened. That is the
    quantity an approval gate is *about*: one is a question, three is three
    questions asked about work the user thought was one step.
    """

    def __init__(self) -> None:
        self.parked = 0
        self.maximum_parked = 0
        self.arrived: list[int] = []
        self.resumed: list[int] = []

    async def observe(self, value: int) -> str:
        self.arrived.append(value)
        self.parked += 1
        self.maximum_parked = max(self.maximum_parked, self.parked)
        try:
            for _ in range(_QUIESCENCE_YIELDS):
                await asyncio.sleep(0)
                self.maximum_parked = max(self.maximum_parked, self.parked)
            decision = interrupt({"operation": value})
        finally:
            self.parked -= 1
        self.resumed.append(value)
        return f"value-{value}-{decision}"


class ApprovalGraphMixin:
    """Drive the real W3 graph with a caller-supplied approval configuration."""

    @staticmethod
    def _graph(body: Any, *, checkpointer: object | None = None) -> Any:
        return create_agent(
            model=_FanoutModel(),
            tools=[
                StructuredTool.from_function(
                    name=_TOOL,
                    description="One observed operation.",
                    coroutine=body,
                )
            ],
            middleware=[RuntimeToolControlMiddleware()],
            **({"checkpointer": checkpointer} if checkpointer is not None else {}),
        )

    async def run(
        self,
        body: Any,
        *,
        declarations: Any = None,
        approvals: object = None,
        checkpointer: object | None = None,
        config: Any = None,
    ) -> tuple[Any, _InMemoryBatchJournal, RunBatchAdmission]:
        """Compose F6 exactly as the worker does, plus an approval surface."""

        journal = _InMemoryBatchJournal()
        token = RunControlContext.bind_for_run(_binding())
        admission = _admission(
            journal,
            declarations=_declarations() if declarations is None else declarations,
            approvals=approvals,
        )
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            graph = self._graph(body, checkpointer=checkpointer)
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="Run all observations.")]},
                **({"config": config} if config is not None else {}),
            )
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)
        return result, journal, admission

    @staticmethod
    def segment_modes(journal: _InMemoryBatchJournal) -> set[BatchSegmentMode]:
        assert journal.plans, "the turn was never planned"
        return {segment.mode for segment in journal.plans[0].record.segments}


class TestTheDefectThisLaneCloses(ApprovalGraphMixin):
    """What an unlinked approval fact actually permitted, measured.

    These are negative controls, not aspirations. The catalog entry below tells
    the truth about the *capability* — a parallel-safe read that never parks —
    and lies about nothing; it is simply written by somebody who could not know
    that this deployment's tool-use policy gates the tool. That is the whole
    defect, and it needs no malice to reproduce.
    """

    async def test_three_approval_gated_reads_parked_simultaneously(self) -> None:
        """Three human decisions open at once where the serial path opens one."""

        probe = _ParkProbe()
        _, journal, _ = await self.run(probe.observe, approvals=None)

        assert probe.maximum_parked == _FANOUT, (
            "an approval-gated cohort no longer parks concurrently; if this is "
            "intentional the finding below has changed and must be re-measured"
        )
        assert self.segment_modes(journal) == {BatchSegmentMode.PARALLEL}

    async def test_a_suspend_is_journalled_as_a_permanent_failure(self) -> None:
        """The coordinator settles every parked child ``FAILED``.

        ``BatchExecutionCoordinator._run_admitted`` catches ``Exception`` around
        the child body, and ``langgraph.errors.GraphInterrupt`` is an
        ``Exception``. A suspend is therefore indistinguishable from a connector
        error at that seam: the durable journal records three failures for work
        that has not failed, has not run, and is waiting on a person.

        This is asserted rather than fixed because the coordinator belongs to
        another lane. Serialising the cohort reduces it from N rows to one, but
        does not make the one row true.
        """

        _, journal, admission = await self.run(_ParkProbe().observe, approvals=None)

        batch_id = journal.plans[0].record.batch_id
        statuses = [
            outcome.status
            for outcome in admission._coordinator.report(batch_id).outcomes
        ]
        assert statuses == [BatchChildStatus.FAILED] * _FANOUT, (
            f"expected every parked child recorded as a failure, got {statuses}"
        )

    async def test_langgraph_cannot_resume_a_multi_interrupt_turn_with_one_decision(
        self,
    ) -> None:
        """A turn with N pending interrupts rejects a scalar resume outright.

        Pinned against the real library because it is what makes a multi-park
        turn expensive rather than merely unusual: no single approval can answer
        it. The product's own ``resume_command`` already works around this by
        targeting an interrupt id, which resumes one and re-parks the rest — so
        a three-park turn costs three human round trips, each re-executing the
        whole tool node from the top.

        This is a *framework* property, not an F6 one — see
        :class:`TestPendingInterruptCountIsAFrameworkProperty`. It is recorded
        here because it is the cost that makes concurrent approval parks worth
        forbidding, not because F6 introduced it.
        """

        probe = _ParkProbe()
        config = {"configurable": {"thread_id": "bug15-multi"}}
        checkpointer = InMemorySaver()
        graph = self._graph(probe.observe, checkpointer=checkpointer)
        token = RunControlContext.bind_for_run(_binding())
        try:
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="Run all observations.")]},
                config=config,
            )
            assert len(list(result.get("__interrupt__") or ())) == _FANOUT
            with pytest.raises(RuntimeError, match="multiple pending interrupts"):
                await graph.ainvoke(
                    Command(resume={"decision": "approved"}),
                    config=config,
                )
        finally:
            RunControlContext.unbind(token)


class TestAnApprovalGatedCapabilityIsNeverAdmittedInParallel(ApprovalGraphMixin):
    """The rule, stated against the exact adversarial case BUG-15 names.

    The catalog says read-only, parallel-safe, and never-parks — every positive
    claim an operator can make for overlap. The *run* says otherwise, and the run
    wins, because approval is a narrowing dimension and later sources may only
    narrow.
    """

    @staticmethod
    def _gated() -> DeclaredApprovalRequirementSource:
        return DeclaredApprovalRequirementSource({_TOOL})

    async def test_a_gated_tool_is_serial_despite_a_parallel_safe_read_catalog(
        self,
    ) -> None:
        probe = _ParkProbe()
        _, journal, _ = await self.run(probe.observe, approvals=self._gated())

        assert probe.maximum_parked == 1, (
            "an approval-gated capability opened more than one human decision "
            f"at a time: {probe.maximum_parked}"
        )
        assert journal.plans == [], "an approval-gated turn was planned"

    async def test_the_turn_is_not_planned_rather_than_planned_serially(self) -> None:
        """Refused a plan entry, not given serial segments — and that matters.

        Serial segments would forbid the overlap just as well, but they would
        put a parked child under the coordinator's segment gate. That gate is
        held while the child waits on a person, so a sibling burns the whole
        300-second admission budget and is then refused ``NOT_ADMITTED``; and
        the suspend is settled durably as ``FAILED`` on the way past. Refusing
        the plan hands the turn back to the pre-F6 exclusive permit, which has
        neither problem.
        """

        _, journal, admission = await self.run(
            _ParkProbe().observe,
            approvals=self._gated(),
        )

        assert journal.plans == []
        assert journal.transitions == []
        assert admission.tracked_children == 0

    async def test_one_gated_call_makes_the_whole_turn_unplannable(self) -> None:
        """A mixed turn is not partially planned.

        The module already refuses to plan a turn containing one unusable call,
        because a batch that silently omitted a call would let that call run
        outside every ordering decision F6 made about its siblings. An
        approval-gated call is exactly such a call, and it is treated the same.
        """

        class _OneGatedTool:
            """Only the *second* call of the turn is approval-gated."""

            def __init__(self) -> None:
                self.seen: list[str] = []

            def approval_requirement_for(
                self,
                *,
                tool_name: str,
                arguments: Any,
            ) -> ApprovalRequirement:
                self.seen.append(tool_name)
                value = arguments.get("value") if hasattr(arguments, "get") else None
                if value == 1:
                    return ApprovalRequirement.ALWAYS
                return ApprovalRequirement.NEVER

        source = _OneGatedTool()
        probe = _ParkProbe()
        _, journal, _ = await self.run(probe.observe, approvals=source)

        assert source.seen, "the approval source was never consulted"
        assert journal.plans == [], "a turn with one gated call was still planned"
        assert probe.maximum_parked == 1

    @pytest.mark.parametrize("requirement", list(ApprovalRequirement))
    async def test_every_requirement_other_than_never_is_unplanned(
        self,
        requirement: ApprovalRequirement,
    ) -> None:
        """Iterated over the closed enum, so a member added later fails closed.

        A new vocabulary member cannot overlap unless somebody deliberately
        declares it wider than ``NEVER``, which the ordering makes impossible:
        ``NEVER`` is the last member and therefore the only one that is not a
        narrowing of something else.
        """

        probe = _ParkProbe()
        _, journal, _ = await self.run(
            probe.observe,
            approvals=DeclaredApprovalRequirementSource(
                (), unknown_requirement=requirement
            ),
        )

        if requirement is ApprovalRequirement.NEVER:
            assert self.segment_modes(journal) == {BatchSegmentMode.PARALLEL}
            assert probe.maximum_parked == _FANOUT
        else:
            assert journal.plans == [], f"{requirement} was planned into a batch"
            assert probe.maximum_parked == 1


class TestUnknownApprovalIsSerial(ApprovalGraphMixin):
    """Unknown means serial, structurally, for the new dimension too."""

    async def test_a_catalog_that_says_nothing_about_approval_is_serial(self) -> None:
        """The floor is ``UNKNOWN``, and the floor is what silence resolves to.

        Every catalog written before this dimension existed lands here. That is
        the intended blast radius: a deployment does not keep overlap it was
        never entitled to just because the question had not been asked yet.
        """

        undeclared = {
            _TOOL: (
                CapabilityConcurrencyDeclaration(
                    capability_ref=_CAPABILITY_REF,
                    source=PolicySource.PRODUCT_CATALOG,
                    mode=ConcurrencyMode.PARALLEL_SAFE,
                    side_effect=SideEffectKind.READ,
                    max_parallelism=_FANOUT,
                ),
            )
        }

        probe = _ParkProbe()
        _, journal, _ = await self.run(probe.observe, declarations=undeclared)

        assert journal.plans == []
        assert probe.maximum_parked == 1

    async def test_a_run_source_that_raises_is_serial(self) -> None:
        """A source that was asked and could not answer establishes nothing."""

        class _BrokenSource:
            def approval_requirement_for(self, **_kwargs: object) -> Any:
                raise RuntimeError("policy lane unavailable")

        _, journal, _ = await self.run(
            _ParkProbe().observe,
            approvals=_BrokenSource(),
        )

        assert journal.plans == []

    async def test_a_run_source_answering_off_vocabulary_is_serial(self) -> None:
        class _ChattySource:
            def approval_requirement_for(self, **_kwargs: object) -> Any:
                return "probably fine"

        _, journal, _ = await self.run(
            _ParkProbe().observe,
            approvals=_ChattySource(),
        )

        assert journal.plans == []

    async def test_no_run_source_leaves_the_catalog_untouched(self) -> None:
        """An absent *source* is not a declaration of ``UNKNOWN``.

        F6.1's rule is that an absent declaration leaves the established value
        alone; only a declared conservative value narrows. Collapsing the two
        would make wiring an approval source strictly worse than not wiring one.
        The fail-closed property is carried by the field's floor instead, which
        the test above pins.
        """

        _, journal, _ = await self.run(_ParkProbe().observe, approvals=None)

        assert self.segment_modes(journal) == {BatchSegmentMode.PARALLEL}

    async def test_a_capability_with_no_catalog_entry_is_unplannable(self) -> None:
        """The deliberate reach of this rule beyond approvals, stated once.

        Silence about a capability includes silence about whether it parks, so
        an undeclared call is no longer merely placed in a serial segment — it
        is not planned at all, and by the existing "one unusable call" rule it
        makes the whole turn unplannable.

        This is a real narrowing of what F6 plans and it is intended: the
        default deployment policy gates ``call_mcp_tool`` at ``write=ask``, so
        the common undeclared call is a parking one, and planning it would put a
        suspend under the coordinator. It can only make things more serial, and
        an unplanned turn is exactly the pre-F6 path.
        """

        probe = _ParkProbe()
        _, journal, admission = await self.run(probe.observe, declarations={})

        assert journal.plans == []
        assert admission.tracked_children == 0
        assert probe.maximum_parked == 1


class TestTheApprovalFoldOnlyNarrows:
    """Resolver-level properties, over the vocabulary rather than by example."""

    @staticmethod
    def _declare(
        source: PolicySource,
        requirement: ApprovalRequirement | None,
    ) -> CapabilityConcurrencyDeclaration:
        return CapabilityConcurrencyDeclaration(
            capability_ref=_CAPABILITY_REF,
            source=source,
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            approval_requirement=requirement,
        )

    @staticmethod
    def _resolve(*declarations: CapabilityConcurrencyDeclaration) -> Any:
        return ConcurrencyPolicyResolver().resolve(
            capability_ref=_CAPABILITY_REF,
            declarations=declarations,
        )

    @pytest.mark.parametrize("catalog", list(ApprovalRequirement))
    @pytest.mark.parametrize("provider", list(ApprovalRequirement))
    def test_a_later_source_can_only_make_a_capability_more_gated(
        self,
        catalog: ApprovalRequirement,
        provider: ApprovalRequirement,
    ) -> None:
        resolution = self._resolve(
            self._declare(PolicySource.PRODUCT_CATALOG, catalog),
            self._declare(PolicySource.TRUSTED_PROVIDER, provider),
        )

        expected = ApprovalRequirement.narrowest(catalog, provider)
        assert resolution.approval_requirement is expected
        assert resolution.approval_requirement.rank <= catalog.rank

    @pytest.mark.parametrize("catalog", list(ApprovalRequirement))
    @pytest.mark.parametrize("provider", list(ApprovalRequirement))
    def test_the_fold_commutes(
        self,
        catalog: ApprovalRequirement,
        provider: ApprovalRequirement,
    ) -> None:
        forward = self._resolve(
            self._declare(PolicySource.PRODUCT_CATALOG, catalog),
            self._declare(PolicySource.TRUSTED_PROVIDER, provider),
        )
        backward = self._resolve(
            self._declare(PolicySource.TRUSTED_PROVIDER, provider),
            self._declare(PolicySource.PRODUCT_CATALOG, catalog),
        )

        assert forward.approval_requirement is backward.approval_requirement
        assert forward.policy == backward.policy

    @pytest.mark.parametrize("requirement", list(ApprovalRequirement))
    def test_a_resolution_never_holds_may_park_and_parallel_at_once(
        self,
        requirement: ApprovalRequirement,
    ) -> None:
        """The pair the whole lane exists to forbid, over the closed enum."""

        resolution = self._resolve(
            self._declare(PolicySource.PRODUCT_CATALOG, requirement)
        )

        if requirement.may_park:
            assert resolution.policy.mode is ConcurrencyMode.SERIAL
        else:
            assert resolution.policy.mode is ConcurrencyMode.PARALLEL_SAFE

    def test_an_undeclared_approval_resolves_to_the_conservative_floor(self) -> None:
        resolution = self._resolve(self._declare(PolicySource.PRODUCT_CATALOG, None))

        assert resolution.approval_requirement is ApprovalRequirement.UNKNOWN
        assert resolution.policy.mode is ConcurrencyMode.SERIAL

    def test_the_digest_binds_the_narrowed_policy_not_the_declared_one(self) -> None:
        """Lineage describes what will run, not what was asked for."""

        gated = self._resolve(
            self._declare(PolicySource.PRODUCT_CATALOG, ApprovalRequirement.ALWAYS)
        )
        ungated = self._resolve(
            self._declare(PolicySource.PRODUCT_CATALOG, ApprovalRequirement.NEVER)
        )

        assert gated.policy_digest != ungated.policy_digest
        assert gated.policy_digest == gated.digest_of(gated.policy)

    def test_the_narrowing_never_touches_the_scheduling_bound(self) -> None:
        """``max_parallelism`` stays a declared bound, never a safety signal.

        Safety rides the closed vocabularies. Pinning the bound to one would
        also erase the difference between "bounded at one" and "declares no
        bound of its own", which the enclosing batch ceiling depends on.
        """

        policy = ConcurrencyPolicy(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            max_parallelism=8,
        )

        narrowed = policy.narrowed_by_approval(ApprovalRequirement.ALWAYS)

        assert narrowed.mode is ConcurrencyMode.SERIAL
        assert narrowed.max_parallelism == 8

    @pytest.mark.parametrize("requirement", list(ApprovalRequirement))
    def test_narrowing_is_idempotent(self, requirement: ApprovalRequirement) -> None:
        policy = ConcurrencyPolicy(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
        )

        once = policy.narrowed_by_approval(requirement)

        assert once.narrowed_by_approval(requirement) == once


class TestTheDeclaredApprovalWireVocabulary:
    """The operator catalog's half of the fact, parsed conservatively."""

    @staticmethod
    def _parse(raw: object) -> CapabilityConcurrencyDeclaration:
        return ConcurrencyDescriptorParser().parse(
            capability_ref=_CAPABILITY_REF,
            source=PolicySource.PRODUCT_CATALOG,
            payload={APPROVAL_REQUIREMENT_KEY: raw},
        )

    @pytest.mark.parametrize("requirement", list(ApprovalRequirement))
    def test_every_member_round_trips_from_its_wire_value(
        self,
        requirement: ApprovalRequirement,
    ) -> None:
        assert self._parse(requirement.value).approval_requirement is requirement

    @pytest.mark.parametrize("raw", ["", "  ", "sometimes", 7, True, [], {}])
    def test_an_unparseable_value_falls_to_the_conservative_floor(
        self,
        raw: object,
    ) -> None:
        """Garbage from an operator catalog narrows; it never widens or raises."""

        assert self._parse(raw).approval_requirement is ApprovalRequirement.UNKNOWN

    def test_an_absent_key_declares_nothing(self) -> None:
        declaration = ConcurrencyDescriptorParser().parse(
            capability_ref=_CAPABILITY_REF,
            source=PolicySource.PRODUCT_CATALOG,
            payload={},
        )

        assert declaration.approval_requirement is None

    def test_the_wire_key_is_not_a_closed_policy_field(self) -> None:
        """``ConcurrencyPolicy`` is a published record; this fact is not on it.

        Pinned because it is a deliberate asymmetry rather than an oversight. If
        somebody later adds ``approval_requirement`` to ``ConcurrencyPolicy``,
        this fails and they are then obliged to extend the TypeScript mirror in
        ``packages/api-types`` in the same change.
        """

        assert APPROVAL_REQUIREMENT_KEY not in ConcurrencyPolicy.model_fields
        assert APPROVAL_REQUIREMENT_KEY in (
            CapabilityConcurrencyDeclaration.model_fields
        )


class TestTheRunScopedSourceIsReadFailClosed:
    """``GraphApprovalRequirementResolver`` is the only reading of a source."""

    @staticmethod
    def _resolve(source: object) -> ApprovalRequirement | None:
        return GraphApprovalRequirementResolver.resolve(
            source,  # type: ignore[arg-type]
            tool_name=_TOOL,
            arguments={},
        )

    def test_no_source_is_not_declared(self) -> None:
        assert self._resolve(None) is None

    @pytest.mark.parametrize("requirement", list(ApprovalRequirement))
    def test_a_declared_answer_is_returned_verbatim(
        self,
        requirement: ApprovalRequirement,
    ) -> None:
        assert (
            self._resolve(
                DeclaredApprovalRequirementSource((), unknown_requirement=requirement)
            )
            is requirement
        )

    def test_a_gated_tool_always_requires_approval(self) -> None:
        source = DeclaredApprovalRequirementSource(
            {_TOOL}, unknown_requirement=ApprovalRequirement.NEVER
        )

        assert self._resolve(source) is ApprovalRequirement.ALWAYS

    def test_blank_names_cannot_gate_everything(self) -> None:
        """A stray empty string in the gated set must not match every tool."""

        source = DeclaredApprovalRequirementSource(
            ["", "   "], unknown_requirement=ApprovalRequirement.NEVER
        )

        assert self._resolve(source) is ApprovalRequirement.NEVER


class TestPendingInterruptCountIsAFrameworkProperty(ApprovalGraphMixin):
    """What actually happens with N concurrent interrupts, measured three ways.

    This was the open question, and the answer is not the one the lane expected.
    The framework's tool node starts a turn's tool calls as concurrent tasks
    whatever F6 decides; the Step-2 exclusive permit serialises their *bodies*,
    but each body still reaches ``interrupt()`` inside the same superstep, so
    the turn ends with one pending interrupt per call regardless.

    ==========================  ===========  ===================
    scenario                    max parked   pending interrupts
    ==========================  ===========  ===================
    F6 unconfigured                       1                    3
    F6 admitting the cohort               3                    3
    F6 with the approval fact             1                    3
    ==========================  ===========  ===================

    So F6 neither caused nor can cure the multi-interrupt turn. What it *did*
    cause is the middle row: three parks held open at once inside one admitted
    cohort, each holding a segment permit while suspended on a human, and each
    settled durably as ``FAILED``. Restoring the top row is the whole of what
    this lane can honestly claim, and the three tests below are the three rows.
    """

    async def test_an_unconfigured_run_parks_one_at_a_time(self) -> None:
        probe = _ParkProbe()
        token = RunControlContext.bind_for_run(_binding())
        try:
            result = await self._graph(
                probe.observe, checkpointer=InMemorySaver()
            ).ainvoke(
                {"messages": [HumanMessage(content="Run all observations.")]},
                config={"configurable": {"thread_id": "parity-off"}},
            )
        finally:
            RunControlContext.unbind(token)

        # Every call arrived exactly once, and one at a time. *Which* order they
        # arrived in is not asserted: with no F6 install the only gate is the
        # Step-2 exclusive lock, whose queue is arrival order at the lock, and
        # that is the framework's scheduling of coroutines it started together.
        # Under load it is observably not input order and never promised to be.
        assert sorted(probe.arrived) == list(range(_FANOUT))
        assert probe.maximum_parked == 1
        assert len(list(result.get("__interrupt__") or ())) == _FANOUT

    async def test_an_admitted_cohort_parks_all_of_them_at_once(self) -> None:
        probe = _ParkProbe()
        result, _, _ = await self.run(
            probe.observe,
            approvals=None,
            checkpointer=InMemorySaver(),
            config={"configurable": {"thread_id": "parity-admitted"}},
        )

        assert probe.maximum_parked == _FANOUT
        assert len(list(result.get("__interrupt__") or ())) == _FANOUT

    async def test_the_approval_fact_restores_the_unconfigured_park_profile(
        self,
    ) -> None:
        """Feature-on with the fact known matches feature-off exactly.

        Same arrivals, same one-at-a-time parking, same interrupt count, and no
        journal at all — because the turn is handed back to the pre-F6 path
        rather than planned into a shape F6 would then have to manage.

        "Same arrivals" is the *set* of arrivals and the one-at-a-time profile,
        which is the whole of the parity claim. Arrival sequence is the exclusive
        lock's queue in both rows, and neither row promises input order — so
        pinning a sequence here would pin the scheduler, not the parity.
        """

        probe = _ParkProbe()
        result, journal, _ = await self.run(
            probe.observe,
            approvals=DeclaredApprovalRequirementSource({_TOOL}),
            checkpointer=InMemorySaver(),
            config={"configurable": {"thread_id": "parity-gated"}},
        )

        assert sorted(probe.arrived) == list(range(_FANOUT))
        assert probe.maximum_parked == 1
        assert len(list(result.get("__interrupt__") or ())) == _FANOUT
        assert journal.plans == []
