"""BUG-14b — the numbers the F1 discovery cases grade are actually produced.

Lane BUG-14's consuming half made ``quality.decision.v1`` able to *carry* four
bounded counts and taught the projector and the scorer to read them. Nothing
emitted them, so on a real system every one of them was ``None`` and every F1
numeric bound resolved to ``capability_discovery_counts_unobserved``: the cases
were runnable in principle and inert in practice.

This module is the proof that they are no longer inert. It drives the **real**
bridge — real catalog, real ranker, real two-tier expansion, real Operation
Gateway, real ``EventJournalRunControlStore`` — reads back what that run wrote
to the canonical journal, projects it with the production ``TrajectoryProjector``
and scores it with the production scorer against the **shipped corpus cases**.
Nothing here authors an observation.

Two of the three F1 discovery cases passed end to end on that real trajectory
when this module was written. The third, ``capability_discovery_selection
_recall``, did not, and its exact residual reason was pinned below rather than
papered over — it was a property of the case's own ceiling, not of the numbers.

That ceiling has now been revisited, which is what the pin existed to force.
BUG-17: the case declared ``minimum_recall_rank: 1`` alongside
``maximum_model_turns: 1``, and those contradict — one bridge call is one model
turn, so a one-turn ceiling admits exactly one call, a search, and a search
selects nothing to rank. The case is now a two-call trajectory whose rank is
read off the describe step, so **all three** cases pass on real emitted
numbers. ``TestTheSelectionRecallCaseIsSatisfiedByARealRun`` below is the
inverted pin: same subject, opposite verdict, and it says which assertion it
replaces.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from agent_runtime.capabilities.discovery import CapabilityBridgeToolName
from agent_runtime.capabilities.discovery.builder import AuthorizedCatalogBuilder
from agent_runtime.capabilities.discovery.contracts import CapabilityCatalogScope
from agent_runtime.capabilities.discovery.telemetry import (
    CapabilityDiscoveryObserverGroup,
)
from agent_runtime.harness_quality.evaluation import TrajectoryProjector
from agent_runtime.harness_quality.evaluation_contracts import TrajectoryManifest
from agent_runtime.harness_quality.operational_corpus import operational_corpus
from agent_runtime.harness_quality.scoring import CapabilityDiscoveryTrajectoryScorer
from runtime_api.schemas import RuntimeApiEventType

from tests.unit.agent_runtime.capabilities.discovery.test_bridge_chain import (
    _NOW,
    _READ_TOOL,
    _REFERENCE_KEY,
    _SELECTION_REF,
)
from tests.unit.agent_runtime.capabilities.discovery.test_telemetry import (
    _ORG,
    _SECRET_QUERY,
    ObservedBridgeHarness,
    _bound_run,
)

_RECALL = "capability_discovery_selection_recall"
_PROBE = "capability_discovery_unauthorized_probe"
_END_TO_END = "capability_discovery_end_to_end"
_UNAUTHORIZED = "cap_" + "0" * 32


def _case(family: str):  # type: ignore[no-untyped-def]
    return next(item for item in operational_corpus() if item.family == family).case


def _score(family: str, trajectory: TrajectoryManifest):  # type: ignore[no-untyped-def]
    return CapabilityDiscoveryTrajectoryScorer().score(
        case=_case(family),
        trajectory=trajectory,
    )


class RealEmissionHarness(ObservedBridgeHarness):
    """Drive the real bridge, then read the run's own journal back."""

    async def journal(self, harness_self: Any = None):  # type: ignore[no-untyped-def]
        """Bind a real run and mount the real journal recorder on the bridge."""

        store, context, recorder = await _bound_run(self)
        observer = CapabilityDiscoveryObserverGroup(observers=(recorder,))
        adapters, client, _seam, _catalog = await self.observed(context, observer)
        return store, context, adapters, client

    @staticmethod
    def release_gateway(operation_token, service_token) -> None:  # type: ignore[no-untyped-def]
        from agent_runtime.capabilities.mcp.gateway_context import (  # noqa: PLC0415
            McpOperationGatewayContext,
        )
        from agent_runtime.capabilities.operations.context import (  # noqa: PLC0415
            OperationContext,
        )

        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    @staticmethod
    async def decisions(store, context):  # type: ignore[no-untyped-def]
        events = await store.list_events_after(
            org_id=_ORG,
            run_id=context.run_id,
            after_sequence=0,
        )
        return tuple(
            event
            for event in events
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
        )

    @classmethod
    async def trajectory(cls, store, context) -> TrajectoryManifest:  # type: ignore[no-untyped-def]
        """Project the run's whole real event stream, exactly as F1 would.

        Every event the run wrote is projected, not just the F3 decisions: the
        projector requires the contiguous sequence, and feeding it the real one
        is also what proves the ``feature == "f3"`` discriminator survives a
        journal that holds other features' rows — this run's ``control_bound``
        row sits at sequence 1 and must contribute no discovery step.
        """

        events = await store.list_events_after(
            org_id=_ORG,
            run_id=context.run_id,
            after_sequence=0,
        )
        return TrajectoryProjector(redaction_policy_revision="redaction-v1").project(
            run_id=context.run_id,
            variant_id="candidate",
            events=events,
        )

    @staticmethod
    def discovery_steps(trajectory: TrajectoryManifest):  # type: ignore[no-untyped-def]
        """The steps the F3 scorer will look at, in run order."""

        return tuple(step for step in trajectory.ordered_steps if step.discovery_phase)

    async def drive_selection(self, adapters, context):  # type: ignore[no-untyped-def]
        """Search, then describe the capability the search offered.

        The shortest trajectory that contains a *selection*, which is the unit
        ``capability_discovery_selection_recall`` grades. It is
        :meth:`drive_chain` stopped one call early: the same real catalog, the
        same real ranker, the same real Operation Gateway, and the same target
        capability — only the invocation is left off, because recall is
        answered the moment a reference is picked.
        """

        operation_token, service_token = self.bind_gateway(context)
        try:
            found = await adapters[
                CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
            ].ainvoke({"query": _SECRET_QUERY, "limit": 10})
            await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
                {"capability_ref": self.ref_for(found, _READ_TOOL)}
            )
        finally:
            self.release_gateway(operation_token, service_token)

    async def probe_run(self, adapters, context):  # type: ignore[no-untyped-def]
        """Search, describe, and invoke a name this run is not authorized for.

        The reference is a syntactically valid one that no search ever offered,
        which is the guess the security case exists to catch.
        """

        operation_token, service_token = self.bind_gateway(context)
        try:
            await adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value].ainvoke(
                {"query": "payroll wire transfer approvals", "limit": 10}
            )
            await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
                {"capability_ref": _UNAUTHORIZED}
            )
            await adapters[CapabilityBridgeToolName.INVOKE_CAPABILITY.value].ainvoke(
                {"capability_ref": _UNAUTHORIZED, "arguments": {}}
            )
        finally:
            self.release_gateway(operation_token, service_token)


class TestARealRunPopulatesTheDecisionRow(RealEmissionHarness):
    """Before any case can grade a number, a real run has to write one."""

    async def test_a_real_chain_writes_counts_into_every_decision(self) -> None:
        store, context, adapters, _client = await self.journal()

        await self.drive_chain(adapters, context)

        decisions = await self.decisions(store, context)
        assert len(decisions) == 3
        payloads = [event.payload for event in decisions]
        # Known for every bridge call, so present on every row.
        assert all(payload["result_tokens"] > 0 for payload in payloads)
        assert all(payload["model_turns"] == 1 for payload in payloads)
        # A search has a candidate list; a describe and an invoke do not, and
        # a zero there would be a measurement nobody took.
        assert payloads[0]["candidate_count"] >= 1
        assert payloads[1]["candidate_count"] is None
        assert payloads[2]["candidate_count"] is None

    async def test_the_counts_reach_the_trajectory_the_scorer_reads(self) -> None:
        store, context, adapters, _client = await self.journal()

        await self.drive_chain(adapters, context)

        trajectory = await self.trajectory(store, context)
        steps = self.discovery_steps(trajectory)
        assert len(trajectory.ordered_steps) > len(steps)
        assert [step.discovery_phase for step in steps] == [
            "capability_search",
            "capability_describe",
            "capability_invoke",
        ]
        assert all(step.discovery_counts_observed for step in steps)
        assert all(step.discovery_model_turns == 1 for step in steps)


class TestSelectionRankIsCorrelatedNotAuthored(RealEmissionHarness):
    """The one fact that spans two bridge calls, measured across them."""

    async def test_a_described_and_invoked_reference_reports_its_offer_rank(
        self,
    ) -> None:
        """The rank is a *selection* fact, so it lands on the selecting call."""

        store, context, adapters, _client = await self.journal()

        await self.drive_chain(adapters, context)

        payloads = [event.payload for event in await self.decisions(store, context)]
        # A search offers references; it selects none, so it reports no rank.
        assert payloads[0]["selection_rank"] is None
        # Describe and invoke each named a reference the search had offered.
        assert payloads[1]["selection_rank"] >= 1
        assert payloads[2]["selection_rank"] == payloads[1]["selection_rank"]

    async def test_a_reference_no_search_offered_reports_an_observed_zero(
        self,
    ) -> None:
        """The guessed reference: measured, and measured as never offered.

        ``0`` here is a real answer rather than a gap, which is what lets the
        probe case's ``maximum_recall_rank: 0`` be satisfied by evidence.
        """

        store, context, adapters, _client = await self.journal()

        await self.probe_run(adapters, context)

        payloads = [event.payload for event in await self.decisions(store, context)]
        assert payloads[1]["selection_rank"] == 0
        assert payloads[2]["selection_rank"] == 0

    async def test_the_correlation_is_shared_across_the_three_wrappers(self) -> None:
        """The search and the describe are different objects; the map is not.

        If each wrapper kept its own offer history the describe below would
        report ``0`` — never offered — instead of the rank the search recorded.
        """

        store, context, adapters, _client = await self.journal()
        search = adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]
        describe = adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value]
        assert search is not describe

        found = await search.ainvoke({"query": "linear issues", "limit": 10})
        ref = found["search"]["candidates"][0]["capability_ref"]
        await describe.ainvoke({"capability_ref": ref})

        payloads = [event.payload for event in await self.decisions(store, context)]
        assert payloads[1]["selection_rank"] == 1

    async def test_no_reference_travels_with_the_rank(self) -> None:
        """Body-free against a real correlated run, not by reading field names.

        The correlator holds opaque references in memory to compute a position.
        This drives a real selection and then greps everything that run made
        durable for the reference it correlated against.
        """

        store, context, adapters, _client = await self.journal()
        search = adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]

        found = await search.ainvoke({"query": "linear issues", "limit": 10})
        ref = found["search"]["candidates"][0]["capability_ref"]
        await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
            {"capability_ref": ref}
        )

        decisions = await self.decisions(store, context)
        assert decisions[1].payload["selection_rank"] == 1
        serialized = "".join(event.model_dump_json() for event in decisions)
        assert ref not in serialized
        assert ref[4:] not in serialized


class TestTheShippedCasesGradeARealTrajectory(RealEmissionHarness):
    """The point of the whole lane: real numbers, shipped cases, no fixtures."""

    async def test_a_real_end_to_end_run_passes_the_end_to_end_case(self) -> None:
        """Inverts the BUG-14 premise for ``capability_discovery_end_to_end``.

        Before the producer existed this trajectory scored
        ``capability_discovery_counts_unobserved``; before the consuming half it
        scored ``capability_discovery_recall_missing``. It passes now, on
        numbers a real run measured.
        """

        store, context, adapters, _client = await self.journal()

        await self.drive_chain(adapters, context)

        result = _score(_END_TO_END, await self.trajectory(store, context))

        assert result.passed, result.reason_code

    async def test_a_real_probe_run_passes_the_security_case(self) -> None:
        """The probe's two ceilings now read observed zeroes, not absent data."""

        store, context, adapters, _client = await self.journal()

        await self.probe_run(adapters, context)

        result = _score(_PROBE, await self.trajectory(store, context))

        assert result.passed, result.reason_code

    async def test_no_case_scores_counts_unobserved_on_a_real_run(self) -> None:
        """The defect BUG-14b names, asserted directly.

        Every numeric bound these cases declare used to refuse to grade because
        nothing measured its quantity. None of them refuses now.
        """

        store, context, adapters, _client = await self.journal()

        await self.drive_chain(adapters, context)
        trajectory = await self.trajectory(store, context)

        for family in (_RECALL, _PROBE, _END_TO_END):
            assert (
                _score(family, trajectory).reason_code
                != "capability_discovery_counts_unobserved"
            ), family


class RankerRegressionHarness(RealEmissionHarness):
    """The same real bridge over a catalog with one similarly named distractor.

    The distractor is the regression: it is a card an operator could genuinely
    install, it matches the same query as the capability the chain actually
    needs, and it outranks it. Nothing about the run is faked — the model
    searches, describes, and invokes exactly as it does in the healthy chain,
    and every outcome is still ``ok``. Only the position the target came back at
    moves, which is the one thing selection recall is for.
    """

    def catalog(self, context):  # type: ignore[no-untyped-def]
        mirror = self.make_card(
            name="linear_issues_mirror",
            short_description="Track and read Linear issues for a team.",
            required_scopes=("docs:read",),
        ).model_copy(update={"server_id": "srv_linear_mirror"})
        return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=CapabilityCatalogScope.from_context(
                context,
                profile_id="research",
                policy_revision="policy_bug08",
                connector_scope_revision="scope_bug08",
            ),
            task_policy_selection_ref=_SELECTION_REF,
            mcp_server_cards=(self.card(), mirror),
            expires_at=_NOW + timedelta(minutes=15),
        )


class TestTheEndToEndCaseStillBitesOnARealRun(RealEmissionHarness):
    """A case that passes against a broken system is worse than no case."""

    async def test_a_leaking_probe_fails_the_security_case_on_a_real_run(
        self,
    ) -> None:
        """A search that *does* return the probed reference is caught.

        Here the probe's own search is answered with the run's real catalog, so
        ``candidate_count`` is positive where the case demands zero. The failure
        comes from a runtime row, not from an authored one.
        """

        store, context, adapters, _client = await self.journal()
        operation_token, service_token = self.bind_gateway(context)
        try:
            await adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value].ainvoke(
                {"query": "linear issues", "limit": 10}
            )
            await adapters[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].ainvoke(
                {"capability_ref": _UNAUTHORIZED}
            )
            await adapters[CapabilityBridgeToolName.INVOKE_CAPABILITY.value].ainvoke(
                {"capability_ref": _UNAUTHORIZED, "arguments": {}}
            )
        finally:
            self.release_gateway(operation_token, service_token)

        result = _score(_PROBE, await self.trajectory(store, context))

        assert not result.passed
        assert result.reason_code == "capability_discovery_candidate_count_exceeded"


class TestARankerRegressionIsCaughtOnARealRun(RankerRegressionHarness):
    """The numeric half of end-to-end quality, from a real ranking.

    This is the mutation that would have been impossible before this lane: the
    chain answers ``ok`` at every phase, so every non-numeric assertion the case
    makes passes, and the case still fails — on a rank a real search produced
    and a real invocation selected.
    """

    async def test_a_distractor_that_outranks_the_target_fails_end_to_end(
        self,
    ) -> None:
        store, context, adapters, _client = await self.journal()

        result = await self.drive_chain(adapters, context)
        assert "error" not in result["invoke"], result["invoke"]

        trajectory = await self.trajectory(store, context)
        steps = self.discovery_steps(trajectory)
        assert [step.discovery_outcome for step in steps] == ["ok", "ok", "ok"]
        # Rank 2 in the healthy catalog; one distractor moves it to 3.
        assert [step.discovery_recall_rank for step in steps] == [0, 3, 3]

        scored = _score(_END_TO_END, trajectory)

        assert not scored.passed
        assert scored.reason_code == "capability_discovery_recall_rank_exceeded"

    async def test_the_healthy_catalog_is_what_makes_that_a_regression(self) -> None:
        """The control: without the distractor the identical chain passes.

        Without this the test above would be indistinguishable from a chain that
        never satisfied the case at all.
        """

        store, context, adapters, _client = await RealEmissionHarness().journal()

        await RealEmissionHarness().drive_chain(adapters, context)

        trajectory = await RealEmissionHarness.trajectory(store, context)
        steps = RealEmissionHarness.discovery_steps(trajectory)
        assert [step.discovery_recall_rank for step in steps] == [0, 2, 2]
        assert _score(_END_TO_END, trajectory).passed


class TestTheSelectionRecallCaseIsSatisfiedByARealRun(RealEmissionHarness):
    """Inverts ``TestTheSelectionRecallCaseRemainsFixtureShaped`` (BUG-17).

    That class pinned ``capability_discovery_selection_recall`` as the one case
    a real trajectory could not satisfy, and named the reason exactly:
    ``minimum_recall_rank: 1`` together with ``maximum_model_turns: 1``. One
    bridge call is one model turn, so a one-turn ceiling admitted exactly one
    call — a search. A search offers references and selects none, and a rank is
    a fact about a selection, so the two bounds were jointly satisfiable only by
    a producer that invented a rank on the search step.

    That refusal to invent one was correct and is preserved: a search reporting
    "my best candidate is at position 1" would make the bound ``min``-dominant
    across the whole trajectory, taking ``maximum_recall_rank`` off the
    end-to-end case entirely and letting the ranker regression proved above
    pass. The producer is therefore unchanged. What changed is the **case**: it
    now spans search *and* describe, reads the rank where the producer honestly
    reports it, and carries a ceiling of two turns — the fewest that admit a
    selection at all, so the budget is still the tightest one this property can
    honestly declare, as the chain test below shows.
    """

    async def test_a_real_search_and_describe_run_passes_the_selection_recall_case(
        self,
    ) -> None:
        """The defect BUG-17 named, asserted directly: no fixture authorship.

        Real catalog, real ranker, real gateway, real journal. Every number the
        case grades was measured by the run, and the case goes green.
        """

        store, context, adapters, _client = await self.journal()

        await self.drive_selection(adapters, context)
        trajectory = await self.trajectory(store, context)

        result = _score(_RECALL, trajectory)

        assert result.passed, result.reason_code

    async def test_the_passing_run_measured_every_bound_the_case_declares(
        self,
    ) -> None:
        """Green because the numbers were observed, not because they were absent.

        Read together with the scorer's ``counts_unobserved`` refusal, this is
        what stops the test above from being satisfiable by a producer that
        emits nothing: the rank is positive and inside the window, it sits on
        the describe step, and the search reports no rank at all.
        """

        store, context, adapters, _client = await self.journal()

        await self.drive_selection(adapters, context)
        trajectory = await self.trajectory(store, context)

        steps = self.discovery_steps(trajectory)
        assert [step.discovery_phase for step in steps] == [
            "capability_search",
            "capability_describe",
        ]
        assert all(step.discovery_counts_observed for step in steps)
        # The search offered candidates and ranked nothing; the describe made
        # the selection and reported the position it came back at.
        assert steps[0].discovery_candidate_count >= 1
        assert steps[0].discovery_recall_rank == 0
        assert steps[1].discovery_recall_rank >= 1

        expected = {
            assertion.scorer_id: assertion
            for assertion in _case(_RECALL).expected_assertions
        }["capability_discovery_trajectory"].expected
        assert isinstance(expected, dict)
        assert expected["minimum_recall_rank"] <= steps[1].discovery_recall_rank
        assert steps[1].discovery_recall_rank <= expected["maximum_recall_rank"]
        assert (
            sum(step.discovery_model_turns for step in steps)
            <= expected["maximum_model_turns"]
        )
        assert (
            sum(step.discovery_result_tokens for step in steps)
            <= expected["maximum_result_tokens"]
        )

    async def test_a_full_chain_still_exceeds_the_recall_budget(
        self,
    ) -> None:
        """Updates ``test_a_real_recall_run_fails_on_the_turn_ceiling_not_the_rank``.

        The ceiling moved from one turn to two, not to "whatever the run costs".
        A third bridge call still breaks it, so the case remains a budget and
        not a rubber stamp — and the rejection is still about cost rather than
        recall, because the rank a real chain measures is inside the window.
        """

        store, context, adapters, _client = await self.journal()

        await self.drive_chain(adapters, context)
        trajectory = await self.trajectory(store, context)

        result = _score(_RECALL, trajectory)

        assert not result.passed
        # Not ``recall_missing`` and not ``counts_unobserved``: the rank was
        # measured and it is inside the case's window. What rejects this run is
        # one of the two budgets — three bridge calls cost three turns and
        # roughly four hundred tokens against ceilings of two and four hundred —
        # and which of the two trips first is not the point being pinned.
        assert result.reason_code in {
            "capability_discovery_result_tokens_exceeded",
            "capability_discovery_model_turns_exceeded",
        }
        expected = {
            assertion.scorer_id: assertion
            for assertion in _case(_RECALL).expected_assertions
        }["capability_discovery_trajectory"].expected
        assert isinstance(expected, dict)
        best_rank = min(
            step.discovery_recall_rank
            for step in self.discovery_steps(trajectory)
            if step.discovery_recall_rank > 0
        )
        assert expected["minimum_recall_rank"] <= best_rank
        assert best_rank <= expected["maximum_recall_rank"]

    async def test_a_single_search_run_is_not_a_recall_run(
        self,
    ) -> None:
        """Inverts ``test_a_single_search_run_measures_everything_except_a_rank``.

        The measurement that test made still holds — a lone search observes its
        counts and reports no rank, because it selected nothing. What changed is
        the verdict: the case no longer *admits* that trajectory as a complete
        recall run, so it is rejected for the missing selection step rather than
        scored as a recall miss. That distinction is the fix: a run that never
        selected anything is unmeasured, not failing.
        """

        store, context, adapters, _client = await self.journal()

        await adapters[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value].ainvoke(
            {"query": "linear issues", "limit": 10}
        )

        trajectory = await self.trajectory(store, context)
        (step,) = self.discovery_steps(trajectory)
        assert step.discovery_counts_observed
        assert step.discovery_candidate_count >= 1
        assert step.discovery_recall_rank == 0

        result = _score(_RECALL, trajectory)

        assert not result.passed
        assert result.reason_code == "capability_discovery_phase_missing"


@pytest.mark.parametrize("family", [_RECALL, _PROBE, _END_TO_END])
def test_every_f3_case_declares_a_numeric_bound(family: str) -> None:
    """The premise of this whole module: there is a number to grade.

    If a case stopped declaring numeric bounds it would go green for the wrong
    reason and every assertion above would still pass.
    """

    assertions = {
        assertion.scorer_id: assertion
        for assertion in _case(family).expected_assertions
    }
    expected = assertions["capability_discovery_trajectory"].expected

    assert isinstance(expected, dict)
    assert {
        "minimum_recall_rank",
        "maximum_recall_rank",
        "maximum_candidate_count",
        "maximum_result_tokens",
        "maximum_model_turns",
    } & set(expected)
